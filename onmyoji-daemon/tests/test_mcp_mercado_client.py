import json
import urllib.error

import pytest

from onmyoji_daemon.mcp.mercado.client import MercadoClient
from onmyoji_daemon.mcp.mercado.exception import MercadoError


class OAuth:
    defaults = {"timeout_seconds": 1, "max_retries": 2, "max_response_bytes": 1000}

    def __init__(self, *args): pass
    def access(self): return "access-token", "42"


class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, limit): return self.data


def test_http_preserves_decimals_as_text_and_token_in_header(monkeypatch, tmp_path):
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.client.MercadoOAuth", OAuth)
    captured = []
    def open_request(request, timeout):
        captured.append(request)
        return Response(b'{"amount":10.50,"offset":2}')
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    result = MercadoClient(tmp_path, "mercadopago", "pessoal").request("GET", "/v1/payments/1")
    assert result == {"amount": "10.50", "offset": 2}
    assert captured[0].headers["Authorization"] == "Bearer access-token"
    assert "access-token" not in captured[0].full_url


def test_caller_id_is_derived_from_authorized_user(monkeypatch, tmp_path):
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.client.MercadoOAuth", OAuth)
    captured = []
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout: captured.append(request) or Response(b"{}"))
    MercadoClient(tmp_path, "mercadopago", "pessoal").request("GET", "/v1/chargebacks/1", caller_id=True)
    assert captured[0].headers["X-caller-id"] == "42"


def test_http_maps_permission_error_without_body_or_url(monkeypatch, tmp_path):
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.client.MercadoOAuth", OAuth)
    error = urllib.error.HTTPError("https://api.mercadopago.com/path?secret=value", 403, "denied", {"X-Request-Id": "safe-id"}, None)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(MercadoError) as captured:
        MercadoClient(tmp_path, "mercadopago", "pessoal").request("GET", "/v1/payments/1")
    assert captured.value.as_dict() == {"code": "provider_permission_denied", "message": "A API Mercado recusou ou não concluiu a solicitação.", "httpStatus": 403, "requestId": "safe-id"}
    assert "secret" not in str(captured.value)


def test_get_retries_rate_limit_but_post_does_not(monkeypatch, tmp_path):
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.client.MercadoOAuth", OAuth)
    monkeypatch.setattr("onmyoji_daemon.mcp.mercado.client.time.sleep", lambda value: None)
    calls = []
    def throttled(*args, **kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise urllib.error.HTTPError("https://api.mercadopago.com", 429, "rate", {"Retry-After": "0"}, None)
        return Response(b"{}")
    monkeypatch.setattr("urllib.request.urlopen", throttled)
    assert MercadoClient(tmp_path, "mercadopago", "pessoal").request("GET", "/v1/payment_methods") == {}
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(MercadoError):
        MercadoClient(tmp_path, "mercadopago", "pessoal").request("POST", "/v1/report", body={})
    assert len(calls) == 1
