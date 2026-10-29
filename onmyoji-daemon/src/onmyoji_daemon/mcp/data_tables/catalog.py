"""Descoberta, validação e instalação administrativa de schemas declarativos."""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import tomllib
from typing import Any

from ...instance import shikigami_skills_path


NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
MIGRATION = re.compile(r"^(\d{4,})-([a-z0-9]+(?:-[a-z0-9]+)*)\.toml$")
TYPES = {"text": "TEXT", "integer": "INTEGER", "real": "REAL", "boolean": "INTEGER", "date": "TEXT", "datetime": "TEXT", "json": "TEXT"}
OPERATIONS = {"create", "get", "list", "update", "upsert", "delete", "search"}


class DataTablesError(ValueError):
    """Erro seguro de catálogo, migration ou armazenamento de tabelas."""


@dataclass(frozen=True)
class Schema:
    identifier: str
    title: str
    description: str
    directory: Path
    migrations: tuple[dict[str, Any], ...]


def _name(value: object, label: str) -> str:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise DataTablesError(f"{label} deve usar letras minúsculas, números e sublinhado, iniciando por letra.")
    return value


def _text(value: object, label: str, maximum: int = 500) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise DataTablesError(f"{label} é obrigatório e deve ter até {maximum} caracteres.")
    return value.strip()


class SchemaCatalog:
    """Lê schemas somente da definição versionada do Shikigami."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.source = shikigami_skills_path(self.root)
        self.storage = self.root / "configs" / "mcp" / "data-tables"

    def discover(self) -> dict[str, Schema]:
        schemas: dict[str, Schema] = {}
        if not self.source.is_dir(): return schemas
        for descriptor in sorted(self.source.glob("*/schemas/*/schema.toml")):
            if any(part.is_symlink() for part in (descriptor.parent, descriptor)):
                raise DataTablesError("Schemas do Shikigami não podem usar links simbólicos.")
            schema = self._read(descriptor)
            if schema.identifier in schemas:
                raise DataTablesError(f"O schema {schema.identifier} foi declarado mais de uma vez.")
            schemas[schema.identifier] = schema
        return schemas

    def get(self, identifier: object) -> Schema:
        value = _name(identifier, "schema")
        schema = self.discover().get(value)
        if schema is None: raise DataTablesError("Schema não encontrado nas skills do Shikigami.")
        return schema

    def database(self, identifier: str) -> Path:
        _name(identifier, "schema")
        path = self.storage / f"{identifier}.sqlite3"
        if path.is_symlink() or not path.resolve().is_relative_to(self.storage.resolve()):
            raise DataTablesError("O banco do schema deve permanecer em configs/mcp/data-tables.")
        return path

    def _read(self, descriptor: Path) -> Schema:
        try: data = tomllib.loads(descriptor.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error: raise DataTablesError(f"Descritor inválido: {descriptor}.") from error
        if data.get("schema_format") != 1: raise DataTablesError("schema_format deve ser 1.")
        identifier = _name(data.get("id"), "id")
        if descriptor.parent.name != identifier: raise DataTablesError("O diretório do schema deve ter o mesmo nome do id.")
        migrations_dir = descriptor.parent / "migrations"
        if not migrations_dir.is_dir(): raise DataTablesError(f"O schema {identifier} não possui migrations.")
        migrations: list[dict[str, Any]] = []
        for file in sorted(migrations_dir.glob("*.toml")):
            match = MIGRATION.fullmatch(file.name)
            if not match: raise DataTablesError("Nome de migration inválido; use NNNN-descricao.toml.")
            try: migration = tomllib.loads(file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error: raise DataTablesError(f"Migration inválida: {file.name}.") from error
            version = int(match.group(1))
            if migration.get("schema_id") != identifier or migration.get("migration_version") != version:
                raise DataTablesError(f"A migration {file.name} não corresponde ao schema ou à versão declarada.")
            operations = migration.get("operations")
            if not isinstance(operations, list) or not operations: raise DataTablesError(f"A migration {file.name} não possui operações.")
            migrations.append({"version": version, "filename": file.name, "checksum": sha256(file.read_bytes()).hexdigest(), "operations": operations})
        if not migrations or [item["version"] for item in migrations] != list(range(1, len(migrations) + 1)):
            raise DataTablesError("As migrations devem iniciar em 0001 e não podem ter lacunas.")
        schema = Schema(identifier, _text(data.get("title"), "title", 120), _text(data.get("description"), "description", 400), descriptor.parent, tuple(migrations))
        self.definition(schema)
        return schema

    def definition(self, schema: Schema) -> dict[str, dict[str, Any]]:
        tables: dict[str, dict[str, Any]] = {}
        for migration in schema.migrations:
            for operation in migration["operations"]:
                if not isinstance(operation, dict): raise DataTablesError("Operação de migration inválida.")
                kind = operation.get("type")
                if kind == "create_table":
                    table = _name(operation.get("table"), "table")
                    if table in tables: raise DataTablesError(f"A tabela {table} já existe.")
                    columns = operation.get("columns")
                    allowed = operation.get("operations")
                    if not isinstance(columns, list) or not columns or not isinstance(allowed, list) or not set(allowed) <= OPERATIONS or not allowed:
                        raise DataTablesError("create_table exige colunas e operações permitidas.")
                    parsed: dict[str, dict[str, Any]] = {}
                    for column in columns:
                        if not isinstance(column, dict): raise DataTablesError("Coluna inválida.")
                        name = _name(column.get("name"), "column")
                        if name in parsed or column.get("type") not in TYPES: raise DataTablesError("Nome ou tipo de coluna inválido.")
                        parsed[name] = dict(column)
                    if sum(bool(column.get("primary_key")) for column in parsed.values()) != 1: raise DataTablesError("Cada tabela deve ter exatamente uma chave primária.")
                    tables[table] = {"columns": parsed, "operations": set(allowed), "indexes": []}
                elif kind == "add_column":
                    table = _name(operation.get("table"), "table"); column = operation.get("column")
                    if table not in tables or not isinstance(column, dict): raise DataTablesError("add_column inválido.")
                    name = _name(column.get("name"), "column")
                    if name in tables[table]["columns"] or column.get("type") not in TYPES: raise DataTablesError("Coluna inválida ou duplicada.")
                    tables[table]["columns"][name] = dict(column)
                elif kind == "create_index":
                    table = _name(operation.get("table"), "table"); columns = operation.get("columns")
                    if table not in tables or not isinstance(columns, list) or not columns or any(_name(value, "column") not in tables[table]["columns"] for value in columns): raise DataTablesError("Índice inválido.")
                    tables[table]["indexes"].append(dict(operation))
                else: raise DataTablesError("Tipo de migration não suportado.")
        return tables

    def plan(self, schema: Schema) -> dict[str, Any]:
        path = self.database(schema.identifier)
        if not path.exists(): return {"schema": schema.identifier, "pending": [item["version"] for item in schema.migrations], "installed": []}
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS __onmyoji_schema_migrations(version INTEGER PRIMARY KEY, filename TEXT NOT NULL, checksum TEXT NOT NULL)")
            applied = {row[0]: (row[1], row[2]) for row in connection.execute("SELECT version,filename,checksum FROM __onmyoji_schema_migrations")}
        for item in schema.migrations:
            current = applied.get(item["version"])
            if current and current != (item["filename"], item["checksum"]): raise DataTablesError("Uma migration já aplicada foi alterada, removida ou renomeada.")
        unknown = set(applied) - {item["version"] for item in schema.migrations}
        if unknown: raise DataTablesError("O banco contém migration sem fonte correspondente.")
        return {"schema": schema.identifier, "pending": [item["version"] for item in schema.migrations if item["version"] not in applied], "installed": sorted(applied)}

    def apply(self, identifier: object) -> dict[str, Any]:
        schema = self.get(identifier); plan = self.plan(schema); path = self.database(schema.identifier)
        self.storage.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA foreign_keys=ON"); connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute("CREATE TABLE IF NOT EXISTS __onmyoji_schema_migrations(version INTEGER PRIMARY KEY, filename TEXT NOT NULL, checksum TEXT NOT NULL)")
                for migration in schema.migrations:
                    if migration["version"] not in plan["pending"]: continue
                    self._apply_operations(connection, migration["operations"])
                    connection.execute("INSERT INTO __onmyoji_schema_migrations VALUES(?,?,?)", (migration["version"], migration["filename"], migration["checksum"]))
                connection.commit()
            except Exception: connection.rollback(); raise
        return {**self.plan(schema), "applied": plan["pending"]}

    def _apply_operations(self, connection: sqlite3.Connection, operations: list[dict[str, Any]]) -> None:
        for operation in operations:
            kind = operation["type"]
            if kind == "create_table":
                columns = []
                for column in operation["columns"]: columns.append(self._column_sql(column))
                connection.execute(f'CREATE TABLE "{_name(operation["table"], "table")}" ({", ".join(columns)})')
            elif kind == "add_column": connection.execute(f'ALTER TABLE "{_name(operation["table"], "table")}" ADD COLUMN {self._column_sql(operation["column"])}')
            elif kind == "create_index":
                table = _name(operation["table"], "table"); columns = [_name(value, "column") for value in operation["columns"]]
                index = _name(operation.get("name") or f"idx_{table}_{'_'.join(columns)}", "index")
                unique = "UNIQUE " if operation.get("unique") is True else ""
                connection.execute(f'CREATE {unique}INDEX "{index}" ON "{table}" ({", ".join(f"\"{value}\"" for value in columns)})')

    @staticmethod
    def _column_sql(column: dict[str, Any]) -> str:
        name = _name(column.get("name"), "column"); kind = TYPES[column["type"]]
        parts = [f'"{name}"', kind]
        if column.get("primary_key") is True: parts.append("PRIMARY KEY")
        if column.get("required") is True: parts.append("NOT NULL")
        if column.get("unique") is True: parts.append("UNIQUE")
        if "default" in column: parts.extend(["DEFAULT", json.dumps(column["default"])])
        return " ".join(parts)
