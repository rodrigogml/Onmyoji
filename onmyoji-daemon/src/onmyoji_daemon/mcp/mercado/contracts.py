"""Contratos públicos das ferramentas Mercado."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


AUTH_OPERATIONS = (
    "get_auth_status", "get_oauth_callback", "start_authorization",
    "get_authorization_status", "cancel_authorization",
)

MODULE_TOOLS: dict[str, tuple[str, ...]] = {
    "mercadolivre.account": (
        "mercadolivre.account_get", "mercadolivre.account_update",
        "mercadolivre.addresses_list", "mercadolivre.accepted_payment_methods_list",
    ),
    "mercadolivre.favorites": (
        "mercadolivre.favorites_list", "mercadolivre.favorite_add", "mercadolivre.favorite_remove",
    ),
    "mercadolivre.purchases": (
        "mercadolivre.purchases_list", "mercadolivre.purchase_get",
        "mercadolivre.pack_get", "mercadolivre.purchase_discounts_get",
    ),
    "mercadolivre.shipping": (
        "mercadolivre.purchase_shipments_list", "mercadolivre.shipment_get",
    ),
    "mercadolivre.billing": (
        "mercadolivre.billing_periods_list", "mercadolivre.billing_documents_list",
        "mercadolivre.billing_summary_get", "mercadolivre.billing_details_list",
        "mercadolivre.billing_order_details_list", "mercadolivre.billing_payment_details_list",
        "mercadolivre.billing_payment_charges_list",
    ),
    "mercadopago.payments": (
        "mercadopago.payments_search", "mercadopago.payment_get",
        "mercadopago.orders_search", "mercadopago.order_get",
        "mercadopago.refunds_list", "mercadopago.refund_get",
        "mercadopago.chargebacks_search", "mercadopago.chargeback_get",
        "mercadopago.payment_methods_list",
    ),
    "mercadopago.reports": (
        "mercadopago.report_configuration_get", "mercadopago.report_configuration_set",
        "mercadopago.report_create", "mercadopago.reports_list",
        "mercadopago.report_status_get", "mercadopago.report_rows_list",
    ),
}
TOOL_MODULE = {tool: module for module, tools in MODULE_TOOLS.items() for tool in tools}

DESCRIPTIONS = {
    "mercado.list_profiles": "Lista os perfis Mercado habilitados e suas capacidades, sem expor credenciais ou caminhos do cofre.",
    "mercadolivre.account_get": "Consulta os dados da conta Mercado Livre vinculada à autorização.",
    "mercadolivre.account_update": "Atualiza somente os campos permitidos da conta vinculada. Exige confirmação explícita.",
    "mercadolivre.addresses_list": "Lista endereços expostos pela conta Mercado Livre autorizada.",
    "mercadolivre.accepted_payment_methods_list": "Lista meios de pagamento aceitos no site Mercado Livre do perfil.",
    "mercadolivre.favorites_list": "Lista favoritos da conta Mercado Livre autorizada.",
    "mercadolivre.favorite_add": "Adiciona um item aos favoritos. Exige confirmação e não repete escrita incerta.",
    "mercadolivre.favorite_remove": "Remove um item dos favoritos. Exige confirmação e não repete escrita incerta.",
    "mercadolivre.purchases_list": "Lista compras usando obrigatoriamente o usuário autenticado como comprador.",
    "mercadolivre.purchase_get": "Consulta uma compra da conta Mercado Livre autorizada.",
    "mercadolivre.pack_get": "Consulta um pacote associado às compras da conta autorizada.",
    "mercadolivre.purchase_discounts_get": "Consulta descontos de uma compra da conta autorizada.",
    "mercadolivre.purchase_shipments_list": "Lista envios de uma compra da conta autorizada.",
    "mercadolivre.shipment_get": "Consulta rastreamento, estado e histórico de um envio.",
    "mercadolivre.billing_periods_list": "Lista períodos de faturamento Mercado Livre.",
    "mercadolivre.billing_documents_list": "Lista documentos JSON de faturamento, sem baixar PDF, CSV ou XLSX.",
    "mercadolivre.billing_summary_get": "Consulta o resumo JSON de um período de faturamento.",
    "mercadolivre.billing_details_list": "Lista detalhes JSON de faturamento.",
    "mercadolivre.billing_order_details_list": "Lista detalhes de pedidos no faturamento.",
    "mercadolivre.billing_payment_details_list": "Lista detalhes de pagamentos no faturamento.",
    "mercadolivre.billing_payment_charges_list": "Lista cobranças de pagamento no faturamento.",
    "mercadopago.payments_search": "Pesquisa pagamentos Mercado Pago em modo somente leitura.",
    "mercadopago.payment_get": "Consulta um pagamento Mercado Pago.",
    "mercadopago.orders_search": "Pesquisa orders Mercado Pago em modo somente leitura.",
    "mercadopago.order_get": "Consulta uma order Mercado Pago.",
    "mercadopago.refunds_list": "Lista reembolsos de um pagamento, sem criar reembolso.",
    "mercadopago.refund_get": "Consulta um reembolso, sem alterá-lo.",
    "mercadopago.chargebacks_search": "Pesquisa chargebacks Mercado Pago.",
    "mercadopago.chargeback_get": "Consulta um chargeback Mercado Pago.",
    "mercadopago.payment_methods_list": "Lista meios de pagamento Mercado Pago.",
    "mercadopago.report_configuration_get": "Consulta a configuração remota de uma família de relatório.",
    "mercadopago.report_configuration_set": "Atualiza configuração fechada de relatório CSV. Exige confirmação.",
    "mercadopago.report_create": "Solicita criação manual de relatório CSV. Exige confirmação.",
    "mercadopago.reports_list": "Lista relatórios remotos de uma família.",
    "mercadopago.report_status_get": "Consulta o estado de um relatório remoto.",
    "mercadopago.report_rows_list": "Lê linhas de relatório disponível usando cache transitório e cursor opaco.",
}

IDENTIFIER = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,100}$"}
DATE_TIME = {"type": "string", "format": "date-time", "maxLength": 40}
CURSOR = {"type": "string", "minLength": 1, "maxLength": 4096}
DOCUMENT_TYPE = {"type": "string", "enum": ["BILL", "CREDIT_NOTE"]}
REPORT_FAMILY = {"type": "string", "enum": ["released_money", "account_money", "activities_collection", "activities_after_collection", "activities_withdraw"]}
REPORT_COLUMNS: dict[str, set[str]] = {
    "released_money": set("DATE DATE_SHORT SOURCE_ID EXTERNAL_REFERENCE RECORD_TYPE DESCRIPTION NET_CREDIT_AMOUNT NET_DEBIT_AMOUNT SELLER_AMOUNT GROSS_AMOUNT METADATA MP_FEE_AMOUNT FINANCING_FEE_AMOUNT SHIPPING_FEE_AMOUNT TAXES_AMOUNT COUPON_AMOUNT INSTALLMENTS PAYMENT_METHOD PAYMENT_METHOD_TYPE TAX_DETAIL TAX_AMOUNT_TELCO TRANSACTION_APPROVAL_DATE TRANSACTION_APPROVAL_DATE_SHORT TRANSACTION_DATE TRANSACTION_DATE_SHORT POS_ID POS_NAME EXTERNAL_POS_ID STORE_ID STORE_NAME EXTERNAL_STORE_ID ORDER_ID SHIPPING_ID SHIPMENT_MODE PACK_ID TAXES_DISAGGREGATED EFFECTIVE_COUPON_AMOUNT POI_ID BALANCE_AMOUNT PAYOUT_BANK_ACCOUNT_NUMBER PRODUCT_SKU SALE_DETAIL CURRENCY FRANCHISE LAST_FOUR_DIGITS ORDER_MP TRANSACTION_INTENT_ID PURCHASE_ID IS_RELEASED SHIPPING_ORDER_ID ISSUER_NAME APPLICATION_ID AUTHORIZATION_CODE CARD_ENTRY_MODE SEGMENT_DETAIL AUTHENTICATED_PAYER".split()),
    "account_money": set("EXTERNAL_REFERENCE SOURCE_ID USER_ID PAYMENT_METHOD PAYMENT_METHOD_TYPE SITE TRANSACTION_TYPE TRANSACTION_AMOUNT TRANSACTION_CURRENCY TRANSACTION_DATE TRANSACTION_DATE_SHORT FEE_AMOUNT SETTLEMENT_NET_AMOUNT SETTLEMENT_CURRENCY SETTLEMENT_DATE SETTLEMENT_DATE_SHORT REAL_AMOUNT COUPON_AMOUNT METADATA MKP_FEE_AMOUNT MP_FEE_AMOUNT FINANCING_FEE_AMOUNT SHIPPING_FEE_AMOUNT TAXES_AMOUNT INSTALLMENTS ORDER_ID SHIPPING_ID SHIPMENT_MODE PACK_ID TAXES_DISAGGREGATED POI_ID POI_WALLET_NAME POI_BANK_NAME DESCRIPTION MONEY_RELEASE_DATE MONEY_RELEASE_DATE_SHORT IS_RELEASED CARD_INITIAL_NUMBER PAY_BANK_TRANSFER_ID PURCHASE_ID SHIPPING_ORDER_ID APPLICATION_ID AUTHORIZATION_CODE CARD_ENTRY_MODE SEGMENT_DETAIL AUTHENTICATED_PAYER".split()),
    "activities_collection": set("date_created date_approved date_released operation_id external_reference status status_detail operation_type transaction_amount net_received_amount mercadopago_fee payment_type installments amount_refunded chargeback_id claim_id store_id pos_id rejection_causes counterpart_name counterpart_email buyer_document".split()),
    "activities_after_collection": set("date_created date_approved operation_id external_reference status status_detail operation_type transaction_amount net_received_amount mercadopago_fee payment_type installments store_id counterpart_name".split()),
    "activities_withdraw": set("date_created operation_id status operation_type transaction_amount net_received_amount counterpart_name bank_account".split()),
}


def _object(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _paged(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return _object({**(extra or {}), "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR})


def _identifier(name: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return _object({name: IDENTIFIER, **(extra or {})}, [name])


def auth_schema(operation: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    if operation in {"get_authorization_status", "cancel_authorization"}:
        properties["session_id"] = {"type": "string", "minLength": 1, "maxLength": 100}
    if operation in {"start_authorization", "cancel_authorization"}:
        properties["confirm"] = {"type": "boolean"}
        required.append("confirm")
    return _object(properties, required)


def tool_schema(name: str) -> dict[str, Any]:
    schemas: dict[str, dict[str, Any]] = {
        "mercadolivre.account_get": _object(),
        "mercadolivre.account_update": _object({
            "first_name": {"type": "string", "minLength": 1, "maxLength": 100},
            "last_name": {"type": "string", "minLength": 1, "maxLength": 100},
            "address": {"type": "string", "minLength": 1, "maxLength": 250},
            "state": {"type": "string", "minLength": 1, "maxLength": 100},
            "city": {"type": "string", "minLength": 1, "maxLength": 100},
            "zip_code": {"type": "string", "pattern": "^[0-9A-Za-z -]{3,20}$"},
            "phone": _object({"area_code": {"type": "string", "pattern": "^[0-9]{1,5}$"}, "number": {"type": "string", "pattern": "^[0-9 -]{5,30}$"}, "extension": {"type": "string", "pattern": "^[0-9]{1,10}$"}}, ["area_code", "number"]),
            "company": _object({"corporate_name": {"type": "string", "minLength": 1, "maxLength": 200}, "brand_name": {"type": "string", "minLength": 1, "maxLength": 200}}),
            "confirm": {"type": "boolean"},
        }, ["confirm"]),
        "mercadolivre.addresses_list": _paged(),
        "mercadolivre.accepted_payment_methods_list": _object(),
        "mercadolivre.favorites_list": _paged(),
        "mercadolivre.favorite_add": _object({"item_id": IDENTIFIER, "confirm": {"type": "boolean"}}, ["item_id", "confirm"]),
        "mercadolivre.favorite_remove": _object({"item_id": IDENTIFIER, "confirm": {"type": "boolean"}}, ["item_id", "confirm"]),
        "mercadolivre.purchases_list": _paged({"date_from": DATE_TIME, "date_to": DATE_TIME, "status": {"type": "string", "enum": ["confirmed", "payment_required", "payment_in_process", "paid", "cancelled", "invalid"]}, "search": {"type": "string", "minLength": 1, "maxLength": 200}, "sort": {"type": "string", "enum": ["date_asc", "date_desc"]}}),
        "mercadolivre.purchase_get": _identifier("purchase_id"),
        "mercadolivre.pack_get": _identifier("pack_id"),
        "mercadolivre.purchase_discounts_get": _identifier("purchase_id"),
        "mercadolivre.purchase_shipments_list": _identifier("purchase_id", {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}),
        "mercadolivre.shipment_get": _identifier("shipment_id"),
        "mercadolivre.billing_periods_list": _object({"group": {"type": "string", "enum": ["ML", "MP"]}, "document_type": DOCUMENT_TYPE, "limit": {"type": "integer", "minimum": 1, "maximum": 12}, "cursor": CURSOR}, ["group", "document_type"]),
        "mercadolivre.billing_documents_list": _object({"group": {"type": "string", "enum": ["ML", "MP"]}, "period": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-01$"}, "document_type": DOCUMENT_TYPE, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["group", "period"]),
        "mercadolivre.billing_summary_get": _object({"group": {"type": "string", "enum": ["ML", "MP"]}, "period": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-01$"}}, ["group", "period"]),
        "mercadolivre.billing_details_list": _object({"group": {"type": "string", "enum": ["ML", "MP"]}, "period": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-01$"}, "document_type": DOCUMENT_TYPE, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["group", "period", "document_type"]),
        "mercadolivre.billing_order_details_list": _object({"group": {"type": "string", "enum": ["ML"]}, "order_ids": {"type": "array", "minItems": 1, "maxItems": 60, "items": IDENTIFIER}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["group", "order_ids"]),
        "mercadolivre.billing_payment_details_list": _object({"group": {"type": "string", "enum": ["ML"]}, "period": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-01$"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["group", "period"]),
        "mercadolivre.billing_payment_charges_list": _object({"payment_id": IDENTIFIER, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["payment_id"]),
        "mercadopago.payments_search": _paged({"date_from": DATE_TIME, "date_to": DATE_TIME, "status": {"type": "string", "maxLength": 60}, "external_reference": {"type": "string", "maxLength": 200}, "sort": {"type": "string", "enum": ["date_created", "date_last_updated"]}, "criteria": {"type": "string", "enum": ["asc", "desc"]}}),
        "mercadopago.payment_get": _identifier("payment_id"),
        "mercadopago.orders_search": _object({"date_from": DATE_TIME, "date_to": DATE_TIME, "status": {"type": "string", "maxLength": 60}, "external_reference": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["date_from", "date_to"]),
        "mercadopago.order_get": _identifier("order_id"),
        "mercadopago.refunds_list": _identifier("payment_id", {"limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}),
        "mercadopago.refund_get": _object({"payment_id": IDENTIFIER, "refund_id": IDENTIFIER}, ["payment_id", "refund_id"]),
        "mercadopago.chargebacks_search": _object({"payment_id": IDENTIFIER, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["payment_id"]),
        "mercadopago.chargeback_get": _identifier("chargeback_id"),
        "mercadopago.payment_methods_list": _object(),
        "mercadopago.report_configuration_get": _object({"family": REPORT_FAMILY}, ["family"]),
        "mercadopago.report_configuration_set": _object({"family": REPORT_FAMILY, "columns": {"type": "array", "minItems": 1, "maxItems": 200, "uniqueItems": True, "items": {"type": "string", "pattern": "^[A-Za-z0-9_]{1,80}$"}}, "file_prefix": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,60}$"}, "separator": {"type": "string", "enum": [",", ";", "|"]}, "timezone": {"type": "string", "minLength": 1, "maxLength": 80}, "include_withdrawal_at_end": {"type": "boolean"}, "execute_after_withdrawal": {"type": "boolean"}, "check_available_balance": {"type": "boolean"}, "compensate_detail": {"type": "boolean"}, "coupon_detailed": {"type": "boolean"}, "include_withdraw": {"type": "boolean"}, "refund_detailed": {"type": "boolean"}, "shipping_detail": {"type": "boolean"}, "show_chargeback_cancel": {"type": "boolean"}, "show_fee_prevision": {"type": "boolean"}, "confirm": {"type": "boolean"}}, ["family", "columns", "confirm"]),
        "mercadopago.report_create": _object({"family": REPORT_FAMILY, "date_from": DATE_TIME, "date_to": DATE_TIME, "confirm": {"type": "boolean"}}, ["family", "date_from", "date_to", "confirm"]),
        "mercadopago.reports_list": _paged({"family": REPORT_FAMILY}),
        "mercadopago.report_status_get": _object({"family": REPORT_FAMILY, "report_id": IDENTIFIER}, ["family", "report_id"]),
        "mercadopago.report_rows_list": _object({"family": REPORT_FAMILY, "report_id": IDENTIFIER, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "cursor": CURSOR}, ["family", "report_id"]),
    }
    return deepcopy(schemas[name])


def description(name: str) -> str:
    if name in DESCRIPTIONS:
        return DESCRIPTIONS[name]
    provider, operation = name.split(".", 1)
    auth = {
        "get_auth_status": "Consulta o estado local da autorização, sem renovar tokens.",
        "get_oauth_callback": "Obtém o callback HTTPS calculado pelo Gateway e compara com o aplicativo configurado.",
        "start_authorization": "Inicia autorização OAuth remota. Exige confirmação e retorna um link para o usuário.",
        "get_authorization_status": "Consulta uma sessão OAuth sem expor códigos ou tokens.",
        "cancel_authorization": "Cancela somente a sessão OAuth pendente. Exige confirmação e preserva tokens válidos.",
    }[operation]
    return auth.replace("OAuth", f"OAuth {provider}")
