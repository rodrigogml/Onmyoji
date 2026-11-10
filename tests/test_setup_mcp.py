from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


SCRIPT = Path(__file__).resolve().parents[1] / "onmyoji-daemon" / "setupMcp.py"
SPEC = importlib.util.spec_from_file_location("onmyoji_mcp_setup", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class McpSetupTests(unittest.TestCase):
    def test_vault_menu_reports_inaccessible_configuration_without_traceback(self) -> None:
        config = MagicMock()
        config.vault_profile_names.side_effect = MODULE.McpConfigurationError("Reparar permissões.")

        with patch.object(MODULE, "result") as show_result:
            MODULE.vault_profiles_menu(config)

        show_result.assert_called_once_with(False, "Reparar permissões.")

    def test_profile_item_displays_the_configured_value_beside_its_edit_action(self) -> None:
        output = io.StringIO()
        profile = {"host": "mail.example.test", "port": 465}

        with patch.object(MODULE.sys, "stdout", output):
            MODULE.profile_screen("Perfil", "Teste", profile)
            MODULE.item("1.", "Alterar host e porta")

        rendered = output.getvalue()
        self.assertIn("host: mail.example.test", rendered)
        self.assertIn("mail.example.test", rendered)
        self.assertIn("port: 465", rendered)

    def test_new_profile_suggests_default_only_when_available(self) -> None:
        with patch.object(MODULE, "ask", return_value="default") as ask:
            self.assertEqual(MODULE.ask_new_profile_name("Nome", {}), "default")
        ask.assert_called_once_with("Nome", "default", required=True)

        with patch.object(MODULE, "ask", return_value="outro") as ask:
            self.assertEqual(MODULE.ask_new_profile_name("Nome", {"default": {}}), "outro")
        ask.assert_called_once_with("Nome", "", required=True)

    def test_eccovox_media_roots_are_created_inside_the_configured_workspace(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd() / "tmp") as temporary:
            root = Path(temporary) / "instance"; workspace = Path(temporary) / "workspace"
            root.mkdir(); workspace.mkdir(); (root / "configs").mkdir()
            (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + repr(str(workspace)).replace("'", '"') + '\n', encoding="utf-8")
            roots = MODULE.eccovox_media_roots(SimpleNamespace(root=root))
            self.assertEqual(roots, [str(workspace)])
            self.assertTrue(all(Path(root).is_dir() for root in roots))
            self.assertEqual(MODULE.eccovox_default_output_directory(SimpleNamespace(root=root)), str(workspace / ".onmyoji" / "eccovox" / "output"))

    def test_eccovox_profile_creation_uses_the_single_workspace_root_for_writing(self) -> None:
        config = MagicMock()
        data = {"profiles": {}}
        config.eccovox.return_value = data

        with patch.object(MODULE, "prompt", side_effect=["1", "x"]), patch.object(MODULE, "ask", side_effect=["tsuki", "Voz do Tsuki", "http://127.0.0.1:8870", "C:/Work", "C:/Work", "C:/Work/.onmyoji/eccovox/output"]), patch.object(MODULE.EccoVoxService, "capabilities_at", return_value={}), patch.object(MODULE, "choose_eccovox_capability", side_effect=["pm_alex", "pt-BR"]), patch.object(MODULE, "eccovox_media_roots", return_value=["C:/Work"]), patch.object(MODULE, "eccovox_default_output_directory", return_value="C:/Work/.onmyoji/eccovox/output"):
            MODULE.eccovox_menu(config)

        self.assertEqual(data["profiles"]["tsuki"]["readable_roots"], ["C:/Work"])
        self.assertEqual(data["profiles"]["tsuki"]["writable_roots"], ["C:/Work"])
        config.save_eccovox.assert_called_once_with(data)

    def test_eccovox_writable_roots_fall_back_to_the_single_workspace_root(self) -> None:
        config = MagicMock()
        profile = {"enabled": True, "description": "Voz do Tsuki", "base_url": "http://127.0.0.1:8870", "voice": "pm_alex", "language": "pt-BR", "default_output_directory": "C:/Work/.onmyoji/eccovox/output", "readable_roots": ["C:/Work"], "writable_roots": []}
        data = {"profiles": {"tsuki": profile}}
        config.eccovox.return_value = data

        with patch.object(MODULE, "prompt", side_effect=["7", "x"]), patch.object(MODULE, "ask", return_value="C:/Work") as ask, patch.object(MODULE, "eccovox_media_roots", return_value=["C:/Work"]):
            MODULE.manage_eccovox_profile(config, "tsuki")

        ask.assert_called_once_with("Raízes separadas por ;", "C:/Work", True)
        self.assertEqual(profile["writable_roots"], ["C:/Work"])
        config.save_eccovox.assert_called_once_with(data)

    def test_profile_selection_is_a_flow_step_not_a_new_screen(self) -> None:
        with patch.object(MODULE, "flow_choice", return_value="principal") as choice, patch.object(MODULE, "screen") as screen:
            selected = MODULE.select("Escolha o perfil de vault", ["principal"])
        self.assertEqual(selected, "principal")
        choice.assert_called_once_with("Escolha o perfil de vault", ["principal"], "")
        screen.assert_not_called()

    def test_vault_credential_targets_are_scoped_to_the_shikigami(self) -> None:
        with patch.object(MODULE, "instance_identity", return_value="Lavelinha"), patch.object(MODULE, "ask", return_value="Lavelinha/MCP/KeePass/default") as ask, patch.object(MODULE, "os", SimpleNamespace(name="nt")):
            auth = MODULE.profile_auth("default", Path("C:/instancia"))
        ask.assert_called_once_with("Destino no Windows Credential Manager", "Lavelinha/MCP/KeePass/default", True)
        self.assertEqual(auth["windows"]["target"], "Lavelinha/MCP/KeePass/default")
        self.assertEqual(auth["linux"]["command"], ["secret-tool", "lookup", "service", "shikigami-mcp", "shikigami", "Lavelinha", "profile", "default"])

    def test_headless_linux_uses_a_protected_local_secret(self) -> None:
        root = Path("/opt/Shikigami-Lavelinha")
        with patch.object(MODULE, "instance_identity", return_value="Lavelinha"), patch.object(MODULE, "os", wraps=os) as operating_system, patch.object(MODULE.shutil, "which", return_value="/usr/bin/secret-tool"):
            operating_system.name = "posix"
            operating_system.environ = {}
            auth = MODULE.profile_auth("default", root)

        self.assertEqual(auth["linux"]["mode"], "protected_file")
        self.assertTrue(auth["linux"]["path"].startswith(str(root / "configs" / "mcp" / "secrets")))

    def test_new_vault_password_is_reused_without_a_third_prompt(self) -> None:
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/default"}}
        with patch.object(MODULE, "os", SimpleNamespace(name="nt")), patch.object(MODULE.getpass, "getpass") as getpass, patch.object(MODULE, "store_windows_password") as store, patch.object(MODULE, "result"):
            saved = MODULE.save_password("default", auth, password="senha-confirmada")
        self.assertTrue(saved)
        getpass.assert_not_called()
        store.assert_called_once_with("Lavelinha/MCP/KeePass/default", "senha-confirmada")

    def test_linux_missing_keepassxc_offers_a_user_local_installation(self) -> None:
        command = ["/usr/bin/flatpak", "run", "--command=keepassxc-cli", "org.keepassxc.KeePassXC"]
        with patch.object(MODULE, "os", SimpleNamespace(name="posix")), patch.object(MODULE, "discover_keepassxc_command", return_value=None), patch.object(MODULE.shutil, "which", return_value="/usr/bin/flatpak"), patch.object(MODULE, "flow_choice", return_value="Instalar KeePassXC somente para esta conta (Flatpak)") as choice, patch.object(MODULE, "install_user_keepassxc", return_value=command) as install:
            selected = MODULE.select_keepassxc_command()

        self.assertEqual(selected, command)
        self.assertIn("Instalar KeePassXC somente para esta conta (Flatpak)", choice.call_args.args[1])
        install.assert_called_once_with()

    def test_flatpak_keepassxc_is_scoped_to_the_database_directory(self) -> None:
        command = ["/usr/bin/flatpak", "run", "--command=keepassxc-cli", "org.keepassxc.KeePassXC"]
        database = "/home/shikigami/vaults/default.kdbx"
        scoped = MODULE.scope_flatpak_to_database(command, database)
        self.assertEqual(scoped[:3], ["/usr/bin/flatpak", "run", f"--filesystem={Path(database).resolve().parent}"])

    def test_vault_creation_stops_before_credentials_when_keepassxc_is_unavailable(self) -> None:
        config = MagicMock()
        config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Criar cofre KDBX local"), patch.object(MODULE, "select_keepassxc_command", return_value=None), patch.object(MODULE.getpass, "getpass") as getpass:
            MODULE.create_vault_profile(config)

        getpass.assert_not_called()
        config.save_credentials.assert_not_called()

    def test_keepassxc_creation_error_preserves_cause_without_password_prompts(self) -> None:
        completed = subprocess.CompletedProcess(
            ["keepassxc-cli"], 1, "", "Enter password to encrypt database (optional):\nRepeat password:\nError: file already exists.\n"
        )

        message = MODULE.keepassxc_creation_error(completed)

        self.assertEqual(message, "KeePassXC não criou o cofre: Error: file already exists.")

    def test_new_vault_does_not_overwrite_an_existing_database(self) -> None:
        config = MagicMock()
        config.root = Path("C:/instancia")
        config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
        database = config.root / "configs" / "mcp" / "vaults" / "principal.kdbx"

        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Criar cofre KDBX local"), patch.object(MODULE, "select_keepassxc_command", return_value=["keepassxc-cli"]), patch.object(MODULE, "ensure_linux_secret_tool", return_value=True), patch.object(MODULE, "ask", return_value=""), patch.object(MODULE, "profile_auth", return_value={}), patch.object(MODULE.getpass, "getpass", return_value="senha-mestra"), patch.object(MODULE.Path, "exists", return_value=True), patch.object(MODULE.subprocess, "run") as run, patch.object(MODULE, "result") as result:
            MODULE.create_vault_profile(config)

        run.assert_not_called()
        config.save_credentials.assert_not_called()
        result.assert_called_once_with(False, f"Já existe um arquivo em {database}. Associe esse KDBX como cofre existente ou escolha outro nome de perfil.")

    def test_existing_vault_is_validated_and_its_password_is_saved(self) -> None:
        config = MagicMock()
        config.root = Path("C:/instancia")
        config.credentials.return_value = {"defaults": {"timeout_seconds": 30}, "vaults": {}, "profiles": {}}
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/principal"}}

        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Associar arquivo KDBX existente"), patch.object(MODULE, "select_keepassxc_command", return_value=["keepassxc-cli"]), patch.object(MODULE, "ensure_linux_secret_tool", return_value=True), patch.object(MODULE, "ask", side_effect=["C:/vaults/principal.kdbx", ""]), patch.object(MODULE, "profile_auth", return_value=auth), patch.object(MODULE.getpass, "getpass", return_value="senha-mestra"), patch.object(MODULE.KeePassCredentialResolver, "validate_vault_access") as validate, patch.object(MODULE, "save_password", return_value=True) as save:
            MODULE.create_vault_profile(config)

        validate.assert_called_once_with({"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vaults/principal.kdbx", "linux": ""}}, {"timeout_seconds": 30}, "senha-mestra")
        config.save_credentials.assert_called_once()
        save.assert_called_once_with("principal", auth, config.root, "senha-mestra")

    def test_existing_vault_with_invalid_password_is_not_saved(self) -> None:
        config = MagicMock()
        config.root = Path("C:/instancia")
        config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/principal"}}

        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Associar arquivo KDBX existente"), patch.object(MODULE, "select_keepassxc_command", return_value=["keepassxc-cli"]), patch.object(MODULE, "ensure_linux_secret_tool", return_value=True), patch.object(MODULE, "ask", side_effect=["C:/vaults/principal.kdbx", ""]), patch.object(MODULE, "profile_auth", return_value=auth), patch.object(MODULE.getpass, "getpass", return_value="senha-incorreta"), patch.object(MODULE.KeePassCredentialResolver, "validate_vault_access", side_effect=MODULE.CredentialError("keepass_error", "recusado")), patch.object(MODULE, "save_password") as save, patch.object(MODULE, "result") as result:
            MODULE.create_vault_profile(config)

        config.save_credentials.assert_not_called()
        save.assert_not_called()
        result.assert_called_once_with(False, "Não foi possível acessar o cofre com a senha informada.")

    def test_existing_vault_is_not_saved_when_the_password_provider_fails(self) -> None:
        config = MagicMock()
        config.root = Path("C:/instancia")
        config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/principal"}}

        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Associar arquivo KDBX existente"), patch.object(MODULE, "select_keepassxc_command", return_value=["keepassxc-cli"]), patch.object(MODULE, "ensure_linux_secret_tool", return_value=True), patch.object(MODULE, "ask", side_effect=["C:/vaults/principal.kdbx", ""]), patch.object(MODULE, "profile_auth", return_value=auth), patch.object(MODULE.getpass, "getpass", return_value="senha-mestra"), patch.object(MODULE.KeePassCredentialResolver, "validate_vault_access"), patch.object(MODULE, "save_password", return_value=False), patch.object(MODULE, "result") as result:
            MODULE.create_vault_profile(config)

        config.save_credentials.assert_not_called()
        result.assert_called_once_with(False, "O perfil não foi salvo porque a senha mestra não pôde ser protegida.")

