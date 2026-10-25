"""Cobertura da base de conexão da integração Omie MCP."""
from __future__ import annotations

import json
from pathlib import Path

from onmyoji_daemon.mcp.omie import OmieService


class Credentials:
    def resolve(self, _profile: str, _entry: str, field: str) -> str:
        return "private-app-key" if field == "username" else "private-app-secret"


class Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps({"pagina": 1, "total_de_paginas": 1, "departamentos": []}).encode("utf-8")


def write_config(root: Path) -> None:
    path = root / "configs" / "mcp" / "omie.toml"
    path.parent.mkdir(parents=True)
    path.write_text('''schema_version = 1

[defaults]
timeout_seconds = 10
max_retries = 0

[profiles.example]
enabled = true
description = "ERP de teste"
credential_profile = "vault"
credential_entry_path = "APIs/Omie/example"
app_key_field = "username"
app_secret_field = "password"
''', encoding="utf-8")


def test_connection_uses_two_vault_fields_without_exposing_them(tmp_path: Path, monkeypatch) -> None:
    write_config(tmp_path)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", open_request)
    assert OmieService(tmp_path, Credentials()).test_connection("example") == {"profile": "example", "authenticated": True}
    payload = json.loads(requests[0][0].data)
    assert payload["call"] == "ListarDepartamentos"
    assert payload["param"] == [{"pagina": 1, "registros_por_pagina": 1}]
    assert "private-app-key" not in requests[0][0].full_url
