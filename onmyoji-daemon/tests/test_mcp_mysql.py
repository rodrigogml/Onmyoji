from pathlib import Path
from unittest.mock import Mock
import pytest
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.mysql import MySQLError, MySQLService

class Credentials:
    def resolve(self, _profile, _entry, field): return "tester" if field == "username" else "secret"

def configured(tmp_path: Path, **changes):
    config = McpConfiguration(tmp_path); document = config.mysql(); scripts = tmp_path / "scripts"; scripts.mkdir(exist_ok=True)
    profile = {"enabled": True, "description": "Banco de teste.", "host": "localhost", "database": "test", "socket": "", "allow_client_commands": False, "credential_profile": "vault", "credential_entry_path": "APIs/MySQL/test", "username_field": "username", "password_field": "password", "readable_roots": [str(scripts)]}
    profile.update(changes); document["profiles"]["test"] = profile; config.save_mysql(document); return MySQLService(tmp_path, Credentials())

def test_query_uses_environment_and_returns_rows(tmp_path, monkeypatch):
    service = configured(tmp_path); calls = []
    def run(command, **kwargs): calls.append((command, kwargs)); return Mock(returncode=0, stdout="id\tname\n1\tAna\n", stderr="")
    monkeypatch.setattr("onmyoji_daemon.mcp.mysql.service.subprocess.run", run)
    result = service.invoke("test", "query", {"sql": "SELECT id,name FROM users"})
    assert result["rows"] == [{"id": "1", "name": "Ana"}]
    command, options = calls[0]; assert "secret" not in command and options["env"]["MYSQL_PWD"] == "secret"

def test_writes_need_confirmation_and_client_needs_profile_permission(tmp_path):
    service = configured(tmp_path)
    with pytest.raises(MySQLError) as error: service.invoke("test", "execute", {"sql": "DELETE FROM users"})
    assert error.value.code == "confirmation_required"
    with pytest.raises(MySQLError) as error: service.invoke("test", "client", {"args": [], "confirm": True})
    assert error.value.code == "client_commands_disabled"

def test_script_is_confined_to_readable_roots(tmp_path, monkeypatch):
    service = configured(tmp_path); outside = tmp_path / "outside.sql"; outside.write_text("SELECT 1")
    with pytest.raises(MySQLError) as error: service.invoke("test", "script", {"path": str(outside), "confirm": True})
    assert error.value.code == "file_denied"
    source = tmp_path / "scripts" / "ok.sql"; source.write_text("SELECT 1")
    monkeypatch.setattr("onmyoji_daemon.mcp.mysql.service.subprocess.run", lambda command, **kwargs: Mock(returncode=0, stdout="", stderr=""))
    assert service.invoke("test", "script", {"path": str(source), "confirm": True})["stdout"] == ""
