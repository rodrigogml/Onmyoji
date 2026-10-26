"""Configuração local e disponibilidade derivada das tools MCP."""
from __future__ import annotations

from onmyoji_daemon.mcp.configuration import McpConfiguration


def vault_profile() -> dict:
    return {"vault": "vault-a", "allowed_entry_roots": ["APIs"], "auth": {"windows": {"mode": "windows_credential_manager", "target": "Onmyoji/MCP/KeePass/vault-a"}, "linux": {"mode": "command", "command": ["secret-tool", "lookup", "service", "onmyoji-mcp", "profile", "vault-a"]}}}


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
