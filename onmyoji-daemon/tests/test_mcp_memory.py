"""Contrato da memória de instruções MCP."""
from __future__ import annotations

from pathlib import Path

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError
from onmyoji_daemon.mcp.memory.service import MemoryError, MemoryService
from onmyoji_daemon.mcp.memory.tools import MemoryToolRegistry
from onmyoji_daemon.mcp.workflows import WorkflowRegistry


def enable(root: Path, max_results: int = 5) -> McpConfiguration:
    configuration = McpConfiguration(root)
    configuration.save_memory({"schema_version": 1, "enabled": True, "max_results": max_results})
    return configuration


def instruction(service: MemoryService, condition: str = "Quando o código validado estiver pronto para entrega.") -> dict:
    return service.invoke("learn_instruction", {"applies_when": condition, "instruction": "Faça commit e envie o código antes de encerrar a tarefa.", "keywords": ["código", "commit", "push", "entrega"], "confirm": True})["instruction"]


def test_configuration_has_one_enabled_state_and_bounded_search(tmp_path: Path) -> None:
    configuration = McpConfiguration(tmp_path)
    assert configuration.memory() == {"schema_version": 1, "enabled": False, "max_results": 5}
    assert configuration.memory_tool_available() is False
    enable(tmp_path, 7)
    assert McpConfiguration(tmp_path).memory() == {"schema_version": 1, "enabled": True, "max_results": 7}
    with pytest.raises(McpConfigurationError): configuration.save_memory({"schema_version": 1, "enabled": True, "max_results": 11})


def test_memory_has_no_lifecycle_and_delete_is_physical(tmp_path: Path) -> None:
    service = MemoryService(tmp_path)
    created = instruction(service)
    found = service.invoke("search_instructions", {"query": "publicar código e fazer push"})
    assert found["instructions"] == [created]
    assert service.invoke("search_instructions", {"query": "código"})["instructions"] == [created]
    assert set(created) == {"id", "applies_when", "instruction", "keywords"}
    service.invoke("delete_instruction", {"id": created["id"], "confirm": True})
    assert service.invoke("list_instructions", {})["total"] == 0
    with pytest.raises(MemoryError, match="não encontrada"): service.invoke("get_instruction", {"id": created["id"]})


def test_memory_requires_deliberate_writes_and_revises_in_place(tmp_path: Path) -> None:
    service = MemoryService(tmp_path)
    with pytest.raises(MemoryError, match="confirm=true"):
        service.invoke("learn_instruction", {"applies_when": "Quando houver uma entrega validada.", "instruction": "Envie o código para o remoto.", "keywords": ["entrega", "código", "remoto"]})
    created = instruction(service)
    with pytest.raises(MemoryError, match="Já existe"):
        service.invoke("learn_instruction", {"applies_when": created["applies_when"].upper(), "instruction": "Crie somente o commit.", "keywords": ["código", "commit", "entrega"], "confirm": True})
    revised = service.invoke("revise_instruction", {"id": created["id"], "applies_when": created["applies_when"], "instruction": "Faça commit, envie para a main e só então encerre.", "keywords": ["código", "commit", "push", "main"], "confirm": True})["instruction"]
    assert revised["id"] == created["id"]
    assert "main" in revised["instruction"]


def test_registry_publishes_memory_tools_only_when_enabled(tmp_path: Path) -> None:
    configuration = McpConfiguration(tmp_path); registry = MemoryToolRegistry(configuration)
    assert registry.definitions() == []
    enable(tmp_path)
    definitions = MemoryToolRegistry(configuration).definitions()
    assert {item["name"] for item in definitions} == {"memory.search_instructions", "memory.get_instruction", "memory.list_instructions", "memory.learn_instruction", "memory.revise_instruction", "memory.delete_instruction"}
    search = next(item for item in definitions if item["name"] == "memory.search_instructions")
    assert "antes de" in search["description"].casefold() and "usuário" in search["description"].casefold()


def test_workflow_search_variants_match_words_without_filtering_by_domain(tmp_path: Path) -> None:
    """Valida os limites lexicais que exigem reformulação e avaliação do escopo."""
    service = MemoryService(tmp_path)
    todoist = service.invoke("learn_instruction", {
        "applies_when": "Quando apresentar tarefas na revisão diária do Todoist.",
        "instruction": "Agrupe as tarefas por projeto e preserve as tarefas sem prazo.",
        "keywords": ["todoist", "tarefa", "projeto", "agrupar"], "confirm": True,
    })["instruction"]
    ssh = service.invoke("learn_instruction", {
        "applies_when": "Quando executar uma sessão SSH para manutenção.",
        "instruction": "Confirme o host antes de iniciar a conexão.",
        "keywords": ["ssh", "host", "conexao"], "confirm": True,
    })["instruction"]

    assert service.invoke("search_instructions", {"query": "organização"})["count"] == 0
    assert service.invoke("search_instructions", {"query": "agrupar projeto"})["instructions"] == [todoist]
    assert service.invoke("search_instructions", {"query": "REVISÃO"})["instructions"] == [todoist]
    mixed = service.invoke("search_instructions", {"query": "ssh projeto"})
    assert {row["id"] for row in mixed["instructions"]} == {todoist["id"], ssh["id"]}


def test_keyword_normalization_and_revision_keep_the_current_rule_identity(tmp_path: Path) -> None:
    """Confirma os campos retornados para o procedimento de revisão integral."""
    service = MemoryService(tmp_path)
    created = service.invoke("learn_instruction", {
        "applies_when": "Quando preparar a revisão diária do Todoist.",
        "instruction": "Agrupe as tarefas por projeto e preserve as tarefas sem prazo.",
        "keywords": [" TODOIST ", "TAREFA", " Revisão   Diária ", "todoist"], "confirm": True,
    })["instruction"]
    assert created["keywords"] == ["todoist", "tarefa", "revisão diária"]
    recovered = service.invoke("search_instructions", {"query": "revisao diaria"})["instructions"][0]
    current = service.invoke("get_instruction", {"id": recovered["id"]})["instruction"]
    revised = service.invoke("revise_instruction", {
        "id": current["id"], "applies_when": current["applies_when"],
        "instruction": "Agrupe as tarefas por prazo e preserve as tarefas sem prazo.",
        "keywords": [*current["keywords"], "prazo"], "confirm": True,
    })["instruction"]
    assert revised["id"] == created["id"]
    assert service.invoke("list_instructions", {})["total"] == 1
    assert service.invoke("get_instruction", {"id": created["id"]})["instruction"] == revised


def test_workflows_are_published_and_read_only_when_memory_is_enabled(tmp_path: Path) -> None:
    configuration = McpConfiguration(tmp_path); workflows = WorkflowRegistry(configuration)
    assert workflows.definitions() == [] and workflows.developer_instructions() is None
    enable(tmp_path)
    workflows = WorkflowRegistry(configuration)
    definition = workflows.definitions()[0]
    assert definition["name"] == "workflows.read"
    assert definition["input_schema"]["properties"]["id"]["enum"] == ["memory-decision", "memory-learning"]
    instructions = workflows.developer_instructions()
    assert instructions and "memory-decision" in instructions and "workflows.read" in instructions
    for identifier in ("memory-decision", "memory-learning"):
        content = workflows.call("workflows.read", {"id": identifier})
        expected = (workflows.resources / f"{identifier}.md").read_text(encoding="utf-8").strip()
        assert content == {"id": identifier, "module": "memory", "content": expected}


def test_data_tables_schema_authoring_workflow_is_independent_of_enabled_schemas(tmp_path: Path) -> None:
    configuration = McpConfiguration(tmp_path)
    state = configuration.data_tables(); state["schema_authoring_enabled"] = True
    configuration.save_data_tables(state)

    workflows = WorkflowRegistry(configuration)
    definition = workflows.definitions()[0]
    assert definition["input_schema"]["properties"]["id"]["enum"] == ["data-tables-schema-authoring"]
    instructions = workflows.developer_instructions()
    assert instructions and "data-tables-schema-authoring" in instructions and "exclusivamente ao serviço MCP data_tables" in instructions
    content = workflows.call("workflows.read", {"id": "data-tables-schema-authoring"})
    assert content["module"] == "data_tables"
    assert "Não use este workflow" in content["content"]
    assert "Use somente `create_table`, `add_column` e `create_index`." in content["content"]
