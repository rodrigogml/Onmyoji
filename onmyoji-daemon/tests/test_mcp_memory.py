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
    content = workflows.call("workflows.read", {"id": "memory-learning"})
    assert content["module"] == "memory" and "confirm=true" in content["content"]
