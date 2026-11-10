"""Publicação dinâmica das ferramentas SSH."""
from __future__ import annotations
from typing import Any
from ..configuration import McpConfiguration
from .service import OPERATIONS, SshService

DESCRIPTIONS = {"exec": "Executa um comando no servidor remoto configurado.", "upload": "Envia um arquivo de uma raiz local autorizada por SFTP.", "download": "Baixa um arquivo remoto por SFTP para uma raiz local autorizada."}

class SshToolRegistry:
    def __init__(self, configuration: McpConfiguration): self.configuration = configuration
    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_ssh_profiles()
        if not profiles: return []
        tools = []
        if len(profiles) > 1: tools.append({"name": "ssh.list_profiles", "description": "Lista perfis SSH habilitados e seus contextos.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation in sorted(OPERATIONS):
            properties: dict[str, Any] = {}; required = []
            if len(profiles) > 1: properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
            if operation == "exec": properties["command"] = {"type": "string", "minLength": 1}; required.append("command")
            else:
                properties.update({"source": {"type": "string", "minLength": 1}, "destination": {"type": "string", "minLength": 1}}); required += ["source", "destination"]
                if operation == "download": properties["overwrite"] = {"type": "boolean"}
            properties["confirm"] = {"type": "boolean", "const": True}; required.append("confirm")
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme ssh.list_profiles."
            tools.append({"name": "ssh." + operation, "description": DESCRIPTIONS[operation] + " Exige confirm=true." + context, "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}})
        return tools
    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_ssh_profiles()
        if name == "ssh.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}: raise ValueError("Perfil SSH indisponível.")
        operation = name.removeprefix("ssh.") if name.startswith("ssh.") else ""
        if operation not in OPERATIONS: raise ValueError("Tool SSH indisponível.")
        return SshService(self.configuration.root).invoke(selected, operation, arguments)
