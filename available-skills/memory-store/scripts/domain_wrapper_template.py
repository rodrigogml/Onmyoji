"""Modelo mínimo para wrappers de domínio que usam memory-store."""
from __future__ import annotations

from memory_store import dispatch

NAMESPACE = "example/inventory"
MANIFEST = {
    "version": 1,
    "migrations": [{"version": 1, "operations": [
        {"op": "create_table", "table": "items", "columns": [
            {"name": "id", "type": "integer", "primary_key": True},
            {"name": "code", "type": "text", "required": True},
            {"name": "name", "type": "text", "required": True},
            {"name": "status", "type": "text", "required": True, "enum": ["active", "inactive"]},
        ], "unique": [["code"]], "indexes": [{"name": "items_status", "columns": ["status"]}]},
    ]}],
}


def provision(workspace):
    return dispatch(workspace, NAMESPACE, {"version": 1, "operation": "schema.apply", "manifest": MANIFEST, "confirm": True})
