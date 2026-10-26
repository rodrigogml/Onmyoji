"""Descrições e schemas fechados das ferramentas de pagamentos BTG."""
from __future__ import annotations

from typing import Any


PAYMENT_DESCRIPTIONS = {
    "prepare_payment": "Registra preparação LOCAL de um pagamento individual após autorização explícita (confirm=true). Não envia ao banco. Devolve ID, resumo completo e preparationHash. Mostre modalidade, conta, valor, beneficiário e datas ao usuário antes de pedir autorização específica para envio. Nunca prepare outro pagamento para contornar uma iniciação incerta.",
    "submit_payment": "Envia UMA preparação após autorização explícita do usuário para o resumo completo, payment_id e preparation_hash exatos; exige confirm=true. Aceite da iniciação NÃO comprova pagamento nem aprovação bancária. Resultado unknown/sending exige refresh_payment; nunca reenvie nem crie nova preparação para contorná-lo. O banco exige aprovação dos responsáveis.",
    "cancel_payment": "Solicita cancelamento de um pagamento após autorização específica para payment_id/preparation_hash (confirm=true). Confere a situação recente; o banco decide elegibilidade. Solicitação aceita NÃO comprova cancelamento. Consulte refresh_payment para observar o resultado; não repita uma solicitação incerta.",
    "list_payments": "Lista memória LOCAL de pagamentos por filtros e paginação. Informe origem e última consulta bem-sucedida; memória antiga não comprova situação atual no banco.",
    "get_payment": "Obtém registro LOCAL de pagamento, evidências, datas distintas e pendências. Não consulta o banco; use refresh_payment para atualizar.",
    "get_payment_history": "Consulta histórico imutável LOCAL de eventos de um pagamento. Não invente aprovador nem horário de assinatura; não são expostos pela API.",
    "refresh_payment": "Consulta o banco e atualiza evidências de um pagamento local. Recupera iniciações incertas sem reenviar, exige correspondência única de referência, conta, valor e modalidade. Execução observada não informa aprovador/horário de assinatura. Falhas preservam o último estado e sua desatualização.",
    "get_payment_receipt": "Obtém PDF de um pagamento observado como CONFIRMED e salva somente no workspace externo; retorna o caminho. Não envie o arquivo a terceiros sem instrução do usuário. Atualize o registro antes quando necessário.",
    "get_monitor_status": "Consulta fila e habilitação LOCAL do monitor do perfil. Não prova saúde do processo contínuo: consulte o status do daemon para isso.",
    "check_pending_payments": "Executa ciclo limitado de consultas para pagamentos pendentes da memória. Nunca cria, envia ou reenvia pagamentos. Devolve checked, busy e hasMore; não conclua que a fila está vazia quando hasMore=true.",
}


def preparation_schema() -> dict[str, Any]:
    text = {"type": "string", "minLength": 1, "maxLength": 500}
    amount = {"type": "string", "pattern": "^[0-9]+(?:\\.[0-9]{1,2})?$", "description": "Valor decimal exato em texto; nunca float."}
    date = {"type": "string", "format": "date", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
    account = {"type": "object", "properties": {"type": {"enum": ["CC", "PG", "PP"]}, **{key: {"type": "string", "pattern": "^[0-9]+$", "maxLength": 40} for key in ("number", "branch", "bankCode")}}, "required": ["type", "number", "branch", "bankCode"], "additionalProperties": False}
    def party(with_account: bool = False):
        properties = {"name": text, "taxId": {"type": "string", "pattern": "^(?:[0-9]{11}|[0-9]{14})$"}}
        if with_account:
            properties["account"] = account
        return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    def detail(properties: dict, required: list[str] | None = None):
        return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, "additionalProperties": False}
    variants = {
        "PIX_KEY": detail({"key": detail({"value": {**text, "maxLength": 200}}), "creditParty": party()}),
        "PIX_QR_CODE": detail({"emv": {**text, "maxLength": 4096}, "creditParty": party()}),
        "PIX_MANUAL": detail({"creditParty": party(True)}),
        "TED": detail({"creditParty": party(True)}),
        "BANKSLIP": detail({"digitableLine": {"type": "string", "pattern": "^[0-79][0-9]{46}$"}}),
        "UTILITIES": {"oneOf": [detail({"barcode": {"type": "string", "pattern": "^8[0-9]{43}$"}}), detail({"digitableLine": {"type": "string", "pattern": "^8[0-9]{47}$"}})]},
        "DARF": detail({"taxPayer": detail({"id": {"type": "string", "pattern": "^[0-9]{14}$"}, "name": text}), "expireDate": date, "baselinePeriodDate": date, "principalAmount": amount, "feeAmount": amount, "fineAmount": amount, "treasuryRevenueCode": {**text, "maxLength": 20}, "referenceNumber": {**text, "maxLength": 80}}, ["taxPayer", "expireDate", "baselinePeriodDate", "principalAmount", "treasuryRevenueCode"]),
    }
    return {"oneOf": [{"type": "object", "properties": {"type": {"const": kind}, "amount": amount, "paymentDate": date, "detail": specific, "description": text, "internalDescription": text, "dueDate": date, "document": text, "reference": text}, "required": ["type", "amount", "paymentDate", "detail"], "additionalProperties": False} for kind, specific in variants.items()]}


def payment_schema(operation: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required = []
    if operation == "prepare_payment":
        properties["body"] = preparation_schema()
        required.append("body")
    elif operation == "list_payments":
        filters = {key: {"type": "string"} for key in ("id", "submissionState", "approvalState", "bankStatus", "createdFrom", "createdTo", "dueFrom", "dueTo", "paymentFrom", "paymentTo")}
        filters.update(limit={"type": "integer", "minimum": 1, "maximum": 500}, offset={"type": "integer", "minimum": 0}, dueOnly={"type": "boolean"})
        properties["filters"] = {"type": "object", "properties": filters, "additionalProperties": False}
    elif operation not in {"get_monitor_status", "check_pending_payments"}:
        properties["payment_id"] = {"type": "string", "minLength": 1, "maxLength": 100, "description": "ID local devolvido por prepare_payment, não ID bancário."}
        required.append("payment_id")
    if operation in {"submit_payment", "cancel_payment"}:
        properties["preparation_hash"] = {"type": "string", "pattern": "^[a-f0-9]{64}$", "description": "preparationHash do resumo exato autorizado pelo usuário."}
        required.append("preparation_hash")
    if operation in {"prepare_payment", "submit_payment", "cancel_payment"}:
        properties["confirm"] = {"type": "boolean", "description": "true somente após autorização explícita do usuário para esta operação e resumo."}
        required.append("confirm")
    result = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        result["required"] = required
    return result
