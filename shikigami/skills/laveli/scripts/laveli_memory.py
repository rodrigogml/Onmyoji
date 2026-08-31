"""Wrapper da memória estruturada financeira da Laveli."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_CONFIG = ROOT / "configs" / "laveli.json"
sys.path.insert(0, str(ROOT / "available-skills" / "memory-store" / "scripts"))
from memory_store import StoreError, dispatch  # noqa: E402

NAMESPACE = "laveli/financeiro"
PROJECT_FIELDS = {
    "omie_code", "integration_code", "name", "search_terms",
    "inactive", "source_updated_at", "agent_notes",
}
MANIFEST = {
    "version": 1,
    "migrations": [{
        "version": 1,
        "operations": [{
            "op": "create_table",
            "table": "projects",
            "columns": [
                {"name": "id", "type": "integer", "primary_key": True},
                {"name": "omie_code", "type": "integer", "required": True},
                {"name": "integration_code", "type": "text"},
                {"name": "name", "type": "text", "required": True, "searchable": True},
                {"name": "search_terms", "type": "text", "required": True, "searchable": True},
                {"name": "inactive", "type": "boolean", "required": True},
                {"name": "source_updated_at", "type": "datetime", "required": True},
                {"name": "synced_at", "type": "datetime", "required": True},
                {"name": "agent_notes", "type": "text", "required": True, "searchable": True},
            ],
            "unique": [["omie_code"]],
            "indexes": [{"name": "projects_inactive", "columns": ["inactive"]}],
        }],
    }],
}


def configured_memory_dir(config: Path) -> Path | None:
    if not config.exists():
        return None
    try:
        settings = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("Configuração local da Laveli inválida.") from error
    if not isinstance(settings, dict):
        raise ValueError("Configuração local da Laveli inválida.")
    memory_dir = settings.get("memory_dir")
    if memory_dir is None:
        return None
    if not isinstance(memory_dir, str) or not memory_dir.strip():
        raise ValueError("memory_dir deve ser null ou um caminho absoluto.")
    result = Path(memory_dir)
    if not result.is_absolute():
        raise ValueError("memory_dir deve ser um caminho absoluto.")
    return result


def call(workspace: Path, memory_dir: Path | None, request: dict) -> dict:
    return dispatch(workspace, NAMESPACE, {"version": 1, **request}, memory_dir=memory_dir)[1]


def provision(workspace: Path, memory_dir: Path | None) -> dict:
    return call(workspace, memory_dir, {
        "operation": "schema.apply", "manifest": MANIFEST, "confirm": True,
    })


def normalize_project(project: object) -> dict:
    if not isinstance(project, dict) or set(project) != PROJECT_FIELDS:
        raise ValueError("project deve conter exatamente os campos declarados.")
    return {**project, "synced_at": datetime.now(timezone.utc).isoformat()}


def upsert_projects(workspace: Path, memory_dir: Path | None, projects: object) -> dict:
    if not isinstance(projects, list) or not projects:
        raise ValueError("projects deve ser uma lista não vazia.")
    provision(workspace, memory_dir)
    records = []
    for project in projects:
        records.append(call(workspace, memory_dir, {
            "operation": "record.upsert",
            "table": "projects",
            "key": ["omie_code"],
            "data": normalize_project(project),
            "confirm": True,
        }))
    return {"inserted_or_updated": len(records), "records": records}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args()
    try:
        memory_dir = configured_memory_dir(args.config)
        request = json.load(sys.stdin)
        if request.get("version") != 1:
            raise ValueError("Somente requisições com version=1 são aceitas.")
        operation = request.get("operation")
        if operation == "status":
            data = {
                "schema": call(args.workspace, memory_dir, {
                    "operation": "schema.plan", "manifest": MANIFEST,
                }),
                "search": call(args.workspace, memory_dir, {"operation": "search.status"}),
            }
        elif operation == "projects.upsert":
            if request.get("confirm") is not True:
                raise ValueError("projects.upsert exige confirm=true.")
            data = upsert_projects(args.workspace, memory_dir, request.get("projects"))
        elif operation == "projects.list":
            data = call(args.workspace, memory_dir, {
                "operation": "record.list", "table": "projects",
            })
        else:
            raise ValueError("Operação não permitida.")
        print(json.dumps({
            "version": 1, "ok": True, "operation": operation, "data": data,
        }, ensure_ascii=False))
        return 0
    except (StoreError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({
            "version": 1, "ok": False, "error": {"message": str(error)},
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
