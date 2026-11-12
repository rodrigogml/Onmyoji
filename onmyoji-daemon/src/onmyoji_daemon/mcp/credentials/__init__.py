"""Resolução interna e segura de credenciais para módulos MCP."""

from .keepass import CredentialError, KeePassCredentialResolver, mcp_credentials_path

__all__ = ["CredentialError", "KeePassCredentialResolver", "mcp_credentials_path"]
