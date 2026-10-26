"""Publicação das operações Google como ferramentas MCP."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import GoogleService, OPERATIONS, WRITE_OPERATIONS


class GoogleToolRegistry:
    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_google_profiles()
        if not profiles: return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "google.list_profiles", "description": "Lista perfis Google habilitados e o contexto de uso de cada conta.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation, (method, path) in sorted(OPERATIONS.items()):
            properties: dict[str, Any] = {
                "params": {"type": "object", "description": "Identificadores usados no caminho da API."},
                "query": {"type": "object", "description": "Parâmetros de consulta da API."},
                "body": {"description": "Corpo JSON; em drive.uploads.create, use file_path."},
                "paginate": {"type": "boolean", "default": False},
                "output_path": {"type": "string", "description": "Destino local autorizado para respostas binárias."},
            }
            required: list[str] = []
            if len(profiles) > 1: properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
            if operation in WRITE_OPERATIONS: properties["confirm"] = {"type": "boolean", "const": True}; required.append("confirm")
            schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required: schema["required"] = required
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme google.list_profiles."
            description = f"Google {method} {path}." + (" Exige confirm=true por alterar dados." if operation in WRITE_OPERATIONS else "") + context
            tools.append({"name": "google." + operation, "description": description, "input_schema": schema})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_google_profiles()
        if name == "google.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}: raise ValueError("Perfil Google indisponível.")
        operation = name.removeprefix("google.") if name.startswith("google.") else ""
        if operation not in OPERATIONS: raise ValueError("Tool Google indisponível.")
        return GoogleService(self.configuration.root).invoke(selected, operation, arguments)
