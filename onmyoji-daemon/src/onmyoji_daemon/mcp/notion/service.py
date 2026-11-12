"""Cliente seguro e determinístico da API pública do Notion."""
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
import time
from typing import Any, Mapping
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS: dict[str, tuple[str, str, bool]] = {
    "blocks.append": ("PATCH", "/v1/blocks/{block_id}/children", True), "blocks.get": ("GET", "/v1/blocks/{block_id}", False),
    "blocks.children": ("GET", "/v1/blocks/{block_id}/children", False), "blocks.update": ("PATCH", "/v1/blocks/{block_id}", True), "blocks.delete": ("DELETE", "/v1/blocks/{block_id}", True),
    "pages.create": ("POST", "/v1/pages", True), "pages.get": ("GET", "/v1/pages/{page_id}", False), "pages.move": ("POST", "/v1/pages/{page_id}/move", True), "pages.update": ("PATCH", "/v1/pages/{page_id}", True), "pages.trash": ("PATCH", "/v1/pages/{page_id}", True), "pages.property": ("GET", "/v1/pages/{page_id}/properties/{property_id}", False),
    "pages.markdown.get": ("GET", "/v1/pages/{page_id}/markdown", False), "pages.markdown.update": ("PATCH", "/v1/pages/{page_id}/markdown", True),
    "databases.create": ("POST", "/v1/databases", True), "databases.get": ("GET", "/v1/databases/{database_id}", False), "databases.update": ("PATCH", "/v1/databases/{database_id}", True),
    "data_sources.create": ("POST", "/v1/data_sources", True), "data_sources.get": ("GET", "/v1/data_sources/{data_source_id}", False), "data_sources.update": ("PATCH", "/v1/data_sources/{data_source_id}", True), "data_sources.properties.update": ("PATCH", "/v1/data_sources/{data_source_id}/properties", True), "data_sources.query": ("POST", "/v1/data_sources/{data_source_id}/query", False), "data_sources.templates": ("GET", "/v1/data_sources/{data_source_id}/templates", False),
    "comments.create": ("POST", "/v1/comments", True), "comments.get": ("GET", "/v1/comments/{comment_id}", False), "comments.list": ("GET", "/v1/comments", False), "comments.update": ("PATCH", "/v1/comments/{comment_id}", True), "comments.delete": ("DELETE", "/v1/comments/{comment_id}", True),
    "views.create": ("POST", "/v1/views", True), "views.get": ("GET", "/v1/views/{view_id}", False), "views.list": ("GET", "/v1/databases/{database_id}/views", False), "views.update": ("PATCH", "/v1/views/{view_id}", True), "views.delete": ("DELETE", "/v1/views/{view_id}", True), "views.query.create": ("POST", "/v1/views/{view_id}/query", True), "views.query.get": ("GET", "/v1/views/{view_id}/query/{query_id}", False), "views.query.delete": ("DELETE", "/v1/views/{view_id}/query/{query_id}", True),
    "search": ("POST", "/v1/search", False), "users.list": ("GET", "/v1/users", False), "users.get": ("GET", "/v1/users/{user_id}", False), "users.self": ("GET", "/v1/users/me", False), "custom_emojis.list": ("GET", "/v1/emojis", False),
    "file_uploads.create": ("POST", "/v1/file_uploads", True), "file_uploads.send": ("POST", "/v1/file_uploads/{file_upload_id}/send", True), "file_uploads.complete": ("POST", "/v1/file_uploads/{file_upload_id}/complete", True), "file_uploads.get": ("GET", "/v1/file_uploads/{file_upload_id}", False), "file_uploads.list": ("GET", "/v1/file_uploads", False),
    "async_tasks.get": ("GET", "/v1/tasks/{task_id}", False), "oauth.introspect": ("POST", "/v1/oauth/introspect", False), "oauth.revoke": ("POST", "/v1/oauth/revoke", True),
}

SENSITIVE_KEYS = {"token", "access_token", "refresh_token", "client_secret", "password", "secret"}


class NotionError(ValueError):
    """Erro de domínio sanitizado da integração Notion."""

    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def sanitize(value: Any) -> Any:
    """Remove segredos conhecidos antes de publicar uma resposta ao agente."""
    if isinstance(value, Mapping):
        return {key: "[REDACTED]" if str(key).lower() in SENSITIVE_KEYS else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str) and any(marker in value.lower() for marker in ("token=", "access_token=", "client_secret=")):
        return "[REDACTED_URL]"
    return value


class NotionService:
    """Executa operações Notion com token interno e uploads confinados."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "notion.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Confirma a integração consultando o usuário associado ao token."""
        data = self.invoke(profile_name, "users.self", {}).get("data")
        if not isinstance(data, Mapping):
            raise NotionError("invalid_response", "Notion não confirmou o usuário da integração.")
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Valida e executa uma operação publicada pelo MCP."""
        if not isinstance(arguments, Mapping):
            raise NotionError("invalid_request", "Os argumentos devem ser um objeto JSON.")
        specification = OPERATIONS.get(operation)
        if specification is None:
            raise NotionError("unsupported_operation", "Operação Notion não suportada.")
        method, template, writes = specification
        if writes and arguments.get("confirm") is not True:
            raise NotionError("confirmation_required", "Operações de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        params = arguments.get("params", {})
        query = arguments.get("query", {})
        if not isinstance(params, Mapping) or not isinstance(query, Mapping):
            raise NotionError("invalid_request", "params e query devem ser objetos JSON.")
        path = self._fill_path(template, params)
        body = arguments.get("body")
        data: bytes | None = None
        content_type: str | None = None
        if operation == "file_uploads.send":
            file_path = str(body.get("file_path", "")) if isinstance(body, Mapping) else ""
            source = self._allowed_file(profile, file_path)
            data, content_type = self._multipart_body(source)
        elif body is not None:
            try:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            except (TypeError, ValueError) as error:
                raise NotionError("invalid_request", "body deve conter JSON válido.") from error
            content_type = "application/json"
        return {"data": self._request(profile, defaults, method, path, query, body, data, content_type, bool(arguments.get("paginate", False)))}

    def _profile(self, profile_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        import tomllib
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise NotionError("config_not_found", "Configuração Notion MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise NotionError("invalid_config", "Configuração Notion MCP inválida.") from error
        if document.get("schema_version") != 1:
            raise NotionError("unsupported_config", "Versão da configuração Notion MCP não suportada.")
        profile = document.get("profiles", {}).get(profile_name)
        defaults = document.get("defaults", {})
        if not isinstance(profile, dict) or not isinstance(defaults, dict):
            raise NotionError("profile_not_found", "Perfil Notion não encontrado.")
        if profile.get("enabled") is not True:
            raise NotionError("profile_disabled", "Perfil Notion está desabilitado.")
        if defaults.get("api_base") != "https://api.notion.com" or defaults.get("notion_version") != "2026-03-11":
            raise NotionError("invalid_config", "Endpoint ou versão Notion não suportados.")
        try:
            timeout = float(defaults.get("timeout_seconds", 30))
            retries = int(defaults.get("max_retries", 2))
            page_size = int(defaults.get("page_size", 100))
        except (TypeError, ValueError) as error:
            raise NotionError("invalid_config", "Configuração operacional Notion inválida.") from error
        if timeout <= 0 or retries < 0 or not 1 <= page_size <= 100:
            raise NotionError("invalid_config", "Configuração operacional Notion inválida.")
        if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "credential_field")) or profile["credential_field"] not in {"password", "notes"}:
            raise NotionError("invalid_config", "Referência de credencial Notion incompleta.")
        roots = profile.get("readable_roots")
        if not isinstance(roots, list) or not all(isinstance(value, str) and value.strip() for value in roots):
            raise NotionError("invalid_config", "Raízes de upload Notion inválidas.")
        return profile, defaults

    @staticmethod
    def _fill_path(template: str, params: Mapping[str, Any]) -> str:
        for part in template.split("/"):
            if part.startswith("{") and part.endswith("}"):
                key = part[1:-1]
                value = params.get(key)
                if value is None or isinstance(value, (dict, list)):
                    raise NotionError("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
                template = template.replace("{" + key + "}", quote(str(value), safe=""))
        return template

    @staticmethod
    def _allowed_file(profile: Mapping[str, Any], file_path: str) -> Path:
        if not file_path:
            raise NotionError("file_not_found", "file_uploads.send exige body.file_path.")
        try:
            source = Path(file_path).expanduser().resolve(strict=True)
        except OSError as error:
            raise NotionError("file_not_found", "Arquivo de upload Notion não encontrado.") from error
        roots = profile.get("readable_roots", [])
        if not isinstance(roots, list):
            raise NotionError("invalid_config", "Raízes de upload Notion inválidas.")
        allowed = False
        for configured in roots:
            try:
                root = Path(configured).expanduser().resolve(strict=True)
            except OSError:
                continue
            if root.is_dir() and (source == root or root in source.parents):
                allowed = True
                break
        if not allowed or not source.is_file():
            raise NotionError("file_denied", "Arquivo fora das raízes permitidas do perfil Notion.")
        return source

    @staticmethod
    def _multipart_body(file_path: Path) -> tuple[bytes, str]:
        boundary = "----OnmyojiNotion" + uuid.uuid4().hex
        name = file_path.name.replace('"', "")
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        data = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: {content_type}\r\n\r\n').encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        return data, f"multipart/form-data; boundary={boundary}"

    def _request(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], method: str, path: str, query: Mapping[str, Any], body: Any, data: bytes | None, content_type: str | None, paginate: bool) -> Any:
        if defaults.get("api_base") != "https://api.notion.com" or defaults.get("notion_version") != "2026-03-11":
            raise NotionError("invalid_config", "Endpoint ou versão Notion não suportados.")
        try:
            timeout = float(defaults.get("timeout_seconds", 30))
            retries = int(defaults.get("max_retries", 2))
            page_size = int(defaults.get("page_size", 100))
        except (TypeError, ValueError) as error:
            raise NotionError("invalid_config", "Configuração operacional Notion inválida.") from error
        if timeout <= 0 or retries < 0 or not 1 <= page_size <= 100:
            raise NotionError("invalid_config", "Configuração operacional Notion inválida.")
        credential_profile, entry, field = profile.get("credential_profile"), profile.get("credential_entry_path"), profile.get("credential_field")
        if not all(isinstance(value, str) and value for value in (credential_profile, entry, field)):
            raise NotionError("invalid_config", "Referência de credencial Notion incompleta.")
        try:
            token = self.credentials.resolve(credential_profile, entry, field)
        except CredentialError as error:
            raise NotionError("credential_" + error.code, "Não foi possível obter o token Notion.") from error
        headers = {"Authorization": "Bearer " + token, "Notion-Version": "2026-03-11", "Accept": "application/json"}
        del token
        if content_type:
            headers["Content-Type"] = content_type
        collected: list[Any] = []
        cursor: str | None = None
        while True:
            request_query = {key: value for key, value in query.items() if value is not None}
            request_data = data
            if cursor:
                if method == "GET":
                    request_query["start_cursor"] = cursor
                else:
                    request_body = dict(body or {})
                    request_body["start_cursor"] = cursor
                    request_data = json.dumps(request_body, ensure_ascii=False).encode("utf-8")
            target = "https://api.notion.com" + path
            if request_query:
                target += "?" + urlencode(request_query, doseq=True)
            response = self._send(Request(target, data=request_data, headers=headers, method=method), timeout, retries)
            if not paginate or not isinstance(response, Mapping):
                return response
            results = response.get("results", [])
            if isinstance(results, list):
                collected.extend(results)
            if not response.get("has_more") or not response.get("next_cursor"):
                return dict(response, results=collected, has_more=False, next_cursor=None)
            cursor = str(response["next_cursor"])

    @staticmethod
    def _send(request: Request, timeout: float, retries: int) -> Any:
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    try:
                        return sanitize(json.loads(raw))
                    except json.JSONDecodeError:
                        return {"raw_base64": base64.b64encode(raw).decode("ascii")}
            except HTTPError as error:
                if error.code in {429, 500, 502, 503, 504} and attempt < retries:
                    try:
                        delay = min(float(error.headers.get("Retry-After", "1")), 10)
                    except (TypeError, ValueError):
                        delay = 1
                    time.sleep(delay)
                    continue
                raise NotionError("notion_http_error", f"Notion retornou HTTP {error.code}.", error.code) from error
            except (URLError, TimeoutError) as error:
                if attempt < retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                raise NotionError("network_error", "Não foi possível conectar à API Notion.") from error
        raise NotionError("request_failed", "A requisição Notion não foi concluída.")
