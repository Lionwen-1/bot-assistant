import base64
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bot_assistant.config import Profile, load_profile, save_profile
from bot_assistant.remote import NO_CONSOLE, Remote, RemoteError, parse_host_keys


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"BOT_ASSISTANT_CONFIG_HOME": self.temp.name})
        self.env.start()
        self.key = Path(self.temp.name) / "testkey"
        self.key.write_text("fake key", encoding="utf-8")
        self.profile = Profile("example.org", 22, "tester", str(self.key))

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_profile_and_host_fingerprint(self):
        save_profile(self.profile)
        self.assertEqual(load_profile(), self.profile)
        line = "example.org ssh-ed25519 " + base64.b64encode(b"sample public key blob").decode()
        key = parse_host_keys(line, self.profile)
        self.assertEqual(key.algorithm, "ssh-ed25519")
        self.assertTrue(key.fingerprint.startswith("SHA256:"))

    def test_project_profile_and_loopback_dashboard_tunnel(self):
        profile = Profile("example.org", 22, "tester", str(self.key),
                          "/opt/example-bot", 6185)
        save_profile(profile)
        self.assertEqual(load_profile(), profile)
        remote = Remote(profile)
        remote.known_hosts.write_text("example.org ssh-ed25519 AAA=\n", encoding="utf-8")
        process = MagicMock()
        process.poll.return_value = None
        with (patch("bot_assistant.remote.subprocess.Popen", return_value=process) as popen,
              patch("bot_assistant.remote.socket.create_connection") as connect):
            returned, local_port = remote.dashboard_tunnel()
        self.assertIs(returned, process)
        self.assertGreater(local_port, 0)
        args = popen.call_args.args[0]
        forward = args[args.index("-L") + 1]
        self.assertEqual(forward, f"127.0.0.1:{local_port}:127.0.0.1:6185")
        self.assertIn("StrictHostKeyChecking=yes", args)
        self.assertEqual(popen.call_args.kwargs["creationflags"], NO_CONSOLE)
        connect.assert_called_once()

    def test_ssh_requires_trusted_host(self):
        remote = Remote(self.profile)
        with self.assertRaises(RemoteError) as caught:
            remote.call("list")
        self.assertEqual(caught.exception.code, "host_untrusted")

    def test_simulated_fingerprint_confirmation_and_deploy(self):
        remote = Remote(self.profile)
        line = "example.org ssh-ed25519 " + base64.b64encode(b"server key").decode()
        with patch("bot_assistant.remote.subprocess.run", return_value=
                   subprocess.CompletedProcess(["ssh-keyscan"], 0, line + "\n", "")):
            key = remote.scan_host_key()
        self.assertFalse(remote.is_trusted(key))
        remote.trust_host_key(key)
        self.assertTrue(remote.is_trusted(key))

        def fake_ssh(args, **kwargs):
            if kwargs.get("input"):
                digest = hashlib.sha256(kwargs["input"]).hexdigest()
                body = {"ok": True, "sha256": digest}
            else:
                body = {"ok": True, "python": "3.12"}
            return subprocess.CompletedProcess(args, 0, json.dumps(body).encode(), b"")
        with patch("bot_assistant.remote.subprocess.run", side_effect=fake_ssh):
            self.assertEqual(remote.preflight()["python"], "3.12")
            self.assertTrue(remote.deploy()["sha256"])

        changed = "example.org ssh-ed25519 " + base64.b64encode(b"another key").decode()
        with patch("bot_assistant.remote.subprocess.run", return_value=
                   subprocess.CompletedProcess(["ssh-keyscan"], 0, changed + "\n", "")):
            with self.assertRaises(RemoteError) as caught:
                remote.scan_host_key()
        self.assertEqual(caught.exception.code, "host_key_changed")

    def test_fallback_when_windows_keyscan_cannot_negotiate(self):
        remote = Remote(self.profile)
        line = "example.org ssh-ed25519 " + base64.b64encode(b"fallback server key").decode()
        def fake_run(args, **_kwargs):
            if args[0] == "ssh-keyscan":
                return subprocess.CompletedProcess(args, 1, "", "unsupported KEX")
            known = next(arg.split("=", 1)[1] for arg in args
                         if arg.startswith("UserKnownHostsFile="))
            Path(known).write_text(line + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, b"", b"")
        with patch("bot_assistant.remote.subprocess.run", side_effect=fake_run):
            key = remote.scan_host_key()
        self.assertEqual(key.line, line)
        self.assertFalse(remote.is_trusted(key))

    def test_simulated_ssh_response_and_network_failure(self):
        remote = Remote(self.profile)
        remote.known_hosts.write_text("example.org ssh-ed25519 AAA=\n", encoding="utf-8")
        response = json.dumps({"protocol": 1, "ok": True, "accounts": []}).encode()
        with patch("bot_assistant.remote.subprocess.run", return_value=
                   subprocess.CompletedProcess(["ssh"], 0, response, b"")) as runner:
            self.assertEqual(remote.call("list")["accounts"], [])
            self.assertIn("StrictHostKeyChecking=yes", runner.call_args.args[0])
            self.assertEqual(runner.call_args.kwargs["creationflags"], NO_CONSOLE)
        with patch("bot_assistant.remote.subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 2)):
            with self.assertRaises(RemoteError) as caught:
                remote.call("list")
            self.assertEqual(caught.exception.code, "ssh_timeout")


if __name__ == "__main__":
    unittest.main()
