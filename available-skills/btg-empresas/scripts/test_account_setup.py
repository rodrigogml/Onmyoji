"""Descoberta da conta no setup, com respostas simuladas e TOML descartável."""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from client import Client, load_profile
from account_setup import save_configuration
from btg import Engine
from contracts import BtgError
from setup_profile_api import simple_load, simple_save, handle, interactive_configure, Field
import setup_profile_api

SPEC = importlib.util.spec_from_file_location("btg_setup", Path(__file__).parents[1] / "setupSkill.py")
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


@pytest.fixture
def configured(tmp_path):
    path = tmp_path / "code" / "configs" / "btg-empresas.toml"
    path.parent.mkdir(parents=True)
    (tmp_path / "work").mkdir()
    (path.parent / "onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(str(tmp_path / "work")), encoding="utf-8")
    value = {"environment": "sandbox", "company_id": "30306294000145", "vault_profile": "vault", "vault_entry_path": "BTG/test", "client_id": "app", "redirect_uri": "https://apps.example.com/oauth/skills/btg-empresas/callback", "scopes": "openid empresas.btgpactual.com/accounts.readonly brn:btg:empresas:banking:payments", "oauth_mode": "remote"}
    data = {"schema_version": 1, "defaults": {"timeout_seconds": 30}, "profiles": {"test": value}}
    assert setup.save(path, data)[0]
    return path, load_profile(path, "test")


def account(**changes):
    return {"accountId": "30306294000145-208-50-000123", "taxId": "30306294000145", "number": "000123", "branchCode": 50, "bankCode": 208, **changes}


def authenticated_client(profile, response):
    client, vault = Client(profile), {}
    client.auth_data = lambda: copy.deepcopy(vault)
    def save(value):
        vault.clear(); vault.update(copy.deepcopy(value))
    client.save_auth = save
    client.token_request = Mock(return_value={"access_token": "private-token", "refresh_token": "private-refresh", "operatorId": "operator", "binding": client.binding()})
    client.http = Mock(return_value=(200, response))
    client.auth_start(state="state", session_id="session")
    return client, vault


def test_profile_creation_does_not_require_account_id_or_number(configured):
    path, profile = configured
    assert profile["account_id"] == profile["account_number"] == profile["branch_code"] == ""
    assert "account_id" not in {f.name for f in setup.FIELDS}
    assert "workspace" not in {f.name for f in setup.FIELDS}
    assert "workspace" not in simple_load(path, {})["profiles"]["test"]
    data = simple_load(path, {})
    values = [key + "=" + json.dumps(value) for key, value in data["profiles"]["test"].items()]
    code, response = handle(action="profile-create", profile_name="other", values=values, confirm_delete=None, path=path, load=setup.load, save=setup.save, fields=setup.FIELDS)
    assert code == 0 and response["ok"]
    assert not load_profile(path, "other")["account_id"]
    code, response = handle(action="profile-update", profile_name="other", values=["company_id=30306294000145", "branch_code=50", "max_amount=10.00"], confirm_delete=None, path=path, load=setup.load, save=setup.save, fields=setup.FIELDS)
    assert code == 0 and response["ok"]
    assert load_profile(path, "other")["branch_code"] == "50"


@pytest.mark.parametrize("central", [None, "broken [", "[codex]", '[codex]\nproject_directory = ""', '[codex]\nproject_directory = 5', '[codex]\nproject_directory = "relative/path"'])
def test_missing_or_invalid_central_workspace_requires_main_setup(configured, central):
    path, _ = configured
    system = path.parent / "onmyoji-system.toml"
    if central is None:
        system.unlink()
    else:
        system.write_text(central, encoding="utf-8")
    with pytest.raises(BtgError) as error:
        load_profile(path, "test")
    assert error.value.code == "invalid_workspace"
    assert "setup principal" in str(error.value)


@pytest.mark.parametrize("inside_root", [True, False])
def test_central_workspace_must_be_external_and_exist(configured, tmp_path, inside_root):
    path, _ = configured
    workspace = path.parent if inside_root else tmp_path / "missing"
    (path.parent / "onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(str(workspace)), encoding="utf-8")
    with pytest.raises(BtgError) as error:
        load_profile(path, "test")
    assert error.value.code == "invalid_workspace"


def test_legacy_workspace_must_match_central_without_moving_history(configured, tmp_path):
    path, profile = configured
    data = simple_load(path, {})
    data["profiles"]["test"]["workspace"] = profile["workspace"]
    simple_save(path, data)
    assert load_profile(path, "test")["workspace"] == profile["workspace"]
    history = Path(profile["workspace"]) / ".onmyoji" / "btg-empresas" / "payments.sqlite3"
    history.parent.mkdir(parents=True, exist_ok=True)
    history.write_bytes(b"existing-history")
    other = tmp_path / "other-work"
    other.mkdir()
    (path.parent / "onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(str(other)), encoding="utf-8")
    with pytest.raises(BtgError) as error:
        load_profile(path, "test")
    assert error.value.code == "invalid_workspace" and "histórico" in str(error.value)
    assert history.read_bytes() == b"existing-history"
    assert not (other / ".onmyoji").exists()


def test_callback_discovers_and_persists_account_before_reporting_success(configured):
    path, profile = configured
    data = simple_load(path, {})
    data["profiles"]["untouched"] = dict(data["profiles"]["test"], client_id="other-app")
    simple_save(path, data)
    client, vault = authenticated_client(profile, {"data": [account()]})
    assert client.auth_complete("code", "state", session_id="session", verify_account=True)["authenticated"]
    updated = load_profile(path, "test")
    assert updated["account_id"] == account()["accountId"]
    assert updated["account_number"] == "000123" and updated["branch_code"] == "50"
    assert vault["tokens"]["access_token"] == "private-token" and "pending" not in vault
    assert client.http.call_args.args[0].endswith("/30306294000145/banking/accounts")
    assert "private-" not in path.read_text(encoding="utf-8")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert simple_load(path, {})["profiles"]["untouched"] == data["profiles"]["untouched"]


@pytest.mark.parametrize("response,code", [
    ({}, "invalid_bank_response"), ({"data": []}, "account_not_found"),
    ({"data": [account(taxId="11111111000111")]}, "account_not_found"),
    ({"data": [account(bankCode=999)]}, "account_not_found"),
    ({"data": [account(), account(accountId="other", number="000124")]}, "account_ambiguous"),
    ({"data": [account(accountId=None)]}, "invalid_request"),
    ({"data": [account(number=None)]}, "invalid_bank_response"),
])
def test_discovery_failure_preserves_profile_and_existing_tokens(configured, response, code):
    path, profile = configured
    before = path.read_bytes()
    client, vault = authenticated_client(profile, response)
    vault["tokens"] = {"access_token": "existing-token"}
    with pytest.raises(BtgError) as error:
        client.auth_complete("code", "state", session_id="session", verify_account=True)
    assert error.value.code == code
    assert path.read_bytes() == before and vault["tokens"]["access_token"] == "existing-token"


def test_filters_select_only_the_requested_account(configured):
    _, profile = configured
    profile["account_number"] = "000124"
    client = Client(profile)
    client.call = Mock(return_value={"data": [account(), account(accountId="other", number="000124")]})
    assert client.discover_account()["accountId"] == "other"
    profile["branch_code"] = "1"
    with pytest.raises(BtgError) as error:
        client.discover_account()
    assert error.value.code == "account_not_found"


def test_profile_edit_during_oauth_is_not_overwritten(configured):
    path, profile = configured
    client, vault = authenticated_client(profile, {"data": [account()]})
    edited = simple_load(path, {})
    edited["profiles"]["test"]["client_id"] = "changed-app"
    simple_save(path, edited)
    before = path.read_bytes()
    with pytest.raises(BtgError) as error:
        client.auth_complete("code", "state", session_id="session", verify_account=True)
    assert error.value.code == "config_changed"
    assert path.read_bytes() == before and "tokens" not in vault


def test_config_write_failure_does_not_save_tokens(configured, monkeypatch):
    path, profile = configured
    client, vault = authenticated_client(profile, {"data": [account()]})
    before = path.read_bytes()
    monkeypatch.setattr(Path, "replace", Mock(side_effect=OSError("write failed")))
    with pytest.raises(OSError):
        client.auth_complete("code", "state", session_id="session", verify_account=True)
    assert path.read_bytes() == before and "tokens" not in vault
    assert not list(path.parent.glob("*.tmp"))


def test_stale_editor_preserves_discovered_account(configured):
    path, profile = configured
    stale = simple_load(path, {})
    Client(profile).persist_account(account())
    stale["profiles"]["test"]["max_amount"] = "5.00"
    assert save_configuration(path, stale)[0]
    assert load_profile(path, "test")["account_id"] == account()["accountId"]
    stale["profiles"]["test"].pop("account_id")
    stale["profiles"]["test"]["company_id"] = "11111111000111"
    assert not setup.save(path, stale)[0]


def test_pending_profile_blocks_operations_without_creating_payment_memory(configured):
    _, profile = configured
    engine = Engine(profile)
    with pytest.raises(BtgError) as error:
        engine.dispatch({"version": 1, "operation": "payments.prepare", "confirm": True, "body": {}})
    assert error.value.code == "account_setup_required"
    with pytest.raises(BtgError):
        Client(profile).call("balances.get")
    assert not (Path(profile["workspace"]) / ".onmyoji" / "btg-empresas" / "payments.sqlite3").exists()


def test_setup_waits_for_callback_and_reads_saved_id(configured, monkeypatch, capsys):
    path, profile = configured
    client = Client(profile)
    def start(*a, **kw):
        assert kw["confirm"] is True
        return {"authorizationUrl": "https://bank.example/authorize", "sessionId": "session", "expiresIn": 600}
    def status(*a, **kw):
        client.persist_account(account())
        return {"status": "completed"}
    monkeypatch.setattr(setup, "prompt", lambda *a: "s")
    monkeypatch.setattr(setup, "connect", start)
    monkeypatch.setattr(Client, "remote_oauth", status)
    messages = []
    monkeypatch.setattr(setup, "result", lambda *a: messages.append(a))
    setup.after_save("test", path)
    assert messages[-1][0] and account()["accountId"] in messages[-1][1]
    assert "https://bank.example/authorize" in capsys.readouterr().out


@pytest.mark.parametrize("success", [True, False])
def test_shared_menu_calls_connection_hook_only_after_successful_save(tmp_path, monkeypatch, success):
    answers = iter(["1", "test", "30306294000145", "x"])
    monkeypatch.setattr(setup_profile_api, "prompt", lambda *a: next(answers))
    monkeypatch.setattr(setup_profile_api, "result", lambda *a: None)
    monkeypatch.setattr(setup_profile_api, "screen", lambda *a: None)
    monkeypatch.setattr(setup_profile_api, "item", lambda *a: None)
    save, connected = Mock(return_value=(success, "saved")), Mock()
    path = tmp_path / "config.toml"
    interactive_configure(root=tmp_path, path=path, title="test", subtitle="test", integration="test", defaults={}, fields=(Field("field", "CNPJ", preserve_text=True),), save=save, after_save=connected)
    if success:
        connected.assert_called_once_with("test", path)
    else:
        connected.assert_not_called()
    assert save.call_args.args[1]["profiles"]["test"] == {"field": "30306294000145"}
