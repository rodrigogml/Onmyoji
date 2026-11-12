"""Cliente HTTP seguro e paginador das APIs Mercado."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .exception import MercadoError
from .oauth import MercadoOAuth


BASE_URLS = {"mercadolivre": "https://api.mercadolibre.com", "mercadopago": "https://api.mercadopago.com"}
_CURSOR_KEY = secrets.token_bytes(32)


def observed_at() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def encode_cursor(tool: str, profile: str, query: dict[str, Any], offset: int) -> str:
    payload = json.dumps({"tool": tool, "profile": profile, "query": query, "offset": offset}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    signature = hmac.new(_CURSOR_KEY, payload, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")


def decode_cursor(value: str, tool: str, profile: str, query: dict[str, Any]) -> int:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.new(_CURSOR_KEY, payload, hashlib.sha256).digest()):
            raise ValueError
        data = json.loads(payload)
        if data != {"tool": tool, "profile": profile, "query": query, "offset": data.get("offset")} or type(data["offset"]) is not int or data["offset"] < 0:
            raise ValueError
        return data["offset"]
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise MercadoError("invalid_request", "Cursor inválido, adulterado ou incompatível com a consulta.") from error


class MercadoClient:
    """Executa apenas paths definidos pelo serviço, com limites e erros sanitizados."""

    def __init__(self, root: Path, provider: str, profile: str):
        self.provider = provider
        self.profile = profile
        self.oauth = MercadoOAuth(root, provider, profile)
        self.defaults = self.oauth.defaults

    def request(self, method: str, path: str, *, query: dict[str, Any] | None = None, body: dict[str, Any] | None = None, retry: bool | None = None, caller_id: bool = False) -> Any:
        raw = self.request_bytes(method, path, query=query, body=body, retry=retry, caller_id=caller_id)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8-sig"), parse_float=str)
        except (UnicodeDecodeError, ValueError) as error:
            raise MercadoError("network_error", "O provedor retornou JSON inválido.") from error

    def request_bytes(self, method: str, path: str, *, query: dict[str, Any] | None = None, body: dict[str, Any] | None = None, retry: bool | None = None, max_bytes: int | None = None, caller_id: bool = False) -> bytes:
        if method not in {"GET", "POST", "PUT", "DELETE"} or not path.startswith("/") or "://" in path or "?" in path:
            raise MercadoError("invalid_request", "Endpoint Mercado interno inválido.")
        token, user_id = self.oauth.access()
        url = BASE_URLS[self.provider] + path
        filtered = {key: value for key, value in (query or {}).items() if value is not None}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered, doseq=True)
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode() if body is not None else None
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json", "User-Agent": "Onmyoji-Mercado-MCP/1"}
        if caller_id:
            if not isinstance(user_id, str) or not user_id.isascii() or not user_id.isdigit():
                raise MercadoError("oauth_required", "A autorização não informa o usuário exigido pela operação.")
            headers["X-Caller-Id"] = user_id
        if payload is not None:
            headers["Content-Type"] = "application/json"
        attempts = self.defaults["max_retries"] if (retry if retry is not None else method == "GET") else 0
        limit = max_bytes or self.defaults["max_response_bytes"]
        for attempt in range(attempts + 1):
            request = urllib.request.Request(url, data=payload, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.defaults["timeout_seconds"]) as response:
                    data = response.read(limit + 1)
                if len(data) > limit:
                    raise MercadoError("network_error", "Resposta do provedor excedeu o limite permitido.")
                return data
            except urllib.error.HTTPError as error:
                code = {401: "oauth_revoked", 403: "provider_permission_denied", 404: "remote_not_found", 409: "remote_conflict", 429: "provider_rate_limited"}.get(error.code, "network_error")
                if attempt < attempts and (error.code == 429 or error.code >= 500):
                    delay = min(5.0, self._retry_after(error.headers.get("Retry-After"), attempt))
                    time.sleep(delay)
                    continue
                request_id = error.headers.get("X-Request-Id") or error.headers.get("X-Correlation-Id")
                raise MercadoError(code, "A API Mercado recusou ou não concluiu a solicitação.", httpStatus=error.code, requestId=request_id) from error
            except MercadoError:
                raise
            except (OSError, TimeoutError) as error:
                if attempt < attempts:
                    time.sleep(min(5.0, 0.25 * (2 ** attempt)))
                    continue
                raise MercadoError("network_error", "Não foi possível acessar a API Mercado.") from error
        raise MercadoError("network_error", "Não foi possível acessar a API Mercado.")

    @staticmethod
    def _retry_after(value: str | None, attempt: int) -> float:
        try:
            return max(0.0, float(value)) if value is not None else 0.25 * (2 ** attempt)
        except ValueError:
            return 0.25 * (2 ** attempt)

    def user_id(self) -> str:
        _, user_id = self.oauth.access()
        if not isinstance(user_id, str) or not user_id:
            raise MercadoError("oauth_required", "A autorização não informa o usuário vinculado; autorize novamente.")
        return user_id


def envelope(profile: str, provider: str, module: str, items: list[Any], limit: int, next_cursor: str | None) -> dict[str, Any]:
    return {"profile": profile, "provider": provider, "module": module, "source": "remote", "observedAt": observed_at(), "items": items, "page": {"limit": limit, "hasMore": next_cursor is not None, "nextCursor": next_cursor}}
