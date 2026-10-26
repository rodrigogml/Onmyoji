"""Cobertura das regras visuais e de navegação do setup MCP."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupMcp.py"
    specification = importlib.util.spec_from_file_location("setup_mcp_under_test", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
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
    config = Mock()
    for integration in ("todoist", "omie", "btg"):
        getattr(config, integration).return_value = {"profiles": {}}
    answers = iter(["3", "x"])
    monkeypatch.setattr(module, "prompt", lambda _label: next(answers))
    btg_menu = Mock()
    monkeypatch.setattr(module, "btg_menu", btg_menu)
    module.integrations_menu(config)
    btg_menu.assert_called_once_with(config)


def test_btg_diagnosis_stays_in_profile_menu(monkeypatch, capsys):
    module = setup_module()
    config = Mock()
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
    config = Mock()
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
    config = Mock()
    module.btg_credentials(config, "vault", "Integrations/BTG/empresa")
    assert configure.call_args.args[-1] == [("client_id_field", "Client ID", "username"), ("client_secret_field", "Client Secret", "password")]


@pytest.mark.parametrize("action,confirmation,operation", [("9", "CoNeCtAr", "start_authorization"), ("10", None, "get_authorization_status"), ("11", "CaNcElAr", "cancel_authorization"), ("12", None, "get_account")])
def test_btg_oauth_and_bank_commands_remain_in_profile_menu(monkeypatch, capsys, action, confirmation, operation):
    module = setup_module()
    config = Mock()
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
    config = Mock()
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
