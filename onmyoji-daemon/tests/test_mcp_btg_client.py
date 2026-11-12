"""Regressões do transporte BTG portadas para o cliente interno MCP."""
import copy
import json
import time
from decimal import Decimal
from unittest.mock import Mock

import pytest
from onmyoji_daemon.mcp.btg.client import Client, NoRedirect
from onmyoji_daemon.mcp.btg.contracts import BtgError


@pytest.fixture
def profile(tmp_path):
    return {"environment": "sandbox", "company_id": "30306294000145", "account_id": "account", "account_number": "001", "branch_code": "50", "workspace": str(tmp_path / "work"), "credential_profile": "vault", "credential_entry_path": "BTG/test", "vault_profile": "vault", "vault_entry_path": "BTG/test", "client_id": "app", "redirect_uri": "https://apps.example.com/oauth/mcp/btg/callback", "scopes": "openid empresas.btgpactual.com/accounts.readonly brn:btg:empresas:banking:payments", "timeout_seconds": 1, "oauth_mode": "remote", "_config": str(tmp_path / "code" / "configs" / "mcp" / "btg-empresas.toml"), "_profile": "test"}


def test_oauth_state_tokens_and_operator_rotation(profile):
    client = Client(profile)
    saved = {}
    client.auth_data = lambda: copy.deepcopy(saved)
    client.save_auth = lambda value: (saved.clear(), saved.update(copy.deepcopy(value)))
    start = client.auth_start()
    assert "prompt=login" in start["authorizationUrl"]
    assert "resource=" not in start["authorizationUrl"]
    assert "code_challenge" not in start["authorizationUrl"]
    assert "verifier" not in saved["pending"]
    with pytest.raises(BtgError):
        client.auth_complete("code", "wrong-state")
    state = saved["pending"]["state"]
    client.token_request = lambda *a, **kw: {"binding": client.binding(), "expiresAt": time.time() + 1000, "access_token": "secret", "refresh_token": "refresh", "operatorId": "operator"}
    assert client.auth_complete("code", state)["authenticated"]
    assert "pending" not in saved and client.token()["operatorId"] == "operator"
    saved["tokens"]["binding"] = "production-other"
    with pytest.raises(BtgError):
        client.token()


def test_closed_client_routes_block_redirects_and_arbitrary_queries(profile):
    client = Client(profile)
    client.token = lambda: {"access_token": "secret", "operatorId": "operator"}
    client.http = Mock(return_value=(200, {"data": []}))
    client.call("payments.remote-list", params={"pageNumber": 1, "pageSize": 100})
    url = client.http.call_args.args[0]
    assert url.startswith("https://api.sandbox.empresas.btgpactual.com/")
    with pytest.raises(BtgError):
        client.call("unknown")
    with pytest.raises(BtgError):
        client.call("balances.get", params={"url": "evil"})
    with pytest.raises(BtgError):
        client.call("payments.remote-list", params={"pageNumber": 0, "pageSize": 100})
    with pytest.raises(BtgError):
        client.call("payments.create", expected_operator="other")
    with pytest.raises(BtgError):
        NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil")


def test_actual_client_token_exchange_refresh_and_account_binding(profile):
    import base64
    client = Client(profile)
    client.vault = lambda *a, **kw: "client-secret"
    sub = base64.urlsafe_b64encode(json.dumps({"sub": "operator"}).encode()).decode().rstrip("=")
    client.http = Mock(return_value=(200, {"access_token": "access", "refresh_token": "refresh", "id_token": "header." + sub + ".signature", "expires_in": 86400}))
    token = client.token_request({"grant_type": "authorization_code", "code": "test-code"})
    assert token["operatorId"] == "operator" and token["binding"] == client.binding()
    assert client.http.call_args.args[0] == "https://id.sandbox.btgpactual.com/oauth2/token"
    with pytest.raises(BtgError):
        client.token_request({}, dict(token, operatorId="other"))
    client.http.return_value = (200, {"access_token": "access", "expires_in": 1000})
    assert client.token_request({}, token)["refresh_token"] == "refresh"
    client.http.return_value = (200, {})
    with pytest.raises(BtgError):
        client.token_request({})
    client.http.return_value = (200, {"access_token": "access", "refresh_token": "refresh", "id_token": "invalid", "expires_in": 100})
    with pytest.raises(BtgError):
        client.token_request({})
    saved = {"tokens": dict(token, expiresAt=0)}
    client.auth_data = lambda: copy.deepcopy(saved)
    client.save_auth = lambda v: saved.update(v)
    client.token_request = lambda *a, **kw: dict(token, expiresAt=time.time() + 1000)
    assert client.token()["expiresAt"] > time.time()
    client.call = lambda *a, **kw: {"data": [{"accountId": profile["account_id"], "taxId": profile["company_id"], "number": profile["account_number"], "branchCode": "50", "bankCode": 208}]}
    assert client.verify_account()["bankCode"] == 208
    client.call = lambda *a, **kw: {"data": []}
    with pytest.raises(BtgError):
        client.verify_account()


def test_http_handles_pdf_json_errors_timeouts_and_size(profile):
    import io
    import urllib.error
    from contextlib import contextmanager
    client = Client(profile)
    @contextmanager
    def response(raw, status=200):
        stream = io.BytesIO(raw)
        stream.status = status
        yield stream
    client.opener.open = lambda *a, **kw: response(b'{"amount":0.01}')
    assert client.http(client.base, "GET", None, {})[1]["amount"] == Decimal("0.01")
    client.opener.open = lambda *a, **kw: response(b'%PDF-test')
    assert client.http(client.base, "GET", None, {"Accept": "application/pdf"})[1].startswith(b"%PDF")
    client.opener.open = lambda *a, **kw: response(b'')
    assert client.http(client.base, "DELETE", None, {}) == (200, None)
    for raw in [b'not JSON', b'x' * (8 * 1024 * 1024 + 1)]:
        client.opener.open = lambda *a, **kw: response(raw)
        with pytest.raises(BtgError):
            client.http(client.base, "GET", None, {})
    for error in [urllib.error.HTTPError(client.base, 403, "secret-error", {}, None), urllib.error.URLError("secret-detail"), TimeoutError()]:
        client.opener.open = Mock(side_effect=error)
        with pytest.raises(BtgError) as caught:
            client.http(client.base, "GET", None, {})
        assert "secret" not in str(caught.value)
    provider_error = urllib.error.HTTPError(
        client.base,
        404,
        "secret-error",
        {},
        io.BytesIO(b'{"name":"TokenIssueError","message":"[A050305] secret detail"}'),
    )
    client.opener.open = Mock(side_effect=provider_error)
    with pytest.raises(BtgError) as caught:
        client.http(client.base, "GET", None, {})
    assert caught.value.code == "bank_http_error_404_tokenissueerror_a050305"
    assert "secret" not in str(caught.value)
    client.deadline = time.monotonic() - 1
    with pytest.raises(BtgError):
        client.http(client.base, "GET", None, {})
