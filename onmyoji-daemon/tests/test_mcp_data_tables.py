"""Contrato de schemas declarativos das skills do Shikigami."""
from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.data_tables import DataTablesError, DataTablesService, SchemaCatalog
from onmyoji_daemon.mcp.data_tables.skill import ManagedDataTablesSkill
from onmyoji_daemon.mcp.data_tables.tools import DataTablesToolRegistry


def source(root: Path, second: bool = False) -> None:
    schema = root / "shikigami" / "skills" / "crm" / "schemas" / "crm_leads"; migrations = schema / "migrations"; migrations.mkdir(parents=True, exist_ok=True)
    (schema / "schema.toml").write_text('schema_format = 1\nid = "crm_leads"\ntitle = "CRM"\ndescription = "Leads comerciais."\n', encoding="utf-8")
    migration = 'schema_id = "crm_leads"\nmigration_version = 1\n[[operations]]\ntype = "create_table"\ntable = "leads"\noperations = ["create", "get", "list", "update", "delete"]\n[[operations.columns]]\nname = "id"\ntype = "text"\nprimary_key = true\ngenerated = "uuid"\n[[operations.columns]]\nname = "name"\ntype = "text"\nrequired = true\nmax_length = 80\n[[operations.columns]]\nname = "status"\ntype = "text"\nrequired = true\nenum = ["new", "won"]\n'
    (migrations / "0001-inicial.toml").write_text(migration, encoding="utf-8")
    if second:
        migration = 'schema_id = "crm_leads"\nmigration_version = 2\n[[operations]]\ntype = "add_column"\ntable = "leads"\ncolumn = { name = "email", type = "text" }\n'
        (migrations / "0002-adicionar-email.toml").write_text(migration, encoding="utf-8")


def enabled(root: Path) -> None:
    configuration = McpConfiguration(root); configuration.save_data_tables({"schema_version": 1, "schemas": {"crm_leads": {"enabled": True}}})


def test_setup_catalog_applies_only_numbered_pending_migrations(tmp_path: Path) -> None:
    source(tmp_path); catalog = SchemaCatalog(tmp_path); schema = catalog.get("crm_leads")
    assert catalog.plan(schema)["pending"] == [1]
    assert catalog.apply("crm_leads")["applied"] == [1]
    assert catalog.plan(schema)["pending"] == []
    source(tmp_path, second=True)
    assert catalog.plan(catalog.get("crm_leads"))["pending"] == [2]


def test_changed_applied_migration_is_blocked(tmp_path: Path) -> None:
    source(tmp_path); catalog = SchemaCatalog(tmp_path); catalog.apply("crm_leads")
    path = tmp_path / "shikigami" / "skills" / "crm" / "schemas" / "crm_leads" / "migrations" / "0001-inicial.toml"
    path.write_text(path.read_text(encoding="utf-8") + "\n# alterada\n", encoding="utf-8")
    with pytest.raises(DataTablesError, match="alterada"):
        catalog.plan(catalog.get("crm_leads"))


def test_agent_can_operate_records_but_not_schema(tmp_path: Path) -> None:
    source(tmp_path); SchemaCatalog(tmp_path).apply("crm_leads"); enabled(tmp_path)
    service = DataTablesService(tmp_path, {"crm_leads"})
    created = service.invoke("create_record", {"schema": "crm_leads", "table": "leads", "data": {"name": "Ana", "status": "new"}})["record"]
    assert service.invoke("list_records", {"schema": "crm_leads", "table": "leads", "filters": [{"field": "status", "op": "eq", "value": "new"}]})["total"] == 1
    assert service.invoke("update_record", {"schema": "crm_leads", "table": "leads", "id": created["id"], "data": {"status": "won"}})["record"]["status"] == "won"
    with pytest.raises(DataTablesError):
        service.invoke("apply_schema", {"schema": "crm_leads"})


def test_registry_only_publishes_installed_enabled_schemas(tmp_path: Path) -> None:
    source(tmp_path); configuration = McpConfiguration(tmp_path)
    configuration.save_data_tables({"schema_version": 1, "schemas": {"crm_leads": {"enabled": True}}})
    assert DataTablesToolRegistry(configuration).definitions() == []
    SchemaCatalog(tmp_path).apply("crm_leads")
    assert "data_tables.create_record" in {item["name"] for item in DataTablesToolRegistry(configuration).definitions()}


def test_managed_skill_is_copied_repaired_and_removed(tmp_path: Path) -> None:
    root = tmp_path / "root"; workspace = tmp_path / "work"; workspace.mkdir()
    source = tmp_path / "canonical"; (source / "references").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: onmyoji-data-tables\ndescription: Teste.\n---\n", encoding="utf-8")
    (source / "references" / "schema-authoring.md").write_text("Regras.\n", encoding="utf-8")
    manager = ManagedDataTablesSkill(root); manager.source = source
    assert manager.synchronize(workspace, True) is True
    target = manager.target(workspace); assert (target / "references" / "schema-authoring.md").is_file()
    (target / "SKILL.md").write_text("alterada", encoding="utf-8")
    assert manager.synchronize(workspace, True) is True
    assert "onmyoji-data-tables" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert manager.synchronize(workspace, False) is True
    assert not target.exists() and not manager.state_path.exists()
