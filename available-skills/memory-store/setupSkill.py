#!/usr/bin/env python3
"""Descoberta e diagnóstico da skill local memory-store."""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, default=ROOT)
    parser.add_argument("--action", choices=["describe", "status", "configure"], default="configure")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.action == "describe":
        data = {"id": "memory-store", "title": "Memória Estruturada", "description": "Texto indexado e tabelas locais tipadas por namespace."}
        print(json.dumps(data, ensure_ascii=False) if args.json else data["title"])
        return 0
    try:
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        connection.close()
        data = {"configured": True, "valid": True, "message": "SQLite com FTS5 disponível; a skill não requer perfil nem credenciais."}
    except sqlite3.Error:
        data = {"configured": False, "valid": False, "message": "O Python atual não oferece SQLite FTS5."}
    print(json.dumps(data, ensure_ascii=False) if args.json else data["message"])
    return 0 if data["valid"] else 2

if __name__ == "__main__":
    raise SystemExit(main())
