"""Public QQ portrait cache; no account credentials leave the server."""

from __future__ import annotations

import os
import tempfile
import time
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image

from .config import config_home

MAX_AGE = 24 * 60 * 60


def avatar_path(qq: str) -> Path:
    return config_home() / "avatars" / f"{qq}.jpg"


def needs_refresh(qq: str) -> bool:
    path = avatar_path(qq)
    return not path.exists() or time.time() - path.stat().st_mtime > MAX_AGE


def fetch_avatar(qq: str) -> Path:
    if not qq.isdecimal():
        raise ValueError("invalid QQ")
    url = f"https://thirdqq.qlogo.cn/g?b=sdk&s=640&nk={qq}"
    with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0 bot-assistant"}),
                 timeout=8) as response:
        data = response.read(2_000_001)
    if len(data) > 2_000_000:
        raise ValueError("avatar too large")
    with Image.open(BytesIO(data)) as source:
        source.load()
        if min(source.size) < 40:
            raise ValueError("avatar too small")
        avatar = source.convert("RGB")
    path = avatar_path(qq)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".avatar-", dir=path.parent)
    os.close(fd)
    try:
        avatar.save(temporary, format="JPEG", quality=90)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
