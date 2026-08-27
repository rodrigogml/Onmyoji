#!/usr/bin/env python3
"""JSON wrapper seguro para a API Todoist v1."""
from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

READ_PREFIXES = ("user.", "tasks.list", "tasks.get", "tasks.filter", "projects.list", "projects.get", "projects.archived", "sections.list", "sections.get", "labels.list", "labels.get", "comments.list", "comments.get", "collaborators.list", "activities.list", "reminders.list", "reminders.get", "backups.list", "backups.download", "emails.get", "notifications.get")
DESTRUCTIVE = (".delete", ".archive", ".close", ".revoke", ".remove")
SENSITIVE = {"token", "access_token", "refresh_token", "client_secret", "password", "secret"}

OPERATIONS: dict[str, tuple[str, str]] = {
    "user.get": ("GET", "/user"), "tasks.list": ("GET", "/tasks"), "tasks.get": ("GET", "/tasks/{task_id}"), "tasks.create": ("POST", "/tasks"), "tasks.update": ("POST", "/tasks/{task_id}"), "tasks.close": ("POST", "/tasks/{task_id}/close"), "tasks.reopen": ("POST", "/tasks/{task_id}/reopen"), "tasks.delete": ("DELETE", "/tasks/{task_id}"), "tasks.filter": ("GET", "/tasks/filter"),
    "projects.list": ("GET", "/projects"), "projects.get": ("GET", "/projects/{project_id}"), "projects.create": ("POST", "/projects"), "projects.update": ("POST", "/projects/{project_id}"), "projects.delete": ("DELETE", "/projects/{project_id}"), "projects.archive": ("POST", "/projects/{project_id}/archive"), "projects.unarchive": ("POST", "/projects/{project_id}/unarchive"), "projects.archived": ("GET", "/projects/archived"),
    "sections.list": ("GET", "/sections"), "sections.get": ("GET", "/sections/{section_id}"), "sections.create": ("POST", "/sections"), "sections.update": ("POST", "/sections/{section_id}"), "sections.delete": ("DELETE", "/sections/{section_id}"), "sections.archive": ("POST", "/sections/{section_id}/archive"), "sections.unarchive": ("POST", "/sections/{section_id}/unarchive"),
    "labels.list": ("GET", "/labels"), "labels.get": ("GET", "/labels/{label_id}"), "labels.create": ("POST", "/labels"), "labels.update": ("POST", "/labels/{label_id}"), "labels.delete": ("DELETE", "/labels/{label_id}"), "labels.shared.list": ("GET", "/labels/shared"), "labels.shared.rename": ("POST", "/labels/shared/rename"), "labels.shared.remove": ("POST", "/labels/shared/remove"),
    "comments.list": ("GET", "/comments"), "comments.get": ("GET", "/comments/{comment_id}"), "comments.create": ("POST", "/comments"), "comments.update": ("POST", "/comments/{comment_id}"), "comments.delete": ("DELETE", "/comments/{comment_id}"), "collaborators.list": ("GET", "/projects/{project_id}/collaborators"), "activities.list": ("GET", "/activities"), "activity.list": ("GET", "/activities"), "reminders.list": ("GET", "/reminders"), "reminders.get": ("GET", "/reminders/{reminder_id}"), "reminders.create": ("POST", "/reminders"), "reminders.update": ("POST", "/reminders/{reminder_id}"), "reminders.delete": ("DELETE", "/reminders/{reminder_id}"), "uploads.create": ("POST", "/uploads"), "uploads.delete": ("DELETE", "/uploads/{upload_id}"), "backups.list": ("GET", "/backups"), "backups.download": ("GET", "/backups/{backup_id}"), "emails.get": ("GET", "/emails"), "notifications.get": ("GET", "/notifications"), "tokens.revoke": ("POST", "/revoke"),
}


class TodoistError(Exception):
    def __init__(self, code: str, message: str): self.code, self.message = code, message


def fail(code: str, message: str) -> None: raise TodoistError(code, message)


@dataclass(frozen=True)
class Settings:
    api_base: str; timeout: float; retries: int; vault_profile: str; vault_entry: str; vault_field: str; access: str; allowed: tuple[str, ...]; attachment_roots: tuple[Path, ...]


def load_settings(path: Path, profile_name: str) -> Settings:
    try: data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: fail("config_not_found", "Arquivo de configuração não encontrado.")
    except (OSError, tomllib.TOMLDecodeError): fail("invalid_config", "Não foi possível ler a configuração Todoist.")
    profile = data.get("profiles", {}).get(profile_name)
    defaults = data.get("defaults", {})
    if not isinstance(profile, dict): fail("profile_not_found", "Perfil Todoist não encontrado.")
    try: timeout, retries = float(defaults.get("timeout_seconds", 30)), int(defaults.get("max_retries", 2))
    except (TypeError, ValueError): fail("invalid_config", "Timeout ou tentativas inválidos.")
    api_base = str(defaults.get("api_base", "https://api.todoist.com/api/v1")).rstrip("/")
    if timeout <= 0 or retries < 0 or not api_base.startswith("https://api.todoist.com/api/v1"): fail("invalid_config", "Configuração da API inválida.")
    values = [profile.get("vault_profile"), profile.get("vault_entry_path"), profile.get("vault_field", "password")]
    if not all(isinstance(value, str) and value for value in values) or values[2] not in {"password", "notes"}: fail("invalid_config", "Configuração do KeePass incompleta.")
    access = profile.get("access", "read_only")
    allowed = profile.get("allowed_operations", [])
    roots = profile.get("allowed_attachment_roots", [])
    if access not in {"read_only", "read_write"} or not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed) or not isinstance(roots, list) or not all(isinstance(item, str) for item in roots): fail("invalid_config", "Permissões do perfil inválidas.")
    return Settings(api_base, timeout, retries, values[0], values[1], values[2], access, tuple(allowed), tuple(Path(item).resolve() for item in roots))


def read_token(settings: Settings, config: Path) -> str:
    vault = Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"
    request = {"operation": "read", "path": settings.vault_entry, "field": settings.vault_field, "auth": {"mode": "configured"}}
    try: result = subprocess.run([sys.executable, str(vault), "--config", str(config.parent / "keepass.toml"), "--profile", settings.vault_profile], input=json.dumps(request), text=True, capture_output=True, timeout=settings.timeout, check=False)
    except (OSError, subprocess.TimeoutExpired): fail("vault_unavailable", "Não foi possível consultar o KeePass Vault.")
    try: payload = json.loads(result.stdout)
    except json.JSONDecodeError: fail("vault_protocol_error", "O KeePass Vault não retornou JSON válido.")
    value = payload.get("result", {}).get("value") if payload.get("ok") is True else None
    if result.returncode != 0 or not isinstance(value, str) or not value: fail("vault_read_failed", "Não foi possível obter o token no KeePass Vault.")
    return value


def sanitize(value: Any) -> Any:
    if isinstance(value, Mapping): return {key: "[REDACTED]" if str(key).casefold() in SENSITIVE else sanitize(item) for key, item in value.items()}
    if isinstance(value, list): return [sanitize(item) for item in value]
    return "[REDACTED_URL]" if isinstance(value, str) and "token=" in value.casefold() else value


def check_request(request: Mapping[str, Any], settings: Settings) -> str:
    operation = request.get("operation")
    if not isinstance(operation, str) or (operation != "sync" and operation not in OPERATIONS): fail("unsupported_operation", "Operação Todoist não permitida.")
    if settings.allowed and operation not in settings.allowed: fail("operation_denied", "Operação não permitida pelo perfil.")
    if settings.access == "read_only" and not operation.startswith(READ_PREFIXES) and operation != "sync": fail("write_denied", "O perfil Todoist permite somente leitura.")
    if operation.endswith(DESTRUCTIVE) and request.get("confirm") is not True: fail("confirmation_required", "Confirme a operação destrutiva com confirm=true.")
    if operation == "uploads.create":
        file_path = Path(str(request.get("body", {}).get("file_path", ""))).resolve()
        if not file_path.is_file(): fail("file_not_found", "Arquivo para upload não encontrado.")
        if settings.attachment_roots and not any(root == file_path or root in file_path.parents for root in settings.attachment_roots): fail("attachment_denied", "Arquivo fora dos diretórios permitidos.")
    return operation


def endpoint(template: str, params: Mapping[str, Any]) -> str:
    for key in [part[1:-1] for part in template.split("/") if part.startswith("{")]:
        value = params.get(key)
        if value is None or isinstance(value, (dict, list)): fail("missing_parameter", f"Parâmetro obrigatório ausente: {key}.")
        template = template.replace("{" + key + "}", str(value))
    return template


def request_json(url: str, method: str, token: str, timeout: float, data: bytes | None = None, content_type: str = "application/json") -> Any:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None: headers["Content-Type"] = content_type
    with urlopen(Request(url, data=data, headers=headers, method=method), timeout=timeout) as response:
        raw = response.read()
    if not raw: return None
    try: return sanitize(json.loads(raw))
    except json.JSONDecodeError: return {"raw_base64": base64.b64encode(raw).decode("ascii")}


def execute(request: Mapping[str, Any], settings: Settings, token: str) -> Any:
    operation = check_request(request, settings)
    if operation == "sync":
        commands = request.get("commands", [])
        if not isinstance(commands, list) or any(not isinstance(item, Mapping) for item in commands): fail("invalid_commands", "commands deve ser uma lista de objetos.")
        data = urlencode({"sync_token": request.get("sync_token", "*"), "resource_types": json.dumps(["all"]), "commands": json.dumps(commands)}).encode()
        return request_json(settings.api_base + "/sync", "POST", token, settings.timeout, data, "application/x-www-form-urlencoded")
    method, template = OPERATIONS[operation]
    path = endpoint(template, request.get("params", {}))
    query = request.get("query", {})
    if not isinstance(query, Mapping): fail("invalid_request", "query deve ser um objeto.")
    if query: path += "?" + urlencode({key: value for key, value in query.items() if value is not None}, doseq=True)
    body = request.get("body")
    if operation == "uploads.create":
        file_path = Path(str(body["file_path"])); content = file_path.read_bytes(); boundary = "----OnmyojiTodoist"
        name = str(body.get("filename") or file_path.name).replace('"', "")
        payload = f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return request_json(settings.api_base + path, method, token, settings.timeout, payload, f"multipart/form-data; boundary={boundary}")
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(settings.retries + 1):
        try: return request_json(settings.api_base + path, method, token, settings.timeout, data)
        except HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == settings.retries: fail("todoist_http_error", f"Todoist retornou HTTP {error.code}.")
        except URLError:
            if attempt == settings.retries: fail("network_error", "Não foi possível conectar à API Todoist.")
        time.sleep(0.25 * (attempt + 1))


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", type=Path, required=True); parser.add_argument("--profile", required=True); args = parser.parse_args()
    operation: Any = None
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, Mapping) or request.get("version") != 1: fail("unsupported_version", "Somente requisições version=1 são aceitas.")
        operation = request.get("operation"); settings = load_settings(args.config, args.profile); data = execute(request, settings, read_token(settings, args.config))
        print(json.dumps({"ok": True, "version": 1, "operation": operation, "data": data}, ensure_ascii=False)); return 0
    except json.JSONDecodeError: error = {"code": "invalid_json", "message": "A entrada não contém JSON válido."}
    except TodoistError as error: error = {"code": error.code, "message": error.message}
    except Exception: error = {"code": "internal_error", "message": "Falha interna ao processar a solicitação."}
    print(json.dumps({"ok": False, "version": 1, "operation": operation, "error": error}, ensure_ascii=False)); return 1


if __name__ == "__main__": raise SystemExit(main())
