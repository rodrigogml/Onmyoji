"""Cobertura das regras visuais e de navegação do setup MCP."""
from __future__ import annotations

import importlib.util
import copy
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest


def mock_configuration():
    config = Mock()
    config.email.return_value = {"accounts": {}}
    return config


def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupMcp.py"
    specification = importlib.util.spec_from_file_location("setup_mcp_under_test", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_integration_summary_uses_inactive_for_no_profiles(monkeypatch):
    module = setup_module()
    monkeypatch.setattr(module.Ui, "enabled", False)

    assert module.integration_profile_summary({}) == "inativa"


def test_integration_summary_colors_states_and_limits_visible_names(monkeypatch):
    module = setup_module()
    monkeypatch.setattr(module.Ui, "enabled", True)
    profiles = {"ativo": {"enabled": True}, "inativo": {"enabled": False}, "outro": {"enabled": True}}

    full_summary = module.integration_profile_summary(profiles)
    monkeypatch.setattr(module, "menu_width", lambda: 56)
    limited_summary = module.integration_profile_summary(profiles)

    assert module.Ui.green in full_summary
    assert module.Ui.red in full_summary
    assert "... +" in limited_summary
    assert len("ativo" + " ... +2") <= 15


def test_integrations_menu_opens_btg_as_third_option(monkeypatch):
    module = setup_module()
    config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["3", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    btg_menu = Mock()
    monkeypatch.setattr(module, "btg_menu", btg_menu)
    module.integrations_menu(config)
    btg_menu.assert_called_once_with(config)


def test_integrations_menu_opens_forward_email_as_sixth_option(monkeypatch):
    module = setup_module()
    config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["6", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    forward_email_menu = Mock()
    monkeypatch.setattr(module, "forward_email_menu", forward_email_menu)

    module.integrations_menu(config)

    forward_email_menu.assert_called_once_with(config)


def test_forward_email_removal_is_case_insensitive_and_returns_to_profile_list(monkeypatch, capsys):
    module = setup_module()
    config = mock_configuration()
    data = {"profiles": {"mail": {"domain": "example.test"}}}
    config.forward_email.return_value = data
    answers = iter(["8", "ReMoVeR"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))

    module.manage_forward_email_profile(config, "mail")

    config.save_forward_email.assert_called_once_with({"profiles": {}})
    assert "Perfil Forward Email removido" in capsys.readouterr().out


def test_integrations_menu_opens_notion_as_seventh_option(monkeypatch):
    module = setup_module()
    config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["7", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    notion_menu = Mock()
    monkeypatch.setattr(module, "notion_menu", notion_menu)

    module.integrations_menu(config)

    notion_menu.assert_called_once_with(config)


def test_notion_removal_is_case_insensitive(monkeypatch, capsys):
    module = setup_module()
    config = mock_configuration()
    config.notion.return_value = {"profiles": {"workspace": {"readable_roots": []}}}
    answers = iter(["8", "ReMoVeR"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))

    module.manage_notion_profile(config, "workspace")

    config.save_notion.assert_called_once_with({"profiles": {}})
    assert "Perfil Notion removido" in capsys.readouterr().out


def test_integrations_menu_opens_aws_as_eighth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["8", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    aws_menu = Mock(); monkeypatch.setattr(module, "aws_menu", aws_menu)
    module.integrations_menu(config)
    aws_menu.assert_called_once_with(config)


def test_aws_removal_is_case_insensitive(monkeypatch, capsys):
    module = setup_module(); config = mock_configuration()
    profile = {"cli_path": "aws", "region": "sa-east-1", "expected_account_id": "123456789012", "readable_roots": [], "writable_roots": []}
    config.aws.return_value = {"profiles": {"prod": profile}}
    answers = iter(["12", "ReMoVeR"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.manage_aws_profile(config, "prod")
    config.save_aws.assert_called_once_with({"profiles": {}})
    assert "Perfil AWS removido" in capsys.readouterr().out


def test_integrations_menu_opens_mysql_as_ninth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["9", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    mysql_menu = Mock(); monkeypatch.setattr(module, "mysql_menu", mysql_menu)
    module.integrations_menu(config); mysql_menu.assert_called_once_with(config)


def test_integrations_menu_opens_ssh_as_tenth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["10", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    ssh_menu = Mock(); monkeypatch.setattr(module, "ssh_menu", ssh_menu)
    module.integrations_menu(config); ssh_menu.assert_called_once_with(config)


def test_integrations_menu_opens_bis2cmd_as_eleventh_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["11", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    bis2cmd_menu = Mock(); monkeypatch.setattr(module, "bis2cmd_menu", bis2cmd_menu)
    module.integrations_menu(config); bis2cmd_menu.assert_called_once_with(config)


def test_integrations_menu_opens_bis10cmd_as_twelfth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["12", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    bis10cmd_menu = Mock(); monkeypatch.setattr(module, "bis10cmd_menu", bis10cmd_menu)
    module.integrations_menu(config); bis10cmd_menu.assert_called_once_with(config)


def test_integrations_menu_opens_google_as_thirteenth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["13", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    google_menu = Mock(); monkeypatch.setattr(module, "google_menu", google_menu)
    module.integrations_menu(config); google_menu.assert_called_once_with(config)


def test_integrations_menu_opens_spotify_as_fourteenth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["14", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    spotify_menu = Mock(); monkeypatch.setattr(module, "spotify_menu", spotify_menu)
    module.integrations_menu(config); spotify_menu.assert_called_once_with(config)


def test_integrations_menu_opens_memory_as_fifteenth_option(monkeypatch):
    module = setup_module(); config = mock_configuration()
    for integration in ("todoist", "omie", "btg", "eccovox", "cloudflare", "forward_email", "notion", "aws", "mysql", "ssh", "bis2cmd", "bis10cmd", "google", "spotify"):
        getattr(config, integration).return_value = {"profiles": {}}
    config.memory_tool_available.return_value = False
    answers = iter(["15", "x"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    memory_menu = Mock(); monkeypatch.setattr(module, "memory_menu", memory_menu)
    module.integrations_menu(config); memory_menu.assert_called_once_with(config)


def test_spotify_removal_is_case_insensitive(monkeypatch, capsys):
    module = setup_module(); config = mock_configuration()
    profile = {"enabled": True, "description": "Música.", "credential_profile": "vault", "credential_entry_path": "APIs/Spotify/music", "client_id_field": "username", "refresh_token_field": "password", "callback_port": 8888}
    config.spotify.return_value = {"profiles": {"music": profile}}
    answers = iter(["9", "ReMoVeR"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.manage_spotify_profile(config, "music")
    config.save_spotify.assert_called_once_with({"profiles": {}})
    assert "Perfil Spotify removido" in capsys.readouterr().out


def test_ssh_removal_is_case_insensitive(monkeypatch, capsys):
    module = setup_module(); config = mock_configuration()
    profile = {"host": "server", "port": 22, "username": "user", "auth_mode": "password", "credential_profile": "vault", "credential_entry_path": "Servers/server", "password_field": "password", "key_attachment": "", "passphrase_field": "", "description": "Servidor.", "known_hosts": "known_hosts", "readable_roots": [], "writable_roots": []}
    config.ssh.return_value = {"profiles": {"server": profile}}
    answers = iter(["10", "ReMoVeR"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.manage_ssh_profile(config, "server")
    config.save_ssh.assert_called_once_with({"profiles": {}})
    assert "Perfil SSH removido" in capsys.readouterr().out


def test_bis2cmd_removal_is_case_insensitive(monkeypatch, capsys):
    module = setup_module(); config = mock_configuration()
    profile = {"enabled": True, "description": "BIS2 de teste.", "jar_path": "BISCMD.jar", "working_dir": "biscmd", "host": "localhost", "port": 8080, "java_options": "", "credential_profile": "vault", "credential_entry_path": "APIs/BIS2CMD/erp", "username_field": "username", "password_field": "password", "readable_roots": [], "writable_roots": []}
    config.bis2cmd.return_value = {"profiles": {"erp": profile}}
    answers = iter(["11", "ReMoVeR"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.manage_bis2cmd_profile(config, "erp")
    config.save_bis2cmd.assert_called_once_with({"profiles": {}})
    assert "Perfil BIS2CMD removido" in capsys.readouterr().out


def test_bis10cmd_removal_is_case_insensitive(monkeypatch, capsys):
    module = setup_module(); config = mock_configuration()
    profile = {"enabled": True, "description": "BIS10.", "jar_path": "BISCMD.jar", "working_dir": "BIS10CMD", "host": "localhost", "port": 8080, "locale": "pt-BR", "jndi_credential_profile": "jndi", "jndi_credential_entry_path": "JNDI", "jndi_username_field": "username", "jndi_password_field": "password", "bis_credential_profile": "bis", "bis_credential_entry_path": "BIS10", "bis_username_field": "username", "bis_password_field": "password"}
    config.bis10cmd.return_value = {"profiles": {"erp": profile}}
    answers = iter(["11", "ReMoVeR"]); monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.manage_bis10cmd_profile(config, "erp")
    config.save_bis10cmd.assert_called_once_with({"profiles": {}})
    assert "Perfil BIS10CMD removido" in capsys.readouterr().out


def test_btg_diagnosis_stays_in_profile_menu(monkeypatch, capsys):
    module = setup_module()
    config = mock_configuration()
    config.btg.return_value = {"profiles": {"empresa": {"environment": "sandbox", "company_id": "30306294000145"}}}
    answers = iter(["6", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    service = Mock()
    service.get_auth_status.return_value = {"authenticated": False, "pending": False}
    monkeypatch.setattr(module, "BtgService", lambda _root: service)
    module.manage_btg_profile(config, "empresa")
    service.get_auth_status.assert_called_once_with("empresa")
    assert capsys.readouterr().out.count("PERFIL BTG EMPRESAS") == 2
    config.save_btg.assert_not_called()


def test_btg_removal_is_case_insensitive_and_returns_to_profile_list(monkeypatch, capsys):
    module = setup_module()
    config = mock_configuration()
    data = {"profiles": {"empresa": {"environment": "sandbox", "company_id": "30306294000145"}}}
    config.btg.return_value = data
    answers = iter(["7", "ReMoVeR"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.manage_btg_profile(config, "empresa")
    config.save_btg.assert_called_once_with({"profiles": {}})
    assert "Perfil BTG removido" in capsys.readouterr().out


def test_btg_credentials_configure_two_fields_and_preserve_notes(monkeypatch):
    module = setup_module()
    configure = Mock(return_value=("Integrations/BTG/empresa", {}))
    monkeypatch.setattr(module, "configure_vault_entry_fields", configure)
    config = mock_configuration()
    module.btg_credentials(config, "vault", "Integrations/BTG/empresa")
    assert configure.call_args.args[-1] == [("client_id_field", "Client ID", "username"), ("client_secret_field", "Client Secret", "password")]


@pytest.mark.parametrize("environment", ["sandbox", "production"])
def test_btg_new_profile_scopes_default_keepass_entry_by_environment(monkeypatch, environment):
    module = setup_module()
    config = mock_configuration()
    data = {"profiles": {}}
    config.btg.return_value = data
    answers = iter(["1", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(module, "ask_new_profile_name", lambda *_args: "empresa")
    monkeypatch.setattr(module, "flow_choice", lambda *_args: environment)
    monkeypatch.setattr(module, "select", lambda *_args: "vault")
    entries = []
    def ask(label, current="", required=False):
        if label == "Entrada KeePass das credenciais":
            entries.append(current)
            return current
        if label == "CNPJ da empresa (14 dígitos)":
            return "30306294000145"
        if label == "Descrição de quando utilizar este perfil":
            return "Conta de teste"
        raise AssertionError(label)
    monkeypatch.setattr(module, "ask", ask)
    monkeypatch.setattr(module, "btg_credentials", lambda _config, _vault, entry: (entry, {}))

    module.btg_menu(config)

    assert entries == [f"Integrations/BTG/{environment}/empresa"]
    assert data["profiles"]["empresa"]["credential_entry_path"] == entries[0]


@pytest.mark.parametrize("action,confirmation,operation", [("9", "CoNeCtAr", "start_authorization"), ("10", None, "get_authorization_status"), ("11", "CaNcElAr", "cancel_authorization"), ("12", None, "get_account")])
def test_btg_oauth_and_bank_commands_remain_in_profile_menu(monkeypatch, capsys, action, confirmation, operation):
    module = setup_module()
    config = mock_configuration()
    config.btg.return_value = {"profiles": {"empresa": {"environment": "sandbox", "company_id": "30306294000145"}}}
    answers = iter([action, *([confirmation] if confirmation else []), "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    service = Mock()
    service.invoke.return_value = {"authorizationUrl": "https://auth.example.test/authorize", "status": "awaiting"}
    monkeypatch.setattr(module, "BtgService", lambda _root: service)
    runtime = Mock(return_value="Runtime pronto")
    monkeypatch.setattr(module, "ensure_mcp_runtime", runtime)
    module.manage_btg_profile(config, "empresa")
    assert service.invoke.call_args.args == ("empresa", operation, {"confirm": True} if confirmation else {})
    assert runtime.call_count == (1 if action in {"9", "12"} else 0)
    assert capsys.readouterr().out.count("PERFIL BTG EMPRESAS") == 2
    config.save_btg.assert_not_called()


def test_btg_runtime_failure_does_not_leave_profile_menu(monkeypatch, capsys):
    module = setup_module()
    config = mock_configuration()
    config.btg.return_value = {"profiles": {"empresa": {"environment": "sandbox", "company_id": "30306294000145"}}}
    answers = iter(["12", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(module, "ensure_mcp_runtime", Mock(side_effect=module.McpRuntimeError("Runtime indisponível")))
    service = Mock()
    monkeypatch.setattr(module, "BtgService", lambda _root: service)
    module.manage_btg_profile(config, "empresa")
    service.invoke.assert_not_called()
    output = capsys.readouterr().out
    assert "Runtime indisponível" in output and output.count("PERFIL BTG EMPRESAS") == 2


@pytest.mark.parametrize("action", ["4", "5"])
def test_btg_payment_queue_commands_remain_in_settings_menu(monkeypatch, capsys, action):
    module = setup_module()
    config = mock_configuration()
    config.btg.return_value = {"profiles": {"company": {"max_amount": "100.00", "monitor_enabled": False}}}
    answers = iter([action, "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    service = Mock()
    service.invoke.return_value = {"duePayments": 2, "hasMore": False, "checked": [], "busy": []}
    monkeypatch.setattr(module, "BtgService", lambda _root: service)
    monkeypatch.setattr(module, "ensure_mcp_runtime", lambda _root: "Runtime pronto")
    module.btg_payment_settings(config, "company")
    service.invoke.assert_called_once_with("company", "get_monitor_status" if action == "4" else "check_pending_payments", {})
    config.save_btg.assert_not_called()
    assert capsys.readouterr().out.count("PAGAMENTOS E MONITOR") == 2


def test_btg_payment_limit_and_monitor_settings_do_not_start_daemon(monkeypatch):
    module = setup_module()
    config = mock_configuration()
    data = {"profiles": {"company": {"max_amount": "100.00", "monitor_enabled": False}}}
    config.btg.side_effect = lambda: copy.deepcopy(data)
    def save(candidate):
        data.clear(); data.update(copy.deepcopy(candidate))
    config.save_btg.side_effect = save
    answers = iter(["1", "50.00", "2", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    monkeypatch.setattr(module, "flow_choice", lambda *_args: "Habilitado")
    runtime = Mock(return_value="Runtime pronto")
    monkeypatch.setattr(module, "ensure_mcp_runtime", runtime)
    run = Mock(side_effect=AssertionError("Não iniciar serviço automaticamente"))
    monkeypatch.setattr(module.subprocess, "run", run)
    module.btg_payment_settings(config, "company")
    assert data["profiles"]["company"] == {"max_amount": "50.00", "monitor_enabled": True}
    runtime.assert_called_once_with(config.root)
    run.assert_not_called()


def test_btg_partial_monitor_settings_are_not_saved_after_back(monkeypatch):
    module = setup_module()
    config = mock_configuration()
    config.btg.side_effect = lambda: {"profiles": {"company": {"max_amount": "100.00"}}}
    answers = iter(["3", "600", "x", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    module.btg_payment_settings(config, "company")
    config.save_btg.assert_not_called()
