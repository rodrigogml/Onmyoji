from __future__ import annotations

import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parent


class BIS10CMDSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "configs").mkdir()
        (self.root / "configs" / "keepass.toml").write_text('[profiles.jndi]\nvault = "jndi"\n[profiles.bis]\nvault = "bis"\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def setup(self, arguments: list[str] | None = None, input_text: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(SKILL / "setupSkill.py"), "--onmyoji-root", str(self.root), *(arguments or [])], input=input_text, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False)

    def test_interactive_create_suggests_two_vault_entries(self) -> None:
        inputs = "\n".join(["1", "principal", "", "", "", "", "", "", "", "", "2", "", "", "", "1", "", "", "", "x", ""])
        process = self.setup(input_text=inputs)
        self.assertEqual(process.returncode, 0, process.stderr)
        data = tomllib.loads((self.root / "configs" / "bis10cmd.toml").read_text(encoding="utf-8"))
        profile = data["profiles"]["principal"]
        self.assertEqual(profile["jndi_vault_profile"], "jndi")
        self.assertEqual(profile["bis_vault_profile"], "bis")
        self.assertEqual(profile["jndi_vault_entry_path"], "APIs/BIS10CMD:JNDI:Principal")
        self.assertEqual(profile["bis_vault_entry_path"], "APIs/BIS10CMD:BIS10:Principal")

    def test_profile_schema_and_cancel(self) -> None:
        result = self.setup(["--action", "profile-schema", "--json"])
        self.assertIn("jndi_vault_entry_path", result.stdout)
        self.assertEqual(self.setup(input_text="x\n").returncode, 0)
        self.assertFalse((self.root / "configs" / "bis10cmd.toml").exists())

    def test_delete_last_profile_keeps_profiles_table_and_allows_recreation(self) -> None:
        create = ["--action", "profile-create", "--profile", "first", "--set", "jar_path=C:/opt/BIS10CMD/BISCMD-10.0.jar", "--set", "working_dir=C:/opt/BIS10CMD", "--set", "jndi_vault_profile=jndi", "--set", "jndi_vault_entry_path=APIs/BIS10CMD:JNDI:First", "--set", "bis_vault_profile=bis", "--set", "bis_vault_entry_path=APIs/BIS10CMD:BIS10:First", "--json"]
        self.assertEqual(self.setup(create).returncode, 0)
        self.assertEqual(self.setup(["--action", "profile-delete", "--profile", "first", "--confirm-delete", "DELETE", "--json"]).returncode, 0)
        config = (self.root / "configs" / "bis10cmd.toml").read_text(encoding="utf-8")
        self.assertIn("[profiles]", config)
        recreate = create.copy(); recreate[recreate.index("first")] = "second"
        self.assertEqual(self.setup(recreate).returncode, 0)

    def test_repair_preserves_damaged_configuration_and_rebuilds_it(self) -> None:
        config = self.root / "configs" / "bis10cmd.toml"
        config.write_text("[profiles\n", encoding="utf-8")
        process = self.setup(["--action", "repair", "--json"])
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8"))["profiles"], {})
        self.assertEqual(len(list(config.parent.glob("bis10cmd.invalid-*.toml.bak"))), 1)
