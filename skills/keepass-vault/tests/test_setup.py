from __future__ import annotations

import subprocess
import os
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]


class SetupTests(unittest.TestCase):
    def test_status_accepts_missing_config_as_empty_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "status"], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Configuração válida", result.stdout)

    def test_configure_bootstraps_missing_file_and_exits_with_x(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="x\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((Path(temporary) / "configs" / "keepass.toml").is_file())

    def test_configure_creates_profile_from_guided_menu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "vault.kdbx"
            vault.touch()
            auth_answers = "1\nOnmyoji/KeePass/pessoal\n" if os.name == "nt" else "1\n"
            answers = f"1\npessoal\npessoal\n{sys.executable}\n{vault.as_posix()}\n1\n{auth_answers}\n\nx\n"
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input=answers, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(temporary) / "configs" / "keepass.toml").read_text(encoding="utf-8")
            self.assertIn("[profiles.pessoal]", content)
            self.assertIn(vault.as_posix(), content)

    def test_x_cancels_profile_wizard_without_saving_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="1\ncancelado\nx\nx\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(temporary) / "configs" / "keepass.toml").read_text(encoding="utf-8")
            self.assertNotIn("[profiles.cancelado]", content)
            self.assertIn("Configuração cancelada", result.stdout)


if __name__ == "__main__":
    unittest.main()
