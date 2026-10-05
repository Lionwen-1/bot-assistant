import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot_assistant.config import LocalProfile, Profile, load_profile, save_profile
from bot_assistant.local import Local, NO_CONSOLE, RemoteError


class LocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"BOT_ASSISTANT_CONFIG_HOME": self.temp.name})
        self.env.start()
        self.profile = LocalProfile(str(Path(self.temp.name) / "astrbot"), 6185)

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_switching_modes_preserves_both_profiles(self):
        key = Path(self.temp.name) / "testkey"
        key.write_text("test", encoding="utf-8")
        remote = Profile("example.org", 22, "tester", str(key))
        save_profile(remote)
        save_profile(self.profile)
        self.assertEqual(load_profile(), self.profile)
        self.assertEqual(load_profile("ssh"), remote)
        save_profile(remote)
        self.assertEqual(load_profile("local"), self.profile)
        self.assertEqual(load_profile(), remote)

    def test_preflight_requires_linux_docker_and_compose(self):
        local = Local(self.profile)
        with patch("bot_assistant.docker_setup.shutil.which", return_value="docker"), patch(
                "bot_assistant.local.subprocess.run") as run:
            run.side_effect = lambda args, **kwargs: subprocess.CompletedProcess(
                args, 0, "windows" if "{{.OSType}}" in args else "2.39", "")
            with self.assertRaises(RemoteError) as caught:
                local.preflight()
            self.assertEqual(caught.exception.code, "linux_containers_required")
            self.assertTrue(all(call.kwargs["creationflags"] == NO_CONSOLE
                                for call in run.call_args_list))

    def test_new_project_deploy_and_attach_existing_without_overwrite(self):
        local = Local(self.profile)
        calls = []
        with patch.object(local, "_docker", side_effect=lambda *args, **kwargs: calls.append(args) or "ok"):
            result = local.deploy()
        self.assertFalse(result["attached"])
        compose = Path(self.profile.project_path) / "compose.yaml"
        content = compose.read_text(encoding="utf-8")
        self.assertIn("soulter/astrbot:latest", content)
        self.assertIn('127.0.0.1:6185:6185', content)
        self.assertIn(local.network_name, content)
        self.assertEqual(calls[0][-2:], ("up", "-d"))
        with patch.object(local, "_docker", return_value="ok"):
            self.assertFalse(local.deploy()["attached"])
        self.assertEqual(compose.read_text(encoding="utf-8"), content)

        other = LocalProfile(str(Path(self.temp.name) / "existing"))
        Path(other.project_path).mkdir()
        existing = Path(other.project_path) / "compose.yaml"
        existing.write_text("services: {custom: {image: example}}\n")
        attached = Local(other)
        with patch.object(attached, "_docker") as docker:
            self.assertTrue(attached.deploy()["attached"])
            docker.assert_not_called()
        self.assertEqual(existing.read_text(), "services: {custom: {image: example}}\n")

    def test_account_calls_use_separate_local_registry(self):
        local = Local(self.profile)
        with patch.object(local, "_docker", return_value="ok"):
            local.deploy()
        with patch("bot_assistant.local._docker_path", return_value=Path("docker")), patch.object(
                local.manager, "preflight", return_value={}), patch.object(
                local.manager, "compose", return_value=""), patch.object(
                local.manager, "state", return_value=("offline", "waiting")):
            first = local.call("add", {"qq": "12345678", "name": "本机账号"})
            self.assertEqual(first["qq"], "12345678")
            with self.assertRaises(RemoteError) as caught:
                local.call("add", {"qq": "12345678", "name": "重复"})
            self.assertEqual(caught.exception.code, "duplicate")
            listed = local.call("list")["accounts"][0]
            self.assertEqual(listed["name"], "本机账号")
            self.assertTrue(6000 <= listed["astrbot_ws_port"] < 10000)
        registry = Path(self.profile.project_path) / "bot-assistant" / "accounts.json"
        self.assertIn("12345678", json.loads(registry.read_text(encoding="utf-8"))["accounts"])
        account = registry.parent / "accounts" / "12345678"
        config = json.loads((account / "config" / "onebot11_12345678.json").read_text(encoding="utf-8"))
        self.assertEqual(config["network"]["websocketClients"][0]["url"],
                         f"ws://astrbot:{listed['astrbot_ws_port']}/ws")
        self.assertIn(local.network_name, (account / "compose.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
