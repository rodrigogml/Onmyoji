"""Integração MySQL interna do servidor MCP."""
from .service import MySQLError, MySQLService
__all__ = ["MySQLError", "MySQLService"]
