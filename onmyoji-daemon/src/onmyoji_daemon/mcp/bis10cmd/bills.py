"""Contratos tipados de contas a pagar, sem dependência de uma sessão Java."""
from __future__ import annotations

import copy
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from .exception import Bis10CmdError


BILL_OPERATIONS = {
    "analyze_bill_code", "create_bill_from_code", "get_bill", "list_bills",
    "list_people", "get_bill_request",
}
BILL_TYPES = [
    "MANUAL", "SERVICE_SANITATION", "SERVICE_ELETRICPOWER_GAS", "SERVICE_TELECOM",
    "SERVICE_OTHERCOMPANIES", "SERVICE_BANKEXCLUSIVEUSE", "SERVICE_CITYHALL",
    "SERVICE_GOVERNMENTAGENCIES", "SERVICE_GOVERNMENTAGENCIES_DARF",
    "SERVICE_GOVERNMENTAGENCIES_DARF_ESOCIAL", "SERVICE_GOVERNMENTAGENCIES_ESOCIAL",
    "SERVICE_GOVERNMENTAGENCIES_FGTS", "SERVICE_GOVERNMENTAGENCIES_GPS",
    "SERVICE_GOVERNMENTAGENCIES_SIMPLESNACIONAL", "SERVICE_GOVERNMENTAGENCIES_DETRAN",
    "SERVICE_TRAFFICTICKETS",
]
MONEY = {"type": "string", "pattern": r"^[0-9]{1,9}(\.[0-9]{1,2})?$", "maxLength": 12}
IDENTIFIER = {"type": "integer", "minimum": 1, "maximum": 9223372036854775807}
CODE = {"type": "string", "minLength": 44, "maxLength": 128, "pattern": r"^[0-9 .\-\t\r\n]+$"}
REQUEST_ID = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:\-]*$"}
COST_CENTER = {
    "type": "object", "additionalProperties": False,
    "required": ["cost_center_id", "ratio"],
    "properties": {"cost_center_id": IDENTIFIER, "ratio": MONEY},
}
CATEGORY = {
    "type": "object", "additionalProperties": False,
    "required": ["category_id", "value", "cost_centers"],
    "properties": {
        "category_id": IDENTIFIER, "value": MONEY,
        "cost_centers": {"type": "array", "minItems": 1, "maxItems": 100, "items": COST_CENTER},
    },
}
DRAFT_FIELDS = {
    "code": CODE,
    "due_date": {"type": "string", "format": "date"},
    "original_value": MONEY,
    "recipient_person_id": IDENTIFIER,
    "payee_person_id": IDENTIFIER,
    "categories": {"type": "array", "minItems": 1, "maxItems": 50, "items": CATEGORY},
    "entry_date": {"type": "string", "maxLength": 19, "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$"},
    "allow_past_due": {"type": "boolean"},
    "notes": {"type": "string", "maxLength": 1000},
    **{field: MONEY for field in ("fine_value", "discount_value", "other_value", "add_value", "fees_value")},
}
PAGINATION = {
    "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
}


def operation_schema(operation: str) -> dict[str, Any]:
    """Retorna schema novo sem os seletores de perfil e empresa do catálogo."""
    required: list[str] = []
    if operation in {"analyze_bill_code", "create_bill_from_code"}:
        properties = copy.deepcopy(DRAFT_FIELDS)
        required = ["code"]
        if operation == "create_bill_from_code":
            properties.update(request_id=copy.deepcopy(REQUEST_ID), confirm={"type": "boolean", "const": True})
            required += ["request_id", "confirm"]
    elif operation == "get_bill":
        properties = {"bill_id": copy.deepcopy(IDENTIFIER)}
        required = ["bill_id"]
    elif operation == "get_bill_request":
        properties = {"request_id": copy.deepcopy(REQUEST_ID)}
        required = ["request_id"]
    elif operation == "list_bills":
        properties = {
            **copy.deepcopy(PAGINATION), "code": copy.deepcopy(CODE),
            "state": {"type": "string", "enum": ["WAITING", "PARTIAL", "PAYED"]},
            "date_from": {"type": "string", "format": "date"},
            "date_to": {"type": "string", "format": "date"},
        }
    elif operation == "list_people":
        properties = {
            **copy.deepcopy(PAGINATION), "id": copy.deepcopy(IDENTIFIER),
            "name": {"type": "string", "minLength": 1, "maxLength": 200},
            "document": {"type": "string", "minLength": 1, "maxLength": 30},
        }
    else:
        raise Bis10CmdError("unsupported_operation", "Operação de contas não suportada.")
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def normalize_request(operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Valida o contrato público antes de credenciais, diário ou subprocesso."""
    payload = {key: value for key, value in arguments.items() if key not in {"company_id", "profile"}}
    validator = Draft202012Validator(operation_schema(operation), format_checker=FormatChecker())
    error = next(validator.iter_errors(payload), None)
    if error:
        field = ".".join(str(value) for value in error.absolute_path)
        raise Bis10CmdError("invalid_request", "Parâmetros de contas a pagar inválidos.", {"field": field, "reason": error.validator})
    payload = copy.deepcopy(payload)
    payload.pop("confirm", None)
    payload.pop("request_id", None)
    if "code" in payload:
        payload["code"] = re.sub(r"[ .\-\t\r\n]", "", payload["code"])
        if len(payload["code"]) not in {44, 47, 48}:
            raise Bis10CmdError("invalid_request", "O código deve conter 44, 47 ou 48 dígitos.", {"field": "code"})
    if "entry_date" in payload:
        try:
            datetime.fromisoformat(payload["entry_date"])
        except ValueError as error:
            raise Bis10CmdError("invalid_request", "Data de lançamento inválida.", {"field": "entry_date"}) from error
    if payload.get("date_from", "") > payload.get("date_to", "9999-12-31"):
        raise Bis10CmdError("invalid_request", "Intervalo de datas invertido.")
    for field in ("original_value", "fine_value", "discount_value", "other_value", "add_value", "fees_value"):
        if field in payload:
            payload[field] = format(Decimal(payload[field]), ".2f")
    for category in payload.get("categories", []):
        category["value"] = format(Decimal(category["value"]), ".2f")
        for center in category["cost_centers"]:
            center["ratio"] = format(Decimal(center["ratio"]), ".2f")
        if sum(Decimal(item["ratio"]) for item in category["cost_centers"]) != 100:
            raise Bis10CmdError("invalid_request", "O rateio por centro de custo deve totalizar 100%.")
        if any(Decimal(item["ratio"]) <= 0 for item in category["cost_centers"]):
            raise Bis10CmdError("invalid_request", "Cada percentual do rateio deve ser positivo.")
        if len({item["cost_center_id"] for item in category["cost_centers"]}) != len(category["cost_centers"]):
            raise Bis10CmdError("invalid_request", "Centro de custo repetido no mesmo rateio.")
    if len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 131072:
        raise Bis10CmdError("invalid_request", "O pedido excede o limite de 128 KiB.")
    return payload


def canonical_identity(code: str) -> str:
    """Uniformiza apenas a identidade local; a validação dos DVs continua no Java.

    Permite recuperar uma tentativa já conhecida sem chamar o servidor, tanto
    com o código de barras quanto com a representação numérica equivalente.
    Nunca autoriza uma inclusão nova sem análise pelo BISCMD.
    """
    if len(code) == 47:
        return code[:4] + code[32] + code[33:47] + code[4:9] + code[10:20] + code[21:31]
    if len(code) == 48:
        return "".join(code[start:start + 11] for start in range(0, 48, 12))
    return code


def positive_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def money(value: Any, minimum: Decimal = Decimal("0")) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d+(\.\d{1,2})?", value):
        return False
    try:
        return Decimal(value).is_finite() and Decimal(value) >= minimum
    except InvalidOperation:
        return False


def iso_date(value: Any) -> bool:
    try:
        return isinstance(value, str) and len(value) == 10 and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def validate_page(page: dict[str, Any]) -> None:
    """Recusa paginação inconsistente dos novos recursos em snake_case."""
    offset, limit, items = page.get("offset"), page.get("limit"), page.get("items")
    valid = (isinstance(offset, int) and not isinstance(offset, bool) and offset >= 0
             and positive_integer(limit) and limit <= 1000 and isinstance(items, list)
             and len(items) <= limit and isinstance(page.get("has_more"), bool))
    if not valid:
        raise Bis10CmdError("invalid_bill_response", "Paginação de contas incompleta ou inválida.")
    if page["has_more"]:
        if (len(items) != limit or not positive_integer(page.get("next_offset"))
                or page["next_offset"] != offset + limit):
            raise Bis10CmdError("invalid_bill_response", "Continuação da consulta inválida.")
    elif "next_offset" in page:
        raise Bis10CmdError("invalid_bill_response", "Página final não pode indicar continuação.")


def validate_resource(page: dict[str, Any]) -> None:
    """Valida recursos novos antes que IDs ou dados financeiros sejam confiados."""
    if (not isinstance(page, dict) or not isinstance(page.get("resource"), str)
            or not isinstance(page.get("items"), list)):
        raise Bis10CmdError("invalid_bill_response", "Envelope de contas inválido.")
    resource, items = page["resource"], page["items"]
    if resource == "billCodeAnalysis":
        if len(items) != 1 or not isinstance(items[0], dict):
            raise Bis10CmdError("invalid_bill_response", "Análise do código ausente ou ambígua.")
        item = items[0]
        if (any(not isinstance(item.get(key), bool) for key in ("code_valid", "supported", "ready_to_create"))
                or any(not isinstance(item.get(key), list) for key in ("pending_fields", "warnings", "duplicates"))):
            raise Bis10CmdError("invalid_bill_response", "Análise do código incompleta.")
        if item["code_valid"]:
            if not isinstance(item.get("barcode"), str) or not re.fullmatch(r"\d{44}", item["barcode"]):
                raise Bis10CmdError("invalid_bill_response", "Código canônico inválido na análise.")
            if not isinstance(item.get("numeric_code"), str) or not re.fullmatch(r"\d{47,48}", item["numeric_code"]):
                raise Bis10CmdError("invalid_bill_response", "Linha digitável inválida na análise.")
            if canonical_identity(item["numeric_code"]) != item["barcode"]:
                raise Bis10CmdError("invalid_bill_response", "Os códigos da análise não são equivalentes.")
        if item["ready_to_create"] and (not item["code_valid"] or not item["supported"] or item["pending_fields"] or item["duplicates"]):
            raise Bis10CmdError("invalid_bill_response", "Análise contraditória sobre possibilidade de cadastro.")
    elif resource == "bills":
        if "offset" in page:
            validate_page(page)
        seen: set[int] = set()
        for item in items:
            if (not isinstance(item, dict) or not positive_integer(item.get("id")) or item["id"] in seen
                    or not positive_integer(item.get("company_id")) or not isinstance(item.get("operation"), str)
                    or item["operation"] not in {"DEBIT", "CREDIT"} or not isinstance(item.get("state"), str)
                    or item["state"] not in {"WAITING", "PARTIAL", "PAYED"}
                    or not isinstance(item.get("bill_type"), str)
                    or not iso_date(item.get("due_date")) or not money(item.get("final_value"), Decimal("0.01"))
                    or not money(item.get("original_value")) or not isinstance(item.get("payments"), list)
                    or not item["payments"]):
                raise Bis10CmdError("invalid_bill_response", "Conta cadastrada incompleta ou inválida.")
            seen.add(item["id"])
            for field in ("recipient_person_id", "payer_person_id"):
                if item.get(field) is not None and not positive_integer(item[field]):
                    raise Bis10CmdError("invalid_bill_response", "Referência de pessoa inválida na conta.")
            payment_ids: set[int] = set()
            for payment in item["payments"]:
                if (not isinstance(payment, dict) or not positive_integer(payment.get("id"))
                        or payment["id"] in payment_ids or not positive_integer(payment.get("bill_id")) or payment["bill_id"] != item["id"]
                        or not isinstance(payment.get("payment_type"), str)
                        or not isinstance(payment.get("payment_state"), str)
                        or payment["payment_state"] not in {"WAITING", "PAYED", "BATCHFILE_CREATED", "BATCHFILE_ERROR", "BATCHFILE_SCHEDULED"}
                        or not iso_date(payment.get("due_date")) or not money(payment.get("original_value"))
                        or not money(payment.get("final_value"), Decimal("0.01"))
                        or not isinstance(payment.get("categories"), list)):
                    raise Bis10CmdError("invalid_bill_response", "Parcela incompleta ou inválida.")
                payment_ids.add(payment["id"])
                if payment.get("payee_person_id") is not None and not positive_integer(payment["payee_person_id"]):
                    raise Bis10CmdError("invalid_bill_response", "Referência de beneficiário inválida na parcela.")
                for category in payment["categories"]:
                    if (not isinstance(category, dict) or not positive_integer(category.get("category_id"))
                            or not isinstance(category.get("operation"), str) or category["operation"] not in {"EXPENSE", "INCOMING"}
                            or not money(category.get("value")) or not isinstance(category.get("cost_centers"), list)):
                        raise Bis10CmdError("invalid_bill_response", "Classificação financeira inválida.")
                    for center in category["cost_centers"]:
                        if (not isinstance(center, dict) or not positive_integer(center.get("cost_center_id"))
                                or not money(center.get("ratio"))):
                            raise Bis10CmdError("invalid_bill_response", "Rateio financeiro inválido.")
    elif resource == "people":
        validate_page(page)
        seen = set()
        for item in items:
            if (not isinstance(item, dict) or not positive_integer(item.get("id")) or item["id"] in seen
                    or not isinstance(item.get("display_name"), str)):
                raise Bis10CmdError("invalid_bill_response", "Pessoa incompleta ou repetida na consulta.")
            seen.add(item["id"])
    elif resource == "billCapabilities":
        if (len(items) != 1 or not isinstance(items[0], dict) or type(items[0].get("api_version")) is not int or items[0]["api_version"] != 1
                or not isinstance(items[0].get("actions"), list)
                or not all(isinstance(action, str) for action in items[0]["actions"])
                or not {"analyzeCode", "createFromCode", "get", "list"}.issubset(items[0]["actions"])):
            raise Bis10CmdError("bill_client_incompatible", "O JAR não oferece o contrato de contas a pagar esperado.")
    elif resource == "billError":
        if (len(items) != 1 or not isinstance(items[0], dict)
                or not isinstance(items[0].get("code"), str) or not isinstance(items[0].get("message"), str)
                or not isinstance(items[0].get("outcome"), str)
                or items[0]["outcome"] not in {"not_submitted", "rejected", "unknown"}):
            raise Bis10CmdError("invalid_bill_response", "Erro de contas sem resultado funcional reconhecido.")


def expected_resource(data: dict[str, Any], resource: str) -> dict[str, Any]:
    pages = [page for page in data.get("lookups", []) if page["resource"] == resource]
    if len(pages) != 1:
        raise Bis10CmdError("bill_query_unavailable", "O JAR não retornou a resposta estruturada de contas esperada.")
    return pages[0]
