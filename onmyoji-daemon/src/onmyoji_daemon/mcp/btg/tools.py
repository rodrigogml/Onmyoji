"""Publicação da primeira fase BTG conforme perfis MCP habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import BtgService


class BtgToolRegistry:
    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_btg_profiles()
        if not profiles:
            return []
        schema: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}
        tools = []
        if len(profiles) > 1:
            tools.append({"name": "btg.list_profiles", "description": "Lista os perfis BTG Empresas habilitados e seus contextos de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
            schema["properties"]["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
            schema["required"] = ["profile"]
            context = " Consulte btg.list_profiles para escolher profile conforme o contexto."
        else:
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        tools.append({"name": "btg.get_auth_status", "description": "Consulta presença de credenciais e validade LOCAL do consentimento OAuth no cofre MCP. Não consulta o banco, não renova tokens e não comprova aprovação ou execução de pagamentos." + context, "input_schema": schema})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_btg_profiles()
        if name == "btg.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        if name != "btg.get_auth_status" or not profiles:
            raise ValueError("Tool BTG indisponível.")
        allowed = {"profile"} if len(profiles) > 1 else set()
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise ValueError("Argumentos BTG inválidos.")
        profile = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(profile, str) or profile not in {item["name"] for item in profiles}:
            raise ValueError("Perfil BTG indisponível.")
        return BtgService(self.configuration.root).get_auth_status(profile)
