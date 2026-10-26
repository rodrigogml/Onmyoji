from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.notion import NotionError, NotionService


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def configured_service(tmp_path, readable_roots=None):
    configuration = McpConfiguration(tmp_path)
    document = configuration.notion()
    document["profiles"]["workspace"] = {"enabled": True, "description": "Notion principal.", "credential_profile": "vault", "credential_entry_path": "APIs/Notion/workspace", "credential_field": "password", "readable_roots": readable_roots or []}
    configuration.save_notion(document)
    credentials = Mock()
    credentials.resolve.return_value = "private-token"
    return NotionService(tmp_path, credentials), credentials


def test_get_page_uses_official_host_version_and_bearer_token(tmp_path, monkeypatch):
    service, credentials = configured_service(tmp_path)
    requests = []
    monkeypatch.setattr("onmyoji_daemon.mcp.notion.service.urlopen", lambda request, timeout: requests.append(request) or Response({"object": "page", "id": "page-id"}))

    result = service.invoke("workspace", "pages.get", {"params": {"page_id": "page/id"}})

    assert result == {"data": {"object": "page", "id": "page-id"}}
    assert requests[0].full_url == "https://api.notion.com/v1/pages/page%2Fid"
    assert requests[0].get_header("Authorization") == "Bearer private-token"
    assert requests[0].get_header("Notion-version") == "2026-03-11"
    credentials.resolve.assert_called_once_with("vault", "APIs/Notion/workspace", "password")
    assert "private-token" not in repr(result)


def test_writes_require_confirmation_before_credentials(tmp_path):
    service, credentials = configured_service(tmp_path)

    with pytest.raises(NotionError, match="confirm=true"):
        service.invoke("workspace", "pages.update", {"params": {"page_id": "page"}, "body": {"archived": True}})
    credentials.resolve.assert_not_called()


def test_path_parameters_are_required(tmp_path):
    service, credentials = configured_service(tmp_path)

    with pytest.raises(NotionError, match="page_id"):
        service.invoke("workspace", "pages.get", {})
    credentials.resolve.assert_not_called()


def test_response_secrets_and_credential_urls_are_sanitized(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.notion.service.urlopen", lambda *_args, **_kwargs: Response({"token": "secret", "nested": {"access_token": "private"}, "url": "https://example.test?token=x"}))

    result = service.invoke("workspace", "users.self", {})

    assert result == {"data": {"token": "[REDACTED]", "nested": {"access_token": "[REDACTED]"}, "url": "[REDACTED_URL]"}}


def test_pagination_aggregates_results_and_uses_cursor(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    requests = []
    responses = iter([Response({"results": [{"id": "1"}], "has_more": True, "next_cursor": "next cursor"}), Response({"results": [{"id": "2"}], "has_more": False, "next_cursor": None})])
    monkeypatch.setattr("onmyoji_daemon.mcp.notion.service.urlopen", lambda request, timeout: requests.append(request) or next(responses))

    result = service.invoke("workspace", "users.list", {"paginate": True})

    assert result["data"]["results"] == [{"id": "1"}, {"id": "2"}]
    assert requests[1].full_url.endswith("/v1/users?start_cursor=next+cursor")


def test_upload_reads_only_files_inside_profile_roots(tmp_path, monkeypatch):
    allowed = tmp_path / "work"
    allowed.mkdir()
    source = allowed / "document.txt"
    source.write_text("content", encoding="utf-8")
    service, _ = configured_service(tmp_path, [str(allowed)])
    requests = []
    monkeypatch.setattr("onmyoji_daemon.mcp.notion.service.urlopen", lambda request, timeout: requests.append(request) or Response({"id": "upload"}))

    result = service.invoke("workspace", "file_uploads.send", {"params": {"file_upload_id": "upload-id"}, "body": {"file_path": str(source)}, "confirm": True})

    assert result == {"data": {"id": "upload"}}
    assert b"content" in requests[0].data
    assert requests[0].get_header("Content-type").startswith("multipart/form-data; boundary=")


def test_upload_rejects_files_outside_profile_roots_before_credentials(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("private", encoding="utf-8")
    service, credentials = configured_service(tmp_path, [str(allowed)])

    with pytest.raises(NotionError, match="fora das raízes"):
        service.invoke("workspace", "file_uploads.send", {"params": {"file_upload_id": "upload-id"}, "body": {"file_path": str(outside)}, "confirm": True})
    credentials.resolve.assert_not_called()


def test_connection_requires_a_user_object(tmp_path, monkeypatch):
    service, _ = configured_service(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.notion.service.urlopen", lambda *_args, **_kwargs: Response({"object": "user", "id": "bot"}))

    assert service.test_connection("workspace") == {"profile": "workspace", "authenticated": True}


def test_tampered_endpoint_is_rejected_before_credentials(tmp_path):
    service, credentials = configured_service(tmp_path)
    path = tmp_path / "configs" / "mcp" / "notion.toml"
    path.write_text(path.read_text(encoding="utf-8").replace("https://api.notion.com", "https://example.test"), encoding="utf-8")

    with pytest.raises(NotionError, match="Endpoint ou versão"):
        service.invoke("workspace", "users.self", {})
    credentials.resolve.assert_not_called()
