"""Registro dinâmico das tools Todoist conforme perfis habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import TodoistService


class TodoistToolRegistry:
    """Aplica a regra de zero, um ou vários perfis à superfície MCP."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_todoist_profiles()
        if not profiles: return []
        tasks_schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "todoist.list_profiles", "description": "Lista os perfis Todoist habilitados e quando usar cada um.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
            tasks_schema = {"type": "object", "properties": {"profile": {"type": "string", "enum": [profile["name"] for profile in profiles]}}, "required": ["profile"], "additionalProperties": False}
        tools.append({"name": "todoist.list_tasks", "description": "Lista tarefas ativas do perfil Todoist disponível.", "input_schema": tasks_schema})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_todoist_profiles()
        if name == "todoist.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        if name == "todoist.list_tasks" and profiles:
            profile = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
            if not isinstance(profile, str) or profile not in {item["name"] for item in profiles}: raise ValueError("Perfil Todoist indisponível.")
            return TodoistService(self.configuration.root).list_tasks(profile)
        raise ValueError("Tool Todoist indisponível.")
