"""Round-trip KDBX com credenciais sintéticas e sem processos com tokens em argv."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver


def test_oauth_state_is_saved_encrypted_preserving_other_credentials(tmp_path, monkeypatch):
    from pykeepass import PyKeePass, create_database
    database = tmp_path / "vault.kdbx"
    vault = create_database(str(database), password="test-master")
    vault.add_entry(vault.root_group, "BTG", "test-client-id", "test-client-secret", notes="{}")
    vault.save()
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr(resolver, "_settings", lambda _name: ({}, {"allowed_entry_roots": []}, {"database": {"windows": str(database), "linux": str(database)}, "cli_command": ["keepassxc-cli"]}))
    monkeypatch.setattr(resolver, "_master_password", lambda _profile: "test-master")
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", lambda *_args, **_kwargs: pytest.fail("Nenhum segredo deve entrar em processo externo"))
    state = {"tokens": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh", "expiresAt": 1000}}
    resolver.save_oauth_state("vault", "BTG", state)
    verified = PyKeePass(str(database), password="test-master")
    entry = verified.find_entries(title="BTG", first=True)
    assert json.loads(entry.notes) == state
    assert entry.username == "test-client-id"
    assert entry.password == "test-client-secret"
    assert b"synthetic-access" not in database.read_bytes()
    assert list(tmp_path.glob("vault.kdbx.*.kdbx")) == []
    assert (tmp_path / "vault.kdbx.oauth.lock").read_bytes() == b"0"
    with pytest.raises(CredentialError) as error:
        resolver.save_oauth_state("vault", "not-found", state)
    assert error.value.code == "entry_not_unique"


def test_oauth_write_refuses_database_changed_during_encryption(tmp_path, monkeypatch):
    from pykeepass import PyKeePass, create_database
    from onmyoji_daemon.mcp.credentials.encrypted import save_notes
    database = tmp_path / "vault.kdbx"
    vault = create_database(str(database), password="test-master")
    vault.add_entry(vault.root_group, "BTG", "test-client-id", "test-client-secret")
    vault.save()
    original = PyKeePass.save
    changed = []
    def save(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        if args and hasattr(args[0], "getvalue") and not changed:
            changed.append(True)
            other = PyKeePass(str(database), password="test-master")
            other.add_entry(other.root_group, "User edit", "user", "user-password")
            original(other)
        return result
    monkeypatch.setattr(PyKeePass, "save", save)
    with pytest.raises(CredentialError) as error:
        save_notes(database, "test-master", "BTG", {"tokens": {"access_token": "synthetic-access"}})
    assert error.value.code == "vault_changed"
    assert PyKeePass(str(database), password="test-master").find_entries(title="User edit", first=True) is not None


def test_oauth_writer_uses_private_runtime_and_stdin_when_global_dependency_absent(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.runtime import mcp_runtime_python
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr(resolver, "_settings", lambda _name: ({}, {"allowed_entry_roots": ["BTG"]}, {}))
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.importlib.util.find_spec", lambda _name: None)
    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='{"ok":true}'))
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)
    state = {"tokens": {"access_token": "synthetic-access"}}
    resolver.save_oauth_state("vault", "BTG/company", state)
    command, options = run.call_args.args[0], run.call_args.kwargs
    assert command[0] == str(mcp_runtime_python(tmp_path))
    assert command[1:3] == ["-m", "onmyoji_daemon.mcp.credentials.oauth_writer"]
    assert "synthetic-access" not in str(command)
    assert json.loads(options["input"])["state"] == state
    assert options["capture_output"] is True
    with pytest.raises(CredentialError) as error:
        resolver.save_oauth_state("vault", "Other/company", state)
    assert error.value.code == "entry_denied" and run.call_count == 1


@pytest.mark.parametrize("stdout,returncode", [('not-json', 0), ('{"ok":false}', 2), ('{}', 0)])
def test_oauth_runtime_writer_errors_do_not_expose_state(tmp_path, monkeypatch, stdout, returncode):
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", Mock(return_value=SimpleNamespace(returncode=returncode, stdout=stdout)))
    with pytest.raises(CredentialError) as error:
        resolver._save_oauth_via_runtime("vault", "BTG", {"access_token": "synthetic-secret"})
    assert "synthetic-secret" not in str(error.value)


def test_concurrent_oauth_writes_in_different_entries_preserve_both(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from pykeepass import PyKeePass, create_database
    from onmyoji_daemon.mcp.credentials.encrypted import save_notes
    database = tmp_path / "vault.kdbx"
    vault = create_database(str(database), password="test-master")
    for title in ("BTG", "Other"):
        vault.add_entry(vault.root_group, title, "client", "secret")
    vault.save()
    def update(title):
        save_notes(database, "test-master", title, {"token": title})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update, ["BTG", "Other"]))
    verified = PyKeePass(str(database), password="test-master")
    for title in ("BTG", "Other"):
        assert json.loads(verified.find_entries(title=title, first=True).notes) == {"token": title}
