import importlib.util
import base64
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "server" / "manager.py"
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = types.SimpleNamespace(
            LOCK_EX=1, LOCK_UN=2, flock=lambda *_args: None)
SPEC = importlib.util.spec_from_file_location("remote_manager", SOURCE)
manager_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager_module)


class FakeManager(manager_module.Manager):
    def __init__(self, home):
        super().__init__(home)
        self.actions = []
        self.fail_up = False
        self.states = {}
        self.ages = []
        self.mtimes = []
        self.qr_waiting = True

    def preflight(self):
        return {"python": "3.12", "docker": "28", "compose": "2.39"}

    def compose(self, qq, *args, timeout=120):
        self.actions.append((qq, *args))
        if args[:2] == ("up", "-d") and self.fail_up:
            raise manager_module.ManagerError("docker_failed", "fake pull failure")
        return ""

    def _run(self, args, timeout=25):
        self.actions.append(tuple(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    def docker(self, *args, timeout=25):
        self.actions.append(tuple(args))
        return ""

    def state(self, qq, account):
        return self.states.get(qq, "offline"), "fake"

    def _running(self, qq):
        return True

    def qr_age(self, qq):
        return self.ages.pop(0) if self.ages else -1

    def qr_mtime(self, qq):
        return self.mtimes.pop(0) if self.mtimes else 1

    def waiting_qr(self, qq):
        return self.qr_waiting

    def _qr_payload(self, qq, age):
        return {"qq": qq, "state": "qr_ready", "age_seconds": age,
                "ttl_seconds": 110, "qr_png_base64": "iVBORw0KGgo="}


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.uid = patch.object(manager_module.os, "getuid", return_value=1000, create=True)
        self.gid = patch.object(manager_module.os, "getgid", return_value=1000, create=True)
        self.uid.start()
        self.gid.start()
        self.manager = FakeManager(Path(self.temp.name))

    def tearDown(self):
        self.uid.stop()
        self.gid.stop()
        self.temp.cleanup()

    def test_add_duplicate_and_remove_preserves_account_data(self):
        first = self.manager.add("12345678", "一号")
        self.assertEqual(first["qq"], "12345678")
        self.manager.add("87654321", "二号")
        config = self.manager.account_dir("12345678") / "config" / "onebot11_12345678.json"
        self.assertTrue(config.exists())
        self.assertEqual(json.loads(config.read_text())["network"]["httpServers"][0]["port"], 3000)
        with self.assertRaises(manager_module.ManagerError) as caught:
            self.manager.add("12345678", "重复")
        self.assertEqual(caught.exception.code, "duplicate")
        self.manager.actions.clear()
        self.assertTrue(self.manager.remove("12345678")["data_kept"])
        self.assertTrue(self.manager.account_dir("12345678").exists())
        self.assertEqual(set(self.manager.registry()), {"87654321"})
        self.assertTrue(all("87654321" not in str(action) for action in self.manager.actions))
        self.manager.add("12345678", "重新添加")
        self.assertTrue(config.exists())

    def test_failed_add_leaves_no_registry_entry_and_only_cleans_target(self):
        self.manager.add("87654321", "已有账号")
        self.manager.actions.clear()
        self.manager.fail_up = True
        with self.assertRaises(manager_module.ManagerError):
            self.manager.add("12345678", "新账号")
        self.assertEqual(set(self.manager.registry()), {"87654321"})
        self.assertTrue(any("down" in action for action in self.manager.actions))
        self.assertTrue(all("87654321" not in str(action) for action in self.manager.actions))

    def test_prepare_reuses_fresh_qr_without_restart(self):
        self.manager.add("12345678", "一号")
        self.manager.actions.clear()
        self.manager.ages = [12]
        payload = self.manager.prepare("12345678")
        self.assertEqual(payload["state"], "qr_ready")
        self.assertEqual(payload["age_seconds"], 12)
        self.assertFalse(any("restart" in action for action in self.manager.actions))

    def test_status_response_omits_server_token(self):
        self.manager.add("12345678", "一号")
        self.manager.states["12345678"] = "online"
        result = self.manager.list_accounts()
        self.assertEqual(result["accounts"][0]["state"], "online")
        self.assertNotIn("token", json.dumps(result))

    def test_qr_payload_is_png_and_contains_no_server_token(self):
        self.manager.add("12345678", "一号")
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ZB9sAAAAASUVORK5CYII=")
        def fake_copy(args, timeout=25):
            Path(args[-1]).write_bytes(png)
            return subprocess.CompletedProcess(args, 0, "", "")
        with patch.object(self.manager, "_run", side_effect=fake_copy):
            result = manager_module.Manager._qr_payload(self.manager, "12345678", 3)
        self.assertEqual(base64.b64decode(result["qr_png_base64"]), png)
        self.assertNotIn("token", json.dumps(result))

    @patch.object(manager_module.time, "sleep", return_value=None)
    def test_prepare_expired_qr_restarts_only_target(self, _sleep):
        self.manager.add("12345678", "一号")
        self.manager.add("87654321", "二号")
        self.manager.actions.clear()
        self.manager.ages = [100, 100, 1]
        self.manager.mtimes = [1, 2]
        payload = self.manager.prepare("12345678")
        self.assertEqual(payload["age_seconds"], 1)
        self.assertIn(("restart", "bot-assistant-u1000-12345678"), self.manager.actions)
        self.assertTrue(all("87654321" not in str(action) for action in self.manager.actions))

    @patch.object(manager_module.time, "sleep", return_value=None)
    def test_unknown_state_does_not_restart(self, _sleep):
        self.manager.add("12345678", "一号")
        self.manager.actions.clear()
        self.manager.states["12345678"] = "unknown"
        self.manager.qr_waiting = False
        with self.assertRaises(manager_module.ManagerError) as caught:
            self.manager.prepare("12345678")
        self.assertEqual(caught.exception.code, "status_unknown")
        self.assertFalse(any("restart" in action for action in self.manager.actions))

    @patch.object(manager_module.time, "sleep", return_value=None)
    def test_unknown_state_with_expired_login_qr_restarts_only_target(self, _sleep):
        self.manager.add("12345678", "一号")
        self.manager.add("87654321", "二号")
        self.manager.actions.clear()
        self.manager.states["12345678"] = "unknown"
        self.manager.ages = [200, 1]
        self.manager.mtimes = [1, 2]
        payload = self.manager.prepare("12345678")
        self.assertEqual(payload["state"], "qr_ready")
        self.assertIn(("restart", "bot-assistant-u1000-12345678"), self.manager.actions)
        self.assertTrue(all("87654321" not in str(action) for action in self.manager.actions))

    def test_adopt_existing_container_without_restarting_or_removing_it(self):
        qq = "12345678"
        config = Path(self.temp.name) / f"onebot11_{qq}.json"
        config.write_text(json.dumps({"network": {"httpServers": [
            {"enable": True, "token": "private-token", "port": 6700}]}}))
        def inspect(args, timeout=25):
            self.manager.actions.append(tuple(args))
            return subprocess.CompletedProcess(args, 0, "true\n", "")
        online = io.BytesIO(b'{"data":{"online":true}}')
        with (patch.object(self.manager, "_run", side_effect=inspect),
              patch.object(manager_module.urllib.request, "urlopen", return_value=online)):
            result = self.manager.adopt(qq, "现有账号", "napcat", 6700, config)
        self.assertEqual(result["mode"], "adopted")
        self.assertEqual(self.manager.container_name(qq), "napcat")
        self.assertEqual(self.manager.registry()[qq]["token"], "private-token")
        self.assertNotIn("token", json.dumps(result))
        self.assertFalse(any("restart" in action for action in self.manager.actions))
        self.manager.actions.clear()
        removed = self.manager.remove(qq)
        self.assertTrue(removed["container_kept"])
        self.assertFalse(self.manager.actions)

    def test_adopt_rejects_unverified_account(self):
        qq = "12345678"
        config = Path(self.temp.name) / f"onebot11_{qq}.json"
        config.write_text(json.dumps({"network": {"httpServers": [
            {"enable": True, "token": "private-token", "port": 6700}]}}))
        with (patch.object(self.manager, "_run", return_value=subprocess.CompletedProcess(
                  [], 0, "true\n", "")),
              patch.object(manager_module.urllib.request, "urlopen",
                           return_value=io.BytesIO(b'{"data":{"online":false}}'))):
            with self.assertRaises(manager_module.ManagerError) as caught:
                self.manager.adopt(qq, "现有账号", "napcat", 6700, config)
        self.assertEqual(caught.exception.code, "account_unverified")
        self.assertFalse(self.manager.registry())

    def test_adopt_checks_reverse_websocket_when_requested(self):
        qq = "12345678"
        config = Path(self.temp.name) / f"onebot11_{qq}.json"
        config.write_text(json.dumps({"network": {"httpServers": [
            {"enable": True, "token": "private-token", "port": 6700}]}}))
        def check(args, timeout=25):
            value = "true\n" if args[1] == "inspect" else "no\n"
            return subprocess.CompletedProcess(args, 0, value, "")
        with (patch.object(self.manager, "_run", side_effect=check),
              patch.object(manager_module.urllib.request, "urlopen",
                           return_value=io.BytesIO(b'{"data":{"online":true}}'))):
            with self.assertRaises(manager_module.ManagerError) as caught:
                self.manager.adopt(qq, "现有账号", "napcat", 6700, config,
                                   "astrbot", 6199)
        self.assertEqual(caught.exception.code, "chat_unverified")
        self.assertFalse(self.manager.registry())


class ProjectOpsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        (self.root / "compose.yaml").write_text("services: {astrbot: {image: example}}\n")
        settings = self.root / "data" / "settings"
        settings.mkdir(parents=True)
        (settings / "role.json").write_text('{"role": "assistant"}', encoding="utf-8")
        (settings / "private.json").write_text('{"api_key": "secret"}', encoding="utf-8")
        plugin = self.root / "data" / "plugins" / "astrbot_plugin_example"
        plugin.mkdir(parents=True)
        (plugin / "metadata.yaml").write_text("name: Example\nversion: 1.0\ndesc: Demo\n")
        self.project = manager_module.ProjectOps(str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def test_status_logs_restart_only_project_services(self):
        def fake_run(command, **_kwargs):
            if command[-2:] == ["config", "--services"]:
                output = "astrbot\nnapcat\n"
            elif command[-4:] == ["ps", "-a", "--format", "json"]:
                output = '{"Service":"astrbot","State":"running","Status":"Up"}\n'
            elif "logs" in command:
                output = "recent log\nAuthorization: Bearer private\ntoken=hidden\n"
            else:
                output = ""
            return subprocess.CompletedProcess(command, 0, output, "")
        with patch.object(manager_module.subprocess, "run", side_effect=fake_run) as runner:
            status = self.project.status()
            self.assertEqual(status["running"], 1)
            self.assertEqual(len(status["services"]), 2)
            text = self.project.logs("astrbot", 20)["text"]
            self.assertIn("recent log", text)
            self.assertNotIn("private", text)
            self.assertNotIn("hidden", text)
            self.assertTrue(self.project.restart("astrbot")["restarted"])
            with self.assertRaises(manager_module.ManagerError):
                self.project.logs("other", 20)
            self.assertTrue(all(command.args[0][0:3] ==
                                ["docker", "compose", "-f"]
                                for command in runner.call_args_list))

    def test_config_view_rejects_secrets_and_outside_paths(self):
        self.assertIn("data/settings/role.json",
                      [item["path"] for item in self.project.configs()["configs"]])
        self.assertEqual(self.project.config_read("data/settings/role.json")["read_only"], True)
        for name in ("data/settings/private.json", "../.env", "data/cmd_config.json"):
            with self.assertRaises(manager_module.ManagerError):
                self.project.config_read(name)
        with self.assertRaises(manager_module.ManagerError):
            manager_module.ProjectOps("/../private")

    def test_plugins_and_full_backup_stay_on_server(self):
        self.assertEqual(self.project.plugins()["plugins"][0]["name"], "Example")
        (self.root / ".env").write_text("TEST_ONLY=1\n")
        saved = self.project.backup()
        archive = self.root / "backups" / saved["name"]
        self.assertTrue(saved["stored_on_server"])
        with tarfile.open(archive) as stream:
            names = stream.getnames()
        self.assertIn(".env", names)
        self.assertIn("data/settings/role.json", names)
        self.assertFalse(any(name.startswith("backups/") for name in names))
        self.assertEqual(self.project.backups()["backups"][0]["name"], saved["name"])

    def test_bot_settings_summary_never_returns_api_credentials(self):
        config = self.root / "data" / "cmd_config.json"
        config.write_text(json.dumps({
            "provider": [{"id": "own-model", "enable": True,
                          "api_key": "private-test-key"}],
            "provider_settings": {"enable": True, "provider_pool": ["*"]},
            "provider_tts_settings": {"enable": False, "provider_id": ""},
            "provider_stt_settings": {"enable": True, "provider_id": "own-stt"},
        }), encoding="utf-8-sig")
        result = self.project.bot_settings()
        self.assertEqual(result["provider_count"], 1)
        self.assertFalse(result["tts_enabled"])
        self.assertTrue(result["stt_enabled"])
        self.assertNotIn("private-test-key", json.dumps(result))
        self.assertNotIn("own-model", json.dumps(result))

    def test_thinking_control_installs_variants_and_isolates_platform_levels(self):
        config = self.root / "data" / "cmd_config.json"
        config.write_text(json.dumps({
            "provider_sources": [{"id": "deepseek-source", "type": "openai_responses",
                                  "provider": "deepseek", "key": ["private-test-key"]}],
            "provider": [{"id": "deepseek-flash-model", "enable": True,
                          "provider_source_id": "deepseek-source",
                          "model": "deepseek-flash"}],
            "agent_runner": {"config": {"model": {"provider_id": "deepseek-flash-model"}}},
            "platform": [{"id": "vina", "type": "aiocqhttp", "enable": True},
                         {"id": "bagpipe", "type": "aiocqhttp", "enable": True}],
        }), encoding="utf-8")
        source_dir = SOURCE.parent / "thinking_plugin"
        source = (source_dir / "main.py").read_text(encoding="utf-8")
        metadata = (source_dir / "metadata.yaml").read_text(encoding="utf-8")
        with (patch.object(self.project, "services", return_value=["astrbot"]),
              patch.object(self.project, "_compose", return_value="") as compose):
            status = self.project.thinking_install(source, metadata)
        self.assertTrue(status["installed"])
        compose.assert_called_once_with("restart", "astrbot", timeout=150)
        updated = json.loads(config.read_text(encoding="utf-8"))
        variants = {item["id"]: item for item in updated["provider"]}
        self.assertEqual(len(variants), 5)
        self.assertEqual([item["custom_extra_body"]["reasoning"]["effort"]
                          for item in updated["provider"][1:]],
                         list(manager_module.THINKING_LEVELS))
        self.assertEqual(self.project.thinking_set("vina", "low")["level"], "low")
        levels = {item["id"]: item["level"]
                  for item in self.project.thinking_status()["platforms"]}
        self.assertEqual(levels, {"vina": "low", "bagpipe": "high"})
        self.assertNotIn("private-test-key", json.dumps(status))
        with self.assertRaises(manager_module.ManagerError):
            self.project.thinking_set("other", "max")

if __name__ == "__main__":
    unittest.main()
