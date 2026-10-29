"""Cobertura do resolvedor interno de credenciais MCP."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver, mcp_credentials_path


def write_config(root: Path, extra: str = "") -> None:
    path = mcp_credentials_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(
        """schema_version = 1

[defaults]
timeout_seconds = 12

[vaults.main]
cli_command = ["keepassxc-cli"]

[vaults.main.database]
windows = "C:/secrets/main.kdbx"
linux = "/secrets/main.kdbx"

[profiles.example]
vault = "main"
allowed_entry_roots = ["APIs/Todoist"]

[profiles.example.auth.windows]
mode = "windows_credential_manager"
target = "Teste/MCP/KeePass/example"

[profiles.example.auth.linux]
mode = "command"
command = ["secret-tool", "lookup", "service", "shikigami-mcp", "shikigami", "Teste", "profile", "example"]
""" + extra,
        encoding="utf-8",
    )


def test_configuration_is_scoped_to_configs_mcp(tmp_path):
    assert mcp_credentials_path(tmp_path) == tmp_path.resolve() / "configs" / "mcp" / "credentials.toml"


def test_windows_resolves_secret_without_putting_master_password_in_command(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    calls = []
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "master-password")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="todoist-token\n")

    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)

    assert resolver.resolve("example", "APIs/Todoist/example") == "todoist-token"
    command, values = calls[0]
    assert "master-password" not in command
    assert values["input"] == "master-password\n"
    assert command[-1] == "APIs/Todoist/example"


def test_linux_uses_configured_secret_provider_then_keepass(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    calls = []
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "linux")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "secret-tool":
            return SimpleNamespace(returncode=0, stdout="master-password\n")
        return SimpleNamespace(returncode=0, stdout="todoist-token\n")

    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)

    assert resolver.resolve("example", "APIs/Todoist/example", "notes") == "todoist-token"
    assert calls[0][0] == ["secret-tool", "lookup", "service", "shikigami-mcp", "shikigami", "Teste", "profile", "example"]
    assert calls[1][1]["input"] == "master-password\n"


@pytest.mark.parametrize("entry,field,code", [("APIs/Outro/token", "password", "entry_denied"), ("APIs/Todoist/example", "invalid", "invalid_field")])
def test_resolver_rejects_unpermitted_requests_before_starting_process(tmp_path, monkeypatch, entry, field, code):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", lambda *_args, **_kwargs: pytest.fail("processo não deveria iniciar"))

    with pytest.raises(CredentialError) as error:
        resolver.resolve("example", entry, field)

    assert error.value.code == code


def test_errors_are_sanitized_when_keepass_fails(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "private-master-password")
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="private CLI detail"))

    with pytest.raises(CredentialError) as error:
        resolver.resolve("example", "APIs/Todoist/example")

    assert error.value.code == "keepass_error"
    assert "private" not in str(error.value)


def test_entry_exists_lists_paths_without_reading_secret(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "master-password")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="APIs/\nAPIs/Todoist/\nAPIs/Todoist/example\n")

    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)

    assert resolver.entry_exists("example", "APIs/Todoist/example") is True
    assert resolver.entry_exists("example", "APIs/Todoist/missing") is False
    assert all("show" not in command for command, _ in calls)
    assert all("master-password" not in command for command, _ in calls)


def test_password_exists_checks_a_protected_field_without_returning_it(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "master-password")
    monkeypatch.setattr(
        "onmyoji_daemon.mcp.credentials.keepass.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="private-token\n"),
    )

    assert resolver.password_exists("example", "APIs/Todoist/example") is True


def test_save_password_creates_missing_groups_and_entry_without_secret_in_command(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "master-password")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="" if command[1] == "ls" else "")

    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)

    assert resolver.save_password("example", "APIs/Todoist/new-account", "todoist-token") is True
    assert [command[1] for command, _ in calls] == ["ls", "ls", "mkdir", "mkdir", "add"]
    assert calls[-1][0][-2:] == ["APIs/Todoist/new-account", "--password-prompt"]
    assert calls[-1][1]["input"] == "master-password\ntodoist-token\n"
    assert all("todoist-token" not in command for command, _ in calls)


def test_save_password_substitutes_existing_entry(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "master-password")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = "APIs/Todoist/example\n" if command[1] == "ls" else ""
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)

    assert resolver.save_password("example", "APIs/Todoist/example", "replacement-token") is False
    assert [command[1] for command, _ in calls] == ["ls", "edit"]
    assert calls[-1][1]["input"] == "master-password\nreplacement-token\n"


def test_resolver_reads_and_writes_username_field(tmp_path, monkeypatch):
    write_config(tmp_path)
    resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda target: "master-password")
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="APIs/Todoist/example\n" if command[1] == "ls" else "app-key\n" if command[1] == "show" else "")

    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)

    assert resolver.resolve("example", "APIs/Todoist/example", "username") == "app-key"
    assert resolver.field_exists("example", "APIs/Todoist/example", "username") is True
    assert resolver.save_field("example", "APIs/Todoist/example", "username", "new-app-key") is False
    assert any("--username" in command for command, _ in calls)


def test_attachment_export_uses_master_password_on_stdin_and_restricts_file(tmp_path, monkeypatch):
    write_config(tmp_path); resolver = KeePassCredentialResolver(tmp_path)
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass._platform_name", lambda: "windows")
    monkeypatch.setattr(resolver, "_windows_credential", lambda _target: "master-password")
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs)); Path(command[-1]).write_text("private-key"); return SimpleNamespace(returncode=0, stdout="")
    monkeypatch.setattr("onmyoji_daemon.mcp.credentials.keepass.subprocess.run", run)
    target = tmp_path / "temporary" / "key"
    resolver.export_attachment("example", "APIs/Todoist/example", "id_ed25519", target)
    command, options = calls[0]
    assert command[1:3] == ["attachment-export", "-q"]
    assert "master-password" not in command and options["input"] == "master-password\n"
    assert target.is_file()
