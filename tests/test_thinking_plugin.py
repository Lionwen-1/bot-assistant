import asyncio
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "server" / "thinking_plugin" / "main.py"


def load_plugin():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    event_module = types.ModuleType("astrbot.api.event")
    star = types.SimpleNamespace(Star=type("Star", (), {"__init__": lambda self, context: setattr(self, "context", context)}),
                                 register=lambda *_args, **_kwargs: lambda cls: cls)
    filters = types.SimpleNamespace(on_waiting_llm_request=lambda **_kwargs: lambda fn: fn)
    api.star = star
    api.logger = types.SimpleNamespace(exception=lambda *_args: None)
    event_module.AstrMessageEvent = object
    event_module.filter = filters
    modules = {"astrbot": astrbot, "astrbot.api": api,
               "astrbot.api.event": event_module}
    with patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("thinking_plugin_test", SOURCE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class Event:
    unified_msg_origin = "qq:test"

    def __init__(self, platform, text, admin=True):
        self.platform = platform
        self.text = text
        self.admin = admin
        self.extra = {}
        self.reply = None
        self.stopped = False

    def get_platform_name(self): return "aiocqhttp"
    def get_platform_id(self): return self.platform
    def get_message_str(self): return self.text
    def is_admin(self): return self.admin
    def get_extra(self, key): return self.extra.get(key)
    def set_extra(self, key, value): self.extra[key] = value
    def plain_result(self, text): return text
    def set_result(self, value): self.reply = value
    def stop_event(self): self.stopped = True


class ThinkingPluginTests(unittest.TestCase):
    def setUp(self):
        self.module = load_plugin()
        self.temp = tempfile.TemporaryDirectory()
        self.state = Path(self.temp.name) / "thinking.json"
        self.state.write_text(json.dumps({"schema": 1, "base_provider_id": "base",
                                          "variants": {"none": "off", "low": "low",
                                                       "high": "high", "max": "max"},
                                          "levels": {"vina": "high", "bagpipe": "high"}}),
                              encoding="utf-8")
        self.path_patch = patch.object(self.module, "STATE_PATH", self.state)
        self.path_patch.start()
        context = types.SimpleNamespace(
            get_config=lambda **_kwargs: {"agent_runner": {"config": {"model": {"provider_id": "base"}}}},
            get_provider_by_id=lambda ident: object() if ident in {"off", "low", "high", "max"} else None)
        self.plugin = self.module.ThinkingControl(context)

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def test_natural_commands_and_admin_guard(self):
        self.assertEqual(self.module.parse_thinking_command("把思考强度调到最高"), "max")
        self.assertEqual(self.module.parse_thinking_command("关闭思考"), "none")
        self.assertEqual(self.module.parse_thinking_command("现在思维强度是多少"), "status")
        self.assertIsNone(self.module.parse_thinking_command("思考强度会影响结果吗"))
        denied = Event("vina", "把思考强度调低", admin=False)
        asyncio.run(self.plugin.select_thinking(denied))
        self.assertTrue(denied.stopped)
        self.assertEqual(self.module.read_state()["levels"]["vina"], "high")
        change = Event("vina", "把思考强度调低")
        asyncio.run(self.plugin.select_thinking(change))
        self.assertTrue(change.stopped)
        self.assertEqual(self.module.read_state()["levels"],
                         {"vina": "low", "bagpipe": "high"})

    def test_provider_routing_only_for_matching_platform_and_model(self):
        vina = Event("vina", "你好")
        asyncio.run(self.plugin.select_thinking(vina))
        self.assertEqual(vina.extra["selected_provider"], "high")
        other = Event("other", "你好")
        asyncio.run(self.plugin.select_thinking(other))
        self.assertNotIn("selected_provider", other.extra)
        chosen = Event("bagpipe", "你好")
        chosen.extra["selected_provider"] = "manual"
        asyncio.run(self.plugin.select_thinking(chosen))
        self.assertEqual(chosen.extra["selected_provider"], "manual")


if __name__ == "__main__":
    unittest.main()
