"""Registro dinâmico das tools Omie conforme perfis habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import OPERATIONS, OmieService


class OmieToolRegistry:
    """Publica operações Omie para os perfis MCP habilitados."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_omie_profiles()
        if not profiles: return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "omie.list_profiles", "description": "Lista perfis Omie habilitados e seu contexto de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation, (_, _, writes) in OPERATIONS.items():
            suffix = " Exige confirm=true porque altera dados no ERP." if writes else ""
            tools.append({"name": "omie." + operation, "description": operation.replace("_", " ") + "." + suffix + self._context(profiles), "input_schema": self._schema(profiles, writes)})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_omie_profiles()
        if name == "omie.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        profile = self._profile(profiles, arguments)
        operation = name.removeprefix("omie.")
        if profile and operation in OPERATIONS:
            return OmieService(self.configuration.root).invoke(profile, operation, arguments)
        raise ValueError("Tool Omie indisponível.")

    @staticmethod
    def _context(profiles: list[dict[str, str]]) -> str:
        if len(profiles) == 1: return f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        return " Escolha profile conforme omie.list_profiles e o contexto de cada perfil."

    @staticmethod
    def _profile(profiles: list[dict[str, str]], arguments: dict[str, Any]) -> str | None:
        if not profiles: return None
        name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(name, str) or name not in {item["name"] for item in profiles}: raise ValueError("Perfil Omie indisponível.")
        return name

    @staticmethod
    def _schema(profiles: list[dict[str, str]], writes: bool) -> dict[str, Any]:
        properties: dict[str, Any] = {"params": {"type": "object", "description": "Parâmetros de consulta Omie."}, "body": {"type": "object", "description": "Corpo da operação Omie."}}
        required: list[str] = []
        if len(profiles) > 1:
            properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
        if writes:
            properties["confirm"] = {"type": "boolean", "description": "Deve ser true para confirmar a escrita."}; required.append("confirm")
        result: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required: result["required"] = required
        return result
