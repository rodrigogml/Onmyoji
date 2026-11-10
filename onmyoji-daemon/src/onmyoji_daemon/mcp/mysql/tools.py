"""Publicação das ferramentas MySQL."""
from __future__ import annotations
from typing import Any
from ..configuration import McpConfiguration
from .service import MySQLService, OPERATIONS, WRITE_OPERATIONS

DESCRIPTIONS = {"query": "Executa SQL de consulta e devolve linhas estruturadas.", "execute": "Executa SQL amplo, inclusive DML, DDL e administração.", "script": "Executa um arquivo SQL de uma raiz autorizada do perfil.", "client": "Executa argumentos explícitos do cliente quando habilitados no perfil.", "ping": "Valida a conexão MySQL com SELECT 1."}

class MySQLToolRegistry:
    def __init__(self, configuration: McpConfiguration): self.configuration = configuration
    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_mysql_profiles()
        if not profiles: return []
        tools = []
        if len(profiles) > 1: tools.append({"name": "mysql.list_profiles", "description": "Lista perfis MySQL habilitados e seus contextos.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation in sorted(OPERATIONS):
            properties: dict[str, Any] = {}; required: list[str] = []
            if len(profiles) > 1: properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
            if operation in {"query", "execute"}: properties["sql"] = {"type": "string", "minLength": 1}; required.append("sql")
            elif operation == "script": properties["path"] = {"type": "string", "minLength": 1}; required.append("path")
            elif operation == "client": properties["args"] = {"type": "array", "items": {"type": "string"}}
            if operation in WRITE_OPERATIONS: properties["confirm"] = {"type": "boolean", "const": True}; required.append("confirm")
            schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required: schema["required"] = required
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme mysql.list_profiles."
            tools.append({"name": "mysql." + operation, "description": DESCRIPTIONS[operation] + (" Exige confirm=true." if operation in WRITE_OPERATIONS else "") + context, "input_schema": schema})
        return tools
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_mysql_profiles()
        if name == "mysql.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}: raise ValueError("Perfil MySQL indisponível.")
        operation = name.removeprefix("mysql.") if name.startswith("mysql.") else ""
        if operation not in OPERATIONS: raise ValueError("Tool MySQL indisponível.")
        return MySQLService(self.configuration.root).invoke(selected, operation, arguments)
