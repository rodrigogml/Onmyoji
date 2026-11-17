"""Configuração local e disponibilidade derivada das tools MCP."""
from __future__ import annotations
import os
from pathlib import Path

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError


def vault_profile() -> dict:
    return {"vault": "vault-a", "allowed_entry_roots": ["APIs"], "auth": {"windows": {"mode": "windows_credential_manager", "target": "Teste/MCP/KeePass/vault-a"}, "linux": {"mode": "command", "command": ["secret-tool", "lookup", "service", "shikigami-mcp", "shikigami", "Teste", "profile", "vault-a"]}}}


def test_todoist_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vault.kdbx", "linux": "/vault.kdbx"}}
    credentials["profiles"]["vault-a"] = vault_profile()
    config.save_credentials(credentials)
    todoist = config.todoist()
    todoist["profiles"]["account-a"] = {"enabled": False, "description": "Tarefas do perfil de teste.", "credential_profile": "vault-a", "credential_entry_path": "APIs/Todoist/account-a", "credential_field": "password", "allowed_operations": ["todoist.list_tasks", "todoist.test_connection"]}
    config.save_todoist(todoist)

    assert config.todoist_tool_available() is False
    todoist["profiles"]["account-a"]["enabled"] = True
    config.save_todoist(todoist)
    assert config.todoist_tool_available() is True
    credentials["profiles"].clear()
    config.save_credentials(credentials)
    assert config.todoist_tool_available() is False


def test_empty_profile_tables_remain_available_after_saving_and_reloading(tmp_path):
    config = McpConfiguration(tmp_path)
    config.save_todoist(config.todoist())
    config.save_credentials(config.credentials())

    reloaded = McpConfiguration(tmp_path)

    assert reloaded.todoist()["profiles"] == {}
    assert reloaded.credentials()["profiles"] == {}
    assert reloaded.vault_profile_names() == []
    assert reloaded.todoist_tool_available() is False


@pytest.mark.skipif(os.name == "nt", reason="Modos POSIX são validados no Linux.")
def test_private_configuration_is_published_for_owner_and_group(tmp_path):
    config = McpConfiguration(tmp_path)
    config.save_credentials(config.credentials())

    assert config.credentials_path.stat().st_mode & 0o777 == 0o660


def test_permission_failure_has_actionable_sanitized_message(tmp_path, monkeypatch):
    config = McpConfiguration(tmp_path)
    config.credentials_path.parent.mkdir(parents=True)
    config.credentials_path.touch()
    original = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == config.credentials_path:
            raise PermissionError("private-detail")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(McpConfigurationError, match="Processo/Serviço do Daemon.*Reparar permissões") as failure:
        config.credentials()
    assert "private-detail" not in str(failure.value)


def test_protected_file_auth_path_survives_credentials_round_trip(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    secret = tmp_path / "configs" / "mcp" / "secrets" / "default.secret"
    credentials["vaults"]["default"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "", "linux": str(tmp_path / "default.kdbx")}}
    credentials["profiles"]["default"] = {
        "vault": "default", "allowed_entry_roots": [],
        "auth": {
            "windows": {"mode": "windows_credential_manager", "target": "Teste/MCP/KeePass/default"},
            "linux": {"mode": "protected_file", "path": str(secret)},
        },
    }

    config.save_credentials(credentials)

    assert config.credentials()["profiles"]["default"]["auth"]["linux"] == {"mode": "protected_file", "path": str(secret)}


def test_omie_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vault.kdbx", "linux": "/vault.kdbx"}}
    credentials["profiles"]["vault-a"] = {"vault": "vault-a", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "test"}, "linux": {"mode": "command", "command": ["secret-tool"]}}}
    config.save_credentials(credentials)
    omie = config.omie()
    omie["profiles"]["account-a"] = {"enabled": False, "description": "ERP de teste.", "credential_profile": "vault-a", "credential_entry_path": "APIs/Omie/account-a", "app_key_field": "username", "app_secret_field": "password"}
    config.save_omie(omie)

    assert config.omie_tool_available() is False
    omie["profiles"]["account-a"]["enabled"] = True
    config.save_omie(omie)
    assert config.omie_tool_available() is True
    assert config.enabled_omie_profiles() == [{"name": "account-a", "description": "ERP de teste."}]


def test_omie_limits_and_cache_survive_save_reload_and_profile_edit(tmp_path):
    """Editar o perfil no setup não apaga as proteções de consumo já configuradas."""
    config = McpConfiguration(tmp_path)
    document = config.omie()
    limits = {
        "timeout_seconds": 3500, "max_retries": 0,
        "min_request_interval_seconds": 1.1, "method_requests_per_minute": 55,
        "process_requests_per_minute": 900, "app_requests_per_minute": 55,
        "app_requests_per_day": 90, "cache_ttl_seconds": 61.5,
    }
    document["defaults"] = limits.copy()
    document["profiles"]["empresa"] = {
        "enabled": False, "description": "ERP antes da edição.",
        "credential_profile": "vault-a", "credential_entry_path": "APIs/Omie/empresa",
        "app_key_field": "username", "app_secret_field": "password",
    }

    config.save_omie(document)
    reloaded = McpConfiguration(tmp_path).omie()

    assert reloaded["defaults"] == limits
    reloaded["profiles"]["empresa"].update(enabled=True, description="ERP depois da edição.")
    McpConfiguration(tmp_path).save_omie(reloaded)
    final = McpConfiguration(tmp_path).omie()
    assert final["defaults"] == limits
    assert final["profiles"]["empresa"]["description"] == "ERP depois da edição."
    assert final["profiles"]["empresa"]["enabled"] is True


def test_omie_absent_or_removed_optional_limits_are_not_reintroduced(tmp_path):
    config = McpConfiguration(tmp_path)
    document = config.omie()
    expected = {"timeout_seconds": 30, "max_retries": 2}
    assert document["defaults"] == expected

    config.save_omie(document)
    assert config.omie()["defaults"] == expected
    document["defaults"].update(cache_ttl_seconds=61, app_requests_per_day=90)
    config.save_omie(document)
    document = config.omie()
    del document["defaults"]["cache_ttl_seconds"]
    del document["defaults"]["app_requests_per_day"]
    config.save_omie(document)

    assert McpConfiguration(tmp_path).omie()["defaults"] == expected


@pytest.mark.parametrize("field,value", [
    ("timeout_seconds", 1), ("timeout_seconds", 3500), ("max_retries", 0), ("max_retries", 10),
    ("min_request_interval_seconds", 0), ("min_request_interval_seconds", 0.35),
    ("method_requests_per_minute", 1), ("method_requests_per_minute", 240),
    ("process_requests_per_minute", 1), ("process_requests_per_minute", 960),
    ("app_requests_per_minute", 1), ("app_requests_per_minute", 960),
    ("app_requests_per_day", 1), ("app_requests_per_day", 10000),
    ("cache_ttl_seconds", 61), ("cache_ttl_seconds", 61.5), ("cache_ttl_seconds", 300),
])
def test_omie_consumption_configuration_accepts_documented_boundaries(tmp_path, field, value):
    config = McpConfiguration(tmp_path)
    document = config.omie()
    document["defaults"][field] = value

    config.save_omie(document)

    assert McpConfiguration(tmp_path).omie()["defaults"][field] == value


@pytest.mark.parametrize("field,value", [
    (field, value)
    for field, values in (
        ("timeout_seconds", [None, True, False, "30", 0, -1, 3501, 1.5]),
        ("max_retries", [None, True, False, "2", -1, 0.0]),
        ("min_request_interval_seconds", [None, True, False, "0.35", -0.01, float("nan"), float("inf"), float("-inf")]),
        ("method_requests_per_minute", [None, True, False, "55", 0, -1, 241, 55.0]),
        ("process_requests_per_minute", [None, True, False, "900", 0, -1, 961, 900.0]),
        ("app_requests_per_minute", [None, True, False, "55", 0, -1, 961, 55.0]),
        ("app_requests_per_day", [None, True, False, "90", 0, -1, 90.0]),
        ("cache_ttl_seconds", [None, True, False, "61", 0, 60.9, 300.1, float("nan"), float("inf"), float("-inf")]),
    )
    for value in values
])
def test_omie_consumption_configuration_rejects_invalid_values_without_replacing_file(tmp_path, field, value):
    config = McpConfiguration(tmp_path)
    original = config.omie()
    config.save_omie(original)
    document = config.omie()
    document["defaults"][field] = value

    with pytest.raises(McpConfigurationError, match=field):
        config.save_omie(document)

    assert McpConfiguration(tmp_path).omie() == original


def test_cloudflare_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vault.kdbx", "linux": "/vault.kdbx"}}
    credentials["profiles"]["vault-a"] = vault_profile()
    config.save_credentials(credentials)
    cloudflare = config.cloudflare()
    cloudflare["profiles"]["dns"] = {"enabled": False, "description": "DNS público de teste.", "credential_profile": "vault-a", "credential_entry_path": "APIs/Cloudflare/dns", "credential_field": "password", "zone_id": "zone-default"}
    config.save_cloudflare(cloudflare)

    assert config.cloudflare_tool_available() is False
    cloudflare["profiles"]["dns"]["enabled"] = True
    config.save_cloudflare(cloudflare)
    assert config.cloudflare_tool_available() is True
    assert config.enabled_cloudflare_profiles() == [{"name": "dns", "description": "DNS público de teste."}]


def test_forward_email_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vault.kdbx", "linux": "/vault.kdbx"}}
    credentials["profiles"]["vault-a"] = vault_profile()
    config.save_credentials(credentials)
    forward_email = config.forward_email()
    forward_email["profiles"]["mail"] = {"enabled": False, "description": "Correio de teste.", "credential_profile": "vault-a", "credential_entry_path": "APIs/ForwardEmail/mail", "credential_field": "password", "domain": "example.test"}
    config.save_forward_email(forward_email)

    assert config.forward_email_tool_available() is False
    forward_email["profiles"]["mail"]["enabled"] = True
    config.save_forward_email(forward_email)
    assert config.forward_email_tool_available() is True
    assert config.enabled_forward_email_profiles() == [{"name": "mail", "description": "Correio de teste."}]


def test_notion_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "C:/vault.kdbx", "linux": "/vault.kdbx"}}
    credentials["profiles"]["vault-a"] = vault_profile()
    config.save_credentials(credentials)
    notion = config.notion()
    notion["profiles"]["workspace"] = {"enabled": False, "description": "Notion de teste.", "credential_profile": "vault-a", "credential_entry_path": "APIs/Notion/workspace", "credential_field": "password", "readable_roots": []}
    config.save_notion(notion)

    assert config.notion_tool_available() is False
    notion["profiles"]["workspace"]["enabled"] = True
    config.save_notion(notion)
    assert config.notion_tool_available() is True
    assert config.enabled_notion_profiles() == [{"name": "workspace", "description": "Notion de teste."}]


def test_aws_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
    credentials["profiles"]["vault-a"] = {"vault": "vault-a", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "target"}, "linux": {"mode": "command", "command": ["secret-tool"]}}}
    config.save_credentials(credentials)
    document = config.aws()
    document["profiles"]["prod"] = {"enabled": False, "description": "Conta AWS de produção.", "credential_profile": "vault-a", "credential_entry_path": "APIs/AWS/prod", "access_key_id_field": "username", "secret_access_key_field": "password", "cli_path": "aws", "region": "sa-east-1", "expected_account_id": "123456789012", "readable_roots": [], "writable_roots": []}
    config.save_aws(document)
    assert config.aws_tool_available() is False
    document["profiles"]["prod"]["enabled"] = True; config.save_aws(document)
    assert config.aws_tool_available() is True
    assert config.enabled_aws_profiles() == [{"name": "prod", "description": "Conta AWS de produção."}]


def test_mysql_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path); credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
    credentials["profiles"]["vault-a"] = {"vault": "vault-a", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "target"}, "linux": {"mode": "command", "command": ["secret-tool"]}}}
    config.save_credentials(credentials); document = config.mysql()
    document["profiles"]["db"] = {"enabled": False, "description": "Banco de teste.", "host": "localhost", "database": "db", "socket": "", "allow_client_commands": False, "credential_profile": "vault-a", "credential_entry_path": "APIs/MySQL/db", "username_field": "username", "password_field": "password", "readable_roots": []}
    config.save_mysql(document); assert config.mysql_tool_available() is False
    document["profiles"]["db"]["enabled"] = True; config.save_mysql(document)
    assert config.mysql_tool_available() is True
    assert config.enabled_mysql_profiles() == [{"name": "db", "description": "Banco de teste."}]


def test_ssh_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path); credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
    credentials["profiles"]["vault-a"] = {"vault": "vault-a", "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": "target"}, "linux": {"mode": "command", "command": ["secret-tool"]}}}
    config.save_credentials(credentials); document = config.ssh()
    document["profiles"]["server"] = {"enabled": False, "description": "Servidor de teste.", "host": "server", "port": 22, "username": "user", "auth_mode": "password", "credential_profile": "vault-a", "credential_entry_path": "Servers/server", "password_field": "password", "key_attachment": "", "passphrase_field": "", "known_hosts": "known_hosts", "readable_roots": [], "writable_roots": []}
    config.save_ssh(document); assert config.ssh_tool_available() is False
    document["profiles"]["server"]["enabled"] = True; config.save_ssh(document)
    assert config.ssh_tool_available() is True
    assert config.enabled_ssh_profiles() == [{"name": "server", "description": "Servidor de teste."}]


def test_bis2cmd_tool_depends_on_enabled_profile_and_existing_vault_profile(tmp_path):
    config = McpConfiguration(tmp_path)
    credentials = config.credentials()
    credentials["vaults"]["vault-a"] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
    credentials["profiles"]["vault-a"] = vault_profile()
    config.save_credentials(credentials)
    document = config.bis2cmd()
    document["profiles"]["erp"] = {
        "enabled": False,
        "description": "BIS2 da empresa de teste.",
        "jar_path": "BISCMD.jar",
        "working_dir": "biscmd",
        "host": "localhost",
        "port": 8080,
        "java_options": "",
        "credential_profile": "vault-a",
        "credential_entry_path": "APIs/BIS2CMD/erp",
        "username_field": "username",
        "password_field": "password",
        "readable_roots": [],
        "writable_roots": [],
    }
    config.save_bis2cmd(document)

    assert config.bis2cmd_tool_available() is False
    document["profiles"]["erp"]["enabled"] = True
    config.save_bis2cmd(document)

    assert config.bis2cmd_tool_available() is True
    assert config.enabled_bis2cmd_profiles() == [{"name": "erp", "description": "BIS2 da empresa de teste."}]


def test_bis10cmd_tool_requires_both_enabled_vault_profiles(tmp_path):
    config = McpConfiguration(tmp_path); credentials = config.credentials()
    for name in ("jndi", "bis"):
        credentials["vaults"][name] = {"cli_command": ["keepassxc-cli"], "database": {"windows": "vault.kdbx", "linux": "vault.kdbx"}}
        credentials["profiles"][name] = {"vault": name, "allowed_entry_roots": [], "auth": {"windows": {"mode": "windows_credential_manager", "target": name}, "linux": {"mode": "command", "command": ["secret-tool"]}}}
    config.save_credentials(credentials); document = config.bis10cmd()
    document["profiles"]["erp"] = {"enabled": True, "description": "BIS10 de teste.", "jar_path": "BISCMD.jar", "working_dir": "BIS10CMD", "host": "localhost", "port": 8080, "locale": "pt-BR", "jndi_credential_profile": "jndi", "jndi_credential_entry_path": "JNDI", "jndi_username_field": "username", "jndi_password_field": "password", "bis_credential_profile": "bis", "bis_credential_entry_path": "BIS10", "bis_username_field": "username", "bis_password_field": "password"}
    config.save_bis10cmd(document)
    assert config.bis10cmd_tool_available() is True
    assert config.enabled_bis10cmd_profiles() == [{"name": "erp", "description": "BIS10 de teste."}]
    del credentials["profiles"]["bis"]; config.save_credentials(credentials)
    assert config.bis10cmd_tool_available() is False
