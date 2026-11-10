"""Cliente seguro da Web API do Spotify."""
from __future__ import annotations

import json
import time
import tomllib
from pathlib import Path
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS: dict[str, tuple[str, str]] = {
    "me.get": ("GET", "/me"), "search": ("GET", "/search"),
    "albums.get": ("GET", "/albums/{id}"), "artists.get": ("GET", "/artists/{id}"), "tracks.get": ("GET", "/tracks/{id}"),
    "playlists.list": ("GET", "/me/playlists"), "playlists.get": ("GET", "/playlists/{id}"), "playlists.items.list": ("GET", "/playlists/{id}/items"),
    "playlists.create": ("POST", "/me/playlists"), "playlists.update": ("PUT", "/playlists/{id}"), "playlists.items.add": ("POST", "/playlists/{id}/items"), "playlists.items.remove": ("DELETE", "/playlists/{id}/items"),
    "library.list": ("GET", "/me/{library_type}"), "library.save": ("PUT", "/me/library"), "library.remove": ("DELETE", "/me/library"),
    "following.list": ("GET", "/me/following"), "following.add": ("PUT", "/me/library"), "following.remove": ("DELETE", "/me/library"),
    "top.items": ("GET", "/me/top/{type}"), "recent.list": ("GET", "/me/player/recently-played"),
    "player.state": ("GET", "/me/player"), "player.devices": ("GET", "/me/player/devices"), "player.current": ("GET", "/me/player/currently-playing"), "player.queue": ("GET", "/me/player/queue"),
    "player.play": ("PUT", "/me/player/play"), "player.pause": ("PUT", "/me/player/pause"), "player.next": ("POST", "/me/player/next"), "player.previous": ("POST", "/me/player/previous"), "player.seek": ("PUT", "/me/player/seek"), "player.volume": ("PUT", "/me/player/volume"), "player.shuffle": ("PUT", "/me/player/shuffle"), "player.repeat": ("PUT", "/me/player/repeat"), "player.queue.add": ("POST", "/me/player/queue"),
}
WRITE_OPERATIONS = {name for name, (method, _path) in OPERATIONS.items() if method != "GET"}


class SpotifyError(ValueError):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message); self.code = code; self.status = status


class SpotifyService:
    def __init__(self, root: Path, credentials: KeePassCredentialResolver | None = None, opener=None):
        self.root = root.resolve(); self.credentials = credentials or KeePassCredentialResolver(self.root); self.opener = opener or urllib.request.urlopen

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in OPERATIONS: raise SpotifyError("unsupported_operation", "Operação Spotify não permitida.")
        if operation in WRITE_OPERATIONS and arguments.get("confirm") is not True: raise SpotifyError("confirmation_required", "Operações Spotify de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        method, template = OPERATIONS[operation]
        query = self._mapping(arguments.get("query"), "query")
        if operation == "library.list":
            library_type = query.pop("type", None)
            if library_type not in {"tracks", "albums", "shows", "episodes", "audiobooks"}: raise SpotifyError("invalid_request", "library.list exige query.type: tracks, albums, shows, episodes ou audiobooks.")
            template = template.replace("{library_type}", library_type)
        path = self._substitute(template, self._mapping(arguments.get("params"), "params"))
        body = arguments.get("body")
        client_id, refresh = self._secrets(profile)
        access, rotated = self._refresh(defaults, client_id, refresh)
        if rotated and rotated != refresh:
            try: self.credentials.save_field(profile["credential_profile"], profile["credential_entry_path"], profile["refresh_token_field"], rotated)
            except CredentialError as error: raise SpotifyError("credential_" + error.code, "O Spotify renovou o refresh token, mas o vault não pôde ser atualizado.") from error
        data = json.dumps(body).encode("utf-8") if body is not None else None
        url = defaults["api_base"] + path
        if query: url += "?" + urllib.parse.urlencode({key: value for key, value in query.items() if value is not None}, doseq=True)
        request = urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + access, **({"Content-Type": "application/json"} if body is not None else {})}, method=method)
        return {"profile": profile_name, "operation": operation, "data": self._send(request, defaults)}

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        result = self.invoke(profile_name, "me.get", {})
        return {"profile": profile_name, "authenticated": isinstance(result.get("data"), Mapping)}

    def save_refresh_token(self, profile_name: str, token: str) -> None:
        profile, _ = self._profile(profile_name, require_enabled=False)
        try: self.credentials.save_field(profile["credential_profile"], profile["credential_entry_path"], profile["refresh_token_field"], token)
        except CredentialError as error: raise SpotifyError("credential_" + error.code, "Não foi possível salvar o refresh token Spotify.") from error

    def _profile(self, name: str, require_enabled: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        path = self.root / "configs" / "mcp" / "spotify.toml"
        try: data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error: raise SpotifyError("invalid_config", "Configuração Spotify MCP ausente ou inválida.") from error
        profile, defaults = data.get("profiles", {}).get(name), data.get("defaults", {})
        if not isinstance(profile, dict) or not isinstance(defaults, dict): raise SpotifyError("profile_not_found", "Perfil Spotify não encontrado.")
        if require_enabled and profile.get("enabled") is not True: raise SpotifyError("profile_disabled", "Perfil Spotify desabilitado.")
        return profile, defaults

    def _secrets(self, profile: Mapping[str, Any]) -> tuple[str, str]:
        try:
            client_id = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["client_id_field"])
            refresh = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["refresh_token_field"])
            if not client_id or not refresh: raise ValueError()
            return client_id, refresh
        except (CredentialError, KeyError, ValueError) as error: raise SpotifyError("oauth_required", "Client ID ou autorização OAuth Spotify não está completa no vault.") from error

    def _refresh(self, defaults: Mapping[str, Any], client_id: str, refresh: str) -> tuple[str, str | None]:
        body = urllib.parse.urlencode({"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id}).encode()
        request = urllib.request.Request(defaults["accounts_base"] + "/api/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with self.opener(request, timeout=defaults["timeout_seconds"]) as response: payload = json.loads(response.read())
            access = payload.get("access_token"); rotated = payload.get("refresh_token")
            if not isinstance(access, str) or not access or (rotated is not None and not isinstance(rotated, str)): raise ValueError()
            return access, rotated
        except Exception as error: raise SpotifyError("oauth_refresh_failed", "Não foi possível renovar o acesso Spotify; autorize o perfil novamente.") from error

    def _send(self, request: urllib.request.Request, defaults: Mapping[str, Any]) -> Any:
        for attempt in range(defaults["max_retries"] + 1):
            try:
                with self.opener(request, timeout=defaults["timeout_seconds"]) as response:
                    raw = response.read()
                    if not raw: return None
                    try: return json.loads(raw)
                    except (json.JSONDecodeError, UnicodeDecodeError) as error: raise SpotifyError("invalid_response", "O Spotify retornou uma resposta inválida.") from error
            except urllib.error.HTTPError as error:
                if error.code == 429 and attempt < defaults["max_retries"]:
                    try: delay = min(float(error.headers.get("Retry-After", "1")), 10)
                    except (TypeError, ValueError): delay = 1
                    time.sleep(delay); continue
                if error.code in {500, 502, 503, 504} and attempt < defaults["max_retries"]: time.sleep(0.05 * (attempt + 1)); continue
                raise SpotifyError("spotify_http_error", f"A API Spotify retornou HTTP {error.code}.", error.code) from error
            except urllib.error.URLError as error:
                if attempt < defaults["max_retries"]: time.sleep(0.05 * (attempt + 1)); continue
                raise SpotifyError("network_error", "Não foi possível conectar ao Spotify.") from error
        raise SpotifyError("request_failed", "A requisição Spotify não foi concluída.")

    @staticmethod
    def _mapping(value: Any, label: str) -> dict[str, Any]:
        if value is None: return {}
        if not isinstance(value, Mapping): raise SpotifyError("invalid_request", f"{label} deve ser um objeto.")
        return dict(value)

    @staticmethod
    def _substitute(template: str, params: Mapping[str, Any]) -> str:
        for part in template.split("/"):
            if part.startswith("{") and part.endswith("}"):
                key = part[1:-1]; value = params.get(key)
                if value is None or isinstance(value, (dict, list)): raise SpotifyError("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
                template = template.replace("{" + key + "}", urllib.parse.quote(str(value), safe=""))
        return template
