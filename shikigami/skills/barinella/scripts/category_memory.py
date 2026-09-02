#!/usr/bin/env python3
"""Wrapper da memória de orientações de categorias da Barinella."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

TABLE = "category_guidance"
NAMESPACE_DEFAULT = "barinella"
MANIFEST = {
    "version": 1,
    "migrations": [{"version": 1, "operations": [{"op": "create_table", "table": TABLE, "columns": [
        {"name": "id", "type": "integer", "primary_key": True},
        {"name": "category_id", "type": "integer", "required": True},
        {"name": "aliases", "type": "text", "searchable": True},
        {"name": "when_to_use", "type": "text", "searchable": True},
        {"name": "when_not_to_use", "type": "text", "searchable": True},
        {"name": "examples", "type": "text", "searchable": True},
        {"name": "notes", "type": "text", "searchable": True},
    ], "unique": [["category_id"]]}]}],
}


def config(root: Path) -> tuple[Path, str]:
    path = root / "configs" / "barinella.toml"
    try: data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error: raise ValueError("A memória da Barinella não está configurada. Use o setup do Onmyōji.") from error
    memory_dir, namespace = data.get("memory_dir"), data.get("memory_namespace", NAMESPACE_DEFAULT)
    if not isinstance(memory_dir, str) or not Path(memory_dir).is_absolute() or not Path(memory_dir).is_dir(): raise ValueError("memory_dir da Barinella é inválido.")
    if not isinstance(namespace, str) or not namespace: raise ValueError("memory_namespace da Barinella é inválido.")
    return Path(memory_dir), namespace


def dispatch(root: Path, workspace: Path, request: dict[str, object]) -> tuple[str, object]:
    sys.path.insert(0, str(root / "available-skills" / "memory-store" / "scripts"))
    from memory_store import dispatch as memory_dispatch
    memory_dir, namespace = config(root)
    return memory_dispatch(workspace, namespace, request, memory_dir=memory_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--action", choices=["schema-plan", "schema-apply", "list", "get", "search", "upsert"], required=True)
    parser.add_argument("--category-id", type=int)
    parser.add_argument("--query")
    parser.add_argument("--data")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    try:
        request: dict[str, object] = {"version": 1}
        if args.action in {"schema-plan", "schema-apply"}:
            request |= {"operation": "schema." + args.action.removeprefix("schema-"), "manifest": MANIFEST, "confirm": args.confirm}
        elif args.action == "list": request |= {"operation": "record.list", "table": TABLE}
        elif args.action == "get":
            if args.category_id is None: raise ValueError("--category-id é obrigatório para get.")
            request |= {"operation": "record.list", "table": TABLE, "filters": [{"field": "category_id", "op": "eq", "value": args.category_id}]}
        elif args.action == "search": request |= {"operation": "search.query", "query": args.query or "", "tables": [TABLE]}
        else:
            data = json.loads(args.data or "{}")
            if not isinstance(data, dict): raise ValueError("data deve ser um objeto JSON.")
            if not isinstance(data.get("category_id"), int): raise ValueError("data.category_id inteiro é obrigatório.")
            request |= {"operation": "record.upsert", "table": TABLE, "key": ["category_id"], "data": data, "confirm": args.confirm}
        operation, data = dispatch(args.onmyoji_root.resolve(), args.workspace.resolve(), request)
        print(json.dumps({"version": 1, "ok": True, "operation": operation, "data": data}, ensure_ascii=False, default=str)); return 0
    except (ValueError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"version": 1, "ok": False, "error": {"code": "invalid_request", "message": str(error)}}, ensure_ascii=False)); return 1


if __name__ == "__main__": raise SystemExit(main())
