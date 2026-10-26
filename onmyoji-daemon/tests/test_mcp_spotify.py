from __future__ import annotations

import json
from pathlib import Path

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration, empty_credentials
from onmyoji_daemon.mcp.spotify.configuration import empty_configuration
from onmyoji_daemon.mcp.spotify.oauth import _challenge
from onmyoji_daemon.mcp.spotify.service import OPERATIONS, SpotifyError, SpotifyService


class Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_args): return None
    def read(self): return self.value if isinstance(self.value, bytes) else json.dumps(self.value).encode()


class Credentials:
    values = {"username": "client-id", "password": "refresh"}
    def resolve(self, _profile, _entry, field): return self.values[field]
    def save_field(self, profile, entry, field, value): self.saved = (profile, entry, field, value)


def configured(root: Path, *, enabled: bool = True) -> McpConfiguration:
    config = McpConfiguration(root)
    credentials = empty_credentials(); credentials["vaults"]["vault"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}; credentials["profiles"]["vault"] = {"vault": "vault", "allowed_entry_roots": ["APIs/Spotify"], "auth": {"windows": {"mode": "credential_manager", "target": "vault"}, "linux": {"mode": "secret_tool", "command": ["secret-tool"]}}}; config.save_credentials(credentials)
    data = empty_configuration(); data["profiles"]["music"] = {"enabled": enabled, "description": "Conta de música.", "credential_profile": "vault", "credential_entry_path": "APIs/Spotify/music", "client_id_field": "username", "refresh_token_field": "password", "callback_port": 8888}; config.save_spotify(data)
    return config


def test_configuration_and_availability(tmp_path):
    config = configured(tmp_path)
    assert config.spotify_tool_available()
    assert config.enabled_spotify_profiles() == [{"name": "music", "description": "Conta de música."}]


def test_operations_preserve_legacy_surface_with_current_endpoints():
    assert len(OPERATIONS) == 33
    assert OPERATIONS["playlists.create"] == ("POST", "/me/playlists")
    assert OPERATIONS["following.add"] == ("PUT", "/me/library")


def test_library_list_routes_to_selected_supported_collection(tmp_path):
    configured(tmp_path); calls = []
    def opener(request, timeout):
        calls.append(request.full_url)
        return Response({"access_token": "access"} if request.full_url.endswith("/api/token") else {"items": []})
    SpotifyService(tmp_path, Credentials(), opener).invoke("music", "library.list", {"query": {"type": "tracks", "limit": 5}})
    assert calls[1].endswith("/me/tracks?limit=5")


def test_invoke_refreshes_token_and_calls_api(tmp_path):
    configured(tmp_path); calls = []
    def opener(request, timeout):
        calls.append((request, timeout))
        return Response({"access_token": "access"} if request.full_url.endswith("/api/token") else {"id": "user"})
    result = SpotifyService(tmp_path, Credentials(), opener).invoke("music", "me.get", {})
    assert result["data"] == {"id": "user"}
    assert calls[1][0].headers["Authorization"] == "Bearer access"


def test_rotated_refresh_token_is_saved(tmp_path):
    configured(tmp_path); credentials = Credentials(); responses = iter([Response({"access_token": "access", "refresh_token": "rotated"}), Response({"id": "user"})])
    SpotifyService(tmp_path, credentials, lambda *_args, **_kwargs: next(responses)).invoke("music", "me.get", {})
    assert credentials.saved == ("vault", "APIs/Spotify/music", "password", "rotated")


def test_write_requires_confirmation(tmp_path):
    configured(tmp_path)
    with pytest.raises(SpotifyError, match="confirm=true"):
        SpotifyService(tmp_path, Credentials()).invoke("music", "player.pause", {})


def test_missing_path_parameter_is_rejected(tmp_path):
    configured(tmp_path)
    with pytest.raises(SpotifyError, match="id"):
        SpotifyService(tmp_path, Credentials()).invoke("music", "tracks.get", {})


def test_pkce_challenge_is_url_safe_without_padding():
    challenge = _challenge("a" * 64)
    assert "=" not in challenge and "+" not in challenge and "/" not in challenge
