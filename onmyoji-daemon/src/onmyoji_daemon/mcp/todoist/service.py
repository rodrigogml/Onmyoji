"""Serviço da integração Todoist publicada pelo servidor MCP interno.

O serviço não conhece a implementação do cofre. Ele obtém o token por um perfil
de credencial MCP e nunca o inclui no resultado devolvido à tool.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import time
import tomllib
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS: dict[str, tuple[str, str, bool]] = {
    "get_user": ("GET", "/user", False), "get_task": ("GET", "/tasks/{task_id}", False), "create_task": ("POST", "/tasks", False), "update_task": ("POST", "/tasks/{task_id}", False), "complete_task": ("POST", "/tasks/{task_id}/close", True), "reopen_task": ("POST", "/tasks/{task_id}/reopen", False), "delete_task": ("DELETE", "/tasks/{task_id}", True), "filter_tasks": ("GET", "/tasks/filter", False),
    "list_projects": ("GET", "/projects", False), "get_project": ("GET", "/projects/{project_id}", False), "create_project": ("POST", "/projects", False), "update_project": ("POST", "/projects/{project_id}", False), "delete_project": ("DELETE", "/projects/{project_id}", True), "archive_project": ("POST", "/projects/{project_id}/archive", True), "unarchive_project": ("POST", "/projects/{project_id}/unarchive", False), "list_archived_projects": ("GET", "/projects/archived", False),
    "list_sections": ("GET", "/sections", False), "get_section": ("GET", "/sections/{section_id}", False), "create_section": ("POST", "/sections", False), "update_section": ("POST", "/sections/{section_id}", False), "delete_section": ("DELETE", "/sections/{section_id}", True), "archive_section": ("POST", "/sections/{section_id}/archive", True), "unarchive_section": ("POST", "/sections/{section_id}/unarchive", False),
    "list_labels": ("GET", "/labels", False), "get_label": ("GET", "/labels/{label_id}", False), "create_label": ("POST", "/labels", False), "update_label": ("POST", "/labels/{label_id}", False), "delete_label": ("DELETE", "/labels/{label_id}", True), "list_shared_labels": ("GET", "/labels/shared", False), "rename_shared_label": ("POST", "/labels/shared/rename", False), "remove_shared_label": ("POST", "/labels/shared/remove", True),
    "list_comments": ("GET", "/comments", False), "get_comment": ("GET", "/comments/{comment_id}", False), "create_comment": ("POST", "/comments", False), "update_comment": ("POST", "/comments/{comment_id}", False), "delete_comment": ("DELETE", "/comments/{comment_id}", True), "list_project_collaborators": ("GET", "/projects/{project_id}/collaborators", False), "list_activities": ("GET", "/activities", False),
    "list_reminders": ("GET", "/reminders", False), "get_reminder": ("GET", "/reminders/{reminder_id}", False), "create_reminder": ("POST", "/reminders", False), "update_reminder": ("POST", "/reminders/{reminder_id}", False), "delete_reminder": ("DELETE", "/reminders/{reminder_id}", True),
    "list_backups": ("GET", "/backups", False), "download_backup": ("GET", "/backups/{backup_id}", False), "get_email_settings": ("GET", "/emails", False), "get_notification_settings": ("GET", "/notifications", False), "revoke_token": ("POST", "/revoke", True),
}

UPLOAD_OPERATIONS = {"upload_file", "delete_upload"}


class TodoistError(ValueError):
    """Erro funcional sanitizado da integração Todoist."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def todoist_config_path(onmyoji_root: Path) -> Path:
    """Retorna a configuração privada da integração Todoist para o MCP."""
    return onmyoji_root.resolve() / "configs" / "mcp" / "todoist.toml"


class TodoistService:
    """Executa as operações Todoist pelos perfis locais do MCP.

    O token permanece no cofre MCP. Um perfil habilitado publica toda a
    superfície suportada da integração; operações destrutivas exigem uma
    confirmação explícita no argumento da tool.
    """

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = todoist_config_path(self.root)
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def list_tasks(self, profile_name: str) -> dict[str, list[dict[str, Any]]]:
        """Lista as tarefas ativas disponíveis para um perfil Todoist.

        Args:
            profile_name: Perfil Todoist local a ser utilizado.

        Raises:
            TodoistError: ``operation_denied`` se o perfil não autorizar a
                listagem; ou um código sanitizado para erros de credencial,
                configuração, rede, protocolo ou API.
        """
        profile, defaults = self._profile(profile_name)
        token = self._token(profile)
        tasks: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            parameters: dict[str, str | int] = {"limit": 200}
            if cursor:
                parameters["cursor"] = cursor
            payload = self._request_json("/tasks?" + urlencode(parameters), token, defaults)
            if not isinstance(payload, Mapping) or not isinstance(payload.get("results"), list):
                raise TodoistError("invalid_response", "Todoist retornou uma lista de tarefas inválida.")
            tasks.extend(self._task(item) for item in payload["results"] if isinstance(item, Mapping))
            cursor = payload.get("next_cursor")
            if cursor is None:
                return {"tasks": tasks}
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise TodoistError("invalid_response", "Todoist retornou uma paginação de tarefas inválida.")
            seen_cursors.add(cursor)

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Executa uma operação Todoist publicada pelo MCP sem expor o token."""
        profile, defaults = self._profile(profile_name)
        if operation in UPLOAD_OPERATIONS:
            return self._upload(profile, defaults, operation, arguments)
        if operation == "sync":
            return {"data": self._sync(profile, defaults, arguments)}
        spec = OPERATIONS.get(operation)
        if spec is None:
            raise TodoistError("unsupported_operation", "Operação Todoist não suportada.")
        method, template, destructive = spec
        if destructive and arguments.get("confirm") is not True:
            raise TodoistError("confirmation_required", "Confirme a operação destrutiva com confirm=true.")
        params = arguments.get("params", {})
        query = arguments.get("query", {})
        body = arguments.get("body")
        if not isinstance(params, Mapping) or not isinstance(query, Mapping) or body is not None and not isinstance(body, Mapping):
            raise TodoistError("invalid_arguments", "Parâmetros, consulta e corpo devem ser objetos.")
        path = self._endpoint(template, params)
        if query:
            path += "?" + urlencode({str(key): value for key, value in query.items() if value is not None}, doseq=True)
        data = None if body is None else json.dumps(body).encode("utf-8")
        return {"data": self._request_json(path, self._token(profile), defaults, method=method, data=data)}

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Confirma a credencial do perfil sem devolver token ou dados da conta."""
        profile, defaults = self._profile(profile_name)
        payload = self._request_json("/user", self._token(profile), defaults)
        if not isinstance(payload, Mapping) or not payload.get("id"):
            raise TodoistError("invalid_response", "Todoist não confirmou a identidade da credencial.")
        return {"profile": profile_name, "authenticated": True}

    def _profile(self, profile_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise TodoistError("invalid_profile", "Perfil Todoist inválido.")
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise TodoistError("config_not_found", "Configuração Todoist MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise TodoistError("invalid_config", "Configuração Todoist MCP inválida.") from error
        if document.get("schema_version") != 1:
            raise TodoistError("unsupported_config", "Versão da configuração Todoist MCP não suportada.")
        profile = document.get("profiles", {}).get(profile_name)
        defaults = document.get("defaults", {})
        if not isinstance(profile, dict):
            raise TodoistError("profile_not_found", "Perfil Todoist não encontrado.")
        if profile.get("enabled") is not True:
            raise TodoistError("profile_disabled", "Perfil Todoist está desabilitado.")
        if not isinstance(defaults, dict):
            raise TodoistError("invalid_config", "Configuração padrão Todoist inválida.")
        return profile, defaults

    def _token(self, profile: Mapping[str, Any]) -> str:
        credential_profile = profile.get("credential_profile")
        credential_entry = profile.get("credential_entry_path")
        credential_field = profile.get("credential_field", "password")
        if not all(isinstance(value, str) and value.strip() for value in (credential_profile, credential_entry, credential_field)):
            raise TodoistError("invalid_config", "Referência de credencial Todoist incompleta.")
        try:
            return self.credentials.resolve(credential_profile, credential_entry, credential_field)
        except CredentialError as error:
            raise TodoistError("credential_" + error.code, "Não foi possível obter a credencial Todoist.") from error

    def _request_json(self, path: str, token: str, defaults: Mapping[str, Any], *, method: str = "GET", data: bytes | None = None, content_type: str = "application/json") -> Any:
        base = defaults.get("api_base", "https://api.todoist.com/api/v1")
        if not isinstance(base, str) or not base.startswith("https://api.todoist.com/api/v1"):
            raise TodoistError("invalid_config", "Endpoint da API Todoist inválido.")
        try:
            timeout = float(defaults.get("timeout_seconds", 30))
            retries = int(defaults.get("max_retries", 2))
        except (TypeError, ValueError) as error:
            raise TodoistError("invalid_config", "Timeout ou tentativas Todoist inválidos.") from error
        if timeout <= 0 or retries < 0:
            raise TodoistError("invalid_config", "Timeout ou tentativas Todoist inválidos.")
        headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
        if data is not None: headers["Content-Type"] = content_type
        request = Request(base.rstrip("/") + path, headers=headers, data=data, method=method)
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    raw = response.read()
                    if not raw:
                        return None
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError:
                        return {"content_base64": base64.b64encode(raw).decode("ascii")}
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == retries:
                    raise TodoistError("todoist_http_error", f"Todoist retornou HTTP {error.code}.") from error
            except (URLError, TimeoutError) as error:
                if attempt == retries:
                    raise TodoistError("network_error", "Não foi possível consultar a API Todoist.") from error
            time.sleep(0.25 * (attempt + 1))
        raise TodoistError("network_error", "Não foi possível consultar a API Todoist.")

    @staticmethod
    def _endpoint(template: str, params: Mapping[str, Any]) -> str:
        path = template
        for segment in template.split("/"):
            if not (segment.startswith("{") and segment.endswith("}")):
                continue
            key = segment[1:-1]
            value = params.get(key)
            if value is None or isinstance(value, (Mapping, list)):
                raise TodoistError("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
            path = path.replace("{" + key + "}", str(value))
        return path

    def _sync(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], arguments: Mapping[str, Any]) -> Any:
        commands = arguments.get("commands")
        if not isinstance(commands, list) or any(not isinstance(command, Mapping) for command in commands):
            raise TodoistError("invalid_arguments", "commands deve ser uma lista de objetos.")
        form = urlencode({"sync_token": arguments.get("sync_token", "*"), "resource_types": json.dumps(["all"]), "commands": json.dumps(commands)}).encode("utf-8")
        return self._request_json("/sync", self._token(profile), defaults, method="POST", data=form, content_type="application/x-www-form-urlencoded")

    def _upload(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation == "delete_upload":
            upload_id = arguments.get("upload_id")
            if not isinstance(upload_id, str) or not upload_id or arguments.get("confirm") is not True:
                raise TodoistError("confirmation_required", "Informe upload_id e confirme a remoção com confirm=true.")
            return {"data": self._request_json(f"/uploads/{upload_id}", self._token(profile), defaults, method="DELETE")}
        file_path = arguments.get("file_path")
        if not isinstance(file_path, str) or not Path(file_path).is_file():
            raise TodoistError("file_not_found", "Arquivo para upload não encontrado.")
        source = Path(file_path)
        boundary = "----OnmyojiTodoist"
        content = source.read_bytes()
        name = str(arguments.get("filename") or source.name).replace('"', "")
        payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + content + f"\r\n--{boundary}--\r\n".encode())
        return {"data": self._request_json("/uploads", self._token(profile), defaults, method="POST", data=payload, content_type=f"multipart/form-data; boundary={boundary}")}

    @staticmethod
    def _task(value: Mapping[str, Any]) -> dict[str, Any]:
        """Projeta apenas campos úteis e estáveis da tarefa retornada pela API."""
        due = value.get("due")
        due_result = None
        if isinstance(due, Mapping):
            due_result = {key: due[key] for key in ("date", "string", "lang", "is_recurring", "timezone") if key in due}
        return {
            key: value[key]
            for key in ("id", "content", "description", "project_id", "section_id", "parent_id", "labels", "priority", "deadline", "duration", "is_completed", "created_at", "updated_at", "url")
            if key in value
        } | {"due": due_result}
