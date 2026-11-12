"""OAuth PKCE e renovação protegida para Mercado Livre e Mercado Pago."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from typing import Any, Iterator
import urllib.error
import urllib.parse
import urllib.request

from ..credentials import CredentialError, KeePassCredentialResolver
from .configuration import load_configuration, load_profile
from .exception import MercadoError


ENDPOINTS = {
    "mercadolivre": ("https://auth.mercadolivre.com.br/authorization", "https://api.mercadolibre.com/oauth/token"),
    "mercadopago": ("https://auth.mercadopago.com/authorization", "https://api.mercadopago.com/oauth/token"),
}
_CLIENT_TOKENS: dict[tuple[str, str], dict[str, Any]] = {}


def _utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@contextmanager
def authorization_lock(root: Path, provider: str, profile: str) -> Iterator[None]:
    """Serializa decisão, renovação e persistência entre processos."""
    directory = root / "configs" / "mcp" / "mercado-locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (_digest([provider, profile]) + ".lock")
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise MercadoError("network_error", "Outra operação está renovando a autorização; tente novamente.")
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class MercadoOAuth:
    """Mantém tokens exclusivamente nas entradas KeePass do aplicativo e perfil."""

    def __init__(self, root: Path, provider: str, profile_name: str, credentials: KeePassCredentialResolver | None = None):
        if provider not in ENDPOINTS:
            raise MercadoError("invalid_request", "Provedor OAuth Mercado inválido.")
        self.root = root.resolve()
        self.provider = provider
        self.profile_name = profile_name
        self.profile, self.defaults, self.configured = load_profile(self.root, profile_name, provider)
        assert self.configured is not None
        self.application = load_configuration(self.root)["applications"][provider][self.configured["application"]]
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def app_credentials(self) -> tuple[str, str]:
        try:
            profile, entry = self.application["credential_profile"], self.application["credential_entry_path"]
            return self.credentials.resolve(profile, entry, "username"), self.credentials.resolve(profile, entry, "password")
        except CredentialError as error:
            raise MercadoError("oauth_required", "Credenciais do aplicativo Mercado não estão disponíveis.") from error

    def binding(self) -> str:
        client_id, _ = self.app_credentials()
        return _digest({"provider": self.provider, "application": self.configured["application"], "clientId": hashlib.sha256(client_id.encode()).hexdigest(), "profile": self.profile_name, "site": self.configured["site_id"], "redirect": self.application["redirect_uri"], "scopes": self.application["scopes"], "mode": self.configured.get("auth_mode", "authorization_code")})

    def _read(self) -> dict[str, Any]:
        if self.configured.get("auth_mode", "authorization_code") != "authorization_code":
            return {}
        try:
            raw = self.credentials.resolve(self.configured["authorization_credential_profile"], self.configured["authorization_entry_path"], "notes")
        except CredentialError as error:
            if error.code == "secret_not_found":
                return {}
            raise MercadoError("oauth_required", "Entrada de autorização Mercado indisponível.") from error
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise MercadoError("oauth_required", "Estado OAuth Mercado inválido; use uma entrada dedicada.") from error
        if not isinstance(data, dict):
            raise MercadoError("oauth_required", "Estado OAuth Mercado inválido; use uma entrada dedicada.")
        return data

    def _save(self, data: dict[str, Any], *, rotation: bool = False) -> None:
        try:
            self.credentials.save_oauth_state(self.configured["authorization_credential_profile"], self.configured["authorization_entry_path"], data)
        except CredentialError as error:
            code = "token_rotation_uncertain" if rotation else "oauth_required"
            message = "O refresh token pode ter sido consumido sem persistência; autorize novamente." if rotation else "Não foi possível persistir a autorização Mercado."
            if rotation:
                self._uncertain_marker().parent.mkdir(parents=True, exist_ok=True)
                self._uncertain_marker().touch(exist_ok=True)
            raise MercadoError(code, message) from error

    def _uncertain_marker(self) -> Path:
        return self.root / "configs" / "mcp" / "mercado-locks" / (_digest([self.provider, self.profile_name]) + ".uncertain")

    def describe(self) -> dict[str, Any]:
        authorization, _ = ENDPOINTS[self.provider]
        return {"issuer": urllib.parse.urlsplit(authorization)._replace(path="", query="", fragment="").geturl().rstrip("/"), "authorizationEndpoint": authorization, "redirectUri": self.application["redirect_uri"], "storage_root": str((self.root / "configs" / "mcp" / "mercado").resolve()), "binding": self.binding()}

    def start(self, state: str, session_id: str, redirect_uri: str) -> dict[str, Any]:
        if self.configured.get("auth_mode", "authorization_code") != "authorization_code":
            raise MercadoError("profile_auth_mode_unavailable", "Este perfil usa client_credentials e não requer consentimento interativo.")
        if redirect_uri != self.application["redirect_uri"]:
            raise MercadoError("invalid_request", "Redirect URI OAuth divergente.")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        with authorization_lock(self.root, self.provider, self.profile_name):
            data = self._read()
            pending = data.setdefault("pending", {})
            if any(value.get("binding") == self.binding() and value.get("expiresAt", 0) > time.time() for value in pending.values() if isinstance(value, dict)):
                raise MercadoError("remote_conflict", "Já existe autorização pendente para este perfil.")
            pending[session_id] = {"stateHash": hashlib.sha256(state.encode()).hexdigest(), "verifier": verifier, "binding": self.binding(), "redirectUri": redirect_uri, "expiresAt": time.time() + 600}
            self._save(data)
        client_id, _ = self.app_credentials()
        query = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        if self.application["scopes"]:
            query["scope"] = " ".join(self.application["scopes"])
        return {"authorizationUrl": ENDPOINTS[self.provider][0] + "?" + urllib.parse.urlencode(query), "expiresIn": 600}

    def complete(self, code: str, state: str, session_id: str, redirect_uri: str) -> dict[str, Any]:
        with authorization_lock(self.root, self.provider, self.profile_name):
            data = self._read()
            pending = data.get("pending", {}).get(session_id)
            if not isinstance(pending, dict) or pending.get("expiresAt", 0) <= time.time() or pending.get("binding") != self.binding() or pending.get("redirectUri") != redirect_uri or not secrets.compare_digest(str(pending.get("stateHash", "")), hashlib.sha256(state.encode()).hexdigest()):
                raise MercadoError("invalid_request", "Sessão OAuth expirada, consumida ou divergente.")
            fields = {"grant_type": "authorization_code", "client_id": self.app_credentials()[0], "client_secret": self.app_credentials()[1], "code": code, "redirect_uri": redirect_uri, "code_verifier": pending["verifier"]}
            token = self._token_request(fields)
            if not isinstance(token.get("refresh_token"), str) or not token["refresh_token"]:
                raise MercadoError("oauth_required", "O provedor não concedeu refresh token.")
            data["tokens"] = self._token_state(token)
            data.setdefault("pending", {}).pop(session_id, None)
            self._save(data)
            self._uncertain_marker().unlink(missing_ok=True)
        return {"authenticated": True}

    def cancel(self, session_id: str) -> dict[str, Any]:
        if self.configured.get("auth_mode", "authorization_code") != "authorization_code":
            raise MercadoError("profile_auth_mode_unavailable", "Este perfil usa client_credentials e não possui sessão de autorização.")
        with authorization_lock(self.root, self.provider, self.profile_name):
            data = self._read()
            data.setdefault("pending", {}).pop(session_id, None)
            self._save(data)
        return {"cancelled": True}

    def status(self) -> dict[str, Any]:
        mode = self.configured.get("auth_mode", "authorization_code")
        if mode == "client_credentials":
            current = _CLIENT_TOKENS.get((self.provider, self.profile_name), {})
            return {"profile": self.profile_name, "provider": self.provider, "authMode": mode, "state": "ready" if current.get("expiresAt", 0) > time.time() + 30 else "not_loaded"}
        data = self._read()
        tokens = data.get("tokens", {})
        valid_binding = isinstance(tokens, dict) and tokens.get("binding") == self.binding()
        state = "authorized" if valid_binding and tokens.get("refreshToken") else "required"
        return {"profile": self.profile_name, "provider": self.provider, "authMode": mode, "state": state, "expiresAt": tokens.get("expiresAt") if valid_binding else None, "authorizedUser": tokens.get("userId") if valid_binding else None, "pending": len(data.get("pending", {}))}

    def access(self) -> tuple[str, str | None]:
        mode = self.configured.get("auth_mode", "authorization_code")
        with authorization_lock(self.root, self.provider, self.profile_name):
            if mode == "client_credentials":
                key = (self.provider, self.profile_name)
                token = _CLIENT_TOKENS.get(key, {})
                if token.get("expiresAt", 0) <= time.time() + 60:
                    client_id, secret = self.app_credentials()
                    token = self._token_state(self._token_request({"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret}))
                    _CLIENT_TOKENS[key] = token
                return token["accessToken"], token.get("userId")
            if self._uncertain_marker().exists():
                raise MercadoError("token_rotation_uncertain", "A rotação anterior ficou incerta; autorize novamente.")
            data = self._read()
            token = data.get("tokens")
            if not isinstance(token, dict) or token.get("binding") != self.binding() or not token.get("refreshToken"):
                raise MercadoError("oauth_required", "Autorize novamente este perfil Mercado.")
            if token.get("expiresEpoch", 0) <= time.time() + 60:
                client_id, secret = self.app_credentials()
                refreshed = self._token_request({"grant_type": "refresh_token", "client_id": client_id, "client_secret": secret, "refresh_token": token["refreshToken"]})
                data["tokens"] = self._token_state(refreshed, token.get("userId"))
                self._save(data, rotation=True)
                token = data["tokens"]
            return token["accessToken"], token.get("userId")

    def _token_request(self, fields: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(ENDPOINTS[self.provider][1], data=urllib.parse.urlencode(fields).encode(), headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.defaults["timeout_seconds"]) as response:
                raw = response.read(self.defaults["max_response_bytes"] + 1)
        except urllib.error.HTTPError as error:
            raise MercadoError("oauth_revoked" if error.code in {400, 401} else "network_error", "O provedor recusou a autorização ou renovação OAuth.", httpStatus=error.code) from error
        except (OSError, TimeoutError) as error:
            raise MercadoError("network_error", "Falha de rede durante a autorização Mercado.") from error
        if len(raw) > self.defaults["max_response_bytes"]:
            raise MercadoError("network_error", "Resposta OAuth excedeu o limite permitido.")
        try:
            data = json.loads(raw.decode("utf-8"), parse_float=str)
        except (UnicodeDecodeError, ValueError) as error:
            raise MercadoError("network_error", "O provedor retornou resposta OAuth inválida.") from error
        if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
            raise MercadoError("oauth_revoked", "O provedor não retornou token de acesso válido.")
        return data

    def _token_state(self, token: dict[str, Any], previous_user: str | None = None) -> dict[str, Any]:
        expires = time.time() + max(1, int(token.get("expires_in", 21600)))
        user = token.get("user_id", previous_user)
        return {"accessToken": token["access_token"], "refreshToken": token.get("refresh_token"), "tokenType": token.get("token_type", "Bearer"), "expiresEpoch": expires, "expiresAt": _utc(expires), "userId": str(user) if user is not None else None, "scopes": token.get("scope", self.application["scopes"]), "binding": self.binding()}
