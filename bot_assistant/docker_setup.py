"""Prepare Docker Desktop from the single Windows executable.

The Docker installer is downloaded from Docker's official endpoint at run time;
it is never redistributed in the bot assistant release.
"""

from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DOCKER_DOWNLOAD = "https://desktop.docker.com/win/main/{arch}/Docker%20Desktop%20Installer.exe"
MAX_INSTALLER_BYTES = 1_500_000_000
NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
Progress = Callable[[str], None]


class DockerSetupError(Exception):
    pass


@dataclass(frozen=True)
class DockerState:
    code: str
    message: str


def desktop_candidates() -> tuple[Path, Path]:
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    programs = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    return (local / "Programs" / "DockerDesktop" / "Docker Desktop.exe",
            programs / "Docker" / "Docker" / "Docker Desktop.exe")


def find_desktop() -> Path | None:
    return next((path for path in desktop_candidates() if path.is_file()), None)


def find_docker_cli() -> Path | None:
    found = shutil.which("docker")
    if found:
        return Path(found)
    for desktop in desktop_candidates():
        candidate = desktop.parent / "resources" / "bin" / "docker.exe"
        if candidate.is_file():
            return candidate
    return None


def _docker(*args: str, timeout: int = 10) -> subprocess.CompletedProcess[str] | None:
    cli = find_docker_cli()
    if cli is None:
        return None
    try:
        return subprocess.run([str(cli), *args], capture_output=True, text=True,
                              errors="replace", timeout=timeout, creationflags=NO_CONSOLE)
    except (OSError, subprocess.TimeoutExpired):
        return None


def docker_state() -> DockerState:
    cli = find_docker_cli()
    desktop = find_desktop()
    if cli is None and desktop is None:
        return DockerState("missing", "尚未安装 Docker Desktop")
    if cli is None:
        return DockerState("incomplete", "Docker Desktop 已安装，但 Docker 命令尚不可用")
    compose = _docker("compose", "version", "--short")
    if compose is None or compose.returncode:
        return DockerState("compose_missing", "Docker Compose 暂不可用")
    info = _docker("info", "--format", "{{.OSType}}")
    if info is None or info.returncode:
        return DockerState("stopped", "Docker 引擎尚未启动")
    if info.stdout.strip() != "linux":
        return DockerState("windows", "当前是 Windows 容器，请切换至 Linux 容器")
    return DockerState("ready", "Docker Linux 引擎已就绪")


def _download_installer(target: Path, progress: Progress) -> None:
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "amd64"
    if machine not in {"arm64", "aarch64", "amd64", "x86_64"}:
        raise DockerSetupError("这台电脑的处理器架构尚不支持 Docker Desktop 自动安装")
    request = urllib.request.Request(DOCKER_DOWNLOAD.format(arch=architecture),
                                     headers={"User-Agent": "BotAssistant/0.5"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            if urllib.parse.urlparse(response.geturl()).hostname != "desktop.docker.com":
                raise DockerSetupError("Docker 下载地址跳转到了非官方域名，已停止安装")
            expected = int(response.headers.get("Content-Length", "0") or 0)
            if expected > MAX_INSTALLER_BYTES:
                raise DockerSetupError("Docker 安装程序超出预期大小，已停止下载")
            total = 0
            with target.open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_INSTALLER_BYTES:
                        raise DockerSetupError("Docker 安装程序超出预期大小，已停止下载")
                    stream.write(chunk)
                    if total // (25 * 1024 * 1024) != (total - len(chunk)) // (25 * 1024 * 1024):
                        progress(f"正在下载 Docker Desktop：{total // (1024 * 1024)} MB"
                                 + (f" / {expected // (1024 * 1024)} MB" if expected else ""))
            if total < 10_000_000 or (expected and total != expected):
                raise DockerSetupError("Docker 安装程序下载不完整，请检查网络后重试")
    except (OSError, urllib.error.URLError) as exc:
        raise DockerSetupError("无法下载 Docker Desktop，请检查网络后重试") from exc


def _verify_docker_signature(installer: Path) -> None:
    # Pass the path through an environment variable so it cannot become script code.
    script = ("$s=Get-AuthenticodeSignature -LiteralPath $env:BOT_ASSISTANT_DOCKER_INSTALLER; "
              "[pscustomobject]@{status=[string]$s.Status; "
              "subject=[string]$s.SignerCertificate.Subject} | ConvertTo-Json -Compress")
    command = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    environment = dict(os.environ, BOT_ASSISTANT_DOCKER_INSTALLER=str(installer))
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                                 "-EncodedCommand", command], capture_output=True,
                                text=True, errors="replace", timeout=45,
                                env=environment, creationflags=NO_CONSOLE)
        signature = json.loads(result.stdout.strip())
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        raise DockerSetupError("无法验证 Docker 安装程序签名，已停止安装") from exc
    if result.returncode or signature.get("status") != "Valid" or "docker" not in signature.get("subject", "").lower():
        raise DockerSetupError("Docker 安装程序签名无效，已停止安装")


def install_docker_desktop(progress: Progress) -> None:
    if os.name != "nt":
        raise DockerSetupError("自动安装仅支持 Windows")
    if find_desktop() is not None:
        return
    with tempfile.TemporaryDirectory(prefix="bot-assistant-docker-") as folder:
        installer = Path(folder) / "Docker Desktop Installer.exe"
        progress("正在从 Docker 官网下载安装程序…")
        _download_installer(installer, progress)
        progress("正在验证 Docker 官方数字签名…")
        _verify_docker_signature(installer)
        progress("请在 Docker 安装窗口完成安装；协议由你本人确认。")
        try:
            result = subprocess.run([str(installer), "install", "--user"], timeout=1200)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DockerSetupError("Docker 安装未完成，请重新打开本机部署向导") from exc
        if result.returncode:
            raise DockerSetupError(f"Docker 安装未完成（退出码 {result.returncode}）")
    if find_desktop() is None:
        raise DockerSetupError("Docker 安装结束，但未找到程序；可能需要重启 Windows 后继续")


def start_docker_desktop(progress: Progress) -> None:
    desktop = find_desktop()
    if desktop is None:
        raise DockerSetupError("未找到 Docker Desktop，请重新运行本机部署")
    progress("正在启动 Docker Desktop；首次启动请确认协议或 WSL 提示…")
    try:
        subprocess.Popen([str(desktop)], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise DockerSetupError("无法启动 Docker Desktop") from exc


def ensure_docker_ready(progress: Progress, timeout: int = 300) -> None:
    state = docker_state()
    if state.code == "ready":
        progress(state.message)
        return
    if state.code == "missing":
        install_docker_desktop(progress)
    if state.code == "windows":
        raise DockerSetupError(state.message + "；切换后点击本机部署继续")
    start_docker_desktop(progress)
    deadline = time.monotonic() + timeout
    last_code = ""
    while time.monotonic() < deadline:
        state = docker_state()
        if state.code == "ready":
            progress(state.message)
            return
        if state.code == "windows":
            raise DockerSetupError(state.message + "；切换后点击本机部署继续")
        if state.code != last_code:
            progress(state.message + "，正在等待…")
            last_code = state.code
        time.sleep(5)
    raise DockerSetupError("Docker 启动超时。请完成 Docker 窗口中的协议或 WSL 设置，必要时重启 Windows，然后重试本机部署")
