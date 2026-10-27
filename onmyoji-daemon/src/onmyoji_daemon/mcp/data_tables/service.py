"""Operações de registros sem DDL, SQL livre ou migrations expostos ao agente."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from .catalog import DataTablesError, SchemaCatalog, TYPES, _name


class DataTablesService:
    """Executa CRUD validado exclusivamente contra schemas instalados."""

    def __init__(self, root: Path, enabled: set[str]):
        self.catalog, self.enabled = SchemaCatalog(root), enabled

    def schemas(self) -> dict[str, object]:
        result = []
        for identifier, schema in self.catalog.discover().items():
            if identifier not in self.enabled: continue
            plan = self.catalog.plan(schema)
            if plan["pending"]: continue
            result.append({"id": identifier, "title": schema.title, "description": schema.description, "tables": sorted(self.catalog.definition(schema))})
        return {"schemas": result}

    def describe(self, identifier: object) -> dict[str, object]:
        schema, tables = self._schema(identifier)
        return {"id": schema.identifier, "title": schema.title, "description": schema.description, "tables": [{"name": name, "columns": table["columns"], "operations": sorted(table["operations"])} for name, table in tables.items()]}

    def invoke(self, operation: str, arguments: dict[str, Any]) -> dict[str, object]:
        if operation == "list_schemas": return self.schemas()
        if operation == "describe_schema": return self.describe(arguments.get("schema"))
        schema, tables = self._schema(arguments.get("schema")); table_name = _name(arguments.get("table"), "table")
        if table_name not in tables: raise DataTablesError("Tabela não declarada neste schema.")
        table = tables[table_name]
        if operation == "list_records": return self._list(schema.identifier, table_name, table, arguments)
        if operation == "get_record": return {"record": self._get(schema.identifier, table_name, table, arguments.get("id"))}
        if operation == "create_record": self._allowed(table, "create"); return {"record": self._create(schema.identifier, table_name, table, arguments.get("data"))}
        if operation == "update_record": self._allowed(table, "update"); return {"record": self._update(schema.identifier, table_name, table, arguments.get("id"), arguments.get("data"))}
        if operation == "delete_record": self._allowed(table, "delete"); return self._delete(schema.identifier, table_name, table, arguments.get("id"))
        raise DataTablesError("Operação de tabela indisponível.")

    def _schema(self, identifier: object):
        value = _name(identifier, "schema")
        if value not in self.enabled: raise DataTablesError("Schema não está habilitado.")
        schema = self.catalog.get(value); plan = self.catalog.plan(schema)
        if plan["pending"]: raise DataTablesError("Schema possui migrations pendentes; atualize-o pelo Setup MCP.")
        return schema, self.catalog.definition(schema)

    @staticmethod
    def _allowed(table: dict[str, Any], operation: str) -> None:
        if operation not in table["operations"]: raise DataTablesError("A operação não é permitida para esta tabela.")

    def _connection(self, schema: str):
        return sqlite3.connect(self.catalog.database(schema), timeout=15)

    @staticmethod
    def _primary(table: dict[str, Any]) -> str:
        return next(name for name, definition in table["columns"].items() if definition.get("primary_key") is True)

    def _decode(self, row: sqlite3.Row, table: dict[str, Any]) -> dict[str, Any]:
        record = dict(row)
        for name, column in table["columns"].items():
            if column["type"] == "boolean" and record.get(name) is not None: record[name] = bool(record[name])
            if column["type"] == "json" and record.get(name) is not None: record[name] = json.loads(record[name])
        return record

    def _value(self, column: dict[str, Any], value: Any) -> Any:
        if value is None:
            if column.get("required") is True: raise DataTablesError("Campo obrigatório ausente.")
            return None
        kind = column["type"]
        if kind == "text" and not isinstance(value, str): raise DataTablesError("Campo text deve ser texto.")
        if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)): raise DataTablesError("Campo integer deve ser inteiro.")
        if kind == "real" and (not isinstance(value, (int, float)) or isinstance(value, bool)): raise DataTablesError("Campo real deve ser numérico.")
        if kind == "boolean" and not isinstance(value, bool): raise DataTablesError("Campo boolean deve ser booleano.")
        if kind in {"date", "datetime"} and not isinstance(value, str): raise DataTablesError(f"Campo {kind} deve ser texto ISO.")
        if kind == "json":
            try: return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError) as error: raise DataTablesError("Campo json inválido.") from error
        if isinstance(column.get("enum"), list) and value not in column["enum"]: raise DataTablesError("Valor fora do enum declarado.")
        if isinstance(column.get("max_length"), int) and isinstance(value, str) and len(value) > column["max_length"]: raise DataTablesError("Texto excede o tamanho permitido.")
        return int(value) if kind == "boolean" else value

    def _record_data(self, table: dict[str, Any], data: object, *, creating: bool) -> dict[str, Any]:
        if not isinstance(data, dict): raise DataTablesError("data deve ser objeto.")
        columns = table["columns"]
        if not set(data) <= set(columns): raise DataTablesError("data contém coluna não declarada.")
        result: dict[str, Any] = {}
        for name, column in columns.items():
            if name in data: result[name] = self._value(column, data[name])
            elif creating and column.get("generated") == "uuid": result[name] = str(uuid4())
            elif creating and "default" in column: result[name] = self._value(column, column["default"])
            elif creating and column.get("required") is True: raise DataTablesError(f"Campo obrigatório ausente: {name}.")
        if not result: raise DataTablesError("data não contém campos atualizáveis.")
        return result

    def _create(self, schema: str, table_name: str, table: dict[str, Any], data: object) -> dict[str, Any]:
        values = self._record_data(table, data, creating=True); columns = list(values)
        with closing(self._connection(schema)) as connection:
            connection.row_factory = sqlite3.Row
            try:
                connection.execute(f'INSERT INTO "{table_name}" ({", ".join(chr(34)+name+chr(34) for name in columns)}) VALUES ({", ".join("?" for _ in columns)})', [values[name] for name in columns]); connection.commit()
            except sqlite3.IntegrityError as error: raise DataTablesError("Registro viola uma restrição declarada.") from error
        return self._get(schema, table_name, table, values[self._primary(table)])

    def _get(self, schema: str, table_name: str, table: dict[str, Any], identifier: object) -> dict[str, Any]:
        primary = self._primary(table); value = self._value(table["columns"][primary], identifier)
        with closing(self._connection(schema)) as connection:
            connection.row_factory = sqlite3.Row; row = connection.execute(f'SELECT * FROM "{table_name}" WHERE "{primary}"=?', (value,)).fetchone()
        if row is None: raise DataTablesError("Registro não encontrado.")
        return self._decode(row, table)

    def _update(self, schema: str, table_name: str, table: dict[str, Any], identifier: object, data: object) -> dict[str, Any]:
        primary = self._primary(table); values = self._record_data(table, data, creating=False)
        if primary in values: raise DataTablesError("A chave primária não pode ser alterada.")
        current = self._get(schema, table_name, table, identifier)
        with closing(self._connection(schema)) as connection:
            try:
                connection.execute(f'UPDATE "{table_name}" SET {", ".join(chr(34)+name+chr(34)+"=?" for name in values)} WHERE "{primary}"=?', [*values.values(), current[primary]]); connection.commit()
            except sqlite3.IntegrityError as error: raise DataTablesError("Atualização viola uma restrição declarada.") from error
        return self._get(schema, table_name, table, current[primary])

    def _delete(self, schema: str, table_name: str, table: dict[str, Any], identifier: object) -> dict[str, object]:
        record = self._get(schema, table_name, table, identifier); primary = self._primary(table)
        with closing(self._connection(schema)) as connection:
            try: connection.execute(f'DELETE FROM "{table_name}" WHERE "{primary}"=?', (record[primary],)); connection.commit()
            except sqlite3.IntegrityError as error: raise DataTablesError("Registro é referenciado por outro dado.") from error
        return {"deleted": True, "id": record[primary]}

    def _list(self, schema: str, table_name: str, table: dict[str, Any], arguments: dict[str, Any]) -> dict[str, object]:
        self._allowed(table, "list"); limit = arguments.get("limit", 50); offset = arguments.get("offset", 0)
        if not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(offset, int) or offset < 0: raise DataTablesError("Paginação inválida.")
        filters = arguments.get("filters", []); clauses: list[str] = []; values: list[Any] = []
        if not isinstance(filters, list): raise DataTablesError("filters deve ser lista.")
        for filter_ in filters:
            if not isinstance(filter_, dict): raise DataTablesError("Filtro inválido.")
            field = _name(filter_.get("field"), "field"); operator = filter_.get("op")
            if field not in table["columns"] or operator not in {"eq", "lt", "lte", "gt", "gte"}: raise DataTablesError("Campo ou operador de filtro inválido.")
            sql = {"eq": "=", "lt": "<", "lte": "<=", "gt": ">", "gte": ">="}[operator]
            clauses.append(f'"{field}" {sql} ?'); values.append(self._value(table["columns"][field], filter_.get("value")))
        order = arguments.get("order_by", self._primary(table)); direction = arguments.get("direction", "asc")
        if not isinstance(order, str) or order not in table["columns"] or direction not in {"asc", "desc"}: raise DataTablesError("Ordenação inválida.")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with closing(self._connection(schema)) as connection:
            connection.row_factory = sqlite3.Row
            total = connection.execute(f'SELECT COUNT(*) FROM "{table_name}"{where}', values).fetchone()[0]
            rows = connection.execute(f'SELECT * FROM "{table_name}"{where} ORDER BY "{order}" {direction.upper()} LIMIT ? OFFSET ?', [*values, limit, offset]).fetchall()
        return {"records": [self._decode(row, table) for row in rows], "total": total, "offset": offset}
