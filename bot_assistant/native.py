"""Manage an isolated AstrBot + NapCat project on Windows without Docker."""

from __future__ import annotations

import base64
from collections import deque
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import urllib.request
import http.cookiejar
import zipfile
from pathlib import Path

from server.manager import (PROTOCOL, QR_FRESH_SECONDS, QR_LIFETIME_SECONDS,
                            install_thinking, set_thinking, thinking_status,
                            validate_name, validate_qq)

from .config import NativeProfile
from .native_runtime import NO_CONSOLE, prepare_runtime
from .native_settings import BotSettings, write_config
from .remote import RemoteError


def _atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    candidate = path.with_suffix(path.suffix + ".new")
    candidate.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(candidate, 0o600)
    os.replace(candidate, path)


def _dashboard_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000)
    return f"pbkdf2_sha256$600000${salt.hex()}${digest.hex()}"


def _running(pid: int) -> bool:
    if not pid:
        return False
    if os.name != "nt":
        return False
    import ctypes
    kernel = ctypes.windll.kernel32
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def _stop(pid: int) -> None:
    if _running(pid):
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=25, creationflags=NO_CONSOLE)


def _port(reserved: set[int], start: int, end: int) -> int:
    for port in range(start, end):
        if port in reserved:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RemoteError("no_port", "没有可用的本机端口")


class NativeLocal:
    def __init__(self, profile: NativeProfile):
        profile.validate()
        self.profile = profile
        self.root = Path(profile.project_path).expanduser()
        self.home = self.root / "bot-assistant"
        self.accounts_file = self.home / "accounts.json"

    def _accounts(self) -> dict:
        if not self.accounts_file.exists():
            return {}
        data = json.loads(self.accounts_file.read_text(encoding="utf-8"))
        if data.get("schema") != 1 or not isinstance(data.get("accounts"), dict):
            raise RemoteError("registry_invalid", "本机账号清单格式无效")
        return data["accounts"]

    def _save(self, accounts: dict) -> None:
        _atomic(self.accounts_file, {"schema": 1, "accounts": accounts})

    def _runtime(self) -> dict[str, Path]:
        paths = {"astrbot": self.root / "runtime" / "astrbot",
                 "napcat": self.root / "runtime" / "napcat-template",
                 "qq": self.root / "runtime" / "qq" / "Files" / "QQ.exe"}
        if (not (paths["astrbot"] / "backend" / "python" / "python.exe").exists()
            or not (paths["napcat"] / "NapCatWinBootMain.exe").exists()
            or not paths["qq"].exists()):
            raise RemoteError("runtime_missing", "本机组件未安装。请在服务器设置中重新执行部署。")
        return paths

    def preflight(self) -> dict:
        if os.name != "nt" or os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() not in {"AMD64", "ARM64"}:
            raise RemoteError("windows_required", "免 Docker 模式目前需要 Windows 10/11 x64")
        if (self.root / "compose.yaml").exists() or (self.root / ".bot-assistant-local-project").exists():
            raise RemoteError("project_exists", "此目录是 Docker 项目。请选择新的空目录，避免覆盖现有数据。")
        if self.root.exists() and not (self.home / "native-v1").exists() and any(self.root.iterdir()):
            raise RemoteError("project_exists", "所选目录已有文件。请使用新的空目录，避免覆盖原有项目。")
        if not (self.home / "native-v1").exists():
            with socket.socket() as sock:
                try:
                    sock.bind(("127.0.0.1", self.profile.dashboard_port))
                except OSError as exc:
                    raise RemoteError("dashboard_port_used", "AstrBot 面板端口已被占用，请选择其他端口") from exc
        return {"windows": True, "project_path": str(self.root)}

    def deploy(self, settings: BotSettings, dashboard_password: str, progress=None) -> dict:
        self.preflight()
        if not dashboard_password or len(dashboard_password) < 12:
            raise ValueError("控制台密码至少需要 12 位")
        self.root.mkdir(parents=True, exist_ok=True)
        self.home.mkdir(exist_ok=True)
        (self.home / "native-v1").write_text("native-v1\n", encoding="utf-8")
        paths = prepare_runtime(self.root, progress)
        write_config(self.root, settings, self.profile.dashboard_port)
        config_path = self.root / "data" / "cmd_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        config.setdefault("dashboard", {}).update({"username": "astrbot", "password": "",
                                                    "pbkdf2_password": _dashboard_hash(dashboard_password),
                                                    "password_storage_upgraded": True,
                                                    "password_change_required": False})
        _atomic(config_path, config)
        self._start_astrbot(paths)
        self._wait_astrbot()
        if settings.persona_prompt.strip():
            self._configure_persona(settings, dashboard_password, progress)
        _atomic(self.home / "settings_public.json", {
            "persona_name": settings.persona_name, "persona_prompt": settings.persona_prompt})
        return {"path": str(self.root), "dashboard_port": self.profile.dashboard_port}

    def update_settings(self, settings: BotSettings, dashboard_password: str = "", progress=None) -> dict:
        self._runtime()
        config_path = self.root / "data" / "cmd_config.json"
        if not config_path.exists():
            raise RemoteError("not_initialized", "项目尚未部署")
        if settings.persona_prompt.strip() and not dashboard_password:
            raise ValueError("修改角色设定时，请输入 AstrBot 控制台密码")
        write_config(self.root, settings, self.profile.dashboard_port)
        if not settings.persona_prompt.strip():
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            config.setdefault("agent_runner", {}).setdefault("config", {}).setdefault("persona", {})["persona_id"] = "default"
            _atomic(config_path, config)
        if dashboard_password:
            if len(dashboard_password) < 12:
                raise ValueError("控制台密码至少需要 12 位")
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            config["dashboard"].update({"username": "astrbot", "password": "",
                                        "pbkdf2_password": _dashboard_hash(dashboard_password),
                                        "password_storage_upgraded": True})
            _atomic(config_path, config)
        self._restart_astrbot()
        self._wait_astrbot()
        if settings.persona_prompt.strip():
            self._configure_persona(settings, dashboard_password, progress)
        _atomic(self.home / "settings_public.json", {
            "persona_name": settings.persona_name, "persona_prompt": settings.persona_prompt})
        return {"updated": True}

    def current_settings(self) -> dict:
        path = self.root / "data" / "cmd_config.json"
        if not path.exists():
            return {}
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        def provider(ident):
            return next((x for x in config.get("provider", []) if x.get("id") == ident), {})
        source = next((x for x in config.get("provider_sources", [])
                       if x.get("id") == "bot-assistant-source"), {})
        public = self.home / "settings_public.json"
        persona = json.loads(public.read_text(encoding="utf-8")) if public.exists() else {}
        return {"api_base": source.get("api_base", ""),
                "model": provider("bot-assistant-model").get("model", ""),
                "chat_enabled": config.get("provider_settings", {}).get("enable", True),
                "wake_prefix": config.get("provider_settings", {}).get("wake_prefix", ""),
                "friend_needs_prefix": config.get("platform_settings", {}).get("friend_message_needs_wake_prefix", False),
                "reply_with_mention": config.get("platform_settings", {}).get("reply_with_mention", False),
                "reply_with_quote": config.get("platform_settings", {}).get("reply_with_quote", False),
                "stt_enabled": config.get("provider_stt_settings", {}).get("enable", False),
                "stt_base": provider("bot-assistant-stt").get("api_base", ""),
                "stt_model": provider("bot-assistant-stt").get("model", "whisper-1"),
                "tts_enabled": config.get("provider_tts_settings", {}).get("enable", False),
                "tts_base": provider("bot-assistant-tts").get("api_base", ""),
                "tts_model": provider("bot-assistant-tts").get("model", "tts-1"),
                "tts_voice": provider("bot-assistant-tts").get("openai-tts-voice", "alloy"),
                **persona}

    def _configure_persona(self, settings: BotSettings, password: str, progress=None) -> None:
        if progress: progress("正在创建 AstrBot 角色…")
        base = f"http://127.0.0.1:{self.profile.dashboard_port}/api/v1"
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        auth = {}
        def request(method, route, data):
            body = json.dumps(data).encode("utf-8") if method != "GET" else None
            req = urllib.request.Request(base + route, body,
                                         headers={"Content-Type": "application/json", **auth}, method=method)
            with opener.open(req, timeout=10) as response:
                answer = json.load(response)
            if answer.get("status") != "ok":
                raise RemoteError("persona_failed", "AstrBot 拒绝角色设置：" + str(answer.get("message", ""))[:100])
            return answer.get("data", {})
        self._wait_astrbot()
        login = request("POST", "/auth/login", {"username": "astrbot", "password": password})
        if not isinstance(login, dict) or not login.get("token"):
            raise RemoteError("persona_auth", "AstrBot 控制台认证未返回令牌")
        auth["Authorization"] = "Bearer " + login["token"]
        ident = settings.persona_name.strip() or "bot-assistant-persona"
        existing = request("GET", "/personas", {})
        names = [x.get("persona_id") for x in existing] if isinstance(existing, list) else []
        route = "/personas/by-id" if ident in names else "/personas"
        request("PUT" if ident in names else "POST", route,
                {"persona_id": ident, "system_prompt": settings.persona_prompt.strip()})
        config_path = self.root / "data" / "cmd_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        config.setdefault("agent_runner", {}).setdefault("config", {}).setdefault("persona", {})["persona_id"] = ident
        _atomic(config_path, config)
        self._restart_astrbot()

    def _wait_astrbot(self) -> None:
        url = f"http://127.0.0.1:{self.profile.dashboard_port}/api/stat/start-time"
        for _ in range(80):
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, ValueError):
                time.sleep(1)
        raise RemoteError("astrbot_timeout", "AstrBot 启动超时，请查看项目日志")

    def _pid_path(self, name: str) -> Path:
        return self.home / "pids" / (name + ".json")

    def _pid(self, name: str) -> int:
        try:
            return int(json.loads(self._pid_path(name).read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError):
            return 0

    def _start(self, name: str, argv: list[str], cwd: Path, env: dict) -> int:
        if _running(self._pid(name)):
            return self._pid(name)
        log_dir = self.home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f"{name}.log").open("ab") as output:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=output,
                                       stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       creationflags=NO_CONSOLE)
        _atomic(self._pid_path(name), {"pid": process.pid, "started": int(time.time())})
        return process.pid

    def _start_astrbot(self, paths: dict[str, Path] | None = None) -> None:
        paths = paths or self._runtime()
        backend = paths["astrbot"] / "backend"
        env = os.environ.copy()
        env.update({"ASTRBOT_ROOT": str(self.root), "ASTRBOT_DASHBOARD_HOST": "127.0.0.1",
                    "ASTRBOT_DASHBOARD_PORT": str(self.profile.dashboard_port),
                    "ASTRBOT_DESKTOP_CLIENT": "1", "PYTHONUTF8": "1"})
        self._start("astrbot", [str(backend / "python" / "python.exe"),
                               str(backend / "launch_backend.py")], backend, env)

    def _account_folder(self, qq: str) -> Path:
        return self.home / "accounts" / qq

    def _configure_platform(self, qq: str, ws_port: int, *, enabled: bool) -> None:
        config_path = self.root / "data" / "cmd_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        platforms = config.setdefault("platform", [])
        ident = f"bot-assistant-qq-{qq}"
        platforms[:] = [item for item in platforms if not (isinstance(item, dict) and item.get("id") == ident)]
        if enabled:
            platforms.append({"id": ident, "type": "aiocqhttp", "enable": True,
                              "ws_reverse_host": "127.0.0.1", "ws_reverse_port": ws_port,
                              "ws_reverse_token": ""})
        _atomic(config_path, config)

    def _restart_astrbot(self) -> None:
        _stop(self._pid("astrbot"))
        self._start_astrbot()

    def _start_napcat(self, qq: str, account: dict) -> None:
        paths = self._runtime()
        folder = self._account_folder(qq)
        shell = folder / "shell"
        if not (shell / "NapCatWinBootMain.exe").exists():
            shutil.copytree(paths["napcat"], shell, dirs_exist_ok=True)
        roaming = folder / "appdata" / "Roaming"
        local = folder / "appdata" / "Local"
        roaming.mkdir(parents=True, exist_ok=True)
        local.mkdir(parents=True, exist_ok=True)
        main = shell / "napcat.mjs"
        (shell / "loadNapCat.js").write_text(
            f'(async () => {{await import({json.dumps(main.as_uri())})}})()\n', encoding="utf-8")
        config_file = shell / "config" / f"onebot11_{qq}.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)
        (shell / "cache" / "qrcode.png").unlink(missing_ok=True)
        onebot = {"network": {"httpServers": [{"enable": True, "name": "bot-assistant-local",
                   "host": "127.0.0.1", "port": account["http_port"], "enableCors": False,
                   "enableWebsocket": False, "messagePostFormat": "array",
                   "token": account["token"], "debug": False, "heartInterval": 30000}],
                   "httpSseServers": [], "httpClients": [], "websocketServers": [],
                   "websocketClients": [{"enable": True, "name": "astrbot-native",
                   "url": f'ws://127.0.0.1:{account["ws_port"]}/ws',
                   "messagePostFormat": "array", "reportSelfMessage": False,
                   "reconnectInterval": 5000, "token": "", "debug": False,
                   "heartInterval": 30000}], "plugins": []}}
        _atomic(config_file, onebot)
        env = os.environ.copy()
        env.update({"APPDATA": str(roaming), "LOCALAPPDATA": str(local),
                    "NAPCAT_PATCH_PACKAGE": str(shell / "qqnt.json"),
                    "NAPCAT_LOAD_PATH": str(shell / "loadNapCat.js"),
                    "NAPCAT_INJECT_PATH": str(shell / "NapCatWinBootHook.dll"),
                    "NAPCAT_LAUNCHER_PATH": str(shell / "NapCatWinBootMain.exe"),
                    "NAPCAT_MAIN_PATH": str(main), "NAPCAT_WEBUI_HOST": "127.0.0.1"})
        self._start(f"napcat-{qq}", [str(shell / "NapCatWinBootMain.exe"),
                                    str(paths["qq"]), str(shell / "NapCatWinBootHook.dll"), qq],
                    shell, env)

    def _state(self, qq: str, account: dict) -> tuple[str, str]:
        if not _running(self._pid(f"napcat-{qq}")):
            return "offline", "NapCat 未运行"
        request = urllib.request.Request(f'http://127.0.0.1:{account["http_port"]}/get_status',
                                         headers={"Authorization": f'Bearer {account["token"]}'})
        try:
            with urllib.request.urlopen(request, timeout=2) as response:
                online = json.load(response).get("data", {}).get("online")
            if online is True:
                return "online", "QQ 已登录"
            if online is False:
                return "offline", "等待 QQ 登录"
        except (OSError, ValueError, AttributeError):
            pass
        return "unknown", "NapCat 接口尚未就绪"

    def _qr(self, qq: str) -> dict | None:
        path = self._account_folder(qq) / "shell" / "cache" / "qrcode.png"
        if not path.is_file():
            return None
        age = max(0, int(time.time() - path.stat().st_mtime))
        if age >= QR_FRESH_SECONDS:
            return None
        content = path.read_bytes()
        if not content.startswith(b"\x89PNG\r\n\x1a\n") or len(content) > 2_000_000:
            raise RemoteError("qr_invalid", "NapCat 登录码图片无效")
        return {"qq": qq, "state": "qr_ready", "age_seconds": age,
                "ttl_seconds": QR_LIFETIME_SECONDS,
                "qr_png_base64": base64.b64encode(content).decode("ascii")}

    def _service_names(self) -> list[str]:
        return ["astrbot", *[f"napcat-{qq}" for qq in self._accounts()]]

    def call(self, action: str, payload: dict | None = None, timeout: int = 35) -> dict:
        payload = payload or {}
        if action == "preflight":
            result = self.preflight()
        elif action == "list":
            self._runtime()
            self._start_astrbot()
            accounts = self._accounts()
            result = {"accounts": []}
            for qq, item in sorted(accounts.items(), key=lambda pair: pair[1]["created"]):
                if not _running(self._pid(f"napcat-{qq}")):
                    self._start_napcat(qq, item)
                state, detail = self._state(qq, item)
                result["accounts"].append({"qq": qq, "name": item["name"],
                                           "state": state, "detail": detail,
                                           "mode": "managed"})
        elif action == "add":
            qq, name = validate_qq(payload.get("qq")), validate_name(payload.get("name"))
            accounts = self._accounts()
            if qq in accounts:
                raise RemoteError("duplicate", "这个 QQ 号已添加")
            self._runtime()
            reserved = {v["ws_port"] for v in accounts.values()} | {v["http_port"] for v in accounts.values()}
            account = {"name": name, "created": time.time(),
                       "ws_port": _port(reserved, 22000, 25000),
                       "http_port": _port(reserved, 26000, 29000),
                       "token": secrets.token_urlsafe(32)}
            self._configure_platform(qq, account["ws_port"], enabled=True)
            try:
                self._restart_astrbot()
                self._start_napcat(qq, account)
                accounts[qq] = account
                self._save(accounts)
            except Exception:
                _stop(self._pid(f"napcat-{qq}"))
                self._configure_platform(qq, account["ws_port"], enabled=False)
                self._restart_astrbot()
                raise
            result = {"qq": qq, "name": name, "state": "unknown"}
        elif action == "rename":
            qq, name = validate_qq(payload.get("qq")), validate_name(payload.get("name"))
            accounts = self._accounts()
            if qq not in accounts:
                raise RemoteError("not_found", "找不到这个账号")
            accounts[qq]["name"] = name
            self._save(accounts)
            result = {"qq": qq, "name": name}
        elif action == "remove":
            qq = validate_qq(payload.get("qq"))
            accounts = self._accounts()
            if qq not in accounts:
                raise RemoteError("not_found", "找不到这个账号")
            _stop(self._pid(f"napcat-{qq}"))
            self._configure_platform(qq, accounts[qq]["ws_port"], enabled=False)
            del accounts[qq]
            self._save(accounts)
            self._restart_astrbot()
            result = {"qq": qq, "data_kept": True, "container_kept": False}
        elif action == "prepare":
            qq = validate_qq(payload.get("qq"))
            accounts = self._accounts()
            if qq not in accounts:
                raise RemoteError("not_found", "找不到这个账号")
            state, _ = self._state(qq, accounts[qq])
            if state == "online":
                result = {"qq": qq, "state": "online"}
            else:
                result = self._qr(qq)
                if result is None:
                    if state == "unknown":
                        for _ in range(12):
                            time.sleep(1)
                            result = self._qr(qq)
                            if result is not None:
                                break
                            state, _ = self._state(qq, accounts[qq])
                            if state == "online":
                                result = {"qq": qq, "state": "online"}
                                break
                    if result is None:
                        if state == "unknown":
                            raise RemoteError("status_unknown", "NapCat 状态未明确，暂不重启")
                        old = self._account_folder(qq) / "shell" / "cache" / "qrcode.png"
                        old.unlink(missing_ok=True)
                        _stop(self._pid(f"napcat-{qq}"))
                        self._start_napcat(qq, accounts[qq])
                        for _ in range(35):
                            time.sleep(2)
                            result = self._qr(qq)
                            if result is not None:
                                break
                            if self._state(qq, accounts[qq])[0] == "online":
                                result = {"qq": qq, "state": "online"}
                                break
                        if result is None:
                            raise RemoteError("qr_timeout", "登录码生成超时，请稍后重试")
        elif action.startswith("project_"):
            if payload.get("path") != self.profile.project_path:
                raise RemoteError("project_path_invalid", "项目路径与当前设置不一致")
            result = self._project(action, payload)
        else:
            raise RemoteError("usage", "不支持的操作")
        return {"protocol": PROTOCOL, "ok": True, **result}

    def _project(self, action: str, payload: dict) -> dict:
        names = self._service_names()
        if action == "project_status":
            services = [{"name": name,
                         "state": "running" if _running(self._pid(name)) else "stopped",
                         "status": "本机进程", "health": ""} for name in names]
            return {"path": str(self.root), "services": services,
                    "running": sum(x["state"] == "running" for x in services)}
        if action in {"project_logs", "project_restart"}:
            name = str(payload.get("service", ""))
            if name not in names:
                raise RemoteError("service_invalid", "所选服务不属于当前项目")
            if action == "project_restart":
                _stop(self._pid(name))
                if name == "astrbot": self._start_astrbot()
                else: self._start_napcat(name.removeprefix("napcat-"), self._accounts()[name[7:]])
                return {"service": name, "restarted": True}
            count = max(1, min(300, int(payload.get("lines", 120))))
            path = self.home / "logs" / (name + ".log")
            if path.exists():
                with path.open("r", encoding="utf-8", errors="replace") as stream:
                    lines = list(deque(stream, maxlen=count))
            else:
                lines = []
            content = "\n".join(lines)[-60000:]
            content = re.sub(r"(?i)(Bearer\s+)[^\s]+", r"\1[REDACTED]", content)
            content = re.sub(r"(?i)\b(token|api[_-]?key|password|secret)(\s*[:=]\s*)[^\s,;]+",
                             r"\1\2[REDACTED]", content)
            content = re.sub(r"https?://\S*(?:ptqrtoken|qrsig|sig|token)\S*", "[登录链接已隐藏]", content)
            return {"service": name, "text": content}
        if action == "project_backups":
            files = sorted((self.root / "backups").glob("bot-assistant-*.zip"), reverse=True)[:30]
            return {"backups": [{"name": f.name, "bytes": f.stat().st_size,
                                 "created": int(f.stat().st_mtime)} for f in files]}
        if action == "project_backup":
            directory = self.root / "backups"
            directory.mkdir(exist_ok=True)
            path = directory / f"bot-assistant-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.zip"
            temporary = path.with_suffix(".zip.part")
            try:
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                    for folder in (self.root / "data", self.home / "accounts"):
                        if folder.exists():
                            for file in folder.rglob("*"):
                                if file.is_file() and not file.is_symlink() and "shell" not in file.relative_to(folder).parts:
                                    zf.write(file, file.relative_to(self.root))
                    for file in (self.accounts_file, self.home / "settings_public.json"):
                        if file.exists(): zf.write(file, file.relative_to(self.root))
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return {"name": path.name, "bytes": path.stat().st_size, "stored_locally": True}
        if action == "project_configs":
            return {"configs": []}  # Secret-bearing native settings are edited through the wizard.
        if action == "project_config_read":
            raise RemoteError("config_sensitive", "原生模式配置包含凭据，请通过设置向导修改")
        if action == "project_plugins":
            plugins = []
            folder = self.root / "data" / "plugins"
            if folder.exists():
                for item in sorted(folder.iterdir()):
                    if item.is_dir() and not item.is_symlink():
                        plugins.append({"id": item.name, "name": item.name,
                                        "version": "", "description": ""})
            return {"plugins": plugins}
        if action == "project_bot_settings":
            config = json.loads((self.root / "data" / "cmd_config.json").read_text(encoding="utf-8-sig"))
            return {"provider_count": sum(not str(item.get("id", "")).startswith(
                        "bot-assistant-thinking-") for item in config.get("provider", [])
                        if isinstance(item, dict)),
                    "chat_enabled": bool(config.get("provider_settings", {}).get("enable", True)),
                    "persona_selected": bool(config.get("agent_runner", {}).get("config", {}).get("persona", {}).get("persona_id", "") != "default"),
                    "stt_enabled": bool(config.get("provider_stt_settings", {}).get("enable")),
                    "tts_enabled": bool(config.get("provider_tts_settings", {}).get("enable"))}
        if action == "project_thinking_status":
            return thinking_status(self.root)
        if action == "project_thinking_install":
            stamp = time.strftime("%Y%m%d-%H%M%S")
            backup = self.root / "backups" / f"thinking-control-{stamp}"
            backup.mkdir(parents=True, exist_ok=False)
            plugin = self.root / "data" / "plugins" / "astrbot_plugin_thinking_control"
            for path in (self.root / "data" / "cmd_config.json",
                         self.root / "data" / "settings" / "bot_assistant_thinking.json",
                         plugin / "main.py", plugin / "metadata.yaml"):
                if path.exists():
                    shutil.copy2(path, backup / path.name)
            result = install_thinking(self.root, payload.get("source"), payload.get("metadata"))
            self._restart_astrbot()
            self._wait_astrbot()
            return {**result, "backup": backup.name, "restarted": "astrbot"}
        if action == "project_thinking_set":
            return set_thinking(self.root, payload.get("platform_id"), payload.get("level"))
        raise RemoteError("usage", "不支持的项目操作")
