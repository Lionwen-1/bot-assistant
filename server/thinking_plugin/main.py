"""Per QQ bot DeepSeek reasoning control shared with bot助手."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter


PLUGIN_NAME = "astrbot_plugin_thinking_control"
STATE_PATH = Path(__file__).resolve().parents[2] / "settings" / "bot_assistant_thinking.json"
LEVELS = {"none": "关闭", "low": "低", "high": "高", "max": "最高"}
THINKING_WORD = re.compile(r"思考|思维|推理|深度思考", re.I)
SETTING_WORD = re.compile(r"调(?:整|节|到|成|为|低|高)?|设(?:置|为|成)?|改(?:成|为)?|切换|开启|打开|关闭|禁用|不要", re.I)


def parse_thinking_command(text: str) -> str | None:
    """Recognize short, explicit control requests without spending an LLM call."""
    value = (text or "").strip()
    if not value or len(value) > 90 or "\n" in value or not THINKING_WORD.search(value):
        return None
    if (re.search(r"现在|当前|目前|查看|查询|多少|几档|什么档", value)
        and re.search(r"多少|几档|什么档|状态|级别|强度", value)
        and not SETTING_WORD.search(value)):
        return "status"
    if not SETTING_WORD.search(value):
        return None
    if re.search(r"关闭|禁用|不要|不(?:用|要)思考|不(?:用|要)推理|无思考", value):
        return "none"
    if re.search(r"最高|最大|最强|极高|max", value, re.I):
        return "max"
    if re.search(r"低(?:一点|一些|档|强度)?|最小|low", value, re.I):
        return "low"
    if re.search(r"高(?:一点|一些|档|强度)?|加强|high|默认|正常|开启|打开", value, re.I):
        return "high"
    return None


def read_state() -> dict:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))
        if data.get("schema") == 1 and isinstance(data.get("levels"), dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save_level(platform_id: str, level: str) -> None:
    data = read_state()
    if platform_id not in data.get("levels", {}) or level not in LEVELS:
        raise ValueError("不支持这个账号或思维档位")
    data["levels"][platform_id] = level
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".thinking-", dir=STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, STATE_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@star.register(PLUGIN_NAME, "local", "允许 AstrBot 管理员按 QQ 账号调整 DeepSeek 思维强度。", "1.0.0")
class ThinkingControl(star.Star):
    @filter.on_waiting_llm_request(priority=200)
    async def select_thinking(self, event: AstrMessageEvent) -> None:
        if event.get_platform_name() != "aiocqhttp":
            return
        data = read_state()
        platform_id = event.get_platform_id()
        if platform_id not in data.get("levels", {}):
            return
        command = parse_thinking_command(event.get_message_str() or "")
        if command:
            if not event.is_admin():
                message = "只有 AstrBot 管理员可以调整思维强度。"
            elif command == "status":
                current = data["levels"][platform_id]
                message = f"当前思维强度：{LEVELS[current]}。可说“把思考强度调低/调高/调到最高/关闭思考”。"
            else:
                try:
                    save_level(platform_id, command)
                    message = f"已将我的思维强度设为{LEVELS[command]}，从下一条消息起生效。"
                except (OSError, ValueError):
                    logger.exception("思维强度设置保存失败")
                    message = "思维强度保存失败，请在 bot助手中重试。"
            event.set_result(event.plain_result(message))
            event.stop_event()
            return
        if event.get_extra("selected_provider"):
            return
        config = self.context.get_config(umo=event.unified_msg_origin)
        selected = (config.get("agent_runner", {}).get("config", {})
                    .get("model", {}).get("provider_id"))
        if selected != data.get("base_provider_id"):
            return
        level = data["levels"][platform_id]
        provider_id = data.get("variants", {}).get(level)
        if provider_id and self.context.get_provider_by_id(provider_id):
            event.set_extra("selected_provider", provider_id)
