from __future__ import annotations

import base64
import json
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.forward_email import ForwardEmailError, ForwardEmailService


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def configured_service(tmp_path, *, domain="example.test"):
    configuration = McpConfiguration(tmp_path)
    document = configuration.forward_email()
    document["profiles"]["mail"] = {"enabled": True, "description": "Correio principal.", "credential_profile": "vault", "credential_entry_path": "APIs/ForwardEmail/mail", "credential_field": "password", "domain": domain}
    configuration.save_forward_email(document)
    credentials = Mock()
    credentials.resolve.return_value = "private-token"
    return ForwardEmailService(tmp_path, credentials), credentials


def test_list_domains_uses_token_only_basic_auth_without_exposing_it(tmp_path, monkeypatch):
    service, credentials = configured_service(tmp_path)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response([{"id": "domain-1", "name": "example.test"}])

    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.service.urlopen", open_request)
    result = service.invoke("mail", "list_domains", {"query": {"limit": 1}})

    expected = base64.b64encode(b"private-token:").decode("ascii")
    assert result == {"data": [{"id": "domain-1", "name": "example.test"}]}
    assert requests[0][0].full_url.endswith("/domains?limit=1")
    assert requests[0][0].get_header("Authorization") == "Basic " + expected
    credentials.resolve.assert_called_once_with("vault", "APIs/ForwardEmail/mail", "password")
    assert "private-token" not in repr(result)


def test_create_alias_does_not_require_an_existing_alias_identifier(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    requests = []
    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.service.urlopen", lambda request, timeout: requests.append(request) or Response({"id": "alias-1", "name": "sales"}))

    result = service.invoke("mail", "create_alias", {"body": {"name": "sales", "recipients": ["owner@example.test"]}, "confirm": True})

    assert result == {"data": {"id": "alias-1", "name": "sales"}}
    assert requests[0].full_url.endswith("/domains/example.test/aliases")
    assert requests[0].method == "POST"


def test_alias_write_requires_confirmation_and_identifier(tmp_path):
    service, credentials = configured_service(tmp_path)

    with pytest.raises(ForwardEmailError, match="confirm=true"):
        service.invoke("mail", "delete_alias", {"alias": "sales"})
    with pytest.raises(ForwardEmailError, match="alias_id ou alias"):
        service.invoke("mail", "delete_alias", {"confirm": True})
    credentials.resolve.assert_not_called()


def test_generate_password_allows_an_empty_body_and_quotes_alias(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    requests = []
    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.service.urlopen", lambda request, timeout: requests.append(request) or Response({"username": "sales@example.test", "password": "generated"}))

    result = service.invoke("mail", "generate_alias_password", {"alias": "sales team", "confirm": True})

    assert result["data"]["password"] == "generated"
    assert requests[0].full_url.endswith("/domains/example.test/aliases/sales%20team/generate-password")
    assert requests[0].data is None


def test_domain_is_required_only_for_domain_scoped_operations(tmp_path, monkeypatch):
    service, credentials = configured_service(tmp_path, domain="")
    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.service.urlopen", lambda *_args, **_kwargs: Response([]))

    assert service.invoke("mail", "list_domains", {}) == {"data": []}
    with pytest.raises(ForwardEmailError, match="domain"):
        service.invoke("mail", "list_aliases", {})
    assert credentials.resolve.call_count == 1


def test_connection_requires_a_domain_list(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.service.urlopen", lambda *_args, **_kwargs: Response([]))

    assert service.test_connection("mail") == {"profile": "mail", "authenticated": True}


def test_non_official_api_endpoint_is_rejected_before_reading_credentials(tmp_path):
    service, credentials = configured_service(tmp_path)
    path = tmp_path / "configs" / "mcp" / "forward-email.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("https://api.forwardemail.net/v1", "https://example.test/v1"), encoding="utf-8")

    with pytest.raises(ForwardEmailError, match="Configuração de acesso"):
        service.invoke("mail", "list_domains", {})
    credentials.resolve.assert_not_called()


def test_invalid_response_is_sanitized(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)

    class InvalidResponse(Response):
        def read(self):
            return b"not-json private detail"

    monkeypatch.setattr("onmyoji_daemon.mcp.forward_email.service.urlopen", lambda *_args, **_kwargs: InvalidResponse(None))
    with pytest.raises(ForwardEmailError, match="resposta inválida") as raised:
        service.invoke("mail", "list_domains", {})
    assert "private detail" not in str(raised.value)
