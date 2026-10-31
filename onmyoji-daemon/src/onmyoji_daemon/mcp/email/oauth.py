"""OAuth delegado com PKCE e estado secreto exclusivamente no vault."""
from __future__ import annotations

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import time
from urllib import parse, request
import webbrowser

from ..credentials import CredentialError, KeePassCredentialResolver
from ..credentials.encrypted import database_lock
from .contracts import EmailError, digest, require


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise EmailError("oauth_redirect", "Redirecionamento inesperado no endpoint OAuth.")


class EmailOAuth:
    def __init__(self, root: Path, account: dict, connection: str, timeout: int = 30):
        self.root, self.account, self.connection, self.timeout = root, account, connection, timeout
        endpoint = account.get(connection, {})
        self.auth = endpoint.get("auth", {})
        require(self.auth.get("mode") == "oauth2", "oauth_unavailable", "Esta conexão não usa OAuth.")
        self.credentials = KeePassCredentialResolver(root)
        self.binding = digest({"auth": self.auth, "address": account["address"], "endpoint": {key: value for key, value in endpoint.items() if key != "auth"},
                               "protocols": [value["protocol"] for value in (account.get("incoming"), account.get("smtp")) if value]})
        self.directory = root / "configs" / "mcp" / "email"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock_file = self.directory / digest([self.auth["credential_profile"], self.auth["credential_entry_path"]])

    def metadata(self) -> dict:
        if self.auth["provider"] == "google":
            issuer, authorize, token = "https://accounts.google.com", "https://accounts.google.com/o/oauth2/v2/auth", "https://oauth2.googleapis.com/token"
            scopes = ["https://mail.google.com/"]
        else:
            issuer = "https://login.microsoftonline.com/" + self.auth.get("tenant", "common") + "/v2.0"
            base = issuer.removesuffix("/v2.0") + "/oauth2/v2.0"
            authorize, token = base + "/authorize", base + "/token"
            protocol_scope = {"imap": "IMAP.AccessAsUser.All", "pop3": "POP.AccessAsUser.All", "smtp": "SMTP.Send"}
            scopes = ["offline_access"] + ["https://outlook.office.com/" + protocol_scope[value["protocol"]]
                      for value in (self.account.get("incoming"), self.account.get("smtp")) if value]
        redirect = self.auth.get("redirect_uri") if self.auth["callback_mode"] == "remote" else f"http://127.0.0.1:{self.auth.get('callback_port', 8765)}/oauth/mcp/email/callback"
        return {"issuer": issuer, "authorizationEndpoint": authorize, "tokenEndpoint": token,
                "redirectUri": redirect, "scopes": scopes, "binding": self.binding,
                "storage_root": str(self.directory), "requireIssuer": False}

    def read(self) -> dict:
        try: raw = self.credentials.resolve(self.auth["credential_profile"], self.auth["credential_entry_path"], "notes")
        except CredentialError as error:
            if error.code != "secret_not_found": raise
            raw = ""
        try: value = json.loads(raw) if raw.strip() else {}
        except ValueError as error: raise EmailError("invalid_vault_notes", "Use entrada dedicada ao OAuth de e-mail com notas JSON.") from error
        require(isinstance(value, dict), "invalid_vault_notes", "Notas OAuth inválidas.")
        return value

    def save(self, value: dict) -> None:
        self.credentials.save_oauth_state(self.auth["credential_profile"], self.auth["credential_entry_path"], value)

    def exchange(self, fields: dict) -> dict:
        fields = {**fields, "client_id": self.auth["client_id"]}
        secret = self.credentials.resolve(self.auth["credential_profile"], self.auth["credential_entry_path"], "password") if self.auth.get("client_type", "confidential") == "confidential" else ""
        if secret: fields["client_secret"] = secret
        req = request.Request(self.metadata()["tokenEndpoint"], data=parse.urlencode(fields).encode(), headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
        try:
            with request.build_opener(NoRedirect).open(req, timeout=self.timeout) as response:
                result = json.loads(response.read(1048577))
            require(isinstance(result.get("access_token"), str) and bool(result["access_token"]), "oauth_exchange_failed", "Provedor não retornou um token válido.")
            return result
        except EmailError: raise
        except Exception as error: raise EmailError("oauth_exchange_failed", "Não foi possível obter o token; revise consentimento e credenciais.") from error

    def start(self, state: str, session_id: str, redirect: str) -> dict:
        require(redirect == self.metadata()["redirectUri"], "redirect_mismatch", "Callback diverge da configuração.")
        verifier = secrets.token_urlsafe(64)
        with database_lock(self.lock_file):
            store = self.read(); email = store.setdefault("email_oauth", {})
            pending = email.setdefault("pending", {})
            pending = {key: value for key, value in pending.items() if value.get("expires", 0) > time.time()}
            require(not any(value.get("binding") == self.binding for value in pending.values()), "oauth_active", "Existe autorização ativa; conclua, cancele ou aguarde expirar.")
            pending[session_id] = {"state_hash": digest(state), "verifier": verifier, "redirect": redirect, "binding": self.binding, "expires": time.time() + 600}
            email["pending"] = pending; self.save(store)
        meta = self.metadata()
        fields = {"client_id": self.auth["client_id"], "redirect_uri": redirect, "response_type": "code", "scope": " ".join(meta["scopes"]), "state": state,
                  "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("="), "code_challenge_method": "S256"}
        if self.auth["provider"] == "google": fields.update(access_type="offline", prompt="consent")
        return {"authorizationUrl": meta["authorizationEndpoint"] + "?" + parse.urlencode(fields), "expiresIn": 600}

    def complete(self, code: str, state: str, session_id: str, redirect: str) -> dict:
        with database_lock(self.lock_file):
            store = self.read(); email = store.get("email_oauth", {}); pending = email.get("pending", {}).pop(session_id, None)
            require(isinstance(pending, dict) and pending["expires"] > time.time() and pending["binding"] == self.binding and
                    secrets.compare_digest(pending["state_hash"], digest(state)) and pending["redirect"] == redirect == self.metadata()["redirectUri"], "oauth_session_invalid", "Autorização expirada, já consumida ou alterada.")
            # Consumir antes da troca impede replay mesmo se o processo cair.
            self.save(store)
            tokens = self.exchange({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect, "code_verifier": pending["verifier"]})
            previous = email.setdefault("tokens", {}).get(self.binding, {})
            refresh = tokens.get("refresh_token", previous.get("refresh_token"))
            require(isinstance(refresh, str) and bool(refresh), "oauth_refresh_missing", "O provedor não concedeu acesso offline.")
            email["tokens"][self.binding] = {"refresh_token": refresh}
            self.save(store)
        return {"authenticated": True}

    def cancel(self, session_id: str) -> dict:
        with database_lock(self.lock_file):
            store = self.read(); pending = store.get("email_oauth", {}).get("pending", {})
            if session_id in pending:
                require(pending[session_id]["binding"] == self.binding, "oauth_session_invalid", "Sessão pertence a outra conexão.")
                del pending[session_id]; self.save(store)
        return {"cancelled": True}

    def access_token(self) -> str:
        with database_lock(self.lock_file):
            store = self.read(); tokens = store.get("email_oauth", {}).get("tokens", {})
            previous = tokens.get(self.binding, {})
            require(bool(previous.get("refresh_token")), "authorization_required", "Autorize esta conta no setup ou pelo callback remoto.")
            result = self.exchange({"grant_type": "refresh_token", "refresh_token": previous["refresh_token"]})
            if result.get("refresh_token") and result["refresh_token"] != previous["refresh_token"]:
                tokens[self.binding] = {"refresh_token": result["refresh_token"]}; self.save(store)
            return result["access_token"]

    def authorize_local(self, url_consumer=print) -> dict:
        require(self.auth["callback_mode"] == "local", "local_mode_required", "Selecione callback local nesta conexão.")
        state, session_id = secrets.token_urlsafe(32), secrets.token_hex(16)
        redirect = self.metadata()["redirectUri"]; result = {}
        class Callback(BaseHTTPRequestHandler):
            def do_GET(handler):
                parsed = parse.urlsplit(handler.path); query = parse.parse_qs(parsed.query)
                valid = parsed.path == "/oauth/mcp/email/callback" and len(query.get("state", [])) == 1 and secrets.compare_digest(query["state"][0], state)
                if not valid:
                    handler.send_response(400); handler.end_headers(); return
                if len(query.get("code", [])) == 1 and not query.get("error"):
                    result["code"] = query["code"][0]
                else: result["error"] = True
                handler.send_response(200); handler.send_header("Content-Type", "text/plain; charset=utf-8"); handler.send_header("Cache-Control", "no-store"); handler.end_headers()
                handler.wfile.write("Retorno recebido. Volte ao setup para verificar o resultado.".encode())
            def log_message(self, *_args): pass
        try:
            with HTTPServer(("127.0.0.1", self.auth.get("callback_port", 8765)), Callback) as server:
                server.timeout = 1
                started = self.start(state, session_id, redirect)
                url_consumer(started["authorizationUrl"])
                webbrowser.open(started["authorizationUrl"])
                deadline = time.monotonic() + 600
                while not result and time.monotonic() < deadline: server.handle_request()
                require(bool(result.get("code")), "oauth_callback_failed", "Autorização recusada ou expirada.")
                return self.complete(result["code"], state, session_id, redirect)
        finally:
            self.cancel(session_id)
