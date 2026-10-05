"""SSH transport with explicit host-key trust and a small JSON protocol."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Profile, config_home

PROTOCOL = 1
REMOTE_SCRIPT = "$HOME/.local/share/bot-assistant/manager.py"
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_hidden(*args, **kwargs):
    """Run SSH tools without flashing a console beside the desktop window."""
    return subprocess.run(*args, creationflags=NO_CONSOLE, **kwargs)

PREFLIGHT = r'''
import json, subprocess, sys
if sys.version_info < (3, 9):
 print(json.dumps({"ok": False, "message": "服务器需要 Python 3.9 或更新版本"})); sys.exit(1)
for command, label in [(["docker", "compose", "version", "--short"], "Docker Compose"),
                       (["docker", "info", "--format", "{{.ServerVersion}}"], "Docker 权限")]:
 try: result = subprocess.run(command, capture_output=True, text=True, timeout=15)
 except (OSError, subprocess.TimeoutExpired):
  print(json.dumps({"ok": False, "message": label + " 不可用，请安装 Docker Compose 并授予当前 SSH 用户 Docker 权限"})); sys.exit(1)
 if result.returncode:
  print(json.dumps({"ok": False, "message": label + " 不可用，请安装 Docker Compose 并授予当前 SSH 用户 Docker 权限"})); sys.exit(1)
print(json.dumps({"ok": True, "python": sys.version.split()[0]}))
'''

INSTALL = r'''
import hashlib, json, os, pathlib, sys, tempfile, time
expected = sys.argv[1]
source = sys.stdin.buffer.read(300001)
if len(source) > 300000 or hashlib.sha256(source).hexdigest() != expected:
 print(json.dumps({"ok": False, "message": "部署包校验失败"})); sys.exit(1)
home = pathlib.Path.home() / ".local/share/bot-assistant"
home.mkdir(parents=True, exist_ok=True, mode=0o700)
home.chmod(0o700)
target = home / "manager.py"
fd, temporary = tempfile.mkstemp(prefix=".manager-", dir=home)
try:
 os.fchmod(fd, 0o700)
 with os.fdopen(fd, "wb") as stream:
  stream.write(source); stream.flush(); os.fsync(stream.fileno())
 if target.exists():
  (home / ("manager.py.bak-" + time.strftime("%Y%m%d-%H%M%S"))).write_bytes(target.read_bytes())
 os.replace(temporary, target)
finally:
 if os.path.exists(temporary): os.unlink(temporary)
print(json.dumps({"ok": True, "sha256": expected}))
'''


class RemoteError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class HostKey:
    line: str
    fingerprint: str
    algorithm: str


def _host_label(profile: Profile) -> str:
    return profile.host if profile.port == 22 else f"[{profile.host}]:{profile.port}"


def parse_host_keys(output: str, profile: Profile) -> HostKey:
    keys = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] != _host_label(profile):
            continue
        if fields[1] not in {"ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa"}:
            continue
        try:
            raw = base64.b64decode(fields[2], validate=True)
        except ValueError:
            continue
        fingerprint = base64.b64encode(hashlib.sha256(raw).digest()).decode().rstrip("=")
        keys.append(HostKey(" ".join(fields[:3]), "SHA256:" + fingerprint, fields[1]))
    if not keys:
        raise RemoteError("host_key_missing", "未能读取服务器 SSH 指纹，请检查地址和端口")
    return next((key for key in keys if key.algorithm == "ssh-ed25519"), keys[0])


def bundled_manager_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    return base / "server" / "manager.py"


def bundled_thinking_plugin() -> tuple[str, str]:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
    folder = base / "server" / "thinking_plugin"
    return ((folder / "main.py").read_text(encoding="utf-8"),
            (folder / "metadata.yaml").read_text(encoding="utf-8"))


class Remote:
    def __init__(self, profile: Profile):
        profile.validate()
        self.profile = profile
        self.known_hosts = config_home() / "known_hosts"

    @property
    def destination(self) -> str:
        return f"{self.profile.user}@{self.profile.host}"

    def scan_host_key(self) -> HostKey:
        try:
            result = run_hidden(["ssh-keyscan", "-T", "8", "-p",
                                 str(self.profile.port), self.profile.host],
                                capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = None
        if result is None or not result.stdout:
            # Older Windows ssh-keyscan builds cannot negotiate some modern KEX
            # methods. Capture a candidate key in a disposable file with ssh;
            # it becomes trusted only after the user checks its fingerprint.
            with tempfile.TemporaryDirectory(prefix="bot-assistant-hostkey-") as folder:
                candidate_file = Path(folder) / "known_hosts"
                args = ["ssh", "-T", "-i", str(Path(self.profile.key_path).expanduser()),
                        "-p", str(self.profile.port), "-o", "BatchMode=yes",
                        "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
                        "-o", "StrictHostKeyChecking=accept-new",
                        "-o", "HashKnownHosts=no",
                        "-o", f"UserKnownHostsFile={candidate_file}",
                        "-o", "GlobalKnownHostsFile=NUL" if os.name == "nt"
                        else "GlobalKnownHostsFile=/dev/null",
                        self.destination, "true"]
                try:
                    run_hidden(args, capture_output=True, timeout=12)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise RemoteError("host_key_missing", "无法连接服务器 SSH 端口") from exc
                if not candidate_file.exists():
                    raise RemoteError("host_key_missing", "无法读取服务器 SSH 指纹")
                output = candidate_file.read_text(encoding="utf-8")
        else:
            output = result.stdout
        key = parse_host_keys(output, self.profile)
        if self.known_hosts.exists():
            for line in self.known_hosts.read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if fields and fields[0] == _host_label(self.profile):
                    if line != key.line:
                        raise RemoteError("host_key_changed", "服务器 SSH 指纹与已保存的记录不一致")
                    return key
        return key

    def is_trusted(self, key: HostKey) -> bool:
        if not self.known_hosts.exists():
            return False
        return key.line in self.known_hosts.read_text(encoding="utf-8").splitlines()

    def trust_host_key(self, key: HostKey) -> None:
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        if self.known_hosts.exists() and self.is_trusted(key):
            return
        with self.known_hosts.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(key.line + "\n")
        os.chmod(self.known_hosts, 0o600)

    def _ssh(self, remote_command: str, payload: bytes | None = None,
             timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
        args = self._ssh_base() + [self.destination, remote_command]
        try:
            return run_hidden(args, input=payload, capture_output=True,
                              timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteError("ssh_timeout", "SSH 连接失败或等待超时") from exc

    def _ssh_base(self) -> list[str]:
        if not self.known_hosts.exists():
            raise RemoteError("host_untrusted", "请先核对并确认服务器 SSH 指纹")
        return ["ssh", "-T", "-i", str(Path(self.profile.key_path).expanduser()),
                "-p", str(self.profile.port), "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1",
                "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={self.known_hosts}",
                "-o", "GlobalKnownHostsFile=NUL" if os.name == "nt"
                else "GlobalKnownHostsFile=/dev/null"]

    def dashboard_tunnel(self) -> tuple[subprocess.Popen, int]:
        """Open a loopback-only SSH tunnel to the private AstrBot dashboard."""
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            local_port = listener.getsockname()[1]
        args = self._ssh_base() + ["-o", "ExitOnForwardFailure=yes", "-N",
            "-L", f"127.0.0.1:{local_port}:127.0.0.1:{self.profile.dashboard_port}",
            self.destination]
        try:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       creationflags=NO_CONSOLE)
        except OSError as exc:
            raise RemoteError("tunnel_failed", "无法启动 SSH 面板隧道") from exc
        for _ in range(30):
            if process.poll() is not None:
                raise RemoteError("tunnel_failed", "SSH 面板隧道建立失败，请检查端口和服务器")
            try:
                with socket.create_connection(("127.0.0.1", local_port), timeout=0.2):
                    return process, local_port
            except OSError:
                time.sleep(0.1)
        process.terminate()
        raise RemoteError("tunnel_timeout", "SSH 面板隧道连接超时")

    @staticmethod
    def _json_result(result: subprocess.CompletedProcess[bytes]) -> dict:
        try:
            data = json.loads(result.stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            detail = result.stderr.decode("utf-8", "replace").strip()[:180]
            raise RemoteError("ssh_failed", detail or "服务器未返回有效结果") from exc
        if not isinstance(data, dict):
            raise RemoteError("protocol_invalid", "服务器返回格式无效")
        if data.get("ok") is False:
            raise RemoteError(str(data.get("error", "server_error")),
                              str(data.get("message", "服务器操作失败")))
        if data.get("ok") is not True or result.returncode:
            raise RemoteError("protocol_invalid", "服务器返回状态无效")
        return data

    def preflight(self) -> dict:
        result = self._ssh(shlex.join(["python3", "-c", PREFLIGHT]), timeout=35)
        return self._json_result(result)

    def deploy(self) -> dict:
        content = bundled_manager_path().read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        command = shlex.join(["python3", "-c", INSTALL, digest])
        result = self._ssh(command, payload=content, timeout=35)
        data = self._json_result(result)
        if data.get("sha256") != digest:
            raise RemoteError("deploy_invalid", "服务器部署校验不一致")
        return data

    def call(self, action: str, payload: dict | None = None,
             timeout: int = 35) -> dict:
        if action not in {"preflight", "list", "add", "adopt", "rename", "remove", "prepare",
                          "project_status", "project_logs", "project_backup",
                          "project_backups", "project_configs", "project_config_read",
                          "project_plugins", "project_restart", "project_bot_settings",
                          "project_thinking_status", "project_thinking_install",
                          "project_thinking_set"}:
            raise ValueError("unsupported action")
        # The script path is fixed; only a known action is interpolated.
        command = f'python3 "{REMOTE_SCRIPT}" {action}'
        raw = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        result = self._ssh(command, payload=raw, timeout=timeout)
        data = self._json_result(result)
        if data.get("protocol") != PROTOCOL:
            raise RemoteError("protocol_mismatch", "客户端与服务器脚本版本不兼容")
        return data
