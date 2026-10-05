"""Local connection settings. Server account state stays on the server."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


def config_home() -> Path:
    override = os.environ.get("BOT_ASSISTANT_CONFIG_HOME")
    if override:
        return Path(override)
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BotAssistant"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "bot-assistant"


@dataclass(frozen=True)
class Profile:
    host: str
    port: int
    user: str
    key_path: str
    project_path: str = ""
    dashboard_port: int = 6185

    def validate(self) -> None:
        if not self.host or len(self.host) > 253 or not re.fullmatch(r"[A-Za-z0-9.:[\]_-]+", self.host):
            raise ValueError("服务器地址无效")
        if not (1 <= self.port <= 65535):
            raise ValueError("SSH 端口应为 1 至 65535")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", self.user):
            raise ValueError("SSH 用户名无效")
        key = Path(self.key_path).expanduser()
        if not key.is_file():
            raise ValueError("找不到 SSH 私钥文件")
        if self.project_path:
            project = Path(self.project_path)
            if (not self.project_path.startswith("/") or project == Path("/")
                or ".." in project.parts or any(ord(char) < 32 for char in self.project_path)):
                raise ValueError("AstrBot 项目目录应为服务器上的绝对路径")
        if not (1 <= self.dashboard_port <= 65535):
            raise ValueError("AstrBot 面板端口应为 1 至 65535")


@dataclass(frozen=True)
class LocalProfile:
    project_path: str
    dashboard_port: int = 6185

    def validate(self) -> None:
        path = Path(self.project_path).expanduser()
        if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
            raise ValueError("请选择本机上的 AstrBot 项目目录")
        if not (1 <= self.dashboard_port <= 65535):
            raise ValueError("AstrBot 面板端口应为 1 至 65535")


@dataclass(frozen=True)
class NativeProfile:
    project_path: str
    dashboard_port: int = 6185

    def validate(self) -> None:
        path = Path(self.project_path).expanduser()
        if not path.is_absolute() or path == Path(path.anchor) or ".." in path.parts:
            raise ValueError("请选择本机上的独立项目目录")
        if not (1 <= self.dashboard_port <= 65535):
            raise ValueError("AstrBot 面板端口应为 1 至 65535")


def load_profile(mode: str | None = None) -> Profile | LocalProfile | NativeProfile | None:
    path = config_home() / "config.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") not in {1, 2, 3}:
            raise ValueError("配置版本不受支持")
        selected = mode or data.get("mode", "ssh")
        if selected == "local":
            if "local" not in data:
                return None
            profile = LocalProfile(**data["local"])
        elif selected == "native":
            if "native" not in data:
                return None
            profile = NativeProfile(**data["native"])
        elif selected == "ssh":
            if "server" not in data:
                return None
            profile = Profile(**data["server"])
        else:
            raise ValueError("配置模式不受支持")
        profile.validate()
        return profile
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise ValueError("本机连接配置无法读取，请在设置中重新填写") from exc


def save_profile(profile: Profile | LocalProfile | NativeProfile) -> None:
    profile.validate()
    home = config_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.json"
    previous = {}
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
    selected = "native" if isinstance(profile, NativeProfile) else (
        "local" if isinstance(profile, LocalProfile) else "ssh")
    data = {"schema": 3, "mode": selected}
    for key in ("local", "server", "native"):
        if isinstance(previous.get(key), dict):
            data[key] = previous[key]
    data[selected if selected != "ssh" else "server"] = asdict(profile)
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=home)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream,
                      ensure_ascii=False, indent=2)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
