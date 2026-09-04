from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parent


class MySQLSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(); self.root = Path(self.temporary.name)
        configs = self.root / "configs"; configs.mkdir()
        (configs / "keepass.toml").write_text('[profiles.local]\nvault = "main"\n', encoding="utf-8")

    def tearDown(self) -> None: self.temporary.cleanup()

    def setup(self, arguments: list[str] = [], input_text: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SKILL / "setupSkill.py"), "--onmyoji-root", str(self.root), *arguments], input=input_text, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)

    def test_interactive_create_allows_a_profile_without_default_database(self) -> None:
        process = self.setup(input_text="1\nprincipal\n\n\n\n1\n\n\n\nx\n")
        self.assertEqual(process.returncode, 0, process.stderr)
        data = tomllib.loads((self.root / "configs" / "mysql.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["profiles"]["principal"]["host"], "127.0.0.1")
        self.assertEqual(data["profiles"]["principal"]["database"], "")
        self.assertEqual(data["profiles"]["principal"]["vault_entry_path"], "APIs/MySQL:Principal")

    def test_profile_create_allows_omitting_database(self) -> None:
        process = self.setup(["--action", "profile-create", "--profile", "principal", "--set", "vault_profile=local", "--set", "vault_entry_path=APIs/MySQL:Principal"])
        self.assertEqual(process.returncode, 0, process.stderr)
        data = tomllib.loads((self.root / "configs" / "mysql.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["profiles"]["principal"]["database"], "")

    def test_cancel_does_not_create_configuration(self) -> None:
        self.assertEqual(self.setup(input_text="x\n").returncode, 0)
        self.assertFalse((self.root / "configs" / "mysql.toml").exists())
