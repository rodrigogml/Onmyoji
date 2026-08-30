#!/usr/bin/env python3
"""Armazenamento local tipado e pesquisável, sem SQL controlado pelo chamador."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

VERSION = 1
NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
NAMESPACE = re.compile(r"^[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*){0,7}$")
TYPES = {"text": "TEXT", "integer": "INTEGER", "real": "REAL", "boolean": "INTEGER", "date": "TEXT", "datetime": "TEXT", "json": "TEXT"}


class StoreError(Exception):
    def __init__(self, code: str, message: str): self.code, self.message = code, message


def fail(code: str, message: str) -> None: raise StoreError(code, message)
def now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
def quoted(name: str) -> str:
    if not NAME.fullmatch(name): fail("invalid_name", "Identificador de tabela, coluna ou índice inválido.")
    return '"' + name + '"'
def canonical(value: Any) -> str: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def checksum(value: Any) -> str: return hashlib.sha256(canonical(value).encode()).hexdigest()


def paths(workspace: Path, namespace: str) -> tuple[Path, Path, Path]:
    if not NAMESPACE.fullmatch(namespace): fail("invalid_namespace", "Namespace inválido.")
    root = workspace.resolve() / ".onmyoji" / "memory"
    root.mkdir(parents=True, exist_ok=True)
    slug = namespace.replace("/", "__")
    return root / "text.sqlite3", root / f"{slug}.sqlite3", root / "backups"


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


TEXT_SCHEMA = """
CREATE TABLE IF NOT EXISTS text_memories(
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
 tags_json TEXT NOT NULL, source_ref TEXT, confidence TEXT, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, expires_at TEXT, archived_at TEXT, superseded_by TEXT REFERENCES text_memories(id));
CREATE INDEX IF NOT EXISTS text_scope ON text_memories(namespace, archived_at, updated_at);
CREATE VIRTUAL TABLE IF NOT EXISTS text_search USING fts5(id UNINDEXED, namespace UNINDEXED, text, tokenize='unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS text_audit(id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, operation TEXT NOT NULL, target_id TEXT, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL);
"""


def text_db(path: Path) -> sqlite3.Connection:
    con = connect(path); con.executescript(TEXT_SCHEMA); con.commit(); return con


def require_confirmation(request: dict[str, Any]) -> None:
    if request.get("confirm") is not True: fail("confirmation_required", "Esta operação exige confirm: true.")


def audit(con: sqlite3.Connection, operation: str, target: str | None, payload: Any) -> None:
    con.execute("INSERT INTO audit_events(operation,target_id,payload_hash,created_at) VALUES(?,?,?,?)", (operation, target, checksum(payload), now()))


def text_audit(con: sqlite3.Connection, namespace: str, operation: str, target: str | None, payload: Any) -> None:
    con.execute("INSERT INTO text_audit(namespace,operation,target_id,payload_hash,created_at) VALUES(?,?,?,?,?)", (namespace, operation, target, checksum(payload), now()))


def text_operation(path: Path, structured_path: Path, namespace: str, operation: str, request: dict[str, Any]) -> Any:
    con = text_db(path)
    try:
        if operation == "text.add":
            require_confirmation(request); text = request.get("text")
            if not isinstance(text, str) or not text.strip(): fail("invalid_request", "text é obrigatório.")
            tags = request.get("tags", [])
            if not isinstance(tags, list) or not all(isinstance(item, str) and item for item in tags): fail("invalid_request", "tags deve ser uma lista de textos.")
            identifier = str(uuid.uuid4()); stamp = now()
            con.execute("INSERT INTO text_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (identifier, namespace, str(request.get("kind", "note")), text, canonical(tags), request.get("source_ref"), request.get("confidence"), stamp, stamp, request.get("expires_at"), None, None))
            con.execute("INSERT INTO text_search(id,namespace,text) VALUES(?,?,?)", (identifier, namespace, text)); text_audit(con, namespace, operation, identifier, request); con.commit(); sync_text_index(structured_path, path, namespace, identifier)
            return {"id": identifier}
        if operation == "text.search":
            query = request.get("query")
            if not isinstance(query, str) or not query.strip(): fail("invalid_request", "query é obrigatório.")
            clauses = ["m.namespace=?", "s.namespace=?"]; params: list[Any] = [namespace, namespace]
            if not request.get("include_archived"): clauses.append("m.archived_at IS NULL")
            if isinstance(request.get("kind"), str): clauses.append("m.kind=?"); params.append(request["kind"])
            if isinstance(request.get("tag"), str): clauses.append("m.tags_json LIKE ?"); params.append("%" + json.dumps(request["tag"], ensure_ascii=False)[1:-1] + "%")
            limit = min(max(int(request.get("limit", 20)), 1), 100)
            rows = con.execute(f"SELECT m.*, snippet(text_search,2,'[',']','…',12) excerpt, bm25(text_search) score FROM text_search s JOIN text_memories m ON m.id=s.id WHERE text_search MATCH ? AND {' AND '.join(clauses)} ORDER BY score LIMIT ?", [query, *params, limit]).fetchall()
            return {"items": [dict(row) for row in rows]}
        identifier = request.get("id")
        if not isinstance(identifier, str): fail("invalid_request", "id é obrigatório.")
        row = con.execute("SELECT * FROM text_memories WHERE id=? AND namespace=?", (identifier, namespace)).fetchone()
        if not row: fail("not_found", "Memória não encontrada neste namespace.")
        if operation == "text.get": return dict(row)
        require_confirmation(request)
        if operation == "text.supersede":
            successor = request.get("successor_id")
            check = con.execute("SELECT 1 FROM text_memories WHERE id=? AND namespace=?", (successor, namespace)).fetchone()
            if not isinstance(successor, str) or not check: fail("invalid_request", "successor_id deve existir no namespace.")
            con.execute("UPDATE text_memories SET superseded_by=?,updated_at=? WHERE id=?", (successor, now(), identifier))
        elif operation in {"text.archive", "text.restore"}:
            con.execute("UPDATE text_memories SET archived_at=?,updated_at=? WHERE id=?", (now() if operation.endswith("archive") else None, now(), identifier))
        else: fail("unsupported_operation", "Operação textual não suportada.")
        text_audit(con, namespace, operation, identifier, request); con.commit(); sync_text_index(structured_path, path, namespace, identifier); return {"id": identifier}
    finally: con.close()


SYSTEM_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS schema_tables(name TEXT PRIMARY KEY, columns_json TEXT NOT NULL, keys_json TEXT NOT NULL, indexes_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY, operation TEXT NOT NULL, target_id TEXT, payload_hash TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
 content,
 source UNINDEXED,
 table_name UNINDEXED,
 item_id UNINDEXED,
 field_name UNINDEXED,
 archived UNINDEXED,
 tokenize='unicode61 remove_diacritics 2'
);
"""


def structured_db(path: Path) -> sqlite3.Connection:
    con = connect(path); con.executescript(SYSTEM_SCHEMA); con.commit(); return con


def column_spec(spec: dict[str, Any]) -> str:
    name = quoted(str(spec.get("name", ""))); kind = spec.get("type")
    if kind not in TYPES: fail("invalid_schema", "Tipo de coluna inválido.")
    if "searchable" in spec and (not isinstance(spec["searchable"], bool) or spec["searchable"] and kind != "text"):
        fail("invalid_schema", "searchable must be a boolean on text columns.")
    sql = f"{name} {TYPES[kind]}"
    if spec.get("primary_key"): sql += " PRIMARY KEY"
    if spec.get("required"): sql += " NOT NULL"
    if spec.get("unique"): sql += " UNIQUE"
    if "default" in spec:
        value = spec["default"]
        if isinstance(value, (dict, list)): value = canonical(value)
        sql += " DEFAULT " + ("NULL" if value is None else repr(value))
    checks: list[str] = []
    if isinstance(spec.get("enum"), list) and spec["enum"]:
        checks.append(f"{name} IN ({','.join(repr(item) for item in spec['enum'])})")
    if "min" in spec: checks.append(f"{name}>={float(spec['min'])}")
    if "max" in spec: checks.append(f"{name}<={float(spec['max'])}")
    if checks: sql += " CHECK(" + " AND ".join(checks) + ")"
    reference = spec.get("reference")
    if reference:
        if not isinstance(reference, dict): fail("invalid_schema", "reference inválida.")
        sql += f" REFERENCES {quoted(str(reference.get('table','')))}({quoted(str(reference.get('column','id')))})"
    return sql


def create_table(con: sqlite3.Connection, spec: dict[str, Any], register: bool = True) -> None:
    table = str(spec.get("table", "")); qtable = quoted(table); columns = spec.get("columns")
    if not isinstance(columns, list) or not columns: fail("invalid_schema", "create_table exige colunas.")
    names = [str(item.get("name", "")) for item in columns]
    if len(names) != len(set(names)): fail("invalid_schema", "Colunas repetidas.")
    rendered = [column_spec(item) for item in columns] + ['"__archived_at" TEXT']
    keys = spec.get("unique", [])
    if not isinstance(keys, list): fail("invalid_schema", "unique inválido.")
    for key in keys:
        if not isinstance(key, list) or not key: fail("invalid_schema", "Chave única inválida.")
        rendered.append("UNIQUE(" + ",".join(quoted(str(column)) for column in key) + ")")
    con.execute(f"CREATE TABLE {qtable}({','.join(rendered)})")
    indexes = spec.get("indexes", [])
    for index in indexes:
        create_index(con, table, index)
    if register:
        con.execute("INSERT INTO schema_tables VALUES(?,?,?,?)", (table, canonical(columns), canonical(keys), canonical(indexes)))


def rebuild_table(con: sqlite3.Connection, spec: dict[str, Any]) -> None:
    table = str(spec.get("table", "")); quoted(table)
    old = con.execute("SELECT 1 FROM schema_tables WHERE name=?", (table,)).fetchone()
    if not old: fail("invalid_schema", "Tabela a reconstruir não foi declarada.")
    temporary = f"rebuild_{table}"
    if len(temporary) > 63: fail("invalid_schema", "Nome de tabela longo demais para reconstrução.")
    con.execute(f"DROP TABLE IF EXISTS {quoted(temporary)}")
    create_table(con, {**spec, "table": temporary, "indexes": []}, register=False)
    previous = {row[1] for row in con.execute(f"PRAGMA table_info({quoted(table)})")}
    copy = spec.get("copy", {})
    if not isinstance(copy, dict): fail("invalid_schema", "copy deve ser objeto.")
    destination, source, params = [], [], []
    for column in spec.get("columns", []):
        name = str(column.get("name", "")); mapped = copy.get(name, name)
        if isinstance(mapped, str) and mapped in previous:
            destination.append(quoted(name)); source.append(quoted(mapped))
        elif isinstance(mapped, dict) and "value" in mapped:
            destination.append(quoted(name)); source.append("?"); params.append(coerce(column, mapped["value"]))
        elif "default" not in column and column.get("required"):
            fail("invalid_schema", f"Reconstrução não define valor para {name}.")
    if "__archived_at" in previous:
        destination.append('"__archived_at"'); source.append('"__archived_at"')
    if destination:
        con.execute(f"INSERT INTO {quoted(temporary)}({','.join(destination)}) SELECT {','.join(source)} FROM {quoted(table)}", params)
    con.execute(f"DROP TABLE {quoted(table)}")
    con.execute(f"ALTER TABLE {quoted(temporary)} RENAME TO {quoted(table)}")
    indexes = spec.get("indexes", [])
    for index in indexes: create_index(con, table, index)
    con.execute("UPDATE schema_tables SET columns_json=?,keys_json=?,indexes_json=? WHERE name=?", (canonical(spec["columns"]), canonical(spec.get("unique", [])), canonical(indexes), table))


def create_index(con: sqlite3.Connection, table: str, spec: dict[str, Any]) -> None:
    name = quoted(str(spec.get("name", ""))); cols = spec.get("columns")
    if not isinstance(cols, list) or not cols: fail("invalid_schema", "Índice sem colunas.")
    con.execute(f"CREATE {'UNIQUE ' if spec.get('unique') else ''}INDEX {name} ON {quoted(table)}({','.join(quoted(str(c)) for c in cols)})")


def validate_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION or not isinstance(manifest.get("migrations"), list): fail("invalid_schema", "Manifesto inválido.")
    migrations = manifest["migrations"]; versions = [item.get("version") for item in migrations if isinstance(item, dict)]
    if len(versions) != len(migrations) or versions != sorted(versions) or len(set(versions)) != len(versions) or not all(isinstance(v, int) and v > 0 for v in versions): fail("invalid_schema", "Versões de migration inválidas.")
    return migrations


def apply_migrations(path: Path, text_path: Path, namespace: str, request: dict[str, Any], dry_run: bool) -> Any:
    migrations = validate_manifest(request.get("manifest")); con = structured_db(path)
    try:
        applied = {row["version"]: row["checksum"] for row in con.execute("SELECT * FROM schema_migrations")}; pending = []
        for migration in migrations:
            digest = checksum(migration); version = migration["version"]
            if version in applied:
                if applied[version] != digest: fail("migration_checksum_mismatch", "Migration aplicada foi alterada.")
            else: pending.append(migration)
        if dry_run: return {"pending": [item["version"] for item in pending], "applied": sorted(applied)}
        require_confirmation(request)
        backup(path, "pre-migration")
        with con:
            for migration in pending:
                for operation in migration.get("operations", []):
                    if not isinstance(operation, dict): fail("invalid_schema", "Operação de migration inválida.")
                    op = operation.get("op")
                    if op == "create_table": create_table(con, operation)
                    elif op == "add_column":
                        table = str(operation.get("table", "")); spec = operation.get("column")
                        if not isinstance(spec, dict) or spec.get("required") and "default" not in spec: fail("invalid_schema", "Coluna obrigatória adicionada exige default.")
                        con.execute(f"ALTER TABLE {quoted(table)} ADD COLUMN {column_spec(spec)}")
                        row = con.execute("SELECT columns_json FROM schema_tables WHERE name=?", (table,)).fetchone()
                        if not row: fail("invalid_schema", "Tabela não declarada.")
                        cols = json.loads(row[0]); cols.append(spec); con.execute("UPDATE schema_tables SET columns_json=? WHERE name=?", (canonical(cols), table))
                    elif op == "create_index": create_index(con, str(operation.get("table", "")), operation)
                    elif op == "drop_index": con.execute(f"DROP INDEX {quoted(str(operation.get('name','')))}")
                    elif op == "rebuild_table": rebuild_table(con, operation)
                    else: fail("invalid_schema", "Operação de migration não suportada.")
                con.execute("INSERT INTO schema_migrations VALUES(?,?,?)", (migration["version"], checksum(migration), now()))
            audit(con, "schema.apply", None, request)
        result = {"applied": [item["version"] for item in pending]}
        if pending: result["search_index"] = rebuild_search_index(con, text_path, namespace)
        return result
    finally: con.close()


def table_meta(con: sqlite3.Connection, table: Any) -> tuple[str, list[dict[str, Any]], list[list[str]], list[dict[str, Any]]]:
    if not isinstance(table, str) or not NAME.fullmatch(table): fail("invalid_request", "Tabela inválida.")
    row = con.execute("SELECT * FROM schema_tables WHERE name=?", (table,)).fetchone()
    if not row: fail("unknown_table", "Tabela não declarada.")
    return table, json.loads(row["columns_json"]), json.loads(row["keys_json"]), json.loads(row["indexes_json"])


def coerce(spec: dict[str, Any], value: Any) -> Any:
    kind = spec["type"]
    if value is None:
        if spec.get("required"): fail("invalid_value", f"{spec['name']} é obrigatório.")
        return None
    if kind == "text" and not isinstance(value, str): fail("invalid_value", f"{spec['name']} deve ser texto.")
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)): fail("invalid_value", f"{spec['name']} deve ser inteiro.")
    if kind == "real" and (not isinstance(value, (int, float)) or isinstance(value, bool)): fail("invalid_value", f"{spec['name']} deve ser numérico.")
    if kind == "boolean":
        if not isinstance(value, bool): fail("invalid_value", f"{spec['name']} deve ser booleano.")
        return int(value)
    if kind in {"date", "datetime"}:
        try: dt.date.fromisoformat(value[:10])
        except (TypeError, ValueError): fail("invalid_value", f"{spec['name']} deve usar ISO-8601.")
    if kind == "json":
        try: return canonical(value)
        except TypeError: fail("invalid_value", f"{spec['name']} deve ser JSON.")
    if "enum" in spec and value not in spec["enum"]: fail("invalid_value", f"{spec['name']} fora do enum.")
    if "min" in spec and value < spec["min"]: fail("invalid_value", f"{spec['name']} abaixo do mínimo.")
    if "max" in spec and value > spec["max"]: fail("invalid_value", f"{spec['name']} acima do máximo.")
    return value


def sanitized(columns: list[dict[str, Any]], data: Any, partial: bool = False) -> dict[str, Any]:
    if not isinstance(data, dict): fail("invalid_request", "data deve ser objeto.")
    known = {item["name"]: item for item in columns}
    if set(data) - set(known): fail("unknown_field", "Há campos não declarados.")
    result: dict[str, Any] = {}
    for name, spec in known.items():
        if name in data: result[name] = coerce(spec, data[name])
        elif not partial and spec.get("required") and "default" not in spec: fail("invalid_value", f"{name} é obrigatório.")
    return result


def record_operation(path: Path, operation: str, request: dict[str, Any]) -> Any:
    con = structured_db(path)
    try:
        table, columns, unique_keys, indexes = table_meta(con, request.get("table")); qtable = quoted(table)
        if operation == "record.list":
            filters = request.get("filters", []); clauses = ["__archived_at IS NULL"] if not request.get("include_archived") else []; params: list[Any] = []
            valid = {item["name"] for item in columns}
            if not isinstance(filters, list): fail("invalid_request", "filters deve ser lista.")
            for item in filters:
                if not isinstance(item, dict) or item.get("field") not in valid or item.get("op") not in {"eq","in","lt","lte","gt","gte"}: fail("invalid_request", "Filtro inválido.")
                field, op, value = quoted(item["field"]), item["op"], item.get("value")
                if op == "in":
                    if not isinstance(value, list) or not value: fail("invalid_request", "Filtro in inválido.")
                    clauses.append(f"{field} IN ({','.join('?' for _ in value)})"); params.extend(value)
                else: clauses.append(f"{field}{ {'eq':'=','lt':'<','lte':'<=','gt':'>','gte':'>='}[op] }?"); params.append(value)
            order = request.get("order_by", "id")
            if order != "id" and order not in valid: fail("invalid_request", "order_by inválido.")
            direction = "DESC" if request.get("descending") else "ASC"; limit = min(max(int(request.get("limit", 50)), 1), 200); offset = max(int(request.get("offset", 0)), 0)
            rows = con.execute(f"SELECT * FROM {qtable}{' WHERE ' + ' AND '.join(clauses) if clauses else ''} ORDER BY {quoted(order)} {direction} LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
            return {"items": [dict(row) for row in rows]}
        if operation == "record.get":
            row = con.execute(f"SELECT * FROM {qtable} WHERE id=?", (request.get("id"),)).fetchone()
            if not row: fail("not_found", "Registro não encontrado.")
            return dict(row)
        require_confirmation(request)
        if operation == "record.create":
            data = sanitized(columns, request.get("data")); fields = list(data); values = [data[key] for key in fields]
            con.execute(f"INSERT INTO {qtable}({','.join(quoted(key) for key in fields)}) VALUES({','.join('?' for _ in fields)})", values)
            identifier = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        elif operation == "record.update":
            data = sanitized(columns, request.get("data"), True)
            if not data: fail("invalid_request", "Nenhum campo para atualizar.")
            cur = con.execute(f"UPDATE {qtable} SET {','.join(quoted(key)+'=?' for key in data)} WHERE id=?", [*data.values(), request.get("id")])
            if not cur.rowcount: fail("not_found", "Registro não encontrado.")
            identifier = request.get("id")
        elif operation == "record.upsert":
            data = sanitized(columns, request.get("data")); key = request.get("key")
            if not isinstance(key, list) or key not in unique_keys and key != [item["name"] for item in columns if item.get("primary_key")]: fail("invalid_request", "key deve ser uma chave única declarada.")
            if not all(name in data for name in key): fail("invalid_request", "key deve estar em data.")
            fields = list(data); updates = [name for name in fields if name not in key]
            conflict = ','.join(quoted(name) for name in key); action = "DO UPDATE SET " + ','.join(f"{quoted(name)}=excluded.{quoted(name)}" for name in updates) if updates else "DO NOTHING"
            con.execute(f"INSERT INTO {qtable}({','.join(quoted(name) for name in fields)}) VALUES({','.join('?' for _ in fields)}) ON CONFLICT({conflict}) {action}", [data[name] for name in fields])
            row = con.execute(f"SELECT id FROM {qtable} WHERE {' AND '.join(quoted(name)+'=?' for name in key)}", [data[name] for name in key]).fetchone(); identifier = row[0]
        elif operation in {"record.archive", "record.restore"}:
            cur = con.execute(f"UPDATE {qtable} SET __archived_at=? WHERE id=?", (now() if operation.endswith("archive") else None, request.get("id")))
            if not cur.rowcount: fail("not_found", "Registro não encontrado.")
            identifier = request.get("id")
        else: fail("unsupported_operation", "Operação de registro não suportada.")
        sync_record_index(con, table, columns, identifier)
        audit(con, operation, str(identifier), request); con.commit(); return {"id": identifier}
    finally: con.close()


def searchable_fields(columns: list[dict[str, Any]]) -> list[str]:
    return [str(column["name"]) for column in columns if column.get("searchable") is True and column.get("type") == "text"]


def insert_search_document(con: sqlite3.Connection, content: str, source: str, table: str, identifier: Any, field: str, archived: bool) -> None:
    if content.strip():
        con.execute("INSERT INTO search_index(content,source,table_name,item_id,field_name,archived) VALUES(?,?,?,?,?,?)", (content, source, table, str(identifier), field, int(archived)))


def expected_search_documents(con: sqlite3.Connection, text_path: Path, namespace: str) -> list[tuple[str, str, str, str, str, int]]:
    documents: list[tuple[str, str, str, str, str, int]] = []
    text = text_db(text_path)
    try:
        for row in text.execute("SELECT id,text,archived_at FROM text_memories WHERE namespace=?", (namespace,)):
            if row["text"].strip(): documents.append((row["text"], "text", "", str(row["id"]), "text", int(row["archived_at"] is not None)))
    finally:
        text.close()
    for row in con.execute("SELECT name,columns_json FROM schema_tables"):
        fields = searchable_fields(json.loads(row["columns_json"]))
        if not fields: continue
        table = str(row["name"]); selected = ",".join([quoted("id"), '"__archived_at"', *(quoted(field) for field in fields)])
        for record in con.execute(f"SELECT {selected} FROM {quoted(table)}"):
            for field in fields:
                value = record[field]
                if isinstance(value, str) and value.strip(): documents.append((value, "record", table, str(record["id"]), field, int(record["__archived_at"] is not None)))
    return documents


def rebuild_search_index(con: sqlite3.Connection, text_path: Path, namespace: str) -> dict[str, int]:
    documents = expected_search_documents(con, text_path, namespace)
    with con:
        con.execute("DELETE FROM search_index")
        for document in documents: insert_search_document(con, *document)
    return {"documents": len(documents)}


def search_status(con: sqlite3.Connection, text_path: Path, namespace: str) -> dict[str, Any]:
    expected = Counter(expected_search_documents(con, text_path, namespace))
    indexed = Counter(tuple(row) for row in con.execute("SELECT content,source,table_name,item_id,field_name,archived FROM search_index"))
    return {"valid": expected == indexed, "expected_documents": sum(expected.values()), "indexed_documents": sum(indexed.values()), "missing_documents": sum((expected - indexed).values()), "stale_documents": sum((indexed - expected).values())}


def sync_record_index(con: sqlite3.Connection, table: str, columns: list[dict[str, Any]], identifier: Any) -> None:
    con.execute("DELETE FROM search_index WHERE source='record' AND table_name=? AND item_id=?", (table, str(identifier)))
    fields = searchable_fields(columns)
    if not fields: return
    selected = ",".join([quoted("id"), '"__archived_at"', *(quoted(field) for field in fields)])
    row = con.execute(f"SELECT {selected} FROM {quoted(table)} WHERE id=?", (identifier,)).fetchone()
    if not row: return
    for field in fields:
        value = row[field]
        if isinstance(value, str): insert_search_document(con, value, "record", table, row["id"], field, row["__archived_at"] is not None)


def sync_text_index(path: Path, text_path: Path, namespace: str, identifier: str) -> None:
    text = text_db(text_path)
    try:
        row = text.execute("SELECT id,text,archived_at FROM text_memories WHERE id=? AND namespace=?", (identifier, namespace)).fetchone()
    finally:
        text.close()
    con = structured_db(path)
    try:
        with con:
            con.execute("DELETE FROM search_index WHERE source='text' AND item_id=?", (identifier,))
            if row: insert_search_document(con, row["text"], "text", "", row["id"], "text", row["archived_at"] is not None)
    finally:
        con.close()


def search_query(path: Path, text_path: Path, namespace: str, request: dict[str, Any]) -> Any:
    query = request.get("query")
    if not isinstance(query, str) or not query.strip(): fail("invalid_request", "query is required.")
    raw_sources = request.get("sources", ["text", "records"])
    sources = [raw_sources] if isinstance(raw_sources, str) else raw_sources
    if not isinstance(sources, list) or not sources or not all(source in {"text", "records"} for source in sources): fail("invalid_request", "sources must contain text and/or records.")
    source_values = ["record" if source == "records" else source for source in dict.fromkeys(sources)]
    tables = request.get("tables")
    if tables is not None and ("record" not in source_values or not isinstance(tables, list) or not tables or not all(isinstance(table, str) and NAME.fullmatch(table) for table in tables)):
        fail("invalid_request", "tables requires records and must contain valid table names.")
    limit = min(max(int(request.get("limit", 20)), 1), 100)
    con = structured_db(path)
    try:
        if tables is not None:
            known_tables = {str(row[0]) for row in con.execute("SELECT name FROM schema_tables")}
            if set(tables) - known_tables: fail("unknown_table", "tables contains a table not declared in this namespace.")
        status = search_status(con, text_path, namespace)
        if not status["valid"]: fail("search_index_stale", "Search index is stale; run search.rebuild with confirm: true.")
        clauses = ["source IN (" + ",".join("?" for _ in source_values) + ")"]; params: list[Any] = [query, *source_values]
        if not request.get("include_archived"): clauses.append("archived=0")
        if tables is not None: clauses.append("table_name IN (" + ",".join("?" for _ in tables) + ")"); params.extend(tables)
        rows = con.execute("SELECT source,table_name,item_id,field_name,snippet(search_index,0,'[',']','â€¦',12) excerpt,bm25(search_index) score FROM search_index WHERE search_index MATCH ? AND " + " AND ".join(clauses) + " ORDER BY score LIMIT ?", [*params, min(limit * 10, 1000)]).fetchall()
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            key = (row["source"], row["table_name"], row["item_id"])
            item = grouped.setdefault(key, {"source": row["source"], "id": row["item_id"], "matched_fields": [], "matches": [], "score": row["score"], "excerpt": row["excerpt"]})
            if row["table_name"]: item["table"] = row["table_name"]
            if row["field_name"] not in item["matched_fields"]: item["matched_fields"].append(row["field_name"])
            item["matches"].append({"field": row["field_name"], "excerpt": row["excerpt"]})
            item["score"] = min(item["score"], row["score"])
        items = sorted(grouped.values(), key=lambda item: item["score"])[:limit]
        return {"items": items, "score_order": "ascending"}
    finally:
        con.close()


def backup(path: Path, label: str) -> Path:
    directory = path.parent / "backups" / path.stem; directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{dt.datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{label}.sqlite3"
    source, destination = connect(path), sqlite3.connect(target)
    try: source.backup(destination)
    finally: destination.close(); source.close()
    return target


def snapshot_text(path: Path, namespace: str, target: Path) -> None:
    con = text_db(path)
    try:
        rows = [dict(row) for row in con.execute("SELECT * FROM text_memories WHERE namespace=?", (namespace,))]
        target.with_suffix(".text.json").write_text(canonical(rows), encoding="utf-8")
    finally: con.close()


def restore_text(path: Path, namespace: str, source: Path) -> None:
    sidecar = source.with_suffix(".text.json")
    if not sidecar.is_file(): return
    rows = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(rows, list): fail("invalid_request", "Snapshot textual inválido.")
    con = text_db(path)
    try:
        with con:
            con.execute("UPDATE text_memories SET superseded_by=NULL WHERE namespace=?", (namespace,))
            con.execute("DELETE FROM text_search WHERE namespace=?", (namespace,))
            con.execute("DELETE FROM text_memories WHERE namespace=?", (namespace,))
            for row in rows:
                if not isinstance(row, dict) or row.get("namespace") != namespace: fail("invalid_request", "Snapshot textual fora do namespace.")
                con.execute("INSERT INTO text_memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", tuple(row.get(key) for key in ("id","namespace","kind","text","tags_json","source_ref","confidence","created_at","updated_at","expires_at","archived_at","superseded_by")))
                con.execute("INSERT INTO text_search(id,namespace,text) VALUES(?,?,?)", (row["id"], namespace, row["text"]))
    finally: con.close()


def admin(path: Path, text_path: Path, namespace: str, operation: str, request: dict[str, Any]) -> Any:
    if operation in {"search.status", "search.rebuild"}:
        con = structured_db(path)
        try:
            if operation == "search.status": return search_status(con, text_path, namespace)
            require_confirmation(request)
            result = rebuild_search_index(con, text_path, namespace)
            audit(con, operation, None, request); con.commit()
            return {**result, "valid": search_status(con, text_path, namespace)["valid"]}
        finally:
            con.close()
    if operation == "health.check":
        con = structured_db(path)
        try:
            text = text_db(text_path)
            try: text_integrity = text.execute("PRAGMA integrity_check").fetchone()[0]
            finally: text.close()
            return {"integrity": con.execute("PRAGMA integrity_check").fetchone()[0], "text_integrity": text_integrity, "foreign_keys": [dict(r) for r in con.execute("PRAGMA foreign_key_check")], "migrations": [r[0] for r in con.execute("SELECT version FROM schema_migrations ORDER BY version")]}
        finally: con.close()
    backups = path.parent / "backups" / path.stem
    if operation == "backup.create":
        require_confirmation(request); target = backup(path, "manual"); snapshot_text(text_path, namespace, target); return {"path": str(target)}
    if operation == "backup.list": return {"items": [str(item) for item in sorted(backups.glob("*.sqlite3"))] if backups.exists() else []}
    if operation == "backup.prune":
        require_confirmation(request); keep = max(int(request.get("keep", 10)), 0); items = sorted(backups.glob("*.sqlite3"), reverse=True)
        for item in items[keep:]: item.unlink(); item.with_suffix(".text.json").unlink(missing_ok=True)
        return {"removed": len(items[keep:])}
    if operation == "export":
        con = structured_db(path)
        try:
            tables = [row[0] for row in con.execute("SELECT name FROM schema_tables")]
            text = text_db(text_path)
            try: memories = [dict(row) for row in text.execute("SELECT * FROM text_memories WHERE namespace=?", (namespace,))]
            finally: text.close()
            return {"tables": {table: [dict(row) for row in con.execute(f"SELECT * FROM {quoted(table)}")] for table in tables}, "text_memories": memories}
        finally: con.close()
    if operation == "restore":
        require_confirmation(request); source = Path(str(request.get("backup", ""))).resolve()
        if not backups.exists() or source.parent != backups.resolve() or not source.is_file(): fail("invalid_request", "Backup inválido.")
        backup(path, "pre-restore")
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        shutil.copy2(source, path)
        restore_text(text_path, namespace, source)
        con = structured_db(path)
        try: rebuild_search_index(con, text_path, namespace)
        finally: con.close()
        return {"restored": str(source)}
    fail("unsupported_operation", "Operação administrativa não suportada.")


def dispatch(workspace: Path, namespace: str, request: dict[str, Any]) -> tuple[str, Any]:
    if request.get("version") != VERSION: fail("unsupported_version", "version deve ser 1.")
    operation = request.get("operation")
    if not isinstance(operation, str): fail("invalid_request", "operation é obrigatório.")
    text_path, structured_path, _ = paths(workspace, namespace)
    if operation.startswith("text."): return operation, text_operation(text_path, structured_path, namespace, operation, request)
    if operation == "schema.plan": return operation, apply_migrations(structured_path, text_path, namespace, request, True)
    if operation == "schema.apply": return operation, apply_migrations(structured_path, text_path, namespace, request, False)
    if operation.startswith("record."): return operation, record_operation(structured_path, operation, request)
    if operation == "search.query": return operation, search_query(structured_path, text_path, namespace, request)
    return operation, admin(structured_path, text_path, namespace, operation, request)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--workspace", type=Path, required=True); parser.add_argument("--namespace", required=True); args = parser.parse_args()
    try:
        request = json.load(sys.stdin); operation, data = dispatch(args.workspace, args.namespace, request)
        print(json.dumps({"version": VERSION, "ok": True, "operation": operation, "data": data}, ensure_ascii=False, default=str)); return 0
    except StoreError as error:
        print(json.dumps({"version": VERSION, "ok": False, "error": {"code": error.code, "message": error.message}}, ensure_ascii=False)); return 1
    except (json.JSONDecodeError, ValueError, sqlite3.Error, OSError) as error:
        print(json.dumps({"version": VERSION, "ok": False, "error": {"code": "invalid_request", "message": "Requisição ou armazenamento inválido."}}, ensure_ascii=False)); return 1


if __name__ == "__main__": raise SystemExit(main())
