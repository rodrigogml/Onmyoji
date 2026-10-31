from __future__ import annotations

import importlib.util
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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

