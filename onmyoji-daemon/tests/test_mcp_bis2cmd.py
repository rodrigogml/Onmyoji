from pathlib import Path
from unittest.mock import Mock
import pytest
from onmyoji_daemon.mcp.bis2cmd import Bis2CmdError, Bis2CmdService
from onmyoji_daemon.mcp.bis2cmd.service import parse_output, validate_args
from onmyoji_daemon.mcp.configuration import McpConfiguration

class Credentials:
    def resolve(self, _profile, _entry, field): return "user" if field == "username" else "secret"
def configured(tmp_path: Path):
    jar = tmp_path / "BISCMD.jar"; jar.write_bytes(b"jar"); imports = tmp_path / "imports"; exports = tmp_path / "exports"; imports.mkdir(); exports.mkdir()
    config = McpConfiguration(tmp_path); document = config.bis2cmd(); document["profiles"]["bis"] = {"enabled": True, "description": "BIS2 de teste.", "jar_path": str(jar), "working_dir": str(tmp_path), "host": "localhost", "port": 8080, "java_options": "", "credential_profile": "vault", "credential_entry_path": "BIS2", "username_field": "username", "password_field": "password", "readable_roots": [str(imports)], "writable_roots": [str(exports)]}; config.save_bis2cmd(document); return Bis2CmdService(tmp_path, Credentials())
def test_command_uses_credentials_only_in_environment(tmp_path, monkeypatch):
    service = configured(tmp_path); calls = []
    def run(command, **kwargs): calls.append((command, kwargs)); return Mock(returncode=0, stdout=b'BISJSON {"id":1}\nBISMETA {"complete":true}\n', stderr=b"")
    monkeypatch.setattr("onmyoji_daemon.mcp.bis2cmd.service.subprocess.run", run)
    result = service.invoke("bis", "companies_list", {"args": []}); assert result["records"] == [{"id": 1}]
    command, options = calls[0]; assert "secret" not in command and options["env"]["BISCMD_PASSWORD"] == "secret"
def test_mutation_requires_confirmation_before_credentials(tmp_path):
    service = configured(tmp_path); service.credentials = Mock()
    with pytest.raises(Bis2CmdError) as error: service.invoke("bis", "item_update", {"args": []})
    assert error.value.code == "confirmation_required"; service.credentials.resolve.assert_not_called()
def test_file_commands_are_confined(tmp_path):
    service = configured(tmp_path)
    with pytest.raises(Bis2CmdError) as error: service.invoke("bis", "export_file", {"args": ["outputPath", str(tmp_path)], "confirm": True})
    assert error.value.code == "file_denied"
def test_nfce_xml_output_is_confined_to_writable_roots(tmp_path):
    service = configured(tmp_path)
    with pytest.raises(Bis2CmdError) as error:
        service.invoke("bis", "nfce_download_xml", {"args": ["output", str(tmp_path / "nfce.xml")], "confirm": True})
    assert error.value.code == "file_denied"
def test_special_argument_validation_and_output():
    with pytest.raises(Bis2CmdError): validate_args("docFiscalDetail", ["key", "123"])
    validate_args("nfceInutilizeNumber", ["companyId", "2", "certificateId", "6", "serie", "1", "numberStart", "2", "numberEnd", "3", "confirm"])
    assert parse_output('BISJSON {"ok":true}\nMensagem\n')["messages"] == ["Mensagem"]
