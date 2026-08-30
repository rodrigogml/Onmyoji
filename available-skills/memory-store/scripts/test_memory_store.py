from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import io
import json
import sqlite3

from memory_store import StoreError, dispatch, main


MANIFEST = {
    "version": 1,
    "migrations": [{"version": 1, "operations": [
        {"op": "create_table", "table": "projects", "columns": [
            {"name": "id", "type": "integer", "primary_key": True},
            {"name": "code", "type": "text", "required": True},
            {"name": "name", "type": "text", "required": True, "searchable": True},
            {"name": "state", "type": "text", "required": True, "enum": ["open", "closed"]},
        ], "unique": [["code"]], "indexes": [{"name": "projects_state", "columns": ["state"]}]},
        {"op": "create_table", "table": "tasks", "columns": [
            {"name": "id", "type": "integer", "primary_key": True},
            {"name": "project_id", "type": "integer", "required": True, "reference": {"table": "projects", "column": "id"}},
            {"name": "title", "type": "text", "required": True, "searchable": True},
            {"name": "priority", "type": "integer", "min": 1, "max": 5},
        ], "indexes": [{"name": "tasks_project", "columns": ["project_id"]}]},
    ]}],
}


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.workspace = Path(self.temp.name); self.namespace = "example/projects"
    def tearDown(self): self.temp.cleanup()
    def call(self, operation, **request):
        return dispatch(self.workspace, self.namespace, {"version": 1, "operation": operation, **request})[1]

    def test_text_search_is_namespaced_and_accent_insensitive(self):
        first = self.call("text.add", text="Decisão sobre medição da obra São José", kind="decision", tags=["obra"], source_ref="ata-1", confidence="high", confirm=True)["id"]
        other = dispatch(self.workspace, "other/domain", {"version": 1, "operation": "text.add", "text": "São José externo", "confirm": True})[1]["id"]
        found = self.call("text.search", query="sao jose")
        self.assertEqual([item["id"] for item in found["items"]], [first])
        self.call("text.archive", id=first, confirm=True)
        self.assertEqual(self.call("text.search", query="sao jose")["items"], [])
        self.assertNotEqual(first, other)

    def test_schema_records_validation_and_archive(self):
        self.assertEqual(self.call("schema.plan", manifest=MANIFEST)["pending"], [1])
        self.assertEqual(self.call("schema.apply", manifest=MANIFEST, confirm=True)["applied"], [1])
        project = self.call("record.create", table="projects", data={"code": "P1", "name": "Projeto", "state": "open"}, confirm=True)["id"]
        task = self.call("record.create", table="tasks", data={"project_id": project, "title": "Planejar", "priority": 3}, confirm=True)["id"]
        self.assertEqual(self.call("record.get", table="tasks", id=task)["title"], "Planejar")
        self.call("record.archive", table="tasks", id=task, confirm=True)
        self.assertEqual(self.call("record.list", table="tasks")["items"], [])
        self.call("record.restore", table="tasks", id=task, confirm=True)
        self.assertEqual(len(self.call("record.list", table="tasks", filters=[{"field": "priority", "op": "gte", "value": 3}])["items"]), 1)
        with self.assertRaises(StoreError): self.call("record.create", table="projects", data={"code": "P2", "name": "Erro", "state": "wrong"}, confirm=True)

    def test_unified_search_returns_text_and_searchable_record_fields(self):
        self.call("schema.apply", manifest=MANIFEST, confirm=True)
        memory = self.call("text.add", text="Decisão sobre a medição da Laveli", confirm=True)["id"]
        project = self.call("record.create", table="projects", data={"code": "P1", "name": "Laveli Engenharia", "state": "open"}, confirm=True)["id"]
        task = self.call("record.create", table="tasks", data={"project_id": project, "title": "Medição Laveli", "priority": 3}, confirm=True)["id"]
        found = self.call("search.query", query="laveli")["items"]
        self.assertEqual({(item["source"], item["id"]) for item in found}, {("text", memory), ("record", str(project)), ("record", str(task))})
        records = [item for item in found if item["source"] == "record"]
        self.assertTrue(all(item["matched_fields"] in (["name"], ["title"]) and item["matches"] for item in records))
        self.call("record.update", table="projects", id=project, data={"name": "Akuma Engenharia"}, confirm=True)
        self.assertEqual([item["id"] for item in self.call("search.query", query="akuma", sources="records")["items"]], [str(project)])
        self.call("record.archive", table="tasks", id=task, confirm=True)
        active = self.call("search.query", query="laveli", sources="records", tables=["tasks"])["items"]
        self.assertEqual(active, [])
        archived = self.call("search.query", query="laveli", sources=["records"], tables=["tasks"], include_archived=True)["items"]
        self.assertEqual([item["id"] for item in archived], [str(task)])
        self.call("record.restore", table="tasks", id=task, confirm=True)
        self.assertEqual([item["id"] for item in self.call("search.query", query="laveli", sources="records", tables=["tasks"])["items"]], [str(task)])

    def test_search_status_and_rebuild_repair_a_stale_index(self):
        self.call("schema.apply", manifest=MANIFEST, confirm=True)
        self.call("record.create", table="projects", data={"code": "P1", "name": "Laveli Engenharia", "state": "open"}, confirm=True)
        database = self.workspace / ".onmyoji" / "memory" / "example__projects.sqlite3"
        con = sqlite3.connect(database)
        try: con.execute("DELETE FROM search_index"); con.commit()
        finally: con.close()
        self.assertFalse(self.call("search.status")["valid"])
        with self.assertRaises(StoreError) as error: self.call("search.query", query="laveli")
        self.assertEqual(error.exception.code, "search_index_stale")
        self.assertEqual(self.call("search.rebuild", confirm=True)["documents"], 1)
        self.assertTrue(self.call("search.status")["valid"])

    def test_migration_checksum_confirmation_and_backup(self):
        with self.assertRaises(StoreError): self.call("schema.apply", manifest=MANIFEST)
        self.call("schema.apply", manifest=MANIFEST, confirm=True)
        changed = {**MANIFEST, "migrations": [{**MANIFEST["migrations"][0], "operations": []}]}
        with self.assertRaises(StoreError) as error: self.call("schema.plan", manifest=changed)
        self.assertEqual(error.exception.code, "migration_checksum_mismatch")
        backup = self.call("backup.create", confirm=True)["path"]
        self.assertTrue(Path(backup).is_file())
        health = self.call("health.check")
        self.assertEqual(health["integrity"], "ok")

    def test_rebuild_table_copies_data_with_declared_default(self):
        self.call("schema.apply", manifest=MANIFEST, confirm=True)
        project = self.call("record.create", table="projects", data={"code": "P1", "name": "Projeto", "state": "open"}, confirm=True)["id"]
        rebuilt = {"version": 1, "migrations": [*MANIFEST["migrations"], {"version": 2, "operations": [{"op": "rebuild_table", "table": "projects", "columns": [
            {"name": "id", "type": "integer", "primary_key": True}, {"name": "code", "type": "text", "required": True},
            {"name": "name", "type": "text", "required": True}, {"name": "state", "type": "text", "required": True, "enum": ["open", "closed"]},
            {"name": "priority", "type": "integer", "required": True, "default": 1},
        ], "unique": [["code"]], "indexes": [{"name": "projects_state", "columns": ["state"]}]}]}]}
        self.call("schema.apply", manifest=rebuilt, confirm=True)
        self.assertEqual(self.call("record.get", table="projects", id=project)["priority"], 1)

    def test_upsert_requires_declared_key_and_is_idempotent(self):
        self.call("schema.apply", manifest=MANIFEST, confirm=True)
        first = self.call("record.upsert", table="projects", key=["code"], data={"code": "P1", "name": "Original", "state": "open"}, confirm=True)["id"]
        second = self.call("record.upsert", table="projects", key=["code"], data={"code": "P1", "name": "Atualizado", "state": "closed"}, confirm=True)["id"]
        self.assertEqual(first, second)
        self.assertEqual(self.call("record.get", table="projects", id=first)["name"], "Atualizado")

    def test_invalid_protocol_and_confirmation_are_rejected(self):
        with self.assertRaises(StoreError) as error: dispatch(self.workspace, self.namespace, {"version": 2, "operation": "health.check"})
        self.assertEqual(error.exception.code, "unsupported_version")
        with self.assertRaises(StoreError) as error: self.call("text.add", text="sem confirmação")
        self.assertEqual(error.exception.code, "confirmation_required")

    def test_skill_instructions_require_model_selection_and_wrappers(self):
        skill = Path(__file__).resolve().parents[1] / "SKILL.md"
        text = skill.read_text(encoding="utf-8").casefold()
        self.assertIn("escolher o modelo antes de gravar", text)
        self.assertIn("promova-o para uma coluna", text)
        self.assertIn("wrapper da skill de domínio", text)
        self.assertIn("não à operação cotidiana do domínio", text)

    def test_restore_replaces_data_and_cli_emits_protocol_response(self):
        self.call("schema.apply", manifest=MANIFEST, confirm=True)
        record = self.call("record.create", table="projects", data={"code": "P1", "name": "Antes", "state": "open"}, confirm=True)["id"]
        memory = self.call("text.add", text="Memória anterior", confirm=True)["id"]
        snapshot = self.call("backup.create", confirm=True)["path"]
        self.call("record.update", table="projects", id=record, data={"name": "Depois"}, confirm=True)
        self.call("text.archive", id=memory, confirm=True)
        self.call("restore", backup=snapshot, confirm=True)
        self.assertEqual(self.call("record.get", table="projects", id=record)["name"], "Antes")
        self.assertEqual(self.call("text.get", id=memory)["archived_at"], None)
        self.assertEqual(len(self.call("export")["text_memories"]), 1)
        request = {"version": 1, "operation": "health.check"}
        with patch("sys.argv", ["memory_store.py", "--workspace", str(self.workspace), "--namespace", self.namespace]), patch("sys.stdin", io.StringIO(json.dumps(request))), patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(main(), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])


if __name__ == "__main__": unittest.main()
