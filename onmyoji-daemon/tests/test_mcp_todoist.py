"""Cobertura do primeiro serviço de integração MCP: Todoist."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from onmyoji_daemon.mcp.todoist import TodoistError, TodoistService, todoist_config_path


class Credentials:
    def __init__(self, token: str = "private-token"):
        self.token = token
        self.calls: list[tuple[str, str, str]] = []

    def resolve(self, profile: str, entry: str, field: str) -> str:
        self.calls.append((profile, entry, field))
        return self.token


def write_config(root: Path, operations: str = '"todoist.list_tasks", "todoist.test_connection"') -> None:
    path = todoist_config_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(
        f'''schema_version = 1

[defaults]
api_base = "https://api.todoist.com/api/v1"
timeout_seconds = 10
max_retries = 0

[profiles.example]
enabled = true
description = "Perfil de teste"
credential_profile = "example"
credential_entry_path = "APIs/Todoist/example"
credential_field = "password"
allowed_operations = [{operations}]
''',
        encoding="utf-8",
    )


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_configuration_is_scoped_to_configs_mcp(tmp_path):
    assert todoist_config_path(tmp_path) == tmp_path.resolve() / "configs" / "mcp" / "todoist.toml"


def test_list_tasks_uses_credential_profile_and_projects_safe_fields(tmp_path, monkeypatch):
    write_config(tmp_path)
    credentials = Credentials()
    service = TodoistService(tmp_path, credentials)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response([{"id": "1", "content": "Comprar café", "project_id": "10", "due": {"date": "2026-10-26", "string": "amanhã", "unexpected": "discard"}, "unexpected": "discard"}])

    monkeypatch.setattr("onmyoji_daemon.mcp.todoist.service.urlopen", open_request)

    assert service.list_tasks("example") == {"tasks": [{"id": "1", "content": "Comprar café", "project_id": "10", "due": {"date": "2026-10-26", "string": "amanhã"}}]}
    assert credentials.calls == [("example", "APIs/Todoist/example", "password")]
    assert requests[0][0].full_url == "https://api.todoist.com/api/v1/tasks"
    assert "private-token" not in requests[0][0].full_url


def test_list_tasks_requires_explicit_permission(tmp_path):
    write_config(tmp_path, '"todoist.test_connection"')

    with pytest.raises(TodoistError) as error:
        TodoistService(tmp_path, Credentials()).list_tasks("example")

    assert error.value.code == "operation_denied"


def test_test_connection_confirms_identity_without_returning_user_data(tmp_path, monkeypatch):
    write_config(tmp_path)
    service = TodoistService(tmp_path, Credentials())
    monkeypatch.setattr("onmyoji_daemon.mcp.todoist.service.urlopen", lambda *_args, **_kwargs: Response({"id": "123", "email": "private@example.test", "full_name": "Private Name"}))

    assert service.test_connection("example") == {"profile": "example", "authenticated": True}


def test_invalid_todoist_response_is_sanitized(tmp_path, monkeypatch):
    write_config(tmp_path)
    service = TodoistService(tmp_path, Credentials())
    monkeypatch.setattr("onmyoji_daemon.mcp.todoist.service.urlopen", lambda *_args, **_kwargs: Response({"private": "unexpected"}))

    with pytest.raises(TodoistError) as error:
        service.list_tasks("example")

    assert error.value.code == "invalid_response"
    assert "private" not in str(error.value)


def test_disabled_profile_cannot_call_the_integration(tmp_path):
    write_config(tmp_path)
    path = todoist_config_path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("enabled = true", "enabled = false"), encoding="utf-8")

    with pytest.raises(TodoistError) as error:
        TodoistService(tmp_path, Credentials()).list_tasks("example")

    assert error.value.code == "profile_disabled"
