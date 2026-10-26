"""Publicação das operações BIS10CMD."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import Bis10CmdService, OPERATIONS, WRITE_ACTIONS


DESCRIPTIONS = {
    "ping": "Valida a fachada e retorna o horário do servidor BIS10.",
    "get_session": "Mostra informações básicas da sessão BIS10 autenticada.",
    "list_companies": "Lista empresas permitidas para o usuário BIS10.",
    "select_company": "Valida e seleciona uma empresa durante esta chamada.",
    "list_accounts": "Lista contas da empresa. args aceita name e enabled em pares.",
    "list_categories": "Lista categorias. args aceita name, enabled, manual e operation (EXPENSE/INCOMING).",
    "list_cost_centers": "Lista centros de custo. args aceita name, enabled e categoryId.",
    "get_account_statement": "Consulta um lançamento financeiro pelo ID sem bloqueio.",
    "list_account_statements": "Lista lançamentos. args exige accountId/dateFrom/dateTo; aceita categoryId, operation, type, value ou valueMin/valueMax, offset e limit.",
    "create_account_statement": "Cria lançamento MANUAL. args exige accountId, categoryId, date e value; aceita displayLine, notes e audited.",
    "update_account_statement": "Altera lançamento MANUAL. args exige id e aceita accountId, categoryId, date, value, displayLine, notes e audited.",
    "create_transfer": "Cria transferência. args exige debitAccountId, creditAccountId, date e value; aceita displayLine, notes e audited.",
    "update_transfer": "Altera transferência. args exige id e aceita contas, date, value, displayLine, notes e audited.",
    "delete_account_statement": "Exclui lançamento MANUAL ou TRANSFER. args exige id.",
}


class Bis10CmdToolRegistry:
    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_bis10cmd_profiles()
        if not profiles:
            return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "bis10cmd.list_profiles", "description": "Lista perfis BIS10CMD habilitados e seus contextos.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation in sorted(OPERATIONS):
            properties: dict[str, Any] = {}
            required: list[str] = []
            if len(profiles) > 1:
                properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
                required.append("profile")
            if operation in {"select_company", "list_accounts", "list_categories", "list_cost_centers", "get_account_statement", "list_account_statements", *WRITE_ACTIONS}:
                properties["company_id"] = {"type": "integer", "minimum": 1, "description": "Empresa selecionada para a chamada."}
                required.append("company_id")
            if operation == "get_account_statement":
                properties["statement_id"] = {"type": "integer", "minimum": 1}
                required.append("statement_id")
            if operation in {"list_accounts", "list_categories", "list_cost_centers", "list_account_statements", *WRITE_ACTIONS}:
                properties["args"] = {"type": "array", "items": {"type": "string"}, "description": "Parâmetros nomeados do comando, sem action e sem confirm."}
                if operation in {"list_account_statements", *WRITE_ACTIONS}:
                    properties["args"]["minItems"] = 1
                    required.append("args")
            if operation in WRITE_ACTIONS:
                properties["confirm"] = {"type": "boolean", "const": True}
                required.append("confirm")
            schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required:
                schema["required"] = required
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme bis10cmd.list_profiles."
            description = DESCRIPTIONS[operation] + (" Exige confirm=true." if operation in WRITE_ACTIONS else "") + context
            tools.append({"name": "bis10cmd." + operation, "description": description, "input_schema": schema})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_bis10cmd_profiles()
        if name == "bis10cmd.list_profiles" and len(profiles) > 1:
            return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}:
            raise ValueError("Perfil BIS10CMD indisponível.")
        operation = name.removeprefix("bis10cmd.") if name.startswith("bis10cmd.") else ""
        if operation not in OPERATIONS:
            raise ValueError("Tool BIS10CMD indisponível.")
        return Bis10CmdService(self.configuration.root).invoke(selected, operation, arguments)
