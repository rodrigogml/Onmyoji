"""Primeira etapa BTG: perfis MCP e diagnóstico local sem banco ou skill legada."""
from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.btg.configuration import DEFAULT_SCOPES
from onmyoji_daemon.mcp.btg.service import BtgError, BtgService
from onmyoji_daemon.mcp.btg.tools import BtgToolRegistry
from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError
from onmyoji_daemon.mcp.reconciliation import McpReconciliation, mcp_reconciliation_pending


def profile(**overrides):
    return {"enabled": True, "description": "Conta de teste", "environment": "sandbox", "company_id": "30306294000145", "credential_profile": "vault", "credential_entry_path": "Integrations/BTG/test", "redirect_uri": "https://test.invalid/callback", **overrides}


def configuration(root):
    config = McpConfiguration(root)
    credentials = config.credentials()
    credentials["vaults"]["vault"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/test.kdbx", "linux": "/test.kdbx"}}
    credentials["profiles"]["vault"] = {"vault": "vault", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "test"}, "linux": {"mode": "command", "command": ["false"]}}}
    config.save_credentials(credentials)
    data = config.btg()
    data["profiles"]["empresa"] = profile()
    config.save_btg(data)
    return config


def test_configuration_is_private_and_change_marks_reconciliation_pending(tmp_path):
    config = configuration(tmp_path)
    assert config.btg_path == tmp_path / "configs" / "mcp" / "btg-empresas.toml"
    assert config.btg()["profiles"]["empresa"]["scopes"] == DEFAULT_SCOPES
    assert config.enabled_btg_profiles() == [{"name": "empresa", "description": "Conta de teste"}]
    McpReconciliation(tmp_path).mark_synchronized()
    data = config.btg()
    data["profiles"]["empresa"]["enabled"] = False
    config.save_btg(data)
    assert mcp_reconciliation_pending(tmp_path) is True
    assert BtgToolRegistry(config).definitions() == []
    data["profiles"].clear()
    config.save_btg(data)
    assert config.btg()["profiles"] == {}


@pytest.mark.parametrize("overrides", [
    {"enabled": "true"}, {"description": ""}, {"description": "x" * 241},
    {"environment": "other"}, {"environment": []}, {"company_id": "123"}, {"company_id": 30306294000145},
    {"company_id": "３０３０６２９４０００１４５"}, {"credential_profile": ""},
    {"credential_entry_path": ""}, {"account_number": "A"}, {"branch_code": 1},
    {"account_id": "known-id"}, {"redirect_uri": "http://external.invalid/callback"},
    {"redirect_uri": "https://user:pass@external.invalid/callback"},
    {"redirect_uri": "https://external.invalid/callback#fragment"}, {"redirect_uri": "https://"},
    {"oauth_mode": "other"}, {"oauth_mode": []}, {"scopes": "openid"}, {"max_amount": "NaN"},
    {"max_amount": "-1"}, {"max_amount": "1.001"}, {"max_amount": 1.5}, {"monitor_enabled": 1},
    {"pending_interval": None}, {"pending_interval": True}, {"pending_interval": 0},
    {"scheduled_interval": "3600"}, {"confirmed_interval": 100001},
    {"confirmation_days": -1}, {"max_pages": 1.5}, {"max_pending_days": []},
    {"timeout_seconds": 61}, {"timeout_seconds": False},
])
def test_invalid_profiles_are_rejected_without_writing_file(tmp_path, overrides):
    config = McpConfiguration(tmp_path)
    data = config.btg()
    data["profiles"]["test"] = profile(**overrides)
    with pytest.raises(McpConfigurationError):
        config.save_btg(data)
    assert not config.btg_path.exists()


def test_missing_vault_hides_tools(tmp_path):
    config = configuration(tmp_path)
    data = config.credentials()
    data["profiles"].clear()
    config.save_credentials(data)
    assert BtgToolRegistry(config).definitions() == []


def auth_data(**overrides):
    binding = hashlib.sha256(json.dumps({"environment": "sandbox", "clientId": "private-id", "redirect": "https://test.invalid/callback"}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"tokens": {"binding": binding, "expiresAt": 2000, "access_token": "private-token", "refresh_token": "private-refresh", "operatorId": "private-operator"}, "pending": {"expiresAt": 1500}, **overrides}


class Credentials:
    def __init__(self, notes): self.notes, self.calls = notes, []
    def resolve(self, vault, entry, field):
        self.calls.append((vault, entry, field))
        return {"username": "private-id", "password": "private-secret", "notes": self.notes}[field]


def test_status_only_returns_booleans_and_context_without_tokens(tmp_path, monkeypatch):
    configuration(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.btg.service.time.time", lambda: 1000)
    credentials = Credentials(json.dumps(auth_data()))
    status = BtgService(tmp_path, credentials).get_auth_status("empresa")
    assert status["authenticated"] is True
    assert status["pending"] is True
    assert status["account_configured"] is False
    assert status["source"] == "vault-metadata"
    assert "private" not in json.dumps(status)
    assert [call[2] for call in credentials.calls] == ["username", "password", "notes"]


@pytest.mark.parametrize("tokens", [
    {}, {"binding": "wrong", "expiresAt": 2000, "access_token": "private"},
    {"binding": "wrong", "expiresAt": float("inf"), "access_token": "private"},
])
def test_missing_or_different_consent_does_not_authenticate(tmp_path, monkeypatch, tokens):
    configuration(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.btg.service.time.time", lambda: 1000)
    result = BtgService(tmp_path, Credentials(json.dumps(auth_data(tokens=tokens)))).get_auth_status("empresa")
    assert result["authenticated"] is False


@pytest.mark.parametrize("notes", ["private-invalid-json", "null", "[]", '{"tokens":null}', '{"pending":[]}'])
def test_invalid_oauth_notes_are_sanitized(tmp_path, notes):
    configuration(tmp_path)
    with pytest.raises(BtgError) as error:
        BtgService(tmp_path, Credentials(notes)).get_auth_status("empresa")
    assert error.value.code == "invalid_auth_state"
    assert "private" not in str(error.value)


def test_empty_notes_indicates_unconnected_profile_without_changing_vault(tmp_path):
    from onmyoji_daemon.mcp.credentials import CredentialError
    configuration(tmp_path)
    credentials = Credentials("{}")
    original = credentials.resolve
    def resolve(vault, entry, field):
        if field == "notes": raise CredentialError("secret_not_found", "Empty")
        return original(vault, entry, field)
    credentials.resolve = resolve
    assert BtgService(tmp_path, credentials).get_auth_status("empresa")["authenticated"] is False


def test_publication_has_context_and_single_profile_needs_no_argument(tmp_path, monkeypatch):
    config = configuration(tmp_path)
    registry = BtgToolRegistry(config)
    tool = registry.definitions()[0]
    assert tool["name"] == "btg.get_auth_status"
    assert tool["input_schema"]["properties"] == {}
    assert "Conta de teste" in tool["description"]
    call = Mock(return_value={"authenticated": False})
    monkeypatch.setattr(BtgService, "get_auth_status", call)
    assert registry.call("btg.get_auth_status", {}) == {"authenticated": False}
    call.assert_called_once_with("empresa")


def test_multiple_profiles_require_selection(tmp_path):
    config = configuration(tmp_path)
    data = config.btg()
    data["profiles"]["other"] = profile(description="Outra conta")
    config.save_btg(data)
    registry = BtgToolRegistry(config)
    assert registry.definitions()[0]["name"] == "btg.list_profiles"
    assert registry.definitions()[1]["input_schema"]["required"] == ["profile"]
    assert registry.call("btg.list_profiles", {})["profiles"] == [{"name": "empresa", "description": "Conta de teste"}, {"name": "other", "description": "Outra conta"}]
    with pytest.raises(ValueError, match="Perfil BTG indisponível"):
        registry.call("btg.get_auth_status", {})
