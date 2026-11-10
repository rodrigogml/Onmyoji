from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.cloudflare import CloudflareError, CloudflareService
from onmyoji_daemon.mcp.configuration import McpConfiguration


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def configured_service(tmp_path, *, zone_id="zone-default"):
    configuration = McpConfiguration(tmp_path)
    document = configuration.cloudflare()
    document["profiles"]["dns"] = {"enabled": True, "description": "DNS público.", "credential_profile": "vault", "credential_entry_path": "APIs/Cloudflare/dns", "credential_field": "password", "zone_id": zone_id}
    configuration.save_cloudflare(document)
    credentials = Mock()
    credentials.resolve.return_value = "private-token"
    return CloudflareService(tmp_path, credentials), credentials


def test_list_zones_uses_bearer_token_and_query_without_exposing_it(tmp_path, monkeypatch):
    service, credentials = configured_service(tmp_path)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response({"success": True, "result": [{"id": "zone-1", "name": "example.test"}]})

    monkeypatch.setattr("onmyoji_daemon.mcp.cloudflare.service.urlopen", open_request)
    result = service.invoke("dns", "list_zones", {"query": {"name": "example.test"}})

    assert result == {"data": [{"id": "zone-1", "name": "example.test"}]}
    assert requests[0][0].full_url.endswith("/zones?name=example.test")
    assert requests[0][0].get_header("Authorization") == "Bearer private-token"
    credentials.resolve.assert_called_once_with("vault", "APIs/Cloudflare/dns", "password")
    assert "private-token" not in repr(result)


def test_dns_operations_use_profile_zone_and_require_write_confirmation(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    requests = []
    monkeypatch.setattr("onmyoji_daemon.mcp.cloudflare.service.urlopen", lambda request, timeout: requests.append(request) or Response({"success": True, "result": {"id": "record-1"}}))

    with pytest.raises(CloudflareError, match="confirm=true"):
        service.invoke("dns", "create_dns_record", {"body": {"type": "A", "name": "www", "content": "192.0.2.1"}})
    result = service.invoke("dns", "create_dns_record", {"body": {"type": "A", "name": "www", "content": "192.0.2.1"}, "confirm": True})

    assert result == {"data": {"id": "record-1"}}
    assert requests[0].full_url.endswith("/zones/zone-default/dns_records")
    assert requests[0].method == "POST"


def test_record_identifier_and_zone_are_validated_before_credentials(tmp_path):
    service, credentials = configured_service(tmp_path, zone_id="")

    with pytest.raises(CloudflareError, match="zone_id"):
        service.invoke("dns", "list_dns_records", {})
    with pytest.raises(CloudflareError, match="record_id"):
        service.invoke("dns", "get_dns_record", {"zone_id": "zone-1"})
    credentials.resolve.assert_not_called()


def test_connection_requires_a_zone_list(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.cloudflare.service.urlopen", lambda *_args, **_kwargs: Response({"success": True, "result": []}))

    assert service.test_connection("dns") == {"profile": "dns", "authenticated": True}


def test_cloudflare_failure_is_sanitized(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.cloudflare.service.urlopen", lambda *_args, **_kwargs: Response({"success": False, "errors": [{"message": "private upstream detail"}]}))

    with pytest.raises(CloudflareError, match="recusou") as raised:
        service.invoke("dns", "list_zones", {})
    assert "private upstream detail" not in str(raised.value)
