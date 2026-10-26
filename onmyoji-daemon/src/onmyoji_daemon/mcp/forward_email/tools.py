"""Publicação dinâmica das ferramentas Forward Email."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import ForwardEmailService, OPERATIONS


DESCRIPTIONS = {
    "list_domains": "Lista domínios da conta Forward Email.",
    "get_domain": "Consulta um domínio por nome ou identificador.",
    "create_domain": "Adiciona um domínio à conta.",
    "update_domain": "Atualiza a configuração de um domínio.",
    "verify_domain_records": "Verifica os registros DNS exigidos pelo domínio.",
    "verify_domain_smtp": "Verifica os registros SMTP do domínio.",
    "list_aliases": "Lista aliases de um domínio.",
    "get_alias": "Consulta um alias por nome ou identificador.",
    "create_alias": "Cria um alias no domínio.",
    "update_alias": "Atualiza um alias do domínio.",
    "delete_alias": "Remove um alias do domínio.",
    "generate_alias_password": "Gera ou altera a senha de um alias; a resposta contém um segredo exibido uma única vez. is_override=true apaga permanentemente o armazenamento IMAP existente.",
}


class ForwardEmailToolRegistry:
    """Expõe operações Forward Email para os perfis MCP habilitados."""

    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_forward_email_profiles()
        if not profiles:
            return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "forward_email.list_profiles", "description": "Lista perfis Forward Email habilitados e seu contexto de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation, (_, method, writes) in OPERATIONS.items():
            suffix = " Exige confirm=true porque altera dados do serviço." if writes else ""
            tools.append({"name": "forward_email." + operation, "description": DESCRIPTIONS[operation] + suffix + self._context(profiles), "input_schema": self._schema(profiles, operation, method, writes)})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_forward_email_profiles()
        if name == "forward_email.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        profile = self._profile(profiles, arguments)
        operation = name.removeprefix("forward_email.") if name.startswith("forward_email.") else ""
        if profile and operation in OPERATIONS:
            return ForwardEmailService(self.configuration.root).invoke(profile, operation, arguments)
        raise ValueError("Tool Forward Email indisponível.")

    @staticmethod
    def _context(profiles: list[dict[str, str]]) -> str:
        if len(profiles) == 1:
            return f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        return " Escolha profile conforme forward_email.list_profiles e o contexto de cada perfil."

    @staticmethod
    def _profile(profiles: list[dict[str, str]], arguments: dict[str, Any]) -> str | None:
        if not profiles:
            return None
        name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(name, str) or name not in {item["name"] for item in profiles}:
            raise ValueError("Perfil Forward Email indisponível.")
        return name

    @staticmethod
    def _schema(profiles: list[dict[str, str]], operation: str, method: str, writes: bool) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required: list[str] = []
        if len(profiles) > 1:
            properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
            required.append("profile")
        route = OPERATIONS[operation][0]
        if "{domain}" in route:
            properties["domain"] = {"type": "string", "description": "Opcional quando o perfil já define o domínio."}
        if "{alias}" in route:
            properties["alias_id"] = {"type": "string", "minLength": 1}
            properties["alias"] = {"type": "string", "minLength": 1, "description": "Nome do alias, como alternativa a alias_id."}
        if method == "GET":
            properties["query"] = {"type": "object", "description": "Filtros e paginação aceitos pela API Forward Email."}
        if operation in {"create_domain", "update_domain", "create_alias", "update_alias"}:
            properties["body"] = {"type": "object", "description": "Campos aceitos pela operação Forward Email."}
            required.append("body")
        elif operation == "generate_alias_password":
            properties["body"] = {"type": "object", "description": "new_password, password, is_override ou emailed_instructions; opcional para gerar senha aleatória."}
        if writes:
            properties["confirm"] = {"type": "boolean", "description": "Deve ser true para confirmar a alteração."}
            required.append("confirm")
        schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required:
            schema["required"] = required
        if "{alias}" in route:
            schema["anyOf"] = [{"required": ["alias_id"]}, {"required": ["alias"]}]
        return schema
