"""Tabelas estruturadas declaradas por skills do Shikigami."""

from .catalog import DataTablesError, SchemaCatalog
from .service import DataTablesService

__all__ = ["DataTablesError", "DataTablesService", "SchemaCatalog"]
