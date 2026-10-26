"""Consentimento Spotify por Authorization Code com PKCE."""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import secrets
import socketserver
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable

from .service import SpotifyError, SpotifyService


class _Callback(http.server.BaseHTTPRequestHandler):
    expected_state = ""
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query); handler = type(self)
        if not secrets.compare_digest(query.get("state", [""])[0], handler.expected_state): handler.result = {"error": "invalid_state"}
        elif query.get("error"): handler.result = {"error": query["error"][0]}
        else: handler.result = {"code": query.get("code", [""])[0]}
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write("<html><body>Autorização recebida. Você pode fechar esta janela.</body></html>".encode())

    def log_message(self, *_args: Any) -> None: return


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def authorize(service: SpotifyService, profile_name: str, open_browser: bool = True, url_consumer: Callable[[str], None] | None = None) -> str:
    """Autoriza o perfil por PKCE e salva o refresh token no vault."""
    profile, defaults = service._profile(profile_name, require_enabled=False)
    try: client_id = service.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["client_id_field"])
    except Exception as error: raise SpotifyError("invalid_vault_credentials", "Client ID Spotify ausente no vault.") from error
    state = secrets.token_urlsafe(32); verifier = secrets.token_urlsafe(64); _Callback.expected_state = state; _Callback.result = {}
    try:
        with socketserver.TCPServer(("127.0.0.1", profile["callback_port"]), _Callback) as server:
            redirect_uri = f"http://127.0.0.1:{profile['callback_port']}/callback"
            query = urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri, "scope": " ".join(defaults["scopes"]), "code_challenge_method": "S256", "code_challenge": _challenge(verifier), "state": state})
            url = defaults["accounts_base"] + "/authorize?" + query
            if url_consumer is not None: url_consumer(url)
            if open_browser:
                try: webbrowser.open(url)
                except webbrowser.Error: pass
            server.timeout = 300; server.handle_request(); result = _Callback.result
    except OSError as error: raise SpotifyError("callback_unavailable", "A porta de callback Spotify está ocupada ou indisponível.") from error
    if result.get("error") or not result.get("code"): raise SpotifyError("oauth_callback_failed", "A autorização Spotify não retornou um código válido.")
    body = urllib.parse.urlencode({"client_id": client_id, "grant_type": "authorization_code", "code": result["code"], "redirect_uri": redirect_uri, "code_verifier": verifier}).encode()
    request = urllib.request.Request(defaults["accounts_base"] + "/api/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with service.opener(request, timeout=defaults["timeout_seconds"]) as response: tokens = json.loads(response.read())
        refresh = tokens.get("refresh_token")
        if not isinstance(refresh, str) or not refresh: raise ValueError()
    except Exception as error: raise SpotifyError("oauth_exchange_failed", "O Spotify não retornou um refresh token válido.") from error
    service.save_refresh_token(profile_name, refresh)
    return url
