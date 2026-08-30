from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parent / "setupSkill.py"
SPEC = importlib.util.spec_from_file_location("ssh_setup", SCRIPT)
assert SPEC and SPEC.loader
SETUP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SETUP
SPEC.loader.exec_module(SETUP)


class SshSetupTests(unittest.TestCase):
    def data(self, root: Path, temporary: Path) -> dict:
        return {"schema_version": 1, "defaults": {"timeout_seconds": 30, "temp_dir": ""}, "profiles": {"server": {"host": "server.internal", "port": 22, "username": "operator", "auth_mode": "password", "vault_profile": "pessoal", "vault_entry_path": "APIs/SSH:Server", "keepass_password_field": "password", "keepass_key_attachment": "id_ed25519", "keepass_key_passphrase_field": "password", "known_hosts": "", "temp_dir": str(temporary)}}}

    def prepare(self, root: Path) -> Path:
        project = root / "project"; project.mkdir()
        configs = root / "configs"; configs.mkdir()
        (configs / "onmyoji-system.toml").write_text(f'[codex]\nproject_directory = "{project.as_posix()}"\n', encoding="utf-8")
        (configs / "keepass.toml").write_text("[profiles.pessoal]\nvault = \"local\"\n", encoding="utf-8")
        return project

    def test_profile_save_creates_local_configuration_with_workspace_temp_area(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); project = self.prepare(root); path = SETUP.config_path(root)
            ok, message = SETUP.save(path, self.data(root, project / ".onmyoji" / "ssh" / "temporary-keys"))
            self.assertTrue(ok, message)
            self.assertTrue(path.is_file())

    def test_profile_outside_workspace_is_rejected_and_not_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); self.prepare(root); path = SETUP.config_path(root)
            ok, message = SETUP.save(path, self.data(root, root / "outside"))
            self.assertFalse(ok)
            self.assertIn("workspace", message)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
