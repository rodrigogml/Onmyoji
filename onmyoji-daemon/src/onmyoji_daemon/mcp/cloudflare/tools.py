"""Publicação dinâmica das ferramentas Cloudflare."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import CloudflareService, OPERATIONS


DESCRIPTIONS = {
    "list_zones": "Lista zonas acessíveis pelo API Token.",
    "get_zone": "Consulta uma zona Cloudflare.",
    "list_dns_records": "Lista registros DNS de uma zona.",
    "get_dns_record": "Consulta um registro DNS pelo identificador.",
    "create_dns_record": "Cria um registro DNS.",
    "update_dns_record": "Atualiza parcialmente um registro DNS.",
    "replace_dns_record": "Substitui integralmente um registro DNS.",
    "delete_dns_record": "Remove um registro DNS.",
    "export_dns_records": "Exporta os registros DNS de uma zona.",
    "import_dns_records": "Importa registros DNS em uma zona.",
    "get_dns_usage": "Consulta o uso de registros DNS de uma zona.",
}


class CloudflareToolRegistry:
    """Expõe toda a superfície Cloudflare quando há perfil válido habilitado."""

    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_cloudflare_profiles()
        if not profiles:
            return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "cloudflare.list_profiles", "description": "Lista perfis Cloudflare habilitados e seu contexto de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation, (_, method, writes) in OPERATIONS.items():
            suffix = " Exige confirm=true porque altera DNS." if writes else ""
            tools.append({"name": "cloudflare." + operation, "description": DESCRIPTIONS[operation] + suffix + self._context(profiles), "input_schema": self._schema(profiles, operation, method, writes)})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_cloudflare_profiles()
        if name == "cloudflare.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        profile = self._profile(profiles, arguments)
        operation = name.removeprefix("cloudflare.") if name.startswith("cloudflare.") else ""
        if profile and operation in OPERATIONS:
            return CloudflareService(self.configuration.root).invoke(profile, operation, arguments)
        raise ValueError("Tool Cloudflare indisponível.")

    @staticmethod
    def _context(profiles: list[dict[str, str]]) -> str:
        if len(profiles) == 1:
            return f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        return " Escolha profile conforme cloudflare.list_profiles e o contexto de cada perfil."

    @staticmethod
    def _profile(profiles: list[dict[str, str]], arguments: dict[str, Any]) -> str | None:
        if not profiles:
            return None
        name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(name, str) or name not in {item["name"] for item in profiles}:
            raise ValueError("Perfil Cloudflare indisponível.")
        return name

    @staticmethod
    def _schema(profiles: list[dict[str, str]], operation: str, method: str, writes: bool) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        if len(profiles) > 1:
            properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
            required.append("profile")
        route = OPERATIONS[operation][0]
        if "{zone_id}" in route:
            properties["zone_id"] = {"type": "string", "description": "Opcional quando o perfil já define a zona."}
        if "{record_id}" in route:
            properties["record_id"] = {"type": "string", "minLength": 1}
            required.append("record_id")
        if method == "GET":
            properties["query"] = {"type": "object", "description": "Filtros e paginação aceitos pela API Cloudflare."}
        if method in {"POST", "PATCH", "PUT"}:
            properties["body"] = {"description": "Corpo aceito pela API Cloudflare para a operação."}
            required.append("body")
        if writes:
            properties["confirm"] = {"type": "boolean", "description": "Deve ser true para confirmar a escrita."}
            required.append("confirm")
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        return schema
