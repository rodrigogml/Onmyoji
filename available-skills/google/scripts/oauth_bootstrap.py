#!/usr/bin/env python3
"""Authorize a Google Desktop OAuth client through a local loopback callback."""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.server
import json
import secrets
import socketserver
import threading
import urllib.parse
import webbrowser
from pathlib import Path

from google import GoogleError, load_configured_client_credentials, load_settings, token_exchange, write_refresh_token


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    state = ""
    result: dict[str, str] = {}

    def do_GET(self):  # noqa: N802
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        print("[oauth] callback_query code_present=" + str(bool(query.get("code"))) + " state_present=" + str(bool(query.get("state"))) + " error_present=" + str(bool(query.get("error"))), flush=True)
        if query.get("state", [""])[0] != CallbackHandler.state:
            CallbackHandler.result = {"error": "invalid_state"}
        elif query.get("error"):
            CallbackHandler.result = {"error": query["error"][0]}
        else:
            CallbackHandler.result = {"code": query.get("code", [""])[0]}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><body>Duh!</body></html>")

    def log_message(self, *_args):
        return


def authorize(config_path: str, profile_name: str, open_browser: bool = True) -> None:
    settings = load_settings(config_path, profile_name)
    client_id, client_secret = load_configured_client_credentials(settings)
    state = secrets.token_urlsafe(32)
    CallbackHandler.state, CallbackHandler.result = state, {}
    with socketserver.TCPServer(("127.0.0.1", 0), CallbackHandler) as server:
        port = server.server_address[1]
        redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"
        query = urllib.parse.urlencode({"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(settings.scopes), "access_type": "offline", "prompt": "consent", "state": state})
        url = "https://accounts.google.com/o/oauth2/v2/auth?" + query
        print("Abra esta URL no navegador da mesma máquina se ela não abrir automaticamente:")
        print(url)
        if open_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                pass
        server.timeout = 300
        server.handle_request()
        result = CallbackHandler.result
    if result.get("error"):
        raise GoogleError("oauth_callback_failed", "A autorização Google falhou: " + result["error"])
    if not result.get("code"):
        raise GoogleError("oauth_callback_failed", "O callback Google não trouxe código de autorização.")
    print("[oauth] callback_received")
    tokens = token_exchange(settings, client_id, client_secret, result["code"], redirect_uri)
    refresh = tokens.get("refresh_token")
    if not isinstance(refresh, str) or not refresh:
        raise GoogleError("oauth_no_refresh_token", "O Google não retornou refresh token; use consentimento explícito novamente.")
    print("[oauth] token_exchange_completed refresh_token_present=true")
    write_refresh_token(settings, refresh)
    print("[oauth] vault_write_completed profile=" + settings.profile)
    print("Autorização concluída; refresh token armazenado na KeePassVault.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--no-browser", action="store_true", help="não abrir uma janela; exibir a URL para abertura manual")
    args = parser.parse_args()
    try:
        authorize(args.config, args.profile, open_browser=not args.no_browser)
        return 0
    except GoogleError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": error.message}}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
