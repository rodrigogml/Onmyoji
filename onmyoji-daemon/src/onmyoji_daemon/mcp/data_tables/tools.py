"""Ferramentas MCP para registros de schemas instalados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .catalog import DataTablesError, SchemaCatalog
from .service import DataTablesService


class DataTablesToolRegistry:
    """Publica somente CRUD de schemas habilitados e íntegros."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def _enabled(self) -> set[str]:
        catalog = SchemaCatalog(self.configuration.root); configured = self.configuration.data_tables()["schemas"]
        result: set[str] = set()
        for identifier, state in configured.items():
            if state.get("enabled") is not True: continue
            try:
                schema = catalog.get(identifier)
                if not catalog.plan(schema)["pending"]: result.add(identifier)
            except DataTablesError: continue
        return result

    def definitions(self) -> list[dict[str, Any]]:
        if not self._enabled(): return []
        schema = {"schema": {"type": "string", "minLength": 1, "description": "ID retornado por data_tables.list_schemas."}}
        table = {"table": {"type": "string", "minLength": 1, "description": "Tabela declarada no schema."}}
        data = {"data": {"type": "object", "description": "Campos declarados pela tabela; não envie colunas não descritas."}}
        identity = {"id": {"description": "Chave primária do registro."}}
        return [
            {"name": "data_tables.list_schemas", "description": "Lista schemas habilitados e já instalados. Chame antes de operar tabelas quando ainda não conhecer o schema.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
            {"name": "data_tables.describe_schema", "description": "Descreve tabelas, colunas, tipos e operações permitidas de um schema habilitado.", "input_schema": {"type": "object", "properties": schema, "required": ["schema"], "additionalProperties": False}},
            {"name": "data_tables.list_records", "description": "Lista registros usando somente filtros e ordenação declarados.", "input_schema": {"type": "object", "properties": {**schema, **table, "filters": {"type": "array", "default": []}, "order_by": {"type": "string"}, "direction": {"type": "string", "enum": ["asc", "desc"], "default": "asc"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}, "offset": {"type": "integer", "minimum": 0, "default": 0}}, "required": ["schema", "table"], "additionalProperties": False}},
            {"name": "data_tables.get_record", "description": "Obtém um registro pela chave primária.", "input_schema": {"type": "object", "properties": {**schema, **table, **identity}, "required": ["schema", "table", "id"], "additionalProperties": False}},
            {"name": "data_tables.create_record", "description": "Cria um registro validado pela tabela; não altera a estrutura do schema.", "input_schema": {"type": "object", "properties": {**schema, **table, **data}, "required": ["schema", "table", "data"], "additionalProperties": False}},
            {"name": "data_tables.update_record", "description": "Atualiza campos permitidos de um registro; não altera colunas ou chave primária.", "input_schema": {"type": "object", "properties": {**schema, **table, **identity, **data}, "required": ["schema", "table", "id", "data"], "additionalProperties": False}},
            {"name": "data_tables.delete_record", "description": "Exclui definitivamente um registro somente quando a tabela declarar a operação delete.", "input_schema": {"type": "object", "properties": {**schema, **table, **identity}, "required": ["schema", "table", "id"], "additionalProperties": False}},
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, object]:
        if not name.startswith("data_tables."): raise DataTablesError("Ferramenta de tabelas indisponível.")
        return DataTablesService(self.configuration.root, self._enabled()).invoke(name.removeprefix("data_tables."), arguments)
