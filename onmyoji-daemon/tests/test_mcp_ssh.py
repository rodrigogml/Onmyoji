from pathlib import Path
from unittest.mock import Mock
import pytest
from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.ssh import SshError, SshService, save_host_key

class Stream:
    def __init__(self, value=b"ok\n"): self.value = value; self.channel = Mock(); self.channel.recv_exit_status.return_value = 0
    def read(self): return self.value
class Client:
    def __init__(self): self.closed = False
    def exec_command(self, command, timeout=None): self.command = command; return None, Stream(), Stream(b"")
    def close(self): self.closed = True
class Credentials:
    def resolve(self, *_args): return "secret"
    def export_attachment(self, _profile, _entry, _name, target): target.write_text("private")

def configured(tmp_path: Path, **changes):
    known = tmp_path / "known_hosts"; known.write_text("host ssh-ed25519 AAAA\n"); read = tmp_path / "read"; write = tmp_path / "write"; read.mkdir(); write.mkdir()
    config = McpConfiguration(tmp_path); document = config.ssh()
    profile = {"enabled": True, "description": "Servidor de teste.", "host": "host", "port": 22, "username": "user", "auth_mode": "password", "credential_profile": "vault", "credential_entry_path": "Servers/host", "password_field": "password", "key_attachment": "", "passphrase_field": "", "known_hosts": str(known), "readable_roots": [str(read)], "writable_roots": [str(write)]}
    profile.update(changes); document["profiles"]["server"] = profile; config.save_ssh(document); return SshService(tmp_path, Credentials())

def test_exec_returns_output_and_closes_session(tmp_path, monkeypatch):
    service = configured(tmp_path); client = Client(); monkeypatch.setattr(service, "_connect", lambda *_args: client)
    result = service.invoke("server", "exec", {"command": "uname -a", "confirm": True})
    assert result == {"stdout": "ok\n", "stderr": "", "exit_code": 0}; assert client.closed

def test_all_operations_require_confirmation_before_connection(tmp_path):
    service = configured(tmp_path); service._connect = Mock()
    for operation, arguments in (("exec", {"command": "true"}), ("upload", {"source": "a", "destination": "b"}), ("download", {"source": "a", "destination": "b"})):
        with pytest.raises(SshError) as error: service.invoke("server", operation, arguments)
        assert error.value.code == "confirmation_required"
    service._connect.assert_not_called()

def test_key_attachment_is_removed_after_session(tmp_path, monkeypatch):
    service = configured(tmp_path, auth_mode="key", key_attachment="id_ed25519", passphrase_field="")
    exported = []
    def export(_profile, _entry, _name, target): target.write_text("private"); exported.append(target)
    service.credentials.export_attachment = export; client = Client()
    def connect(_profile, _defaults, key_path): assert key_path.is_file(); return client
    monkeypatch.setattr(service, "_connect", connect)
    service.invoke("server", "exec", {"command": "true", "confirm": True})
    assert len(exported) == 1 and not exported[0].exists()

def test_upload_path_is_confined(tmp_path, monkeypatch):
    service = configured(tmp_path); service._connect = Mock()
    outside = tmp_path / "outside"; outside.write_text("x")
    with pytest.raises(SshError) as error: service.invoke("server", "upload", {"source": str(outside), "destination": "/tmp/x", "confirm": True})
    assert error.value.code == "file_denied"


def test_known_host_key_is_saved_and_divergence_is_rejected(tmp_path):
    import paramiko
    path = tmp_path / "ssh" / "known_hosts"; first = paramiko.RSAKey.generate(1024); second = paramiko.RSAKey.generate(1024)
    save_host_key(path, "server", 2222, first)
    loaded = paramiko.HostKeys(str(path)); assert loaded.lookup("[server]:2222")[first.get_name()].asbytes() == first.asbytes()
    with pytest.raises(SshError) as error: save_host_key(path, "server", 2222, second)
    assert error.value.code == "host_key_changed"
