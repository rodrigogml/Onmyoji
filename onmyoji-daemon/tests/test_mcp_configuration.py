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
