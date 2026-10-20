import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from bot_assistant.config import NativeProfile, load_profile, save_profile
from bot_assistant.native import NativeLocal
from bot_assistant.native_runtime import safe_extract
from bot_assistant.native_settings import BotSettings, merge_config
from bot_assistant.remote import RemoteError


class NativeSettingsTests(unittest.TestCase):
    def test_config_only_uses_supplied_key_and_preserves_other_fields(self):
        original = {"other": {"untouched": True}, "provider": []}
        settings = BotSettings(api_base="https://api.example/v1", api_key="my-own-key",
                               model="my-model", persona_name="角色", persona_prompt="请友好回复")
        merged = merge_config(original, settings, 6185)
        self.assertEqual(original, {"other": {"untouched": True}, "provider": []})
        self.assertEqual(merged["provider_sources"][0]["key"], ["my-own-key"])
        self.assertEqual(merged["agent_runner"]["config"]["model"]["provider_id"],
                         "bot-assistant-model")
        self.assertEqual(merged["other"], {"untouched": True})
        self.assertEqual(merged["dashboard"]["host"], "127.0.0.1")
        self.assertTrue(merged["provider_settings"]["enable"])
        updated = merge_config(merged, BotSettings(api_base="https://api.example/v1",
                                                    model="next-model"), 6185)
        self.assertEqual(updated["provider_sources"][0]["key"], ["my-own-key"])
        self.assertEqual(updated["provider"][0]["model"], "next-model")

    def test_voice_opt_in(self):
        settings = BotSettings(api_base="https://api.example/v1", api_key="own-key",
                               model="model", stt_enabled=True, stt_base="https://speech.example/v1",
                               stt_key="speech-key", tts_enabled=True,
                               tts_base="https://speech.example/v1", tts_key="voice-key")
        merged = merge_config({}, settings, 6185)
        self.assertTrue(merged["provider_stt_settings"]["enable"])
        self.assertTrue(merged["provider_tts_settings"]["enable"])
        self.assertEqual(len(merged["provider"]), 3)

    def test_rejects_nonlocal_http_api(self):
        with self.assertRaises(ValueError):
            BotSettings(api_base="http://outside.example/v1", api_key="key",
                        model="m").validate()

    def test_private_wake_word_requires_text(self):
        with self.assertRaises(ValueError):
            BotSettings(api_base="https://api.example/v1", api_key="key",
                        model="m", friend_needs_prefix=True).validate()


class NativeRuntimeTests(unittest.TestCase):
    def test_native_profile_persists_without_credentials(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            "os.environ", {"BOT_ASSISTANT_CONFIG_HOME": temp}
        ):
            profile = NativeProfile(str(Path(temp) / "project"), 6266)
            save_profile(profile)
            self.assertEqual(load_profile(), profile)
            content = (Path(temp) / "config.json").read_text(encoding="utf-8")
            self.assertNotIn("api_key", content)

    def test_zip_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../escape", "bad")
            with self.assertRaises(ValueError):
                safe_extract(archive, root / "target")
            self.assertFalse((root / "escape").exists())


class NativeAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        (self.root / "data").mkdir(parents=True)
        (self.root / "data" / "cmd_config.json").write_text("{}", encoding="utf-8")
        self.native = NativeLocal(NativeProfile(str(self.root)))
        self.native._runtime = lambda: {}
        self.native._restart_astrbot = lambda: None
        self.native._start_napcat = lambda qq, account: None

    def tearDown(self):
        self.temp.cleanup()

    def test_add_duplicate_remove_and_readd_preserve_data(self):
        with patch("bot_assistant.native._stop"):
            self.native.call("add", {"qq": "123456789", "name": "初始"})
            with self.assertRaises(RemoteError) as error:
                self.native.call("add", {"qq": "123456789", "name": "重复"})
            self.assertEqual(error.exception.code, "duplicate")
            data = self.native._account_folder("123456789") / "appdata" / "saved"
            data.parent.mkdir(parents=True)
            data.write_text("login-data")
            self.native.call("remove", {"qq": "123456789"})
            self.assertEqual(data.read_text(), "login-data")
            self.native.call("add", {"qq": "123456789", "name": "重加"})
            self.assertEqual(self.native._accounts()["123456789"]["name"], "重加")

    def test_expired_qr_is_not_returned(self):
        import os
        path = self.native._account_folder("123456789") / "shell" / "cache" / "qrcode.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 24)
        self.assertEqual(self.native._qr("123456789")["state"], "qr_ready")
        past = time.time() - 1000
        os.utime(path, (past, past))
        self.assertIsNone(self.native._qr("123456789"))

    def test_pause_survives_refresh_and_resume_only_starts_selected_account(self):
        self.native._save({
            "12345678": {"name": "一号", "created": 1},
            "87654321": {"name": "二号", "created": 2},
        })
        saved = self.native._account_folder("12345678") / "appdata" / "session"
        saved.parent.mkdir(parents=True)
        saved.write_text("login-data", encoding="utf-8")
        starts = []
        running = {101: True, 202: False}

        def stop(pid):
            running[pid] = False

        def start(qq, account):
            starts.append(qq)

        with patch.object(self.native, "_pid", side_effect=lambda name: {
            "napcat-12345678": 101, "napcat-87654321": 202,
        }.get(name, 0)), patch.object(
            self.native, "_start_astrbot"
        ), patch("bot_assistant.native._running", side_effect=lambda pid: running.get(pid, False)), patch(
            "bot_assistant.native._stop", side_effect=stop
        ), patch.object(self.native, "_start_napcat", side_effect=start):
            self.native.call("pause", {"qq": "12345678"})
            accounts = self.native._accounts()
            self.assertEqual(accounts["12345678"]["desired_state"], "stopped")
            self.assertNotIn("desired_state", accounts["87654321"])
            listed = self.native.call("list")["accounts"]
            self.assertEqual([item["state"] for item in listed], ["paused", "offline"])
            self.assertEqual(starts, ["87654321"])
            running[101] = True
            self.assertEqual(self.native.call("list")["accounts"][0]["state"], "pause_conflict")
            running[101] = False
            with self.assertRaises(RemoteError) as blocked:
                self.native.call("prepare", {"qq": "12345678"})
            self.assertEqual(blocked.exception.code, "paused")
            with self.assertRaises(RemoteError) as blocked:
                self.native.call("project_restart", {
                    "path": self.native.profile.project_path,
                    "service": "napcat-12345678",
                })
            self.assertEqual(blocked.exception.code, "paused")
            self.native.call("resume", {"qq": "12345678"})

        self.assertNotIn("desired_state", self.native._accounts()["12345678"])
        self.assertEqual(starts[-1], "12345678")
        self.assertEqual(starts.count("12345678"), 1)
        self.assertEqual(saved.read_text(encoding="utf-8"), "login-data")

    def test_pause_does_not_persist_when_process_remains_running(self):
        self.native._save({"12345678": {"name": "一号", "created": 1}})
        with patch.object(self.native, "_pid", return_value=101), patch(
            "bot_assistant.native._running", return_value=True
        ), patch("bot_assistant.native._stop"):
            with self.assertRaises(RemoteError) as blocked:
                self.native.call("pause", {"qq": "12345678"})
        self.assertEqual(blocked.exception.code, "stop_failed")
        self.assertNotEqual(self.native._accounts()["12345678"].get("desired_state"), "stopped")


if __name__ == "__main__":
    unittest.main()
