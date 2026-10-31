"""Navegação e cancelamento de contas de e-mail sem gravações parciais."""
from __future__ import annotations

import copy
from unittest.mock import Mock

from onmyoji_daemon.mcp.email.setup import email_menu, manage, edit_endpoint


def state():
    return {"schema_version": 1, "accounts": {"default": {"enabled": True, "address": "test@example.test", "display_name": "Teste", "description": "Contexto", "allowed_senders": [], "folders": {"inbox": "INBOX"}, "sent_copy": "server"}}}


def test_cancel_identity_does_not_save_partial_changes(tmp_path):
    config = Mock(root=tmp_path); document = state(); config.email.side_effect = lambda: copy.deepcopy(document)
    ui = Mock(); ui.prompt.side_effect = ["1", "x"]; ui.ask.side_effect = ["changed@example.test", None]
    manage(config, ui, "default")
    config.save_email.assert_not_called()
    assert document["accounts"]["default"]["address"] == "test@example.test"


def test_email_profile_binds_configured_values_to_its_edit_menu(tmp_path):
    config = Mock(root=tmp_path); config.email.side_effect = lambda: copy.deepcopy(state())
    ui = Mock(); ui.prompt.return_value = "x"

    manage(config, ui, "default")

    ui.profile_screen.assert_called_once_with("MCP · Conta de e-mail", "default · ATIVA", state()["accounts"]["default"])


def test_removal_is_case_insensitive_and_returns_to_accounts_menu(tmp_path):
    config = Mock(root=tmp_path); document = state(); config.email.side_effect = lambda: copy.deepcopy(document)
    def save(value): document.clear(); document.update(copy.deepcopy(value))
    config.save_email.side_effect = save
    ui = Mock(); ui.prompt.side_effect = ["2", "9", "x"]; ui.select.return_value = "default"; ui.ask.return_value = "ReMoVeR"
    email_menu(config, ui)
    assert document["accounts"] == {}
    assert [call.args[0] for call in ui.screen.call_args_list] == ["MCP · E-mail", "MCP · E-mail"]
    assert [call.args[0] for call in ui.profile_screen.call_args_list] == ["MCP · Conta de e-mail"]
    assert any("Conta removida" in call.args[1] for call in ui.result.call_args_list)


def test_reused_credentials_skip_vault_and_secret_prompts(tmp_path):
    config = Mock(root=tmp_path); ui = Mock()
    ui.flow_choice.side_effect = ["SMTP", "TLS direto"]; ui.ask.side_effect = ["smtp.example.test", "465", "test@example.test"]
    auth = {"mode": "password", "credential_profile": "vault", "credential_entry_path": "Email/test"}
    result = edit_endpoint(config, ui, None, "smtp", reuse_auth=auth)
    assert result["auth"] == auth and result["auth"] is not auth
    ui.configure_vault_entry_fields.assert_not_called(); ui.select.assert_not_called()
    ui.screen.assert_not_called()


def test_connection_test_returns_to_same_menu(tmp_path, monkeypatch):
    config = Mock(root=tmp_path); config.email.side_effect = lambda: copy.deepcopy(state())
    admin = Mock()
    monkeypatch.setattr("onmyoji_daemon.mcp.email.setup.run_admin", admin)
    ui = Mock(); ui.prompt.side_effect = ["7", "x"]
    manage(config, ui, "default")
    admin.assert_called_once_with(config, ui, "default", "test")
    assert ui.profile_screen.call_count == 2
    config.save_email.assert_not_called()


def test_setup_admin_uses_the_private_runtime_and_no_secret_in_arguments(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.email.setup import run_admin
    from pathlib import Path
    config = Mock(root=tmp_path); ui = Mock(); ui.ensure_mcp_runtime.return_value = "Pronto"
    run = Mock(); run.return_value.returncode = 0
    monkeypatch.setattr("onmyoji_daemon.mcp.email.setup.mcp_runtime_python", lambda root: Path("private-python"))
    monkeypatch.setattr("onmyoji_daemon.mcp.email.setup.subprocess.run", run)
    run_admin(config, ui, "default", "authorize-local", "smtp")
    ui.ensure_mcp_runtime.assert_called_once_with(tmp_path)
    assert run.call_args.args[0][0] == "private-python"
    assert run.call_args.args[0][-4:] == ["--action", "authorize-local", "--connection", "smtp"]
    assert Path(run.call_args.kwargs["env"]["PYTHONPATH"]).name == "src"
