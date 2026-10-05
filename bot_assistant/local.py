"""Run the same account protocol against Docker Desktop on this computer."""

from __future__ import annotations

import os
import hashlib
import json
import re
import socket
import subprocess
from pathlib import Path

from server import manager as manager_module

from .config import LocalProfile
from .docker_setup import find_docker_cli
from .remote import RemoteError

NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MARKER = ".bot-assistant-local-project"
ASTRBOT_IMAGE = "soulter/astrbot:latest"


def _manager_module():
    return manager_module


def _docker_path() -> Path:
    found = find_docker_cli()
    if found is not None:
        entries = [part.casefold() for part in os.environ.get("PATH", "").split(os.pathsep)]
        if str(found.parent).casefold() not in entries:
            os.environ["PATH"] = str(found.parent) + os.pathsep + os.environ.get("PATH", "")
        return found
    raise RemoteError("docker_missing", "未找到 Docker Desktop。点击本机部署可自动安装并启动。")


class LocalManager(manager_module.Manager):
    def __init__(self, home: Path, project_root: Path, network_name: str):
        super().__init__(home)
        self.project_root = project_root
        self.network_name = network_name

    def write_account_files(self, qq: str, port: int, token: str) -> None:
        super().write_account_files(qq, port, token)
        if not (self.project_root / MARKER).is_file():
            return  # An existing project may have its own network and adapters.
        folder = self.account_dir(qq)
        config_path = folder / "config" / f"onebot11_{qq}.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["network"]["websocketClients"] = [{
            "enable": True, "name": "astrbot-local", "url": f"ws://astrbot:{port - 15000}/ws",
            "messagePostFormat": "array", "reportSelfMessage": False,
            "reconnectInterval": 5000, "token": "", "debug": False,
            "heartInterval": 30000,
        }]
        manager_module.atomic_text(config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        compose_path = folder / "compose.yaml"
        compose = compose_path.read_text(encoding="utf-8")
        compose += f"networks:\n  default:\n    external: true\n    name: {self.network_name}\n"
        manager_module.atomic_text(compose_path, compose)


class Local:
    def __init__(self, profile: LocalProfile):
        profile.validate()
        self.profile = profile
        self.module = _manager_module()
        self.network_name = "ba-local-" + hashlib.sha256(
            str(Path(profile.project_path)).casefold().encode("utf-8")).hexdigest()[:10]
        self.manager = LocalManager(Path(profile.project_path) / "bot-assistant",
                                    Path(profile.project_path), self.network_name)

    def _docker(self, *args: str, timeout: int = 35) -> str:
        docker = _docker_path()
        try:
            result = subprocess.run([str(docker), *args], capture_output=True, text=True,
                                    timeout=timeout, creationflags=NO_CONSOLE)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteError("docker_unavailable", "Docker Desktop 未响应。请确认已启动并切换到 Linux 容器。") from exc
        if result.returncode:
            raise RemoteError("docker_failed", (result.stderr or result.stdout).strip()[:250]
                              or "Docker 命令失败")
        return result.stdout.strip()

    def preflight(self) -> dict:
        self._docker("compose", "version", "--short")
        if self._docker("info", "--format", "{{.OSType}}") != "linux":
            raise RemoteError("linux_containers_required", "请在 Docker Desktop 中切换为 Linux 容器。")
        return {"docker": self._docker("info", "--format", "{{.ServerVersion}}")}

    def deploy(self) -> dict:
        root = Path(self.profile.project_path).expanduser()
        compose = root / "compose.yaml"
        marker = root / MARKER
        if compose.exists() and not marker.exists():
            return {"attached": True, "path": str(root)}
        root.mkdir(parents=True, exist_ok=True)
        if not compose.exists():
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", self.profile.dashboard_port))
                except OSError as exc:
                    raise RemoteError("dashboard_port_used", "AstrBot 面板端口已被占用，请在设置中更换端口。") from exc
            (root / "data").mkdir(exist_ok=True)
            content = ("services:\n  astrbot:\n"
                       f"    image: {ASTRBOT_IMAGE}\n"
                       "    restart: unless-stopped\n"
                       "    environment:\n      TZ: Asia/Shanghai\n"
                       "    ports:\n"
                       f"      - \"127.0.0.1:{self.profile.dashboard_port}:6185\"\n"
                       "    volumes:\n      - ./data:/AstrBot/data\n"
                       f"networks:\n  default:\n    name: {self.network_name}\n")
            self.module.atomic_text(compose, content)
            self.module.atomic_text(marker, "bot-assistant-local-v1\n")
        self._docker("compose", "-f", str(compose), "up", "-d", timeout=240)
        return {"attached": False, "path": str(root)}

    def call(self, action: str, payload: dict | None = None, timeout: int = 35) -> dict:
        _docker_path()
        payload = payload or {}
        try:
            if action == "preflight":
                result = self.preflight()
            elif action == "list":
                result = self.manager.list_accounts()
                if (Path(self.profile.project_path) / MARKER).is_file():
                    registry = self.manager.registry()
                    for account in result["accounts"]:
                        account["astrbot_ws_port"] = registry[account["qq"]]["port"] - 15000
            elif action == "add":
                result = self.manager.add(payload.get("qq"), payload.get("name"))
            elif action == "rename":
                result = self.manager.rename(payload.get("qq"), payload.get("name"))
            elif action == "remove":
                result = self.manager.remove(payload.get("qq"))
            elif action == "prepare":
                result = self.manager.prepare(payload.get("qq"))
            elif action.startswith("project_"):
                if payload.get("path") != self.profile.project_path:
                    raise RemoteError("project_path_invalid", "本机项目路径与当前设置不一致")
                project = self.module.ProjectOps(self.profile.project_path)
                service = str(payload.get("service", ""))
                match = re.fullmatch(r"napcat-([1-9][0-9]{4,11})", service)
                accounts = self.manager.registry() if action in {
                    "project_status", "project_logs", "project_restart"} else {}
                if match and match.group(1) in accounts and action == "project_logs":
                    count = max(1, min(300, int(payload.get("lines", 120))))
                    content = self.manager.docker("logs", "--tail", str(count),
                                                  self.manager.container_name(match.group(1)))
                    content = re.sub(r"(?i)(Bearer\s+)[^\s]+", r"\1[REDACTED]", content)
                    content = re.sub(r"(?i)\b(token|api[_-]?key|password|secret)(\s*[:=]\s*)[^\s,;]+",
                                     r"\1\2[REDACTED]", content)
                    result = {"service": service, "text": content[-60000:]}
                    return {"protocol": self.module.PROTOCOL, "ok": True, **result}
                if match and match.group(1) in accounts and action == "project_restart":
                    self.manager.docker("restart", self.manager.container_name(match.group(1)), timeout=90)
                    return {"protocol": self.module.PROTOCOL, "ok": True,
                            "service": service, "restarted": True}
                methods = {
                    "project_status": lambda: project.status(),
                    "project_logs": lambda: project.logs(payload.get("service"), payload.get("lines", 120)),
                    "project_restart": lambda: project.restart(payload.get("service")),
                    "project_backup": lambda: project.backup(),
                    "project_backups": lambda: project.backups(),
                    "project_configs": lambda: project.configs(),
                    "project_config_read": lambda: project.config_read(payload.get("config")),
                    "project_plugins": lambda: project.plugins(),
                    "project_bot_settings": lambda: project.bot_settings(),
                    "project_thinking_status": lambda: project.thinking_status(),
                    "project_thinking_install": lambda: project.thinking_install(
                        payload.get("source"), payload.get("metadata")),
                    "project_thinking_set": lambda: project.thinking_set(
                        payload.get("platform_id"), payload.get("level")),
                }
                if action not in methods:
                    raise ValueError("unsupported action")
                result = methods[action]()
                if action == "project_status":
                    for qq, account in sorted(accounts.items()):
                        state, detail = self.manager.state(qq, account)
                        result["services"].append({"name": f"napcat-{qq}",
                                                   "state": ("running" if self.manager._running(qq)
                                                             else "stopped"),
                                                   "status": detail, "health": state})
                    result["running"] = sum(item["state"] == "running"
                                            for item in result["services"])
                if action == "project_backup":
                    result["stored_on_server"] = False
                    result["stored_locally"] = True
            else:
                raise ValueError("unsupported action")
        except self.module.ManagerError as exc:
            raise RemoteError(exc.code, str(exc)) from exc
        return {"protocol": self.module.PROTOCOL, "ok": True, **result}
