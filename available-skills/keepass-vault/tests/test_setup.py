from __future__ import annotations

import subprocess
import os
import sys
import tempfile
import shutil
import unittest
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("keepass_setup", SKILL_DIR / "setupSkill.py")
assert SPEC and SPEC.loader
SETUP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SETUP
SPEC.loader.exec_module(SETUP)


class SetupTests(unittest.TestCase):
    def write_profiles(self, temporary: str) -> None:
        root, vault = Path(temporary), Path(temporary) / "vault.kdbx"
        vault.touch()
        config = root / "configs" / "keepass.toml"
        config.parent.mkdir()
        profile = lambda name: f'''\n[profiles.{name}]\nvault = "shared"\naccess = "read_only"\nallowed_operations = ["list"]\nallowed_entry_roots = []\nallowed_attachment_roots = []\n\n[profiles.{name}.auth]\nallowed_modes = ["configured"]\n\n[profiles.{name}.auth.windows]\nmode = "windows_credential_manager"\ntarget = "Onmyoji/KeePass/{name}"\n\n[profiles.{name}.auth.linux]\nmode = "command"\ncommand = []\n'''
        config.write_text(f'''schema_version = 1\n\n[defaults]\ntimeout_seconds = 30\n\n[vaults.shared]\ncli_command = ["{Path(sys.executable).as_posix()}"]\n\n[vaults.shared.database]\nwindows = "{vault.as_posix()}"\nlinux = "{vault.as_posix()}"\n''' + profile("alpha") + profile("beta"), encoding="utf-8")
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
            self.assertIn("+ OK", result.stdout)

    def test_operation_failure_has_a_prominent_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="4\ninvalido\nx\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("! ERRO", result.stdout)
            self.assertIn("Informe um número inteiro.", result.stdout)

    def test_configure_creates_profile_from_guided_menu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "vault.kdbx"
            vault.touch()
            auth_answers = "1\nOnmyoji/KeePass/pessoal\n" if os.name == "nt" else "1\n"
            answers = f"1\npessoal\n1\npessoal\n{sys.executable}\n{vault.as_posix()}\n1\n{auth_answers}\n\nx\n"
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input=answers, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(temporary) / "configs" / "keepass.toml").read_text(encoding="utf-8")
            self.assertIn("[profiles.pessoal]", content)
            self.assertIn(vault.as_posix(), content)

    def test_configure_removes_legacy_model_example_before_saving_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "configs" / "keepass.toml"
            config.parent.mkdir()
            shutil.copy2(SKILL_DIR / "configs" / "keepass.toml.model", config)
            vault = Path(temporary) / "vault.kdbx"
            vault.touch()
            auth_answers = "1\nOnmyoji/KeePass/pessoal\n" if os.name == "nt" else "1\n"
            answers = f"1\npessoal\n1\npessoal\n{sys.executable}\n{vault.as_posix()}\n1\n{auth_answers}\n\nx\n"
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input=answers, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = config.read_text(encoding="utf-8")
            self.assertIn("Perfil demonstrativo 'example' removido", result.stdout)
            self.assertIn("[profiles.pessoal]", content)
            self.assertNotIn("[profiles.example]", content)

    def test_x_cancels_profile_wizard_without_saving_a_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="1\ncancelado\nx\nx\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(temporary) / "configs" / "keepass.toml").read_text(encoding="utf-8")
            self.assertNotIn("[profiles.cancelado]", content)
            self.assertIn("Configuração cancelada", result.stdout)

    def test_local_vault_creation_passes_password_only_through_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "configs" / "vaults" / "lavelinha.kdbx"
            completed = SimpleNamespace(returncode=0, stdout="", stderr="")
            def run(command: list[str], **kwargs: object) -> SimpleNamespace:
                self.assertEqual(command, [sys.executable, "db-create", "--set-password", str(database)])
                self.assertEqual(kwargs["input"], "senha-secreta\nsenha-secreta\n")
                database.parent.mkdir(parents=True, exist_ok=True); database.touch()
                return completed
            with patch.object(SETUP.subprocess, "run", side_effect=run):
                ok, _message = SETUP.create_local_vault(sys.executable, database, "senha-secreta")
            self.assertTrue(ok)
            self.assertTrue(database.is_file())

    def test_local_profile_creates_isolated_vault_and_stores_password(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); config = root / "configs" / "keepass.toml"; data = SETUP.empty_config()
            def create(_executable: str, database: Path, _password: str) -> tuple[bool, str]:
                database.parent.mkdir(parents=True, exist_ok=True); database.touch(); return True, "criado"
            with patch.object(SETUP, "ask", side_effect=["lavelinha", sys.executable, "", ""]), patch.object(SETUP, "ask_choice", side_effect=["2", "1"]), patch.object(SETUP.getpass, "getpass", side_effect=["senha", "senha"]), patch.object(SETUP, "create_local_vault", side_effect=create), patch.object(SETUP, "write_system_password", return_value=(True, "salva")) as stored:
                SETUP.create_profile(data, config, root, "lavelinha")
            database = SETUP.local_vault_for(root, "lavelinha")
            self.assertTrue(database.is_file())
            self.assertEqual(data["profiles"]["lavelinha"]["vault"], "lavelinha")
            self.assertEqual(data["vaults"]["lavelinha"]["database"]["windows" if os.name == "nt" else "linux"], str(database))
            stored.assert_called_once()

    def test_edit_menu_lists_profiles_and_shows_current_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.write_profiles(temporary)
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="2\n1\nx\nx\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("1.  alpha", result.stdout)
            self.assertIn("2.  beta", result.stdout)
            self.assertIn("Nome do perfil", result.stdout)
            self.assertIn("Diretórios de anexos", result.stdout)
            self.assertIn("Salvar senha no provedor do SO", result.stdout)
            self.assertIn("Testar acesso ao vault", result.stdout)
            self.assertIn("X.  Voltar", result.stdout)

    def test_edit_menu_changes_only_selected_item(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.write_profiles(temporary)
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="2\n1\n7\nWork;Admin\nx\nx\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(temporary) / "configs" / "keepass.toml").read_text(encoding="utf-8")
            self.assertIn('allowed_entry_roots = ["Work", "Admin"]', content)

    def test_remove_menu_uses_number_and_explicit_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            self.write_profiles(temporary)
            result = subprocess.run([sys.executable, str(SKILL_DIR / "setupSkill.py"), "--onmyoji-root", temporary, "--action", "configure"], input="3\n2\nREMOVER\nx\n", capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            content = (Path(temporary) / "configs" / "keepass.toml").read_text(encoding="utf-8")
            self.assertIn("[profiles.alpha]", content)
            self.assertNotIn("[profiles.beta]", content)
            self.assertIn("Digite REMOVER para confirmar", result.stdout)


if __name__ == "__main__":
    unittest.main()
