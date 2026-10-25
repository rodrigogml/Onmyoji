"""Serviço Todoist de leitura para a primeira integração MCP.

O serviço não conhece a implementação do cofre nem retorna respostas brutas da
API. Ele obtém o token por um perfil de credencial MCP e oferece casos de uso
explícitos, começando por :meth:`TodoistService.list_tasks`.
"""
from __future__ import annotations

import json
from pathlib import Path
import time
import tomllib
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..credentials import CredentialError, KeePassCredentialResolver


class TodoistError(ValueError):
    """Erro funcional sanitizado da integração Todoist."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def todoist_config_path(onmyoji_root: Path) -> Path:
    """Retorna a configuração privada da integração Todoist para o MCP."""
    return onmyoji_root.resolve() / "configs" / "mcp" / "todoist.toml"


class TodoistService:
    """Executa consultas Todoist autorizadas pelos perfis locais do MCP.

    A primeira versão deliberadamente contém apenas operações de leitura. Cada
    operação requer autorização explícita no perfil, o que evita que uma futura
    expansão da API amplie permissões de perfis existentes por acidente.
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
        self._require_operation(profile, "todoist.list_tasks")
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

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        """Confirma a credencial do perfil sem devolver token ou dados da conta."""
        profile, defaults = self._profile(profile_name)
        self._require_operation(profile, "todoist.test_connection")
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

    @staticmethod
    def _require_operation(profile: Mapping[str, Any], operation: str) -> None:
        allowed = profile.get("allowed_operations")
        if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
            raise TodoistError("invalid_config", "Operações permitidas do perfil Todoist são inválidas.")
        if operation not in allowed:
            raise TodoistError("operation_denied", "Operação Todoist não permitida para este perfil.")

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

    def _request_json(self, path: str, token: str, defaults: Mapping[str, Any]) -> Any:
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
        request = Request(base.rstrip("/") + path, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, method="GET")
        for attempt in range(retries + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                if error.code not in {429, 500, 502, 503, 504} or attempt == retries:
                    raise TodoistError("todoist_http_error", f"Todoist retornou HTTP {error.code}.") from error
            except (URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt == retries:
                    raise TodoistError("network_error", "Não foi possível consultar a API Todoist.") from error
            time.sleep(0.25 * (attempt + 1))
        raise TodoistError("network_error", "Não foi possível consultar a API Todoist.")

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
