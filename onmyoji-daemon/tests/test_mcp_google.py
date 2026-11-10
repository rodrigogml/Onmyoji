from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration, empty_credentials
from onmyoji_daemon.mcp.google.configuration import empty_configuration
from onmyoji_daemon.mcp.google.service import GoogleError, GoogleService, OPERATIONS, sanitize


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self): return self.value if isinstance(self.value, bytes) else json.dumps(self.value).encode()


class Credentials:
    values = {"username": "client-id", "password": "client-secret", "notes": json.dumps({"version": 1, "profiles": {"conta": {"refresh_token": "refresh"}}})}
    def resolve(self, _profile, _entry, field): return self.values[field]
    def save_oauth_state(self, _profile, _entry, state): self.saved = state


def configured(root: Path, *, enabled: bool = True) -> McpConfiguration:
    config = McpConfiguration(root)
    credentials = empty_credentials(); credentials["vaults"]["vault"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}; credentials["profiles"]["vault"] = {"vault": "vault", "allowed_entry_roots": ["APIs/Google"], "auth": {"windows": {"mode": "credential_manager", "target": "vault"}, "linux": {"mode": "secret_tool", "command": ["secret-tool"]}}}; config.save_credentials(credentials)
    data = empty_configuration(); data["profiles"]["work"] = {"enabled": enabled, "description": "Conta corporativa.", "oauth_profile": "conta", "credential_profile": "vault", "credential_entry_path": "APIs/Google/work", "client_id_field": "username", "client_secret_field": "password", "user_id": "me", "readable_roots": [str(root / "input")], "writable_roots": [str(root / "output")]}; config.save_google(data)
    return config


def test_configuration_and_availability(tmp_path):
    config = configured(tmp_path)
    assert config.google_tool_available()
    assert config.enabled_google_profiles() == [{"name": "work", "description": "Conta corporativa."}]


def test_operations_preserve_full_legacy_surface():
    assert len(OPERATIONS) == 87
    assert {"gmail.messages.send", "people.createContact", "drive.uploads.create", "calendar.channels.stop"} <= set(OPERATIONS)


def test_invoke_refreshes_token_and_sanitizes_response(tmp_path):
    configured(tmp_path); calls = []
    def opener(request, timeout):
        calls.append((request, timeout))
        return Response({"access_token": "access"} if request.full_url.endswith("/token") else {"emailAddress": "a@example.test", "token": "secret"})
    result = GoogleService(tmp_path, Credentials(), opener).invoke("work", "gmail.profile.get", {})
    assert result["data"]["token"] == "[REDACTED]"
    assert calls[1][0].headers["Authorization"] == "Bearer access"


def test_write_requires_confirmation(tmp_path):
    configured(tmp_path)
    with pytest.raises(GoogleError, match="confirm=true"):
        GoogleService(tmp_path, Credentials()).invoke("work", "gmail.messages.send", {"body": {}})


def test_upload_and_download_are_confined(tmp_path):
    configured(tmp_path); source = tmp_path / "input" / "a.txt"; source.parent.mkdir(); source.write_text("hello", encoding="utf-8")
    responses = iter([Response({"access_token": "access"}), Response({"id": "1"})])
    result = GoogleService(tmp_path, Credentials(), lambda *_args, **_kwargs: next(responses)).invoke("work", "drive.uploads.create", {"confirm": True, "body": {"file_path": str(source)}})
    assert result["data"] == {"id": "1"}
    outside = tmp_path.parent / "outside.txt"; outside.write_text("x", encoding="utf-8")
    with pytest.raises(GoogleError, match="raiz de leitura"):
        GoogleService(tmp_path, Credentials()).invoke("work", "drive.uploads.create", {"confirm": True, "body": {"file_path": str(outside)}})


def test_store_refresh_token_preserves_other_profiles(tmp_path):
    configured(tmp_path); credentials = Credentials(); credentials.values = dict(credentials.values); credentials.values["notes"] = json.dumps({"version": 1, "profiles": {"other": {"refresh_token": "keep"}}})
    GoogleService(tmp_path, credentials).store_refresh_token("work", "new")
    assert credentials.saved["profiles"] == {"other": {"refresh_token": "keep"}, "conta": {"refresh_token": "new"}}


def test_sanitize_is_recursive():
    assert sanitize({"nested": [{"refresh_token": "secret"}]}) == {"nested": [{"refresh_token": "[REDACTED]"}]}
