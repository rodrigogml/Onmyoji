"""Sessões OAuth das integrações MCP com callback estável e adaptador fechado."""
from __future__ import annotations

import hashlib
import os
import json
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable


class OAuthError(ValueError):
    """Erro funcional sanitizado, sem valores recebidos de provedores."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


def ensure(condition: bool, message: str) -> None:
    if not condition:
        raise OAuthError(message)


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


BTG_INTEGRATION = "btg"
BTG_CALLBACK_PATH = "/oauth/mcp/btg/callback"


class McpOAuthBroker:
    def __init__(self, root: Path, public_origin: Callable[[], str]):
        self.root = root.resolve()
        self.public_origin = public_origin
        directory = self.root / "configs" / "daemon" / "services" / "mini-apps" / "oauth"
        ensure(directory.resolve().is_relative_to(self.root / "configs"), "Estado OAuth deve permanecer em configs.")
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "sessions.sqlite3"
        self.lock = threading.RLock()
        with self.connection() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS authorization (
              id TEXT PRIMARY KEY, integration TEXT NOT NULL, profile TEXT NOT NULL,
              stateHash TEXT NOT NULL UNIQUE, binding TEXT NOT NULL,
              issuer TEXT NOT NULL, redirectUri TEXT NOT NULL,
              status TEXT NOT NULL, createdAt REAL NOT NULL, expiresAt REAL NOT NULL,
              processingAt REAL, completedAt REAL, errorCode TEXT, issuerRequired INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX IF NOT EXISTS authorization_active ON authorization(integration,profile)
              WHERE status IN ('creating','awaiting','processing');
            """)

    def connection(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def adapter(self, integration: str, profile: str, action: str, **values: Any) -> dict[str, Any]:
        ensure(integration == BTG_INTEGRATION, "Integração OAuth MCP inválida")
        ensure(isinstance(profile, str) and bool(re.fullmatch(r"[A-Za-z0-9_-]+", profile)), "Perfil OAuth inválido")
        ensure(action in {"describe", "start", "complete", "cancel"}, "Ação OAuth inválida")
        from .runtime import mcp_runtime_python
        command = [str(mcp_runtime_python(self.root)), "-m", "onmyoji_daemon.mcp.btg.oauth_adapter", "--onmyoji-root", str(self.root)]
        request = {"version": 1, "action": action, "profile": profile, **values}
        try:
            environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent), "PYTHONUTF8": "1"}
            result = subprocess.run(command, input=json.dumps(request), text=True, encoding="utf-8", capture_output=True, timeout=180, env=environment)
            payload = json.loads(result.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise OAuthError("Adaptador OAuth indisponível; verifique o perfil") from error
        if not (result.returncode == 0 and isinstance(payload, dict) and payload.get("ok") is True and isinstance(payload.get("data"), dict)):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            code = error.get("code") if isinstance(error, dict) else None
            raise OAuthError("Adaptador OAuth recusou a operação" + (" (" + str(code) + ")" if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code) else ""), code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code) else None)
        return payload["data"]

    def describe(self, integration: str, profile: str) -> dict[str, Any]:
        ensure(integration == BTG_INTEGRATION, "Integração OAuth MCP inválida")
        origin = self.public_origin()
        parsed = urllib.parse.urlsplit(origin)
        ensure(parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment, "Origem OAuth pública inválida")
        metadata = self.adapter(integration, profile, "describe")
        fields = {"issuer", "authorizationEndpoint", "redirectUri", "binding"}
        location = "storage_root"
        required = fields | {location}
        ensure(required <= set(metadata) <= required | {"requireIssuer"}, "Metadados do adaptador OAuth inválidos")
        ensure(all(isinstance(metadata[k], str) and bool(metadata[k]) for k in required) and type(metadata.get("requireIssuer", False)) is bool, "Metadados OAuth incompletos")
        for field in ("issuer", "authorizationEndpoint"):
            value = urllib.parse.urlsplit(metadata[field])
            ensure(value.scheme == "https" and bool(value.hostname) and not value.username and not value.password and not value.fragment, "URI OAuth inválida")
        declared = Path(metadata[location]).resolve()
        expected = self.root / "configs" / "mcp" / "btg-empresas"
        ensure(declared == expected.resolve(), "Estado OAuth BTG MCP inválido")
        callback = origin.rstrip("/") + BTG_CALLBACK_PATH
        return {**metadata, "callbackUrl": callback, "redirectMatches": callback == metadata["redirectUri"]}

    @staticmethod
    def expire(con: sqlite3.Connection) -> None:
        stamp = time.time()
        con.execute("UPDATE authorization SET status='expired',completedAt=? WHERE status IN ('creating','awaiting') AND expiresAt<=?", (stamp, stamp))
        con.execute("UPDATE authorization SET status='interrupted',completedAt=?,errorCode='interrupted' WHERE status='processing' AND processingAt<?", (stamp, stamp - 300))

    def info(self, integration: str, profile: str) -> dict[str, Any]:
        data = self.describe(integration, profile)
        return {"integration": integration, "profile": profile, "callbackUrl": data["callbackUrl"], "configuredRedirectUri": data["redirectUri"], "redirectMatches": data["redirectMatches"]}

    def start(self, integration: str, profile: str, confirm: bool) -> dict[str, Any]:
        ensure(confirm is True, "Autorização explícita exigida para iniciar OAuth")
        data = self.describe(integration, profile)
        ensure(data["redirectMatches"], "Cadastre a URL de callback no provedor e no perfil antes de iniciar")
        identifier, state, stamp = uuid.uuid4().hex, secrets.token_urlsafe(32), time.time()
        with self.lock, self.connection() as con:
            self.expire(con)
            try:
                con.execute("INSERT INTO authorization(id,integration,profile,stateHash,binding,issuer,redirectUri,status,createdAt,expiresAt,issuerRequired) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (identifier, integration, profile, hash_value(state), data["binding"], data["issuer"], data["redirectUri"], "creating", stamp, stamp + 600, int(data.get("requireIssuer", False))))
            except sqlite3.IntegrityError as error:
                raise OAuthError("Existe autorização ativa para este perfil; conclua ou cancele") from error
        try:
            result = self.adapter(integration, profile, "start", state=state, sessionId=identifier, redirectUri=data["redirectUri"])
            url = result.get("authorizationUrl")
            ensure(isinstance(url, str) and len(url) <= 16000, "URL de autorização inválida")
            parsed = urllib.parse.urlsplit(url)
            endpoint = urllib.parse.urlsplit(data["authorizationEndpoint"])
            query = urllib.parse.parse_qs(parsed.query)
            ensure(not {"client_secret", "access_token", "refresh_token", "code", "code_verifier"} & set(query), "URL de autorização contém dados secretos ou retorno OAuth")
            ensure((parsed.scheme, parsed.netloc, parsed.path) == (endpoint.scheme, endpoint.netloc, endpoint.path) and not parsed.fragment, "URL de autorização diverge do provedor")
            ensure(query.get("state") == [state] and query.get("redirect_uri") == [data["redirectUri"]] and query.get("response_type") == ["code"], "URL OAuth sem vínculo correto com sessão/callback")
            ttl = result.get("expiresIn")
            ensure(type(ttl) is int and 30 <= ttl <= 600, "Expiração OAuth inválida")
            with self.lock, self.connection() as con:
                changed = con.execute("UPDATE authorization SET status='awaiting',expiresAt=? WHERE id=? AND status='creating'", (time.time() + ttl, identifier)).rowcount
                ensure(changed == 1, "Autorização foi encerrada durante a criação")
            return {"sessionId": identifier, "authorizationUrl": url, "callbackUrl": data["callbackUrl"], "expiresIn": ttl, "status": "awaiting"}
        except Exception as error:
            try:
                self.adapter(integration, profile, "cancel", sessionId=identifier)
            except Exception:
                pass
            self.finish(identifier, "failed", "start_failed")
            raise

    def finish(self, identifier: str, status: str, error: str | None = None) -> None:
        with self.lock, self.connection() as con:
            con.execute("UPDATE authorization SET status=?,errorCode=?,completedAt=? WHERE id=?", (status, error, time.time(), identifier))

    def status(self, identifier: str, integration: str | None = None, profile: str | None = None) -> dict[str, Any]:
        with self.lock, self.connection() as con:
            self.expire(con)
            row = con.execute("SELECT * FROM authorization WHERE id=?", (identifier,)).fetchone()
            ensure(row is not None and (integration is None or row["integration"] == integration) and (profile is None or row["profile"] == profile), "Sessão OAuth não encontrada neste contexto")
            return {key: row[key] for key in ("id", "integration", "profile", "status", "createdAt", "expiresAt", "completedAt", "errorCode")}

    def active(self, integration: str, profile: str) -> dict[str, Any] | None:
        """Retorne a autorização ativa para que o setup possa oferecer substituição."""
        with self.lock, self.connection() as con:
            self.expire(con)
            row = con.execute("SELECT * FROM authorization WHERE integration=? AND profile=? AND status IN ('creating','awaiting','processing') ORDER BY createdAt DESC LIMIT 1", (integration, profile)).fetchone()
            if row is None:
                return None
            return {key: row[key] for key in ("id", "integration", "profile", "status", "createdAt", "expiresAt")}

    def cancel_active(self, integration: str, profile: str, confirm: bool) -> dict[str, Any]:
        """Cancele a autorização ativa do perfil após confirmação explícita."""
        active = self.active(integration, profile)
        ensure(active is not None, "Não existe autorização ativa para este perfil")
        return self.cancel(active["id"], confirm, integration, profile)

    def cancel(self, identifier: str, confirm: bool, integration: str | None = None, profile: str | None = None) -> dict[str, Any]:
        ensure(confirm is True, "Confirme o cancelamento OAuth")
        with self.lock, self.connection() as con:
            self.expire(con)
            row = con.execute("SELECT * FROM authorization WHERE id=?", (identifier,)).fetchone()
            ensure(row is not None and (integration is None or row["integration"] == integration) and (profile is None or row["profile"] == profile), "Sessão OAuth não encontrada neste contexto")
            ensure(row["status"] == "awaiting", "Sessão não pode ser cancelada nesta situação")
            changed = con.execute("UPDATE authorization SET status='cancelled',completedAt=? WHERE id=? AND status='awaiting'", (time.time(), identifier)).rowcount
            ensure(changed == 1, "Sessão mudou durante o cancelamento")
        try:
            self.adapter(row["integration"], row["profile"], "cancel", sessionId=identifier)
        except Exception:
            self.finish(identifier, "cancelled", "cancellation_cleanup_failed")
            raise
        return self.status(identifier, integration, profile)

    def callback(self, integration: str, query: dict[str, list[str]]) -> str:
        ensure(len(query.get("state", [])) == 1, "Callback OAuth sem state único")
        state = query["state"][0]
        ensure(isinstance(state, str) and 20 <= len(state) <= 200, "State OAuth inválido")
        with self.lock, self.connection() as con:
            self.expire(con)
            row = con.execute("SELECT * FROM authorization WHERE stateHash=? AND integration=?", (hash_value(state), integration)).fetchone()
            ensure(row is not None and row["status"] == "awaiting", "Callback OAuth expirado, consumido ou desconhecido")
            code, error, issuer = query.get("code", []), query.get("error", []), query.get("iss", [])
            ensure(len(issuer) <= 1 and (not issuer or issuer == [row["issuer"]]), "Emissor OAuth divergente")
            ensure(not row["issuerRequired"] or issuer == [row["issuer"]] or len(error) == 1 and not code, "Emissor OAuth ausente")
            ensure((len(code) == 1 and not error and 0 < len(code[0]) <= 4096) or (len(error) == 1 and not code and 0 < len(error[0]) <= 200), "Callback OAuth inválido")
            changed = con.execute("UPDATE authorization SET status='processing',processingAt=? WHERE id=? AND status='awaiting'", (time.time(), row["id"])).rowcount
            ensure(changed == 1, "Callback OAuth já foi consumido")
        try:
            data = self.describe(row["integration"], row["profile"])
            ensure(data["binding"] == row["binding"] and data["redirectUri"] == row["redirectUri"] and data["redirectMatches"] and data["issuer"] == row["issuer"], "Perfil OAuth mudou durante a autorização")
            if error:
                self.adapter(row["integration"], row["profile"], "cancel", sessionId=row["id"])
                self.finish(row["id"], "denied" if error[0] == "access_denied" else "failed", "provider_denied" if error[0] == "access_denied" else "provider_error")
            else:
                result = self.adapter(row["integration"], row["profile"], "complete", code=code[0], state=state, sessionId=row["id"], redirectUri=row["redirectUri"])
                ensure(result.get("authenticated") is True, "Adaptador não confirmou autorização")
                self.finish(row["id"], "completed")
        except Exception as error:
            try:
                self.adapter(row["integration"], row["profile"], "cancel", sessionId=row["id"])
            except Exception:
                pass
            code = getattr(error, "code", None)
            self.finish(row["id"], "failed", code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code) else "completion_failed")
        return row["id"]

    def result(self, identifier: str) -> str:
        data = self.status(identifier)
        labels = {"completed": "Autorização concluída. Você pode voltar ao chat.", "denied": "A autorização foi recusada. Você pode voltar ao chat.", "expired": "Este link de autorização expirou.", "cancelled": "A autorização foi cancelada.", "failed": "Não foi possível concluir a autorização. Consulte o agente no chat.", "interrupted": "A autorização foi interrompida. Consulte o agente no chat."}
        message = labels.get(data["status"], "A autorização está em andamento. Consulte o agente no chat.")
        code = data.get("errorCode")
        if data["status"] == "failed" and isinstance(code, str) and re.fullmatch(r"[a-z0-9_]+", code):
            message += " Código técnico: " + code + "."
        return message
