"""Pinned, user-owned Windows runtime downloads for the no-Docker mode."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


NO_CONSOLE = getattr(subprocess, "CREATE_NO_WINDOW", 0)
ASTRBOT = (
    "https://github.com/AstrBotDevs/AstrBot-desktop/releases/download/v4.28.1/"
    "AstrBot_4.28.1_windows_amd64_portable.zip",
    "6ce7bf7e9ee7826031c68d21971daac1d38efc4ae59b171291bf69349f72401a",
)
NAPCAT = (
    "https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.28/NapCat.Shell.zip",
    "bcdd8bdb9e44bd0cf6a90908e572141787fd9e98cb8d8eecc5adf25bbdcabb94",
)
SEVEN_ZIP = (
    "https://github.com/NapNeko/NapCatQQ/releases/download/v4.18.19/"
    "NapCat.Shell.Windows.OneKey.zip",
    "fa365537039e9ec29730166f3f624eb147074be18be64d1981a03f35ecb2a2af",
)
QQ_URL = (
    "https://qqdl.gtimg.cn/qqfile/QQNTV2/9.9.36/release/"
    "e8e54bbb/QQ_9.9.36_260924_x64_01.exe"
)
QQ_SHA256 = "63255c70c7ff523e7eed7d52d9708ed7a77ea346ece6474c65dded406edb3155"
# This hash was pinned after verifying Tencent's Authenticode signer.


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def download(url: str, expected: str, path: Path, progress=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and digest(path) == expected:
        return path
    temporary = path.with_suffix(path.suffix + ".part")
    if temporary.exists():
        temporary.unlink()
    request = urllib.request.Request(url, headers={"User-Agent": "BotAssistant/0.6"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as out:
            total = int(response.headers.get("Content-Length", "0"))
            received = 0
            while block := response.read(1024 * 1024):
                out.write(block)
                received += len(block)
                if progress and received % (16 * 1024 * 1024) < len(block):
                    progress(f"已下载 {received // 1048576} MB" +
                             (f" / {total // 1048576} MB" if total else ""))
        if digest(temporary) != expected:
            raise ValueError("安装包校验失败，请检查网络后重试")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def safe_extract(archive: Path, destination: Path, *, strip: str = "") -> None:
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for item in zf.infolist():
            name = item.filename.replace("\\", "/")
            if strip:
                if not name.startswith(strip):
                    continue
                name = name[len(strip):]
            if not name:
                continue
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or ":" in parts[0]:
                raise ValueError("安装包包含不安全的路径")
            target = destination.joinpath(*parts)
            if not target.resolve().is_relative_to(root):
                raise ValueError("安装包路径超出目标目录")
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(item) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def signed_qq_url() -> str:
    import json
    body = json.dumps({"url": QQ_URL}).encode("utf-8")
    request = urllib.request.Request(
        "https://im.qq.com/http2rpc/gotrpc/noauth/trpc.qqntv2.urlsign.UrlSign/GetSign",
        body, headers={"Content-Type": "application/json",
                       "x-oidb": '{"uint32_command":"0x9b8e","uint32_service_type":1}',
                       "Origin": "https://im.qq.com", "Referer": "https://im.qq.com/index/"})
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)
    url = result.get("data", {}).get("url", "")
    if result.get("retcode") != 0 or not url.startswith(QQ_URL + "?"):
        raise ValueError("腾讯下载地址签名失败")
    return url


def verify_qq_signature(path: Path) -> None:
    """Use Windows trust verification on the pinned Tencent-signed installer."""
    import ctypes
    from ctypes import wintypes as w

    if digest(path) != QQ_SHA256:
        raise ValueError("QQ 安装包与已验证的腾讯版本不一致")

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                    ("Data4", ctypes.c_ubyte * 8)]

    class FileInfo(ctypes.Structure):
        _fields_ = [("cbStruct", w.DWORD), ("pcwszFilePath", w.LPCWSTR),
                    ("hFile", w.HANDLE), ("pgKnownSubject", ctypes.POINTER(GUID))]

    class TrustData(ctypes.Structure):
        _fields_ = [("cbStruct", w.DWORD), ("pPolicyCallbackData", ctypes.c_void_p),
                    ("pSIPClientData", ctypes.c_void_p), ("dwUIChoice", w.DWORD),
                    ("fdwRevocationChecks", w.DWORD), ("dwUnionChoice", w.DWORD),
                    ("pFile", ctypes.POINTER(FileInfo)), ("dwStateAction", w.DWORD),
                    ("hWVTStateData", w.HANDLE), ("pwszURLReference", w.LPCWSTR),
                    ("dwProvFlags", w.DWORD), ("dwUIContext", w.DWORD),
                    ("pSignatureSettings", ctypes.c_void_p)]

    file_info = FileInfo(ctypes.sizeof(FileInfo), str(path), None, None)
    data = TrustData(ctypes.sizeof(TrustData), None, None, 2, 0, 1,
                     ctypes.pointer(file_info), 0, None, None, 0, 0, None)
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0,
                  (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    verify = ctypes.WinDLL("wintrust").WinVerifyTrust
    verify.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.POINTER(TrustData)]
    verify.restype = w.LONG
    if verify(None, ctypes.byref(action), ctypes.byref(data)) != 0:
        raise ValueError("QQ 安装包 Authenticode 数字签名无效")


def prepare_runtime(root: Path, progress=None) -> dict[str, Path]:
    """Install pinned binaries into an isolated project, leaving account data alone."""
    if os.name != "nt":
        raise RuntimeError("免 Docker 模式目前仅支持 Windows x64")
    root = Path(root)
    cache, runtime = root / "downloads", root / "runtime"
    cache.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    astrbot = runtime / "astrbot"
    napcat = runtime / "napcat-template"
    qq = runtime / "qq" / "Files" / "QQ.exe"
    if not (astrbot / "backend" / "python" / "python.exe").exists():
        if progress: progress("下载 AstrBot 官方便携版…")
        package = download(*ASTRBOT, cache / "astrbot.zip", progress=progress)
        with tempfile.TemporaryDirectory(dir=runtime) as temp:
            stage = Path(temp)
            safe_extract(package, stage, strip="AstrBot_4.28.1_windows_amd64_portable/")
            if not (stage / "backend" / "python" / "python.exe").exists():
                raise ValueError("AstrBot 安装包缺少运行文件")
            shutil.move(str(stage), astrbot)
    if not (napcat / "NapCatWinBootMain.exe").exists():
        if progress: progress("下载 NapCat 官方 Windows 组件…")
        package = download(*NAPCAT, cache / "napcat.zip", progress=progress)
        with tempfile.TemporaryDirectory(dir=runtime) as temp:
            stage = Path(temp)
            safe_extract(package, stage)
            if not (stage / "NapCatWinBootMain.exe").exists():
                raise ValueError("NapCat 安装包缺少 Windows 启动器")
            shutil.move(str(stage), napcat)
    if not qq.exists():
        if progress: progress("下载腾讯官方 QQ 安装包（约 315 MB）…")
        package = cache / "qq-official.exe"
        if not (package.exists() and digest(package) == QQ_SHA256):
            download(signed_qq_url(), QQ_SHA256, package, progress=progress)
        verify_qq_signature(package)
        tools_zip = download(*SEVEN_ZIP, cache / "qq-extract-tools.zip", progress=progress)
        with tempfile.TemporaryDirectory(dir=runtime) as temp:
            stage = Path(temp)
            safe_extract(tools_zip, stage / "tools")
            tool = stage / "tools" / "7z.exe"
            if not tool.exists():
                raise ValueError("QQ 解压工具缺失")
            for source, dest, kind in (
                (package, stage / "outer", "-tPE"),
                (stage / "outer" / ".rsrc" / "2052" / "MSI" / "101",
                 stage / "qq", ""),
            ):
                command = ([str(tool), "x", "-y"] + ([kind] if kind else []) +
                           [f"-o{dest}", str(source)])
                result = subprocess.run(command, capture_output=True, timeout=300,
                                        creationflags=NO_CONSOLE)
                if result.returncode:
                    raise ValueError("无法从腾讯安装包提取 QQ 运行文件")
            if not (stage / "qq" / "Files" / "QQ.exe").exists():
                raise ValueError("腾讯安装包缺少 QQ.exe")
            shutil.move(str(stage / "qq"), runtime / "qq")
    return {"astrbot": astrbot, "napcat": napcat, "qq": qq}
