"""Configuration for a new, isolated AstrBot Windows project.

This module never reads the developer's AstrBot installation.  The generated
configuration is written only under the project chosen in the setup wizard.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


CONFIG_NAME = "cmd_config.json"
SOURCE_ID = "bot-assistant-source"
MODEL_ID = "bot-assistant-model"
STT_ID = "bot-assistant-stt"
TTS_ID = "bot-assistant-tts"


@dataclass(frozen=True)
class BotSettings:
    api_base: str = ""
    api_key: str = ""
    model: str = ""
    persona_name: str = ""
    persona_prompt: str = ""
    chat_enabled: bool = True
    wake_prefix: str = ""
    friend_needs_prefix: bool = False
    reply_with_mention: bool = False
    reply_with_quote: bool = False
    stt_enabled: bool = False
    stt_base: str = ""
    stt_key: str = ""
    stt_model: str = "whisper-1"
    tts_enabled: bool = False
    tts_base: str = ""
    tts_key: str = ""
    tts_model: str = "tts-1"
    tts_voice: str = "alloy"

    def validate(self, *, existing: dict | None = None) -> None:
        if not self.api_base or not self.model:
            raise ValueError("请填写模型 API 地址与模型名称")
        _check_url(self.api_base, "模型 API 地址")
        old = _entry(existing, "provider_sources", SOURCE_ID)
        if not self.api_key and not (old and old.get("key")):
            raise ValueError("请填写模型 API Key")
        if not re.fullmatch(r"[\w./:-]{1,180}", self.model):
            raise ValueError("模型名称包含不支持的字符")
        if len(self.persona_name) > 80 or len(self.persona_prompt) > 20000:
            raise ValueError("角色名称或设定过长")
        if len(self.wake_prefix) > 80 or any(ord(char) < 32 for char in self.wake_prefix):
            raise ValueError("唤醒词无效或过长")
        if self.friend_needs_prefix and not self.wake_prefix.strip():
            raise ValueError("启用私聊唤醒词时，请填写唤醒词")
        if self.persona_prompt and not self.persona_name.strip():
            raise ValueError("填写角色设定时也需填写角色名称")
        for enabled, base, key, model, ident, label in (
            (self.stt_enabled, self.stt_base, self.stt_key, self.stt_model,
             STT_ID, "语音识别"),
            (self.tts_enabled, self.tts_base, self.tts_key, self.tts_model,
             TTS_ID, "语音回复"),
        ):
            if enabled:
                _check_url(base, label + " API 地址")
                old_voice = _entry(existing, "provider", ident)
                if not key and not (old_voice and old_voice.get("api_key")):
                    raise ValueError(label + "已启用，请填写其 API Key")
                if not model.strip():
                    raise ValueError(label + "已启用，请填写模型名称")


def _check_url(value: str, label: str) -> None:
    parsed = urlparse(value.strip())
    if (parsed.scheme != "https" and not
        (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})):
        raise ValueError(label + "需使用 HTTPS；本机服务可使用 localhost")
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(label + "无效")


def _entry(config: dict | None, group: str, ident: str) -> dict | None:
    if not isinstance(config, dict):
        return None
    for item in config.get(group, []):
        if isinstance(item, dict) and item.get("id") == ident:
            return item
    return None


def _upsert(items: list, entry: dict) -> None:
    for index, old in enumerate(items):
        if isinstance(old, dict) and old.get("id") == entry["id"]:
            items[index] = entry
            return
    items.append(entry)


def merge_config(config: dict, settings: BotSettings, dashboard_port: int) -> dict:
    """Apply the small set of settings owned by bot助手, keeping all other fields."""
    settings.validate(existing=config)
    next_config = json.loads(json.dumps(config))
    dashboard = next_config.setdefault("dashboard", {})
    dashboard.update({"enable": True, "host": "127.0.0.1", "port": dashboard_port})
    source_old = _entry(next_config, "provider_sources", SOURCE_ID) or {}
    source = {**source_old, "id": SOURCE_ID, "provider": "openai",
              "type": "openai_chat_completion", "provider_type": "chat_completion",
              "enable": True, "api_base": settings.api_base.strip().rstrip("/"),
              "timeout": 120, "proxy": "", "custom_headers": {}}
    if settings.api_key:
        source["key"] = [settings.api_key.strip()]
    next_config.setdefault("provider_sources", [])
    _upsert(next_config["provider_sources"], source)
    model_old = _entry(next_config, "provider", MODEL_ID) or {}
    model = {**model_old, "id": MODEL_ID, "enable": True,
             "provider_source_id": SOURCE_ID, "model": settings.model.strip(),
             "modalities": ["text"], "custom_extra_body": {}}
    next_config.setdefault("provider", [])
    _upsert(next_config["provider"], model)
    runner = next_config.setdefault("agent_runner", {})
    runner.setdefault("runner_type", "local")
    runner.setdefault("config", {}).setdefault("model", {})["provider_id"] = MODEL_ID
    chat = next_config.setdefault("provider_settings", {})
    chat["enable"] = settings.chat_enabled
    chat["wake_prefix"] = settings.wake_prefix.strip()
    platform = next_config.setdefault("platform_settings", {})
    platform["friend_message_needs_wake_prefix"] = settings.friend_needs_prefix
    platform["reply_with_mention"] = settings.reply_with_mention
    platform["reply_with_quote"] = settings.reply_with_quote
    for enabled, ident, provider_type, base, key, model_name in (
        (settings.stt_enabled, STT_ID, "speech_to_text", settings.stt_base,
         settings.stt_key, settings.stt_model),
        (settings.tts_enabled, TTS_ID, "text_to_speech", settings.tts_base,
         settings.tts_key, settings.tts_model),
    ):
        section = "provider_stt_settings" if ident == STT_ID else "provider_tts_settings"
        next_config.setdefault(section, {})["enable"] = enabled
        next_config[section]["provider_id"] = ident if enabled else ""
        if not enabled:
            continue
        old = _entry(next_config, "provider", ident) or {}
        voice = {**old, "id": ident, "provider": "openai",
                 "provider_type": provider_type, "enable": True,
                 "type": "openai_whisper_api" if ident == STT_ID else "openai_tts_api",
                 "api_base": base.strip().rstrip("/"), "model": model_name.strip(),
                 "proxy": ""}
        if key:
            voice["api_key"] = key.strip()
        if ident == TTS_ID:
            voice["openai-tts-voice"] = settings.tts_voice.strip() or "alloy"
        _upsert(next_config["provider"], voice)
    return next_config


def write_config(project: Path, settings: BotSettings, dashboard_port: int) -> Path:
    path = project / "data" / CONFIG_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    current = json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
    if not isinstance(current, dict):
        raise ValueError("AstrBot 配置格式无效")
    updated = merge_config(current, settings, dashboard_port)
    fd, temp = tempfile.mkstemp(prefix=".cmd_config-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return path
