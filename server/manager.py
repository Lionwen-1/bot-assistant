#!/usr/bin/env python3
"""Small, daemon-free remote companion for bot助手.

Only the SSH owner can invoke it. Each command writes one JSON response to stdout.
No NapCat API token or QR payload is written to logs.
"""

from __future__ import annotations

import base64
import copy
try:
    import fcntl
except ImportError:  # Windows desktop mode
    fcntl = None
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

if os.name == "nt":
    import msvcrt

PROTOCOL = 1
VERSION = "0.5.0"
IMAGE = "mlikiowa/napcat-docker@sha256:2cc70b45244ae3fabc657ffc3aaa4de8fb893518171e519659ab83d80e6d36b5"
HOME = Path(os.environ.get("BOT_ASSISTANT_HOME", Path.home() / ".local/share/bot-assistant"))


def host_uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else 1000


def host_gid() -> int:
    return os.getgid() if hasattr(os, "getgid") else 1000


def file_lock(stream, acquire: bool) -> None:
    if fcntl is not None:
        fcntl.flock(stream, fcntl.LOCK_EX if acquire else fcntl.LOCK_UN)
    else:
        stream.seek(0)
        if stream.read(1) == b"":
            stream.seek(0)
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if acquire else msvcrt.LK_UNLCK, 1)
QQ_PATTERN = re.compile(r"^[1-9][0-9]{4,11}$")
CONTAINER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
QR_FRESH_SECONDS = 95
QR_LIFETIME_SECONDS = 110
MAX_QR_BYTES = 1_000_000
THINKING_LEVELS = ("none", "low", "high", "max")
THINKING_PLUGIN = "astrbot_plugin_thinking_control"
PROJECT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
SENSITIVE_KEY_PATTERN = re.compile(r"token|secret|password|api.?key|credential|private.?key|cookie", re.I)


class ManagerError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def validate_qq(value: object) -> str:
    qq = str(value)
    if not QQ_PATTERN.fullmatch(qq):
        raise ManagerError("invalid_qq", "QQ 号应为 5 至 12 位数字，且不能以 0 开头")
    return qq


def validate_name(value: object) -> str:
    name = str(value).strip()
    if not name or len(name) > 30 or any(ord(ch) < 32 for ch in name):
        raise ManagerError("invalid_name", "昵称应为 1 至 30 个可见字符")
    return name


def secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic_text(path: Path, content: str, mode: int = 0o600) -> None:
    secure_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Manager:
    def __init__(self, home: Path = HOME):
        self.home = home
        self.registry_path = home / "accounts.json"
        self.lock_path = home / ".lock"

    @contextmanager
    def locked(self):
        secure_dir(self.home)
        with self.lock_path.open("a+b") as stream:
            os.chmod(self.lock_path, 0o600)
            file_lock(stream, True)
            try:
                yield
            finally:
                file_lock(stream, False)

    def registry(self) -> dict[str, dict]:
        if not self.registry_path.exists():
            return {}
        try:
            data = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if data.get("schema") != PROTOCOL or not isinstance(data.get("accounts"), dict):
                raise ValueError("unsupported schema")
            for qq, account in data["accounts"].items():
                validate_qq(qq)
                if (not isinstance(account, dict)
                    or not isinstance(account.get("name"), str)
                    or not isinstance(account.get("port"), int)
                    or not isinstance(account.get("token"), str)
                    or not isinstance(account.get("created"), (int, float))):
                    raise ValueError("invalid account")
                if account.get("mode", "managed") not in {"managed", "adopted"}:
                    raise ValueError("invalid account mode")
                if (account.get("mode") == "adopted" and
                    not CONTAINER_PATTERN.fullmatch(str(account.get("container", "")))):
                    raise ValueError("invalid adopted container")
                if "ws_port" in account and (not isinstance(account["ws_port"], int)
                    or not 1 <= account["ws_port"] <= 65535
                    or not CONTAINER_PATTERN.fullmatch(str(account.get("ws_container", "")))):
                    raise ValueError("invalid reverse WebSocket probe")
            return data["accounts"]
        except (OSError, ValueError, TypeError, ManagerError) as exc:
            raise ManagerError("registry_invalid", "服务器账号清单无法读取，请先备份并修复") from exc

    def save(self, accounts: dict[str, dict]) -> None:
        atomic_text(self.registry_path, json.dumps(
            {"schema": PROTOCOL, "accounts": accounts}, ensure_ascii=False,
            indent=2, sort_keys=True) + "\n")

    def _run(self, args: list[str], timeout: int = 25) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError("docker_unavailable", "无法运行 Docker 命令或操作超时") from exc

    def docker(self, *args: str, timeout: int = 25) -> str:
        result = self._run(["docker", *args], timeout)
        if result.returncode:
            raise ManagerError("docker_failed", (result.stderr or result.stdout).strip()[:220]
                               or "Docker 命令失败")
        return result.stdout.strip()

    def compose(self, qq: str, *args: str, timeout: int = 120) -> str:
        file = self.account_dir(qq) / "compose.yaml"
        return self.docker("compose", "-p", self.project(qq), "-f", str(file),
                           *args, timeout=timeout)

    def account_dir(self, qq: str) -> Path:
        return self.home / "accounts" / validate_qq(qq)

    @staticmethod
    def container(qq: str) -> str:
        return f"bot-assistant-u{host_uid()}-{validate_qq(qq)}"

    def container_name(self, qq: str) -> str:
        account = self.registry().get(validate_qq(qq), {})
        return account.get("container", self.container(qq))

    @staticmethod
    def project(qq: str) -> str:
        return f"ba-u{host_uid()}-{validate_qq(qq)}"

    def choose_port(self, accounts: dict[str, dict]) -> int:
        reserved = {item["port"] for item in accounts.values()}
        for port in range(21000, 25000):
            if port in reserved:
                continue
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port
        raise ManagerError("no_port", "服务器没有可用的本机端口")

    def write_account_files(self, qq: str, port: int, token: str) -> None:
        path = self.account_dir(qq)
        secure_dir(path)
        secure_dir(path / "config")
        secure_dir(path / "qq")
        onebot = {
            "network": {
                "httpServers": [{"enable": True, "name": "bot-assistant-local",
                                 "host": "0.0.0.0", "port": 3000,
                                 "enableCors": False, "enableWebsocket": False,
                                 "messagePostFormat": "array", "token": token,
                                 "debug": False, "heartInterval": 30000}],
                "httpSseServers": [], "httpClients": [], "websocketServers": [],
                "websocketClients": [], "plugins": []},
        }
        atomic_text(path / "config" / f"onebot11_{qq}.json",
                    json.dumps(onebot, ensure_ascii=False, indent=2) + "\n")
        compose = f'''services:
  napcat:
    image: {IMAGE}
    container_name: {self.container(qq)}
    restart: unless-stopped
    environment:
      NAPCAT_UID: "{host_uid()}"
      NAPCAT_GID: "{host_gid()}"
      ACCOUNT: "{qq}"
    ports:
      - "127.0.0.1:{port}:3000"
    volumes:
      - ./config:/app/napcat/config
      - ./qq:/app/.config/QQ
'''
        atomic_text(path / "compose.yaml", compose, 0o600)

    def preflight(self) -> dict:
        if sys.version_info < (3, 9):
            raise ManagerError("python_old", "服务器需要 Python 3.9 或更新版本")
        compose = self.docker("compose", "version", "--short")
        daemon = self.docker("info", "--format", "{{.ServerVersion}}")
        return {"python": sys.version.split()[0], "compose": compose, "docker": daemon}

    def _running(self, qq: str) -> bool | None:
        result = self._run(["docker", "inspect", "--format", "{{.State.Running}}",
                            self.container_name(qq)])
        if result.returncode:
            if "No such object" in result.stderr or "No such container" in result.stderr:
                return False
            return None
        return result.stdout.strip() == "true"

    def reverse_ws_connected(self, account: dict) -> bool | None:
        if "ws_port" not in account:
            return True
        script = ("import sys; p=int(sys.argv[1]); "
                  "print('yes' if any(len(x:=line.split())>3 and x[3]=='01' "
                  "and int(x[1].split(':')[1],16)==p "
                  "for line in open('/proc/net/tcp')) else 'no')")
        result = self._run(["docker", "exec", account["ws_container"],
                            "python3", "-c", script, str(account["ws_port"])])
        if result.returncode:
            return None
        return result.stdout.strip() == "yes"

    def state(self, qq: str, account: dict) -> tuple[str, str]:
        running = self._running(qq)
        if running is None:
            return "unknown", "无法查询容器"
        if not running:
            return "offline", "容器未运行"
        url = f"http://127.0.0.1:{account['port']}/get_status"
        request = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {account['token']}"})
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                data = json.load(response).get("data", {})
            online = data.get("online")
            if online is True:
                if self.reverse_ws_connected(account) is not True:
                    return "unknown", "QQ 在线，但聊天连接尚未确认"
                return "online", "QQ 已登录"
            if online is False:
                return "offline", "等待 QQ 登录"
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        return "unknown", "接口尚未就绪"

    def list_accounts(self) -> dict:
        with self.locked():
            accounts = self.registry()
            items = []
            for qq, account in sorted(accounts.items(), key=lambda pair: pair[1]["created"]):
                state, detail = self.state(qq, account)
                items.append({"qq": qq, "name": account["name"],
                              "state": state, "detail": detail,
                              "mode": account.get("mode", "managed")})
        return {"accounts": items}

    def adopt(self, qq: object, name: object, container: object,
              port: object, config_path: object, ws_container: object = None,
              ws_port: object = None) -> dict:
        """Register an existing NapCat container without changing or restarting it."""
        qq = validate_qq(qq)
        name = validate_name(name)
        container = str(container)
        if not CONTAINER_PATTERN.fullmatch(container):
            raise ManagerError("invalid_container", "容器名称无效")
        try:
            port = int(port)
        except (ValueError, TypeError) as exc:
            raise ManagerError("invalid_port", "接口端口无效") from exc
        if not 1 <= port <= 65535:
            raise ManagerError("invalid_port", "接口端口无效")
        if (ws_container is None) != (ws_port is None):
            raise ManagerError("invalid_probe", "聊天连接容器与端口必须同时提供")
        if ws_container is not None:
            ws_container = str(ws_container)
            if not CONTAINER_PATTERN.fullmatch(ws_container):
                raise ManagerError("invalid_probe", "聊天连接容器名称无效")
            try:
                ws_port = int(ws_port)
            except (ValueError, TypeError) as exc:
                raise ManagerError("invalid_probe", "聊天连接端口无效") from exc
            if not 1 <= ws_port <= 65535:
                raise ManagerError("invalid_probe", "聊天连接端口无效")
        path = Path(str(config_path))
        if path.name != f"onebot11_{qq}.json":
            raise ManagerError("config_invalid", "配置文件名与 QQ 号不匹配")
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            listeners = [item for item in config["network"]["httpServers"]
                         if item.get("enable") and item.get("token")]
            if len(listeners) != 1:
                raise ValueError("expected one authenticated HTTP listener")
            token = listeners[0]["token"]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise ManagerError("config_invalid", "无法从现有 NapCat 配置读取唯一的已启用 HTTP 接口") from exc
        with self.locked():
            accounts = self.registry()
            if qq in accounts:
                raise ManagerError("duplicate", "这个 QQ 号已添加")
            if any(item.get("container", self.container(number)) == container
                   or item["port"] == port for number, item in accounts.items()):
                raise ManagerError("duplicate_target", "容器或接口端口已被其他账号接管")
            running = self._run(["docker", "inspect", "--format", "{{.State.Running}}",
                                 container])
            if running.returncode or running.stdout.strip() != "true":
                raise ManagerError("container_unavailable", "现有容器未运行，接管已取消")
            candidate = {"name": name, "port": port, "token": token,
                         "created": time.time(), "mode": "adopted",
                         "container": container}
            if ws_container is not None:
                candidate.update({"ws_container": ws_container, "ws_port": ws_port})
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/get_status",
                headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(request, timeout=4) as response:
                    online = json.load(response).get("data", {}).get("online")
            except (OSError, ValueError, TypeError, AttributeError) as exc:
                raise ManagerError("account_unverified", "无法确认现有账号在线，接管已取消") from exc
            if online is not True:
                raise ManagerError("account_unverified", "无法确认现有账号在线，接管已取消")
            if self.reverse_ws_connected(candidate) is not True:
                raise ManagerError("chat_unverified", "无法确认聊天连接，接管已取消")
            accounts[qq] = candidate
            self.save(accounts)
        return {"qq": qq, "name": name, "state": "online", "mode": "adopted"}

    def add(self, qq: object, name: object) -> dict:
        qq = validate_qq(qq)
        name = validate_name(name)
        with self.locked():
            accounts = self.registry()
            if qq in accounts:
                raise ManagerError("duplicate", "这个 QQ 号已添加")
            self.preflight()
            port = self.choose_port(accounts)
            token = secrets.token_urlsafe(32)
            self.write_account_files(qq, port, token)
            try:
                self.compose(qq, "up", "-d", timeout=180)
            except ManagerError:
                # Compose may have partially created a container. Leave QQ data intact.
                self._run(["docker", "compose", "-p", self.project(qq), "-f",
                           str(self.account_dir(qq) / "compose.yaml"), "down"], timeout=60)
                raise
            accounts[qq] = {"name": name, "port": port, "token": token,
                            "created": time.time()}
            try:
                self.save(accounts)
            except OSError as exc:
                self._run(["docker", "compose", "-p", self.project(qq), "-f",
                           str(self.account_dir(qq) / "compose.yaml"), "down"], timeout=60)
                raise ManagerError("registry_write_failed", "账号已启动但清单保存失败，已回滚容器") from exc
        return {"qq": qq, "name": name, "state": "unknown"}

    def rename(self, qq: object, name: object) -> dict:
        qq = validate_qq(qq)
        name = validate_name(name)
        with self.locked():
            accounts = self.registry()
            if qq not in accounts:
                raise ManagerError("not_found", "找不到这个账号")
            accounts[qq]["name"] = name
            self.save(accounts)
        return {"qq": qq, "name": name}

    def remove(self, qq: object) -> dict:
        qq = validate_qq(qq)
        with self.locked():
            accounts = self.registry()
            if qq not in accounts:
                raise ManagerError("not_found", "找不到这个账号")
            if accounts[qq].get("mode") != "adopted":
                self.compose(qq, "stop", timeout=60)
                self.compose(qq, "rm", "-f", timeout=60)
            mode = accounts[qq].get("mode", "managed")
            del accounts[qq]
            self.save(accounts)
        return {"qq": qq, "data_kept": True, "container_kept": mode == "adopted"}

    def qr_mtime(self, qq: str) -> int:
        result = self._run(["docker", "exec", self.container_name(qq), "stat", "-c", "%Y",
                            "/app/napcat/cache/qrcode.png"])
        if result.returncode or not result.stdout.strip().isdigit():
            return 0
        return int(result.stdout.strip())

    def qr_age(self, qq: str) -> int:
        mtime = self.qr_mtime(qq)
        return max(0, int(time.time()) - mtime) if mtime else -1

    def waiting_qr(self, qq: str) -> bool:
        result = self._run(["docker", "logs", "--tail", "1200", self.container_name(qq)])
        logs = result.stdout + result.stderr
        qr = max(logs.rfind("请扫描下面的二维码"), logs.rfind("二维码已更新"))
        online = max(logs.rfind("账号状态变更为在线"), logs.rfind("HTTP服务已启动"),
                     logs.rfind("HTTP服务 已启动"), logs.rfind("WebSocket反向服务"))
        return qr >= 0 and qr > online

    def _qr_payload(self, qq: str, age: int) -> dict:
        with tempfile.TemporaryDirectory(prefix="bot-assistant-qr-") as temporary:
            path = Path(temporary) / "qrcode.png"
            result = self._run(["docker", "cp", f"{self.container_name(qq)}:/app/napcat/cache/qrcode.png",
                                str(path)], timeout=15)
            if result.returncode or not path.exists():
                raise ManagerError("qr_missing", "无法从容器读取登录码")
            data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_QR_BYTES:
            raise ManagerError("qr_invalid", "容器返回的登录码图片无效")
        return {"qq": qq, "state": "qr_ready", "age_seconds": age,
                "ttl_seconds": QR_LIFETIME_SECONDS,
                "qr_png_base64": base64.b64encode(data).decode("ascii")}

    def prepare(self, qq: object) -> dict:
        qq = validate_qq(qq)
        with self.locked():
            accounts = self.registry()
            if qq not in accounts:
                raise ManagerError("not_found", "找不到这个账号")
            state, _ = self.state(qq, accounts[qq])
            if state == "online":
                return {"qq": qq, "state": "online"}
            age = self.qr_age(qq)
            if 0 <= age < QR_FRESH_SECONDS and self.waiting_qr(qq):
                return self._qr_payload(qq, age)
            if state == "unknown" and not self.waiting_qr(qq):
                # A newly created container may still be starting. A stale QR
                # prompt, however, confirms that this account needs a new code.
                # NapCat's HTTP API is unavailable until QQ logs in, so an
                # unknown API state alone must not block that recovery.
                for _ in range(15):
                    time.sleep(1)
                    age = self.qr_age(qq)
                    if 0 <= age < QR_FRESH_SECONDS and self.waiting_qr(qq):
                        return self._qr_payload(qq, age)
                    state, _ = self.state(qq, accounts[qq])
                    if state == "online":
                        return {"qq": qq, "state": "online"}
                    if state == "offline":
                        break
                if state == "unknown":
                    raise ManagerError("status_unknown", "账号状态尚不明确，未重启容器")
            previous = self.qr_mtime(qq)
            if self._running(qq):
                self.docker("restart", self.container_name(qq), timeout=60)
            elif accounts[qq].get("mode") == "adopted":
                self.docker("start", self.container_name(qq), timeout=60)
            else:
                self.compose(qq, "up", "-d", timeout=120)
            for _ in range(35):
                time.sleep(2)
                age = self.qr_age(qq)
                if (0 <= age < QR_FRESH_SECONDS and self.qr_mtime(qq) != previous
                    and self.waiting_qr(qq)):
                    return self._qr_payload(qq, age)
                state, _ = self.state(qq, accounts[qq])
                if state == "online":
                    return {"qq": qq, "state": "online"}
            raise ManagerError("qr_not_fresh", "没有取得新的登录码，请稍后重试")


def thinking_state_path(root: Path) -> Path:
    return root / "data" / "settings" / "bot_assistant_thinking.json"


def _thinking_config(root: Path) -> tuple[dict, dict, dict, list[str]]:
    path = root / "data" / "cmd_config.json"
    if not path.is_file() or path.stat().st_size > 5_000_000:
        raise ManagerError("thinking_unavailable", "找不到可读取的 AstrBot 配置")
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(config, dict):
            raise ValueError("invalid config")
    except (OSError, ValueError, TypeError) as exc:
        raise ManagerError("thinking_unavailable", "AstrBot 配置无法解析") from exc
    model_id = (config.get("agent_runner", {}).get("config", {})
                .get("model", {}).get("provider_id"))
    models = config.get("provider", [])
    sources = config.get("provider_sources", [])
    model = next((item for item in models if isinstance(item, dict)
                  and item.get("id") == model_id), None)
    source = next((item for item in sources if isinstance(item, dict)
                   and model and item.get("id") == model.get("provider_source_id")), None)
    platforms = sorted({str(item.get("id")) for item in config.get("platform", [])
                        if isinstance(item, dict) and item.get("enable", True)
                        and item.get("type") == "aiocqhttp" and item.get("id")})
    if not model or not source:
        raise ManagerError("thinking_unavailable", "请先在 AstrBot 中选择一个对话模型")
    kind = source.get("type")
    provider = source.get("provider")
    host = urlparse(str(source.get("api_base", ""))).hostname
    if (kind not in {"openai_responses", "openai_chat_completion"}
        or provider != "deepseek" and host != "api.deepseek.com"
        or model.get("model") not in {"deepseek-flash", "deepseek-v4-pro"}):
        raise ManagerError("thinking_unsupported",
                           "当前模型不支持此调节窗口；请选用 DeepSeek Flash 或 Pro")
    if not platforms:
        raise ManagerError("thinking_unavailable", "尚未配置 QQ 平台")
    return config, model, source, platforms


def _read_thinking(root: Path) -> dict:
    path = thinking_state_path(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if data.get("schema") == 1 else {}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def thinking_status(root: Path) -> dict:
    try:
        config, model, _source, platforms = _thinking_config(root)
    except ManagerError as exc:
        return {"supported": False, "installed": False, "reason": str(exc),
                "platforms": []}
    state = _read_thinking(root)
    variants = state.get("variants", {})
    model_ids = {item.get("id") for item in config.get("provider", [])
                 if isinstance(item, dict)}
    installed = (state.get("base_provider_id") == model["id"]
                 and all(variants.get(level) in model_ids for level in THINKING_LEVELS)
                 and (root / "data" / "plugins" / THINKING_PLUGIN / "main.py").is_file())
    levels = state.get("levels", {}) if installed else {}
    return {"supported": True, "installed": installed,
            "model": str(model.get("model")),
            "platforms": [{"id": item, "level": levels.get(item, "high")}
                          for item in platforms]}


def install_thinking(root: Path, source: object, metadata: object) -> dict:
    if (not isinstance(source, str) or len(source.encode("utf-8")) > 50_000
        or not isinstance(metadata, str) or len(metadata.encode("utf-8")) > 3_000
        or f"name: {THINKING_PLUGIN}" not in metadata):
        raise ManagerError("thinking_package_invalid", "思维调节组件无效")
    try:
        compile(source, "thinking_plugin/main.py", "exec")
    except SyntaxError as exc:
        raise ManagerError("thinking_package_invalid", "思维调节组件代码有误") from exc
    config, model, _source, platforms = _thinking_config(root)
    previous = _read_thinking(root)
    old_ids = set(previous.get("variants", {}).values())
    config["provider"] = [item for item in config["provider"]
                          if not isinstance(item, dict) or item.get("id") not in old_ids]
    suffix = hashlib.sha256(model["id"].encode("utf-8")).hexdigest()[:10]
    variants = {}
    for level in THINKING_LEVELS:
        variant = copy.deepcopy(model)
        variant["id"] = f"bot-assistant-thinking-{suffix}-{level}"
        body = variant.get("custom_extra_body", {})
        body = dict(body) if isinstance(body, dict) else {}
        for key in ("reasoning", "reasoning_effort", "thinking"):
            body.pop(key, None)
        if _source.get("type") == "openai_responses":
            body["reasoning"] = {"effort": level}
        else:
            body["reasoning_effort"] = level
        variant["custom_extra_body"] = body
        config["provider"].append(variant)
        variants[level] = variant["id"]
    prior_levels = previous.get("levels", {}) if previous.get("base_provider_id") == model["id"] else {}
    state = {"schema": 1, "base_provider_id": model["id"],
             "variants": variants,
             "levels": {item: (prior_levels.get(item) if prior_levels.get(item) in THINKING_LEVELS
                               else "high") for item in platforms}}
    plugin_dir = root / "data" / "plugins" / THINKING_PLUGIN
    atomic_text(plugin_dir / "main.py", source)
    atomic_text(plugin_dir / "metadata.yaml", metadata)
    atomic_text(thinking_state_path(root), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    atomic_text(root / "data" / "cmd_config.json",
                json.dumps(config, ensure_ascii=False, indent=2) + "\n")
    return thinking_status(root)


def set_thinking(root: Path, platform_id: object, level: object) -> dict:
    status = thinking_status(root)
    if not status.get("installed"):
        raise ManagerError("thinking_unavailable", "请先启用思维调节组件")
    platform_id = str(platform_id)
    if platform_id not in {item["id"] for item in status["platforms"]}:
        raise ManagerError("thinking_platform_invalid", "找不到这个 QQ 平台")
    if level not in THINKING_LEVELS:
        raise ManagerError("thinking_level_invalid", "思维强度只支持关闭、低、高、最高")
    state = _read_thinking(root)
    state["levels"][platform_id] = level
    atomic_text(thinking_state_path(root),
                json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    return {"platform_id": platform_id, "level": level}


class ProjectOps:
    """Constrained operations on one explicitly configured Compose project."""

    def __init__(self, value: object):
        if not isinstance(value, str) or len(value) > 500 or not Path(value).is_absolute():
            raise ManagerError("project_path_invalid", "请在服务器设置中填写 AstrBot 项目的绝对路径")
        candidate = Path(value)
        if candidate == Path("/") or ".." in candidate.parts:
            raise ManagerError("project_path_invalid", "AstrBot 项目目录无效")
        try:
            self.root = candidate.resolve(strict=True)
        except OSError as exc:
            raise ManagerError("project_missing", "服务器上找不到 AstrBot 项目目录") from exc
        self.compose_file = self.root / "compose.yaml"
        if not self.compose_file.is_file():
            raise ManagerError("project_missing", "项目目录中没有 compose.yaml")

    def _compose(self, *args: str, timeout: int = 35) -> str:
        command = ["docker", "compose", "-f", str(self.compose_file), *args]
        try:
            result = subprocess.run(command, cwd=self.root, capture_output=True,
                                    text=True, timeout=timeout,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ManagerError("project_command_failed", "项目 Docker 命令不可用或等待超时") from exc
        if result.returncode:
            raise ManagerError("project_command_failed",
                               (result.stderr or result.stdout).strip()[:220]
                               or "项目 Docker 命令执行失败")
        return result.stdout

    def services(self) -> list[str]:
        names = [line.strip() for line in self._compose("config", "--services").splitlines()]
        return [name for name in names if PROJECT_NAME_PATTERN.fullmatch(name)]

    def _service(self, value: object) -> str:
        name = str(value)
        if not PROJECT_NAME_PATTERN.fullmatch(name) or name not in self.services():
            raise ManagerError("service_invalid", "所选服务不属于这个项目")
        return name

    def status(self) -> dict:
        names = self.services()
        raw = self._compose("ps", "-a", "--format", "json")
        try:
            stripped = raw.strip()
            records = (json.loads(stripped) if stripped.startswith("[")
                       else [json.loads(line) for line in raw.splitlines() if line.strip()])
            if not isinstance(records, list):
                raise ValueError("invalid docker ps result")
        except (ValueError, TypeError) as exc:
            raise ManagerError("project_status_invalid", "Docker 返回的服务状态无法解析") from exc
        by_name = {record.get("Service"): record for record in records
                   if isinstance(record, dict)}
        services = []
        for name in names:
            record = by_name.get(name, {})
            services.append({"name": name,
                             "state": str(record.get("State", "not_created")),
                             "status": str(record.get("Status", "尚未创建"))[:120],
                             "health": str(record.get("Health", ""))[:60]})
        return {"path": str(self.root), "services": services,
                "running": sum(item["state"] == "running" for item in services)}

    def logs(self, service: object, lines: object) -> dict:
        name = self._service(service)
        try:
            count = int(lines)
        except (TypeError, ValueError) as exc:
            raise ManagerError("lines_invalid", "日志行数无效") from exc
        if not 1 <= count <= 300:
            raise ManagerError("lines_invalid", "每次最多读取 300 行日志")
        content = self._compose("logs", "--no-color", "--tail", str(count),
                                name, timeout=45)
        content = re.sub(r"(?i)(Bearer\s+)[^\s]+", r"\1[REDACTED]", content)
        content = re.sub(r"(?i)\b(token|api[_-]?key|password|secret)(\s*[:=]\s*)[^\s,;]+",
                         r"\1\2[REDACTED]", content)
        content = re.sub(r"(?i)([?&](?:token|api[_-]?key|secret)=)[^&\s]+",
                         r"\1[REDACTED]", content)
        return {"service": name, "text": content[-60000:]}

    def restart(self, service: object) -> dict:
        name = self._service(service)
        self._compose("restart", name, timeout=120)
        return {"service": name, "restarted": True}

    def backups(self) -> dict:
        directory = self.root / "backups"
        files = sorted(directory.glob("bot-assistant-*.tar.gz"), reverse=True)[:30]
        return {"backups": [{"name": path.name, "bytes": path.stat().st_size,
                              "created": int(path.stat().st_mtime)}
                             for path in files if path.is_file()]}

    def backup(self) -> dict:
        directory = self.root / "backups"
        secure_dir(directory)
        members = [path for path in self.root.iterdir()
                   if path.name not in {"backups", ".git", "migration.tar.gz"}
                   and not path.is_symlink()]
        estimated = 0
        for member in members:
            if member.is_file():
                estimated += member.stat().st_size
            elif member.is_dir():
                for base, _dirs, files in os.walk(member, followlinks=False):
                    for name in files:
                        path = Path(base) / name
                        if not path.is_symlink():
                            estimated += path.stat().st_size
        if shutil.disk_usage(directory).free < estimated + 100_000_000:
            raise ManagerError("backup_space", "服务器剩余空间不足，未开始备份")
        filename = (f"bot-assistant-{time.strftime('%Y%m%d-%H%M%S')}-"
                    f"{secrets.token_hex(3)}.tar.gz")
        target = directory / filename
        fd, temporary = tempfile.mkstemp(prefix=".bot-assistant-", dir=directory)
        os.close(fd)
        try:
            os.chmod(temporary, 0o600)
            with tarfile.open(temporary, "w:gz") as archive:
                for member in members:
                    archive.add(member, arcname=member.name, recursive=True)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"name": filename, "bytes": target.stat().st_size,
                "stored_on_server": True}

    def _config_paths(self) -> list[Path]:
        settings = self.root / "data" / "settings"
        paths = [self.compose_file]
        if settings.is_dir():
            paths.extend(path for path in settings.glob("*.json")
                         if ".bak-" not in path.name)
        return [path for path in paths if path.is_file() and not path.is_symlink()]

    def configs(self) -> dict:
        return {"configs": [{"path": path.relative_to(self.root).as_posix(),
                              "bytes": path.stat().st_size,
                              "modified": int(path.stat().st_mtime)}
                             for path in self._config_paths()]}

    def config_read(self, value: object) -> dict:
        name = str(value)
        candidates = {path.relative_to(self.root).as_posix(): path
                      for path in self._config_paths()}
        if name not in candidates:
            raise ManagerError("config_invalid", "不支持查看这个配置文件")
        path = candidates[name]
        if path.stat().st_size > 200_000:
            raise ManagerError("config_too_large", "配置文件过大，无法在工作台预览")
        content = path.read_text(encoding="utf-8-sig")
        if path.suffix == ".json":
            try:
                data = json.loads(content)
            except ValueError as exc:
                raise ManagerError("config_invalid", "配置文件不是有效 JSON") from exc
            def has_sensitive_key(value):
                if isinstance(value, dict):
                    return any(SENSITIVE_KEY_PATTERN.search(str(key))
                               or has_sensitive_key(item) for key, item in value.items())
                if isinstance(value, list):
                    return any(has_sensitive_key(item) for item in value)
                return False
            if has_sensitive_key(data):
                raise ManagerError("config_sensitive", "此配置可能含凭据，请在服务器上查看")
        return {"path": name, "text": content,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "read_only": True}

    def plugins(self) -> dict:
        directory = self.root / "data" / "plugins"
        result = []
        if directory.is_dir():
            for folder in sorted(directory.iterdir()):
                metadata = folder / "metadata.yaml"
                if (not folder.is_dir() or folder.is_symlink() or
                    not metadata.is_file() or metadata.is_symlink()):
                    continue
                info = {"id": folder.name, "name": folder.name,
                        "version": "", "description": ""}
                for line in metadata.read_text(encoding="utf-8-sig").splitlines():
                    match = re.match(r"^(name|version|desc|description):\s*(.*)$", line)
                    if match:
                        field = "description" if match.group(1) == "desc" else match.group(1)
                        info[field] = match.group(2).strip().strip("\"'")[:160]
                result.append(info)
        return {"plugins": result}

    def bot_settings(self) -> dict:
        """Return an allowlisted setup summary without provider credentials."""
        path = self.root / "data" / "cmd_config.json"
        if not path.is_file():
            return {"initialized": False, "provider_count": 0,
                    "model_selected": False, "persona_selected": False,
                    "chat_enabled": False, "stt_enabled": False,
                    "tts_enabled": False, "dual_output": False}
        if path.stat().st_size > 5_000_000:
            raise ManagerError("bot_config_invalid", "AstrBot 配置文件过大，无法读取设置摘要")
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("invalid config")
        except (OSError, ValueError) as exc:
            raise ManagerError("bot_config_invalid", "AstrBot 配置无法解析") from exc
        providers = data.get("provider", [])
        settings = data.get("provider_settings", {})
        stt = data.get("provider_stt_settings", {})
        tts = data.get("provider_tts_settings", {})
        if not isinstance(providers, list):
            providers = []
        if not isinstance(settings, dict):
            settings = {}
        if not isinstance(stt, dict):
            stt = {}
        if not isinstance(tts, dict):
            tts = {}
        provider_count = sum(isinstance(item, dict) and
                             bool(item.get("enable", True)) and
                             not str(item.get("id", "")).startswith("bot-assistant-thinking-")
                             for item in providers)
        return {"initialized": True,
                "provider_count": provider_count,
                "model_selected": bool(provider_count and
                                       (settings.get("default_provider_id") or
                                        settings.get("provider_pool"))),
                "persona_selected": bool(settings.get("default_personality")),
                "chat_enabled": bool(settings.get("enable", True)),
                "stt_enabled": bool(stt.get("enable", False)),
                "tts_enabled": bool(tts.get("enable", False)),
                "dual_output": bool(tts.get("dual_output", False))}

    def thinking_status(self) -> dict:
        status = thinking_status(self.root)
        if not status.get("supported"):
            return status
        try:
            config, _model, _source, _platforms = _thinking_config(self.root)
            accounts = Manager().registry()
            names_by_port = {item.get("ws_port"): item["name"]
                             for item in accounts.values() if item.get("ws_port")}
            ports = {item.get("id"): item.get("ws_reverse_port")
                     for item in config.get("platform", []) if isinstance(item, dict)}
            for platform in status["platforms"]:
                name = names_by_port.get(ports.get(platform["id"]))
                platform["label"] = (f"{name} · {platform['id']}" if name
                                     else platform["id"])
        except (ManagerError, OSError, ValueError, TypeError, KeyError):
            pass
        return status

    def thinking_install(self, source: object, metadata: object) -> dict:
        services = self.services()
        if "astrbot" not in services:
            raise ManagerError("thinking_unavailable", "项目没有 AstrBot 服务")
        config_path = self.root / "data" / "cmd_config.json"
        state_path = thinking_state_path(self.root)
        plugin_dir = self.root / "data" / "plugins" / THINKING_PLUGIN
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = self.root / "backups" / f"thinking-control-{stamp}"
        secure_dir(backup)
        for path in (config_path, state_path, plugin_dir / "main.py",
                     plugin_dir / "metadata.yaml"):
            if path.exists():
                shutil.copy2(path, backup / path.name)
        result = install_thinking(self.root, source, metadata)
        try:
            self._compose("restart", "astrbot", timeout=150)
        except ManagerError:
            # Keep the backup for manual recovery; do not restart QQ containers.
            raise ManagerError("thinking_restart_failed",
                               f"配置已备份到 {backup.name}；AstrBot 重启失败，请检查服务日志")
        return {**result, "backup": backup.name, "restarted": "astrbot"}

    def thinking_set(self, platform_id: object, level: object) -> dict:
        return set_thinking(self.root, platform_id, level)


def response(ok: bool, **data) -> None:
    print(json.dumps({"protocol": PROTOCOL, "ok": ok, **data}, ensure_ascii=False))


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin) if action in {"add", "adopt", "rename", "remove", "prepare",
                                                       "project_status", "project_logs", "project_backup",
                                                       "project_backups", "project_configs",
                                                       "project_config_read", "project_plugins", "project_bot_settings",
                                                       "project_thinking_status", "project_thinking_install",
                                                       "project_thinking_set",
                                                       "project_restart"} else {}
        manager = Manager()
        if action == "preflight":
            result = manager.preflight()
        elif action == "list":
            result = manager.list_accounts()
        elif action == "add":
            result = manager.add(payload.get("qq"), payload.get("name"))
        elif action == "adopt":
            result = manager.adopt(payload.get("qq"), payload.get("name"),
                                   payload.get("container"), payload.get("port"),
                                   payload.get("config_path"),
                                   payload.get("ws_container"), payload.get("ws_port"))
        elif action == "rename":
            result = manager.rename(payload.get("qq"), payload.get("name"))
        elif action == "remove":
            result = manager.remove(payload.get("qq"))
        elif action == "prepare":
            result = manager.prepare(payload.get("qq"))
        elif action.startswith("project_"):
            project = ProjectOps(payload.get("path"))
            if action == "project_status":
                result = project.status()
            elif action == "project_logs":
                result = project.logs(payload.get("service"), payload.get("lines", 120))
            elif action == "project_restart":
                result = project.restart(payload.get("service"))
            elif action == "project_backup":
                result = project.backup()
            elif action == "project_backups":
                result = project.backups()
            elif action == "project_configs":
                result = project.configs()
            elif action == "project_config_read":
                result = project.config_read(payload.get("config"))
            elif action == "project_plugins":
                result = project.plugins()
            elif action == "project_bot_settings":
                result = project.bot_settings()
            elif action == "project_thinking_status":
                result = project.thinking_status()
            elif action == "project_thinking_install":
                result = project.thinking_install(payload.get("source"), payload.get("metadata"))
            elif action == "project_thinking_set":
                result = project.thinking_set(payload.get("platform_id"), payload.get("level"))
            else:
                raise ManagerError("usage", "不支持的项目操作")
        else:
            raise ManagerError("usage", "不支持的操作")
        response(True, **result)
        return 0
    except ManagerError as exc:
        response(False, error=exc.code, message=str(exc))
        return 1
    except (OSError, ValueError, TypeError) as exc:
        response(False, error="internal", message=f"操作失败：{type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
