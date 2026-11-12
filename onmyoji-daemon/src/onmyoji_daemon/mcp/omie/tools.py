"""Registro dinâmico das tools Omie conforme perfis habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import OPERATIONS, OmieService
from .publication import description, input_contract
from .finance import OmieFinanceFacade


FINANCE_LEGACY_OPERATIONS = {
    "document_types_list", "document_types_get", "departments_list", "departments_get", "departments_create", "departments_update", "departments_delete",
    "projects_list", "projects_get", "projects_create", "projects_update", "projects_upsert", "projects_delete",
    "categories_list", "categories_get", "categories_create", "categories_update", "category_groups_create", "category_groups_update",
    "bank_accounts_list", "bank_accounts_get", "financial_movements_list", "account_transactions_list", "account_transactions_get", "account_transactions_create", "account_transactions_update", "account_transactions_delete", "account_transfers_create",
    "payables_list", "payables_get", "payables_create", "payables_update", "payables_upsert", "payables_delete", "payables_pay", "payables_payment_cancel", "payables_create_batch", "payables_upsert_batch",
    "receivables_list", "receivables_get", "receivables_create", "receivables_update", "receivables_upsert", "receivables_delete", "receivables_receive", "receivables_receipt_cancel", "receivables_receipt_reconcile", "receivables_receipt_unreconcile", "receivables_department_allocation_create", "receivables_department_allocation_update", "receivables_department_allocation_delete", "receivables_create_batch", "receivables_upsert_batch",
}


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
            if operation in FINANCE_LEGACY_OPERATIONS:
                continue
            suffix = " Exige confirm=true porque altera dados no ERP." if writes else ""
            tools.append({"name": "omie." + operation, "description": description(operation) + suffix + self._context(profiles), "input_schema": self._schema(profiles, operation, writes)})
        tools.extend({**tool, "description": tool["description"] + self._context(profiles)} for tool in OmieFinanceFacade(self.configuration).definitions(profiles))
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_omie_profiles()
        if name == "omie.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        if not isinstance(arguments, dict):
            raise ValueError("Os argumentos da tool Omie devem ser um objeto JSON.")
        profile = self._profile(profiles, arguments)
        if name.startswith("omie.financeiro."):
            return OmieFinanceFacade(self.configuration).call(name, arguments, profile)
        operation = name.removeprefix("omie.") if name.startswith("omie.") else ""
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
    def _schema(profiles: list[dict[str, str]], operation: str, writes: bool) -> dict[str, Any]:
        field, contract = input_contract(operation)
        properties: dict[str, Any] = {field: contract}
        required: list[str] = []
        if "required" in contract or "anyOf" in contract or "allOf" in contract:
            required.append(field)
        if len(profiles) > 1:
            properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
        if writes:
            properties["confirm"] = {"type": "boolean", "description": "Deve ser true para confirmar a escrita."}; required.append("confirm")
        result: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
        if required: result["required"] = required
        return result
