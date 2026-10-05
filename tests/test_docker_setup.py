import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot_assistant import docker_setup as setup


class DockerSetupTests(unittest.TestCase):
    def test_per_user_install_is_discovered_without_path_entry(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder) / "Programs" / "DockerDesktop"
            cli = base / "resources" / "bin" / "docker.exe"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"stub")
            (base / "Docker Desktop.exe").write_bytes(b"stub")
            with patch.dict(os.environ, {"LOCALAPPDATA": folder}), patch(
                    "bot_assistant.docker_setup.shutil.which", return_value=None):
                self.assertEqual(setup.find_docker_cli(), cli)
                self.assertEqual(setup.find_desktop(), base / "Docker Desktop.exe")

    def test_single_button_installs_starts_and_waits_for_linux_engine(self):
        states = [setup.DockerState("missing", "missing"),
                  setup.DockerState("stopped", "starting"),
                  setup.DockerState("ready", "ready")]
        progress = []
        with patch.object(setup, "docker_state", side_effect=states), patch.object(
                setup, "install_docker_desktop") as install, patch.object(
                setup, "start_docker_desktop") as start, patch.object(
                setup.time, "sleep"):
            setup.ensure_docker_ready(progress.append)
        install.assert_called_once()
        start.assert_called_once()
        self.assertEqual(progress[-1], "ready")

    def test_existing_windows_engine_is_not_switched_implicitly(self):
        with patch.object(setup, "docker_state", return_value=setup.DockerState(
                "windows", "Windows containers")), patch.object(
                setup, "start_docker_desktop") as start:
            with self.assertRaises(setup.DockerSetupError):
                setup.ensure_docker_ready(lambda _message: None)
        start.assert_not_called()

    def test_installer_uses_per_user_mode_without_automatic_license_acceptance(self):
        with patch.object(setup, "find_desktop", side_effect=[None, Path("docker.exe")]), patch.object(
                setup, "_download_installer") as download, patch.object(
                setup, "_verify_docker_signature") as verify, patch.object(
                setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as run:
            setup.install_docker_desktop(lambda _message: None)
        download.assert_called_once()
        verify.assert_called_once()
        self.assertEqual(run.call_args.args[0][1:], ["install", "--user"])

    def test_rejects_invalid_installer_signature(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
                setup.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, json.dumps({"status": "UnknownError", "subject": "Docker Inc."}), "")):
            with self.assertRaises(setup.DockerSetupError):
                setup._verify_docker_signature(Path(folder) / "installer.exe")

    def test_rejects_non_docker_download_redirect(self):
        class Response:
            headers = {"Content-Length": "10000000"}
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def geturl(self):
                return "https://example.org/installer.exe"
        with tempfile.TemporaryDirectory() as folder, patch.object(
                setup.urllib.request, "urlopen", return_value=Response()):
            with self.assertRaises(setup.DockerSetupError):
                setup._download_installer(Path(folder) / "installer.exe", lambda _message: None)


if __name__ == "__main__":
    unittest.main()
