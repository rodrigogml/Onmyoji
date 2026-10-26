"""Consentimento Google Desktop por callback loopback local."""
from __future__ import annotations

import http.server
import json
import secrets
import socketserver
import urllib.parse
import urllib.request
import webbrowser
from typing import Any, Callable

from .service import GoogleError, GoogleService


class _Callback(http.server.BaseHTTPRequestHandler):
    expected_state = ""
    result: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        handler = type(self)
        if not secrets.compare_digest(query.get("state", [""])[0], handler.expected_state): handler.result = {"error": "invalid_state"}
        elif query.get("error"): handler.result = {"error": query["error"][0]}
        else: handler.result = {"code": query.get("code", [""])[0]}
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write("<html><body>Autorização recebida. Você pode fechar esta janela.</body></html>".encode())

    def log_message(self, *_args: Any) -> None:
        return


def authorize(service: GoogleService, profile_name: str, open_browser: bool = True, url_consumer: Callable[[str], None] | None = None) -> str:
    """Autoriza o perfil e persiste somente o refresh token no vault."""
    profile, defaults = service._profile(profile_name, require_enabled=False)
    try:
        client_id = service.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["client_id_field"])
        client_secret = service.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["client_secret_field"])
    except Exception as error:
        raise GoogleError("invalid_vault_credentials", "Client ID ou Client Secret Google ausente no vault.") from error
    state = secrets.token_urlsafe(32); _Callback.expected_state = state; _Callback.result = {}
    with socketserver.TCPServer(("127.0.0.1", 0), _Callback) as server:
        redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/oauth2callback"
        query = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(defaults["scopes"]), "access_type": "offline", "prompt": "consent", "state": state})
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + query
        if url_consumer is not None: url_consumer(url)
        if open_browser:
            try: webbrowser.open(url)
            except webbrowser.Error: pass
        server.timeout = 300; server.handle_request(); result = _Callback.result
    if result.get("error") or not result.get("code"): raise GoogleError("oauth_callback_failed", "A autorização Google não retornou um código válido.")
    body = urllib.parse.urlencode({"code": result["code"], "client_id": client_id, "client_secret": client_secret, "redirect_uri": redirect_uri, "grant_type": "authorization_code"}).encode()
    request = urllib.request.Request(defaults["oauth_base"] + "/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with service.opener(request, timeout=defaults["timeout_seconds"]) as response: tokens = json.loads(response.read())
        refresh = tokens.get("refresh_token")
        if not isinstance(refresh, str) or not refresh: raise ValueError()
    except Exception as error: raise GoogleError("oauth_exchange_failed", "O Google não retornou um refresh token válido.") from error
    service.store_refresh_token(profile_name, refresh)
    return url
