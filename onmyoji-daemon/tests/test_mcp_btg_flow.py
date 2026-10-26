"""OAuth e consultas MCP BTG com banco/cofre simulados."""
from __future__ import annotations

import copy
from decimal import Decimal
import json
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.btg.client import Client, load_profile
from onmyoji_daemon.mcp.btg.contracts import BtgError
from onmyoji_daemon.mcp.btg.service import BtgService
from onmyoji_daemon.mcp.btg.tools import BtgToolRegistry, DESCRIPTIONS, READS
from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError


@pytest.fixture
def setup(tmp_path):
    root, workspace = tmp_path / "code", tmp_path / "work"
    (root / "configs").mkdir(parents=True); workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nproject_directory=' + json.dumps(str(workspace)) + '\n', encoding="utf-8")
    config = McpConfiguration(root)
    data = config.btg()
    data["profiles"]["company"] = {"enabled": True, "description": "Empresa", "environment": "sandbox", "company_id": "30306294000145", "credential_profile": "vault", "credential_entry_path": "BTG/test", "redirect_uri": "https://apps.example.com/oauth/mcp/btg/callback"}
    config.save_btg(data)
    return root, config


class Credentials:
    def __init__(self): self.data = {}
    def resolve(self, _vault, _entry, field):
        return {"username": "test-client-id", "password": "test-client-secret", "notes": json.dumps(self.data)}[field]
    def save_oauth_state(self, _vault, _entry, state): self.data = copy.deepcopy(state)


def connected_client(config):
    credentials = Credentials()
    client = Client(load_profile(config.btg_path, "company"), credentials)
    client.token_request = Mock(return_value={"binding": client.binding(), "expiresAt": 9999999999, "access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "operatorId": "test-operator"})
    client.call = Mock(return_value={"data": [{"accountId": "30306294000145-208-50-001", "bankCode": "208", "taxId": "30306294000145", "number": "001", "branchCode": "50"}]})
    return client, credentials


def test_callback_discovers_and_saves_account_without_changing_profile_identity(setup):
    _root, config = setup
    before = config.btg()
    client, credentials = connected_client(config)
    client.auth_start(state="test-state", session_id="test-session")
    assert client.auth_complete("test-code", "test-state", session_id="test-session", verify_account=True)["authenticated"]
    profile = config.btg()["profiles"]["company"]
    assert profile["account_id"] == "30306294000145-208-50-001"
    assert profile["account_number"] == "001" and profile["branch_code"] == "50"
    assert credentials.data["tokens"]["access_token"] == "synthetic-access"
    assert "pending" not in credentials.data and "provisionalTokens" not in credentials.data
    assert "synthetic" not in config.btg_path.read_text(encoding="utf-8")
    # Um menu aberto antes do callback não pode apagar a conta descoberta.
    before["profiles"]["company"]["description"] = "Descrição atualizada"
    config.save_btg(before)
    assert config.btg()["profiles"]["company"]["account_id"] == profile["account_id"]


def test_callback_refuses_profile_changed_after_authorization_started(setup):
    _root, config = setup
    client, credentials = connected_client(config)
    client.auth_start(state="test-state", session_id="test-session")
    data = config.btg()
    data["profiles"]["company"]["description"] = "Mudou durante a autorização"
    config.save_btg(data)
    with pytest.raises(BtgError) as error:
        client.auth_complete("test-code", "test-state", session_id="test-session", verify_account=True)
    assert error.value.code == "config_changed"
    assert "tokens" not in credentials.data
    assert not config.btg()["profiles"]["company"]["account_id"]


@pytest.mark.parametrize("key,value", [("company_id", "12345678000199"), ("environment", "production"), ("account_id", "other-account")])
def test_discovered_account_cannot_be_silently_reassigned(setup, key, value):
    _root, config = setup
    client, _credentials = connected_client(config)
    client.persist_account(client.call.return_value["data"][0])
    candidate = config.btg()
    candidate["profiles"]["company"][key] = value
    with pytest.raises(McpConfigurationError):
        config.save_btg(candidate)


@pytest.mark.parametrize("operation", READS)
def test_read_operations_route_only_to_registered_bank_contracts(tmp_path, operation):
    client = Mock()
    client.profile = {"account_id": "account"}
    client.call.return_value = {"data": [{"amount": Decimal("0.01")}], "_links": {}}
    client.verify_account.return_value = {"accountId": "account"}
    service = BtgService(tmp_path)
    service.client = lambda _name: client
    params = {"startDate": "2026-10-01", "endDate": "2026-10-02", "type": "simple"} if operation == "list_statements" else {"codes": "001234"} if operation == "get_barcode" else {}
    result = service.invoke("company", operation, {"params": params})
    assert result["source"] == "bank" and result["observedAt"]
    if operation == "get_account":
        client.verify_account.assert_called_once()
    else:
        client.call.assert_called_once_with(READS[operation], params=params)
        assert result["response"]["data"][0]["amount"] == "0.01"


@pytest.mark.parametrize("operation", ["start_authorization", "cancel_authorization", "prepare_payment", "submit_payment", "cancel_payment"])
@pytest.mark.parametrize("confirm", [None, False, "true", 1])
def test_oauth_mutations_require_boolean_confirmation_before_accessing_profile(tmp_path, operation, confirm):
    service = BtgService(tmp_path)
    service.client = Mock(side_effect=AssertionError("Não deve iniciar cliente"))
    with pytest.raises(BtgError) as error:
        service.invoke("company", operation, {"confirm": confirm})
    assert error.value.code == "confirmation_required"


def test_oauth_start_status_cancel_use_remote_broker_without_manual_codes(tmp_path):
    client = Mock()
    client.profile = {"oauth_mode": "remote", "redirect_uri": "https://apps.example.com/oauth/mcp/btg/callback"}
    client.remote_oauth.return_value = {"status": "awaiting"}
    service = BtgService(tmp_path)
    service.client = lambda _name: client
    service.invoke("company", "start_authorization", {"confirm": True})
    service.invoke("company", "get_authorization_status", {"session_id": "session"})
    service.invoke("company", "cancel_authorization", {"session_id": "session", "confirm": True})
    assert [call.args[0] for call in client.remote_oauth.call_args_list] == ["start", "status", "cancel"]


def test_btg_tools_publish_complete_schemas_for_oauth_and_six_read_operations():
    config = Mock()
    config.enabled_btg_profiles.return_value = [{"name": "company", "description": "Conta principal"}]
    registry = BtgToolRegistry(config)
    tools = registry.definitions()
    assert len(tools) == len(DESCRIPTIONS) == 21
    assert not any("payment_create" in tool["name"] for tool in tools)
    for operation in ("prepare_payment", "submit_payment", "cancel_payment"):
        definition = next(tool for tool in tools if tool["name"] == "btg." + operation)
        assert "confirm" in definition["input_schema"]["required"]
    for tool in tools:
        Draft202012Validator.check_schema(tool["input_schema"])
        assert "Conta principal" in tool["description"]
        assert "profile" not in tool["input_schema"]["properties"]
    statements = next(tool for tool in tools if tool["name"] == "btg.list_statements")
    assert statements["input_schema"]["properties"]["params"]["required"] == ["startDate", "endDate", "type"]
