"""Contrato BTG do OAuth remoto, sem credenciais reais ou chamadas bancárias."""
from __future__ import annotations
import copy
import json
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
sys.path.insert(0, str(Path(__file__).parent))
import oauth_adapter
from btg import Engine
from client import Client
from contracts import BtgError


@pytest.fixture
def profile(tmp_path):
    return {"environment": "sandbox", "company_id": "30306294000145", "account_id": "account", "account_number": "001", "branch_code": "50", "workspace": str(tmp_path / "work"), "vault_profile": "vault", "vault_entry_path": "BTG/test", "client_id": "app", "redirect_uri": "https://apps.example.com/oauth/skills/btg-empresas/callback", "scopes": "openid brn:btg:empresas:banking:accounts brn:btg:empresas:banking:payments", "max_amount": "1000.00", "timeout_seconds": 1, "max_pages": 2, "pending_interval": 300, "scheduled_interval": 3600, "confirmed_interval": 86400, "confirmation_days": 7, "oauth_mode": "remote", "_config": str(tmp_path / "code" / "configs" / "btg-empresas.toml"), "_profile": "test"}


def client_with_vault(profile):
    client = Client(profile)
    saved = {}
    client.auth_data = lambda: copy.deepcopy(saved)
    def save(data):
        saved.clear(); saved.update(copy.deepcopy(data))
    client.save_auth = save
    client.token_request = lambda *a, **kw: {"binding": client.binding(), "expiresAt": time.time() + 1000, "access_token": "private-access-token", "refresh_token": "private-refresh-token", "operatorId": "operator"}
    return client, saved


def test_remote_session_requires_matching_context_and_preserves_old_tokens(profile):
    client, saved = client_with_vault(profile)
    start = client.auth_start(state="broker-generated-state", session_id="session")
    assert "broker-generated-state" in start["authorizationUrl"]
    with pytest.raises(BtgError):
        client.auth_start()
    with pytest.raises(BtgError):
        client.auth_complete("code", "broker-generated-state", session_id="different")
    client.verify_account = Mock(side_effect=BtgError("context_mismatch", "Wrong account"))
    with pytest.raises(BtgError):
        client.auth_complete("code", "broker-generated-state", session_id="session", verify_account=True)
    assert "tokens" not in saved
    client.verify_account = Mock(return_value={"accountId": "account"})
    assert client.auth_complete("code", "broker-generated-state", session_id="session", verify_account=True)["authenticated"]
    assert client.verify_account.call_args.kwargs["token_override"]["access_token"] == "private-access-token"
    assert "pending" not in saved
    saved["lastAuthorization"]["access_token"] = "private-secret"
    assert "private-" not in json.dumps(client.auth_status())


def test_cancel_only_affects_its_pending_session(profile):
    client, saved = client_with_vault(profile)
    saved["tokens"] = {"access_token": "existing-token"}
    client.auth_start(state="state", session_id="session")
    with pytest.raises(BtgError):
        client.auth_cancel("other")
    assert client.auth_cancel("session")["cancelled"]
    assert saved["tokens"]["access_token"] == "existing-token" and "pending" not in saved


def test_btg_adapter_uses_server_exchange_and_account_verification(profile, monkeypatch):
    root = Path(profile["_config"]).parent.parent
    monkeypatch.setattr(oauth_adapter, "load_profile", lambda *a: profile)
    client = Client(profile)
    client.auth_start = Mock(return_value={"authorizationUrl": "https://id.sandbox.btgpactual.com/oauth2/authorize", "expiresIn": 600})
    client.auth_complete = Mock(return_value={"authenticated": True})
    client.auth_cancel = Mock(return_value={"cancelled": True})
    monkeypatch.setattr(oauth_adapter, "Client", lambda *a: client)
    describe = oauth_adapter.dispatch(root, {"version": 1, "action": "describe", "profile": "test"})
    assert describe["requireIssuer"] and describe["issuer"] == "https://id.sandbox.btgpactual.com"
    request = {"version": 1, "action": "start", "profile": "test", "state": "state", "sessionId": "session", "redirectUri": profile["redirect_uri"]}
    oauth_adapter.dispatch(root, request)
    assert client.auth_start.call_args.kwargs == {"state": "state", "session_id": "session"}
    oauth_adapter.dispatch(root, dict(request, action="complete", code="code"))
    assert client.auth_complete.call_args.kwargs == {"session_id": "session", "verify_account": True}
    assert oauth_adapter.dispatch(root, {"version": 1, "action": "cancel", "profile": "test", "sessionId": "session"})["cancelled"]
    with pytest.raises(BtgError):
        oauth_adapter.dispatch(root, dict(request, redirectUri="https://attacker.example"))


def test_btg_routes_remote_start_status_cancel_and_rejects_manual_completion(profile):
    client = Client(profile)
    calls = []
    client.remote_oauth = lambda action, **params: calls.append((action, params)) or {"status": "awaiting"}
    engine = Engine(profile, client)
    assert engine.dispatch({"version": 1, "operation": "auth.start", "confirm": True})["status"] == "awaiting"
    engine.dispatch({"version": 1, "operation": "auth.callback-info"})
    engine.dispatch({"version": 1, "operation": "auth.status", "params": {"session_id": "session"}})
    engine.dispatch({"version": 1, "operation": "auth.cancel", "params": {"session_id": "session"}, "confirm": True})
    assert [action for action, _ in calls] == ["start", "info", "status", "cancel"]
    with pytest.raises(BtgError):
        engine.dispatch({"version": 1, "operation": "auth.complete", "confirm": True, "code": "code", "state": "state"})
