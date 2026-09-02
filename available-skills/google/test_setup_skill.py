from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parent
SCRIPT = SKILL / "setupSkill.py"


class GoogleSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        configs = self.root / "configs"
        configs.mkdir()
        (configs / "keepass.toml").write_text("[profiles.local]\nvault = \"main\"\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_setup(self, arguments: list[str], input_text: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SCRIPT), "--onmyoji-root", str(self.root), *arguments], input=input_text, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)

    def test_interactive_create_uses_keepass_without_a_json_file(self) -> None:
        process = self.run_setup([], "1\nprincipal\n\n1\n\n\n\n\n\nx\n")
        self.assertEqual(process.returncode, 0, process.stderr)
        data = tomllib.loads((self.root / "configs" / "google.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["profiles"]["principal"]["oauth_profile"], "principal")
        self.assertEqual(data["profiles"]["principal"]["vault_profile"], "local")
        self.assertEqual(data["profiles"]["principal"]["vault_entry_path"], "APIs/Google:Principal")
        self.assertEqual(data["profiles"]["principal"]["credentials_file"], "")

    def test_interactive_cancel_does_not_create_configuration(self) -> None:
        process = self.run_setup([], "x\n")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse((self.root / "configs" / "google.toml").exists())

    def test_profile_api_creates_and_lists_profiles(self) -> None:
        created = self.run_setup(["--action", "profile-create", "--profile", "principal", "--set", "oauth_profile=principal", "--set", "credentials_file=C:/oauth-client.json", "--set", "vault_profile=local", "--set", "vault_entry_path=APIs/Google:Principal"])
        self.assertEqual(created.returncode, 0, created.stderr)
        listed = self.run_setup(["--action", "profile-list"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertEqual(json.loads(listed.stdout)["profiles"][0]["name"], "principal")


if __name__ == "__main__":
    unittest.main()
