from __future__ import annotations

import importlib.util
import io
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
            self.assertEqual(roots, [str(workspace / ".onmyoji" / "eccovox" / "input"), str(workspace / ".onmyoji" / "eccovox" / "output")])
            self.assertTrue(all(Path(root).is_dir() for root in roots))

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

    def test_new_vault_password_is_reused_without_a_third_prompt(self) -> None:
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/default"}}
        with patch.object(MODULE, "os", SimpleNamespace(name="nt")), patch.object(MODULE.getpass, "getpass") as getpass, patch.object(MODULE, "store_windows_password") as store, patch.object(MODULE, "result"):
            MODULE.save_password("default", auth, password="senha-confirmada")
        getpass.assert_not_called()
        store.assert_called_once_with("Lavelinha/MCP/KeePass/default", "senha-confirmada")

    def test_existing_vault_is_validated_and_its_password_is_saved(self) -> None:
        config = MagicMock()
        config.root = Path("C:/instancia")
        config.credentials.return_value = {"defaults": {"timeout_seconds": 30}, "vaults": {}, "profiles": {}}
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/principal"}}

        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Associar arquivo KDBX existente"), patch.object(MODULE, "ask", side_effect=["keepassxc-cli", "C:/vaults/principal.kdbx", ""]), patch.object(MODULE, "profile_auth", return_value=auth), patch.object(MODULE.getpass, "getpass", return_value="senha-mestra"), patch.object(MODULE.KeePassCredentialResolver, "validate_vault_access") as validate, patch.object(MODULE, "save_password") as save:
            MODULE.create_vault_profile(config)

        validate.assert_called_once_with({"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vaults/principal.kdbx", "linux": ""}}, {"timeout_seconds": 30}, "senha-mestra")
        config.save_credentials.assert_called_once()
        save.assert_called_once_with("principal", auth, config.root, "senha-mestra")

    def test_existing_vault_with_invalid_password_is_not_saved(self) -> None:
        config = MagicMock()
        config.root = Path("C:/instancia")
        config.credentials.return_value = {"defaults": {}, "vaults": {}, "profiles": {}}
        auth = {"windows": {"target": "Lavelinha/MCP/KeePass/principal"}}

        with patch.object(MODULE, "ask_new_profile_name", return_value="principal"), patch.object(MODULE, "flow_choice", return_value="Associar arquivo KDBX existente"), patch.object(MODULE, "ask", side_effect=["keepassxc-cli", "C:/vaults/principal.kdbx", ""]), patch.object(MODULE, "profile_auth", return_value=auth), patch.object(MODULE.getpass, "getpass", return_value="senha-incorreta"), patch.object(MODULE.KeePassCredentialResolver, "validate_vault_access", side_effect=MODULE.CredentialError("keepass_error", "recusado")), patch.object(MODULE, "save_password") as save, patch.object(MODULE, "result") as result:
            MODULE.create_vault_profile(config)

        config.save_credentials.assert_not_called()
        save.assert_not_called()
        result.assert_called_once_with(False, "Não foi possível acessar o cofre com a senha informada.")

