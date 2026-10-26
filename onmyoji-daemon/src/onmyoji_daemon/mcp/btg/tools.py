"""Publicação de diagnóstico, OAuth e consultas BTG conforme perfis habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .contracts import CONTRACTS
from .service import BtgService


READS = {"get_account": "accounts.get", "get_balance": "balances.get", "list_statements": "statements.list", "list_dda": "dda.list", "get_barcode": "barcodes.get", "list_bank_payments": "payments.remote-list"}
DESCRIPTIONS = {
    "get_auth_status": "Consulta credenciais e validade LOCAL do consentimento OAuth no cofre MCP, sem renovar tokens ou consultar o banco.",
    "get_oauth_callback": "Obtém a URI de callback BTG MCP calculada pelo Gateway/Tunnel; cadastre-a no aplicativo BTG e no perfil antes de conectar.",
    "start_authorization": "Inicia consentimento OAuth remoto e devolve o link para o usuário abrir no navegador. Exige confirm=true. O callback grava tokens no cofre e descobre a conta. Não inicia pagamentos.",
    "get_authorization_status": "Consulta a sessão OAuth indicada ou a sessão ativa/última do perfil. Não expõe código nem tokens.",
    "cancel_authorization": "Cancela a sessão OAuth indicada ou a ativa do perfil. Exige confirm=true; não revoga consentimento já concluído.",
    "get_account": "Consulta e confere a identidade da conta BTG vinculada ao perfil.",
    "get_balance": "Consulta saldo atual da conta BTG vinculada ao perfil.",
    "list_statements": "Consulta extrato bancário no intervalo YYYY-MM-DD e tipo informados. Preserve origem e instante da consulta; não conclua completude sem verificar paginação.",
    "list_dda": "Consulta DDA disponível no banco com filtros e paginação explícita; não prepara nem paga títulos.",
    "get_barcode": "Consulta código de barras/linha digitável no banco, incluindo valor, beneficiário e vencimento. Não inicia pagamento.",
    "list_bank_payments": "Lista iniciações e pagamentos do banco. Envio aceito não comprova execução; não infira aprovador/horário de assinatura nem reenvie pagamentos com resultado incerto.",
}


def operation_schema(operation: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if operation in READS and operation != "get_account":
        query = {"type": "object", "properties": {}, "additionalProperties": False}
        fields = []
        for parameter in CONTRACTS[READS[operation]]["parameters"]:
            if parameter["in"] != "query":
                continue
            key, field = parameter["name"], dict(parameter["schema"])
            if key in {"minAmount", "maxAmount"}:
                field = {"type": "string", "description": "Decimal em texto, sem conversão por float."}
            if key in {"pageNumber", "pageSize"}:
                field = {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 1000}, {"type": "string", "pattern": "^[0-9]+$"}]}
            query["properties"][key] = field
            if parameter.get("required"):
                fields.append(key)
        if fields:
            query["required"] = fields
            required.append("params")
        properties["params"] = query
    if operation in {"get_authorization_status", "cancel_authorization"}:
        properties["session_id"] = {"type": "string", "minLength": 1, "maxLength": 100}
    if operation in {"start_authorization", "cancel_authorization"}:
        properties["confirm"] = {"type": "boolean"}
        required.append("confirm")
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


class BtgToolRegistry:
    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_btg_profiles()
        if not profiles:
            return []
        tools = []
        if len(profiles) > 1:
            tools.append({"name": "btg.list_profiles", "description": "Lista os perfis BTG Empresas habilitados e seus contextos de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
            context = " Consulte btg.list_profiles para escolher profile conforme o contexto."
        else:
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}'
        for operation, description in DESCRIPTIONS.items():
            schema = operation_schema(operation)
            if len(profiles) > 1:
                schema["properties"]["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
                schema.setdefault("required", []).append("profile")
            tools.append({"name": "btg." + operation, "description": description + context, "input_schema": schema})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_btg_profiles()
        if name == "btg.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        operation = name.removeprefix("btg.") if name.startswith("btg.") else ""
        if operation not in DESCRIPTIONS or not profiles:
            raise ValueError("Tool BTG indisponível.")
        allowed = set(operation_schema(operation)["properties"]) | ({"profile"} if len(profiles) > 1 else set())
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise ValueError("Argumentos BTG inválidos.")
        profile = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(profile, str) or profile not in {item["name"] for item in profiles}:
            raise ValueError("Perfil BTG indisponível.")
        service = BtgService(self.configuration.root)
        if operation == "get_auth_status":
            return service.get_auth_status(profile)
        return service.invoke(profile, operation, {key: value for key, value in arguments.items() if key != "profile"})
