"""Navegação e cancelamento de contas de e-mail sem gravações parciais."""
from __future__ import annotations

import copy
from unittest.mock import Mock

from onmyoji_daemon.mcp.email.setup import edit_endpoint, edit_monitoring, edit_roots, email_menu, manage


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
    result = edit_endpoint(config, ui, None, "smtp", "default", reuse_auth=auth)
    assert result["auth"] == auth and result["auth"] is not auth
    ui.configure_vault_entry_fields.assert_not_called(); ui.select.assert_not_called()
    ui.screen.assert_not_called()


def test_new_email_credential_uses_instance_mcp_entry_convention(tmp_path):
    config = Mock(root=tmp_path); ui = Mock()
    ui.flow_choice.side_effect = ["SMTP", "TLS direto", "Senha ou senha de aplicativo"]
    ui.ask.side_effect = ["smtp.example.test", "465", "test@example.test", "Lavelinha/MCP/Email:default:SMTP"]
    ui.select.return_value = "vault"
    ui.configure_vault_entry_fields.return_value = ("Lavelinha/MCP/Email:default:SMTP", {"password_field": "password"})
    (tmp_path / "configs").mkdir(); (tmp_path / "configs" / "onmyoji-system.toml").write_text('[shikigami]\nidentity = "Lavelinha"\n', encoding="utf-8")

    edit_endpoint(config, ui, None, "smtp", "default")

    assert ui.ask.call_args_list[3].args == ("Entrada KeePass dedicada", "Lavelinha/MCP/Email:default:SMTP", True)


def test_attachment_roots_default_to_and_create_workspace_root(tmp_path):
    workspace = tmp_path / "workspace"
    system = tmp_path / "configs" / "onmyoji-system.toml"; system.parent.mkdir(parents=True)
    system.write_text('[codex]\nproject_directory = "' + str(workspace).replace("\\", "\\\\") + '"\n', encoding="utf-8")
    config = Mock(root=tmp_path); ui = Mock()
    root = str(workspace)
    ui.ask.side_effect = [root, root]
    account = {"readable_roots": [], "writable_roots": []}

    assert edit_roots(config, ui, account) is True

    assert account == {"readable_roots": [root], "writable_roots": [root]}
    assert [call.args[1] for call in ui.ask.call_args_list] == [root, root]
    assert workspace.is_dir()


def test_attachment_roots_preserve_explicit_opt_out_without_creating_directory(tmp_path):
    workspace = tmp_path / "workspace"
    system = tmp_path / "configs" / "onmyoji-system.toml"; system.parent.mkdir(parents=True)
    system.write_text('[codex]\nproject_directory = "' + str(workspace).replace("\\", "\\\\") + '"\n', encoding="utf-8")
    config = Mock(root=tmp_path); ui = Mock(); ui.ask.side_effect = ["-", "-"]
    account = {"readable_roots": [], "writable_roots": []}

    assert edit_roots(config, ui, account) is True

    assert account == {"readable_roots": [], "writable_roots": []}
    assert not (workspace / ".onmyoji" / "email").exists()


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


def test_monitoring_setup_collects_interval_instructions_channel_and_policy(tmp_path):
    config = Mock(root=tmp_path)
    config.email.return_value = {"accounts": {"default": {"enabled": True, "smtp": {}}}}
    ui = Mock()
    ui.flow_choice.side_effect = ["Habilitada", "Telegram", "Nova thread"]
    ui.ask.side_effect = ["30m", "Resuma somente mensagens que exigem ação.", "-100:42"]
    value = {"incoming": {"protocol": "imap"}}

    result = edit_monitoring(config, ui, "default", value)

    assert result == {"enabled": True, "interval": "30M", "instructions": "Resuma somente mensagens que exigem ação.",
                      "response_channel": {"type": "telegram", "value": "-100:42"}, "thread_policy": "new"}
    ui.screen.assert_not_called()


def test_monitoring_setup_refuses_activation_without_incoming(tmp_path):
    config = Mock(root=tmp_path); ui = Mock(); ui.flow_choice.return_value = "Habilitada"
    assert edit_monitoring(config, ui, "default", {}) is None
    ui.result.assert_called_once()


def test_monitor_diagnostic_and_rebaseline_use_mcp_runtime(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.email.setup import run_monitor_admin
    from pathlib import Path
    config = Mock(root=tmp_path); ui = Mock(); ui.ensure_mcp_runtime.return_value = "Pronto"
    run = Mock(); run.return_value.returncode = 0
    monkeypatch.setattr("onmyoji_daemon.mcp.email.setup.mcp_runtime_python", lambda root: Path("private-python"))
    monkeypatch.setattr("onmyoji_daemon.mcp.email.setup.subprocess.run", run)
    run_monitor_admin(config, ui, "default", rebaseline=True)
    command = run.call_args.args[0]
    assert command[:4] == ["private-python", "-B", "-m", "onmyoji_daemon.mcp.email.monitor"]
    assert command[-1] == "--rebaseline"
