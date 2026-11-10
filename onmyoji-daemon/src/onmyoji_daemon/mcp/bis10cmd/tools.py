"""Publicação das operações BIS10CMD."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import Bis10CmdService, COMPANY_OPERATIONS, LEGACY_WRITE_ACTIONS, OPERATIONS, WRITE_ACTIONS
from .bills import BILL_OPERATIONS, BILL_TYPES, operation_schema


DESCRIPTIONS = {
    "ping": "Valida a fachada e retorna o horário do servidor BIS10.",
    "get_session": "Mostra informações básicas da sessão BIS10 autenticada.",
    "list_companies": "Lista empresas permitidas para o usuário BIS10.",
    "select_company": "Valida e seleciona uma empresa durante esta chamada.",
    "list_accounts": "Lista contas da empresa. args aceita name e enabled em pares.",
    "list_categories": "Lista categorias. args aceita name, enabled, manual e operation (EXPENSE/INCOMING). bill_type e bill_payment_type filtram elegibilidade para contas a pagar.",
    "list_cost_centers": "Lista centros de custo. args aceita name, enabled e categoryId.",
    "get_account_statement": "Consulta um lançamento financeiro pelo ID sem bloqueio.",
    "list_account_statements": "Lista lançamentos. args exige accountId/dateFrom/dateTo; aceita categoryId, operation, type, value ou valueMin/valueMax, offset e limit.",
    "create_account_statement": "Cria lançamento MANUAL. args exige accountId, categoryId, date e value; aceita displayLine, notes e audited.",
    "update_account_statement": "Altera lançamento MANUAL. args exige id e aceita accountId, categoryId, date, value, displayLine, notes e audited.",
    "create_transfer": "Cria transferência. args exige debitAccountId, creditAccountId, date e value; aceita displayLine, notes e audited.",
    "update_transfer": "Altera transferência. args exige id e aceita contas, date, value, displayLine, notes e audited.",
    "delete_account_statement": "Exclui lançamento MANUAL ou TRANSFER. args exige id.",
    "analyze_bill_code": "Analisa boleto ou conta de consumo com bibliotecas atuais do BIS10, sem gravar. Retorna dados extraídos, origem das sugestões, duplicidades e pendências. Complemente due_date quando o ciclo do boleto for ambíguo; análise não confirma registro bancário.",
    "create_bill_from_code": "Cadastra uma conta a pagar com uma parcela WAITING pela fachada atual. Requer request_id único e complementos da análise; não realiza pagamento. Após timeout consulte get_bill_request, sem reenviar com outra chave.",
    "get_bill": "Consulta uma conta e suas parcelas pelo bill_id, sem bloqueio e sem depender de extrato.",
    "list_bills": "Lista contas paginadas por código, estado e intervalo inclusivo de vencimento. has_more exige continuar com next_offset.",
    "list_people": "Localiza sacador, concessionária ou beneficiário por id, nome ou documento, com paginação. Não cadastra pessoas.",
    "get_bill_request": "Consulta a tentativa no diário local por request_id. Para resultado incerto, busca candidatos no BIS10 sem atribuir automaticamente a criação nem reenviar a escrita.",
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
            typed = operation_schema(operation) if operation in BILL_OPERATIONS else {}
            properties: dict[str, Any] = typed.get("properties", {})
            required: list[str] = typed.get("required", [])
            if len(profiles) > 1:
                properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}
                required.append("profile")
            if operation in COMPANY_OPERATIONS:
                properties["company_id"] = {"type": "integer", "minimum": 1, "description": "Empresa selecionada para a chamada."}
                required.append("company_id")
            if operation == "get_account_statement":
                properties["statement_id"] = {"type": "integer", "minimum": 1}
                required.append("statement_id")
            if operation in {"list_accounts", "list_categories", "list_cost_centers", "list_account_statements", *LEGACY_WRITE_ACTIONS}:
                properties["args"] = {"type": "array", "items": {"type": "string"}, "description": "Parâmetros nomeados do comando, sem action e sem confirm."}
                if operation in {"list_account_statements", *LEGACY_WRITE_ACTIONS}:
                    properties["args"]["minItems"] = 1
                    required.append("args")
            if operation == "list_categories":
                properties["bill_type"] = {"type": "string", "enum": BILL_TYPES}
                properties["bill_payment_type"] = {"type": "string", "enum": ["BOLETO", *[value for value in BILL_TYPES if value != "MANUAL"]]}
            if operation in WRITE_ACTIONS:
                properties["confirm"] = {"type": "boolean", "const": True}
                if "confirm" not in required:
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
