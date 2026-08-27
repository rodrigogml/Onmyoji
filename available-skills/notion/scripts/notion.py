#!/usr/bin/env python3
"""Secure JSON wrapper for the public Notion API."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import tomllib


class NotionError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class Settings:
    api_base: str
    version: str
    timeout: float
    retries: int
    page_size: int
    vault_profile: str
    vault_entry: str
    vault_field: str


def fail(code: str, message: str, status: int | None = None) -> None:
    raise NotionError(code, message, status)


def load_settings(path: str, profile_name: str) -> Settings:
    config_path = Path(path)
    if not config_path.is_file():
        fail("config_not_found", "Arquivo de configuração não encontrado.")
    try:
        document = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        fail("invalid_config", "Não foi possível ler o arquivo de configuração.")
    defaults = document.get("defaults", {})
    profiles = document.get("profiles", {})
    profile = profiles.get(profile_name)
    if not isinstance(defaults, Mapping) or not isinstance(profile, Mapping):
        fail("missing_config", "O perfil solicitado não foi encontrado.")
    values = {**defaults, **profile}
    try:
        timeout = float(values.get("timeout_seconds", 30))
        retries = int(values.get("max_retries", 2))
        page_size = int(values.get("page_size", 100))
    except (TypeError, ValueError):
        fail("invalid_config", "timeout_seconds, max_retries e page_size devem ser numéricos.")
    version = str(values.get("notion_version", "2026-03-11")).strip()
    if timeout <= 0 or retries < 0 or not 1 <= page_size <= 100:
        fail("invalid_config", "Valores de timeout, retries ou page_size inválidos.")
    if version != "2026-03-11":
        fail("unsupported_version", "A versão Notion suportada é 2026-03-11.")
    api_base = str(values.get("api_base", "https://api.notion.com")).rstrip("/")
    if api_base != "https://api.notion.com":
        fail("invalid_config", "api_base deve ser https://api.notion.com.")
    vault_profile = str(values.get("vault_profile", "")).strip()
    entry = str(values.get("vault_entry_path", "")).strip()
    field = str(values.get("vault_field", "password")).strip()
    if not vault_profile or not entry or field not in {"password", "notes"}:
        fail("invalid_config", "Configuração do Vault incompleta ou campo inválido.")
    return Settings(api_base, version, timeout, retries, page_size, vault_profile, entry, field)


def read_token(settings: Settings) -> str:
    request = {"operation": "read", "path": settings.vault_entry, "field": settings.vault_field, "auth": {"mode": "configured"}}
    vault_script = Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"
    vault_config = Path(__file__).resolve().parents[3] / "configs" / "keepass.toml"
    try:
        result = subprocess.run([sys.executable, str(vault_script), "--config", str(vault_config), "--profile", settings.vault_profile], input=json.dumps(request), text=True, capture_output=True, timeout=settings.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        fail("vault_unavailable", "Não foi possível consultar o provedor KeePassVault.")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("vault_protocol_error", "O provedor KeePassVault não retornou JSON válido.")
    if result.returncode != 0 or payload.get("ok") is not True:
        fail("vault_read_failed", "Não foi possível ler o token do Vault.")
    value = payload.get("result", {}).get("value")
    if not isinstance(value, str) or not value:
        fail("vault_secret_missing", "O campo configurado no Vault está vazio.")
    return value


SENSITIVE_KEYS = {"token", "access_token", "refresh_token", "client_secret", "password", "secret"}


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and any(marker in value.lower() for marker in ("token=", "access_token=", "client_secret=")):
        return "[REDACTED_URL]"
    return value


OPERATIONS: dict[str, tuple[str, str]] = {
    "blocks.append": ("PATCH", "/v1/blocks/{block_id}/children"), "blocks.get": ("GET", "/v1/blocks/{block_id}"),
    "blocks.children": ("GET", "/v1/blocks/{block_id}/children"), "blocks.update": ("PATCH", "/v1/blocks/{block_id}"), "blocks.delete": ("DELETE", "/v1/blocks/{block_id}"),
    "pages.create": ("POST", "/v1/pages"), "pages.get": ("GET", "/v1/pages/{page_id}"), "pages.move": ("POST", "/v1/pages/{page_id}/move"), "pages.update": ("PATCH", "/v1/pages/{page_id}"), "pages.trash": ("PATCH", "/v1/pages/{page_id}"), "pages.property": ("GET", "/v1/pages/{page_id}/properties/{property_id}"),
    "pages.markdown.get": ("GET", "/v1/pages/{page_id}/markdown"), "pages.markdown.update": ("PATCH", "/v1/pages/{page_id}/markdown"),
    "databases.create": ("POST", "/v1/databases"), "databases.get": ("GET", "/v1/databases/{database_id}"), "databases.update": ("PATCH", "/v1/databases/{database_id}"),
    "data_sources.create": ("POST", "/v1/data_sources"), "data_sources.get": ("GET", "/v1/data_sources/{data_source_id}"), "data_sources.update": ("PATCH", "/v1/data_sources/{data_source_id}"), "data_sources.properties.update": ("PATCH", "/v1/data_sources/{data_source_id}/properties"), "data_sources.query": ("POST", "/v1/data_sources/{data_source_id}/query"), "data_sources.templates": ("GET", "/v1/data_sources/{data_source_id}/templates"),
    "comments.create": ("POST", "/v1/comments"), "comments.get": ("GET", "/v1/comments/{comment_id}"), "comments.list": ("GET", "/v1/comments"), "comments.update": ("PATCH", "/v1/comments/{comment_id}"), "comments.delete": ("DELETE", "/v1/comments/{comment_id}"),
    "views.create": ("POST", "/v1/views"), "views.get": ("GET", "/v1/views/{view_id}"), "views.list": ("GET", "/v1/databases/{database_id}/views"), "views.update": ("PATCH", "/v1/views/{view_id}"), "views.delete": ("DELETE", "/v1/views/{view_id}"), "views.query.create": ("POST", "/v1/views/{view_id}/query"), "views.query.get": ("GET", "/v1/views/{view_id}/query/{query_id}"), "views.query.delete": ("DELETE", "/v1/views/{view_id}/query/{query_id}"),
    "search": ("POST", "/v1/search"), "users.list": ("GET", "/v1/users"), "users.get": ("GET", "/v1/users/{user_id}"), "users.self": ("GET", "/v1/users/me"), "custom_emojis.list": ("GET", "/v1/emojis"),
    "file_uploads.create": ("POST", "/v1/file_uploads"), "file_uploads.send": ("POST", "/v1/file_uploads/{file_upload_id}/send"), "file_uploads.complete": ("POST", "/v1/file_uploads/{file_upload_id}/complete"), "file_uploads.get": ("GET", "/v1/file_uploads/{file_upload_id}"), "file_uploads.list": ("GET", "/v1/file_uploads"),
    "async_tasks.get": ("GET", "/v1/tasks/{task_id}"), "oauth.introspect": ("POST", "/v1/oauth/introspect"), "oauth.revoke": ("POST", "/v1/oauth/revoke"),
}


def fill_path(template: str, params: Mapping[str, Any]) -> str:
    for part in template.split("/"):
        if part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            value = params.get(key)
            if value is None or isinstance(value, (dict, list)):
                fail("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
            template = template.replace("{" + key + "}", str(value))
    return template


def multipart_body(file_path: Path, field: str = "file") -> tuple[bytes, str]:
    boundary = "----NotionSkill" + uuid.uuid4().hex
    name = file_path.name.replace('"', "")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{name}\"\r\nContent-Type: {content_type}\r\n\r\n").encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


class Client:
    def __init__(self, settings: Settings, token: str):
        self.settings, self.token = settings, token

    def request(self, operation: str, params: Mapping[str, Any], query: Mapping[str, Any], body: Any = None, paginate: bool = False) -> Any:
        if operation not in OPERATIONS:
            fail("unsupported_operation", "Operação Notion não permitida.")
        method, template = OPERATIONS[operation]
        path = fill_path(template, params)
        if operation == "file_uploads.send":
            file_path = Path(str((body or {}).get("file_path", ""))) if isinstance(body, Mapping) else Path("")
            if not file_path.is_file():
                fail("file_not_found", "file_uploads.send exige um body.file_path existente.")
            data, content_type = multipart_body(file_path)
        else:
            data, content_type = (json.dumps(body).encode("utf-8"), "application/json") if body is not None else (None, None)
        headers = {"Authorization": f"Bearer {self.token}", "Notion-Version": self.settings.version, "Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        return self._request(method, path, query, body, data, headers, paginate)

    def _request(self, method: str, path: str, query: Mapping[str, Any], body: Any, data: bytes | None, headers: Mapping[str, str], paginate: bool) -> Any:
        collected: list[Any] = []
        cursor: str | None = None
        while True:
            request_body = body
            request_query = dict(query or {})
            if cursor:
                if method == "GET":
                    request_query["start_cursor"] = cursor
                else:
                    request_body = dict(body or {})
                    request_body["start_cursor"] = cursor
                    data = json.dumps(request_body).encode("utf-8")
            target = self.settings.api_base + path
            if request_query:
                target += "?" + urlencode({k: v for k, v in request_query.items() if v is not None}, doseq=True)
            req = Request(target, data=data, headers=dict(headers), method=method)
            response = self._send(req)
            if not paginate or not isinstance(response, Mapping):
                return response
            collected.extend(response.get("results", []))
            if not response.get("has_more"):
                return dict(response, results=collected, has_more=False, next_cursor=None)
            cursor = response.get("next_cursor")
            if not cursor:
                return dict(response, results=collected, has_more=False, next_cursor=None)

    def _send(self, request: Request) -> Any:
        for attempt in range(self.settings.retries + 1):
            try:
                with urlopen(request, timeout=self.settings.timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    try:
                        return sanitize(json.loads(raw))
                    except json.JSONDecodeError:
                        return {"raw_base64": base64.b64encode(raw).decode("ascii")}
            except HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and attempt < self.settings.retries:
                    try:
                        delay = min(float(exc.headers.get("Retry-After", "1")), 10)
                    except ValueError:
                        delay = 1
                    time.sleep(delay)
                    continue
                fail("notion_http_error", f"Notion retornou HTTP {exc.code}.", exc.code)
            except URLError:
                if attempt < self.settings.retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                fail("network_error", "Não foi possível conectar à API Notion.")
        fail("request_failed", "A requisição não foi concluída.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, Mapping) or request.get("version") != 1:
            fail("unsupported_version", "Somente requisições com version=1 são aceitas.")
        settings = load_settings(args.config, args.profile)
        client = Client(settings, read_token(settings))
        operation = request.get("operation")
        data = client.request(operation, request.get("params", {}), request.get("query", {}), request.get("body"), bool(request.get("paginate", False)))
        print(json.dumps({"version": 1, "ok": True, "operation": operation, "data": data}, ensure_ascii=True))
        return 0
    except json.JSONDecodeError:
        error = {"code": "invalid_json", "message": "A entrada não contém JSON válido."}
    except NotionError as exc:
        error = {"code": exc.code, "message": exc.message}
    except Exception:
        error = {"code": "internal_error", "message": "Falha interna ao processar a solicitação."}
    print(json.dumps({"version": 1, "ok": False, "error": error}, ensure_ascii=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
