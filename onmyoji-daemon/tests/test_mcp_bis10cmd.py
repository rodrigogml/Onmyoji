from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.bis10cmd import Bis10CmdError, Bis10CmdService
from onmyoji_daemon.mcp.bis10cmd.service import parse_output, validate_statement_page
from onmyoji_daemon.mcp.configuration import McpConfiguration


class Credentials:
    def resolve(self, profile, _entry, field):
        return f"{profile}-{field}"


def configured(tmp_path: Path) -> Bis10CmdService:
    jar = tmp_path / "BISCMD-10.0.jar"; jar.write_bytes(b"jar"); (tmp_path / "libs").mkdir(exist_ok=True)
    config = McpConfiguration(tmp_path); document = config.bis10cmd()
    document["profiles"]["bis"] = {"enabled": True, "description": "BIS10 de teste.", "jar_path": str(jar), "working_dir": str(tmp_path), "host": "localhost", "port": 8080, "locale": "pt-BR", "jndi_credential_profile": "jndi", "jndi_credential_entry_path": "JNDI", "jndi_username_field": "username", "jndi_password_field": "password", "bis_credential_profile": "user", "bis_credential_entry_path": "BIS10", "bis_username_field": "username", "bis_password_field": "password"}
    config.save_bis10cmd(document)
    return Bis10CmdService(tmp_path, Credentials())


def statement_page() -> dict:
    return {"version": 1, "resource": "accountStatements", "offset": 0, "limit": 1, "hasMore": False, "items": [{"id": 10, "accountId": 5, "date": "2026-09-14T00:00", "value": "104.70", "operation": "DEBIT", "type": "MANUAL", "categories": [{"id": 98, "name": "TI", "value": "104.70"}]}]}


def test_query_injects_credentials_and_selects_company(tmp_path, monkeypatch):
    service = configured(tmp_path); calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs)); return Mock(returncode=0, stdout=json.dumps(statement_page()), stderr="")
    monkeypatch.setattr("onmyoji_daemon.mcp.bis10cmd.service.subprocess.run", run)
    result = service.invoke("bis", "get_account_statement", {"company_id": 2, "statement_id": 10})
    command, options = calls[0]
    assert command[-7:] == ["-company", "id", "2", "-accountStatement", "get", "id", "10"]
    assert "jndi-password" not in command and options["env"]["BISCMD_JNDI_PASSWORD"] == "jndi-password"
    assert result["lookups"][0]["items"][0]["id"] == 10


def test_write_requires_confirmation_and_adds_native_confirm(tmp_path, monkeypatch):
    service = configured(tmp_path); service.credentials = Mock()
    with pytest.raises(Bis10CmdError) as error:
        service.invoke("bis", "delete_account_statement", {"company_id": 2, "args": ["id", "10"]})
    assert error.value.code == "confirmation_required"; service.credentials.resolve.assert_not_called()
    service = configured(tmp_path); calls = []
    monkeypatch.setattr("onmyoji_daemon.mcp.bis10cmd.service.subprocess.run", lambda command, **kwargs: calls.append(command) or Mock(returncode=0, stdout="ok", stderr=""))
    service.invoke("bis", "delete_account_statement", {"company_id": 2, "args": ["id", "10"], "confirm": True})
    assert calls[0][-5:] == ["-accountStatement", "delete", "id", "10", "confirm"]


def test_rejects_command_injection_java_properties_and_confirmation_in_args(tmp_path):
    service = configured(tmp_path)
    for value in ("-accountStatement", "-Dbiscmd.host=x", "confirm"):
        with pytest.raises(Bis10CmdError) as error:
            service.invoke("bis", "list_accounts", {"company_id": 2, "args": [value]})
        assert error.value.code == "invalid_request"


def test_output_redacts_secrets_and_preserves_structured_data():
    page = statement_page(); page["items"][0]["notes"] = "secret"
    result = parse_output(json.dumps(page) + "\nsecret", "secret", ["secret"])
    assert result["lookups"][0]["items"][0]["notes"] == "[REDACTED]"
    assert result["messages"] == ["[REDACTED]"] and result["stderr"] == ["[REDACTED]"]


def test_statement_validation_rejects_invalid_pages():
    invalid = copy.deepcopy(statement_page()); invalid["items"][0]["value"] = -1
    with pytest.raises(Bis10CmdError): validate_statement_page(invalid)
    continuing = statement_page(); continuing.update(hasMore=True, nextOffset=1)
    validate_statement_page(continuing)
