"""Contratos fechados BTG e representação exata de valores monetários."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class BtgError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def bank_company_id(profile: dict[str, Any]) -> str:
    """Retorne o tenant técnico usado pela API no ambiente selecionado."""
    return "30306294000145" if profile.get("environment") == "sandbox" else str(profile["company_id"])


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise BtgError(code, message)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def future(seconds: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def money(value: Any, positive: bool = True) -> Decimal:
    require(isinstance(value, (str, int, Decimal)) and not isinstance(value, bool), "invalid_amount", "Use valor decimal em texto, sem ponto flutuante.")
    try:
        amount = Decimal(value)
        require(amount.is_finite() and amount >= 0 and (not positive or amount > 0), "invalid_amount", "Valor monetário inválido.")
        require(amount <= Decimal("999999999999.99") and amount == amount.quantize(Decimal("0.01")), "invalid_amount", "Valor excede o limite ou possui mais de duas casas decimais.")
        return amount.quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise BtgError("invalid_amount", "Valor decimal inválido.") from error


def date(value: Any) -> str:
    require(isinstance(value, str) and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), "invalid_date", "Use data YYYY-MM-DD.")
    try:
        dt.date.fromisoformat(value)
    except ValueError as error:
        raise BtgError("invalid_date", "Data civil inválida.") from error
    return value


def bank_date(value: Any) -> str | None:
    if value is None:
        return None
    require(isinstance(value, str), "invalid_bank_response", "Data bancária inválida.")
    return date(value[:10])


def text(value: Any, label: str, maximum: int = 500) -> str:
    require(isinstance(value, str) and 0 < len(value.strip()) <= maximum and not any(ord(c) < 32 for c in value), "invalid_request", f"{label} inválido.")
    return value.strip()


def json_exact(value: Any) -> str:
    """Serialize Decimal como número JSON exato, sem passagem por float."""
    if isinstance(value, Decimal):
        require(value.is_finite(), "invalid_amount", "Decimal não finito.")
        return format(value, "f")
    if isinstance(value, dict):
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + json_exact(v) for k, v in sorted(value.items())) + "}"
    if isinstance(value, list):
        return "[" + ",".join(json_exact(v) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def public(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items()}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json_exact(value).encode("utf-8")).hexdigest()


CONTRACTS = json.loads(Path(__file__).with_name("api_contracts.json").read_text(encoding="utf-8"))["operations"]
TYPES = {"BANKSLIP", "UTILITIES", "PIX_KEY", "PIX_QR_CODE", "PIX_MANUAL", "TED", "DARF"}


def beneficiary(value: Any, account: bool = False) -> dict[str, Any]:
    require(isinstance(value, dict), "invalid_request", "Informe o beneficiário.")
    allowed = {"name", "taxId", "account"} if account else {"name", "taxId"}
    require(set(value) <= allowed, "invalid_request", "Campo de beneficiário não suportado.")
    result = {"name": text(value.get("name"), "Nome"), "taxId": text(value.get("taxId"), "CPF/CNPJ")}
    require(bool(re.fullmatch(r"(?:\d{11}|\d{14})", result["taxId"])), "invalid_request", "CPF/CNPJ deve conter 11 ou 14 dígitos.")
    if account:
        data = value.get("account")
        require(isinstance(data, dict) and set(data) == {"type", "number", "branch", "bankCode"}, "invalid_request", "Dados bancários incompletos.")
        require(data["type"] in {"CC", "PG", "PP"}, "invalid_request", "Tipo de conta inválido.")
        result["account"] = {k: text(v, k, 40) for k, v in data.items()}
        require(all(re.fullmatch(r"\d+", result["account"][k]) for k in ("number", "branch", "bankCode")), "invalid_request", "Banco, agência e conta devem conter dígitos.")
    return result


def prepare_item(body: Any, profile: dict[str, Any], identifier: str) -> tuple[dict[str, Any], dict[str, Any]]:
    require(isinstance(body, dict), "invalid_request", "body deve ser um objeto.")
    require(set(body) <= {"type", "amount", "paymentDate", "detail", "description", "internalDescription", "dueDate", "document", "reference"}, "invalid_request", "Campo de preparação não suportado.")
    kind = body.get("type")
    require(isinstance(kind, str) and kind in TYPES, "unsupported_payment_type", "Modalidade fora do escopo.")
    amount = money(body.get("amount"))
    require(amount <= money(profile.get("max_amount", "1000000.00")), "amount_limit", "Valor excede o limite do perfil.")
    detail = body.get("detail")
    require(isinstance(detail, dict), "invalid_request", "detail deve ser um objeto.")
    detail = json.loads(json_exact(detail), parse_float=Decimal)
    allowed = {
        "BANKSLIP": {"digitableLine"}, "UTILITIES": {"digitableLine", "barcode"},
        "PIX_KEY": {"key", "creditParty"}, "PIX_QR_CODE": {"emv", "creditParty"},
        "TED": {"creditParty"}, "PIX_MANUAL": {"creditParty"},
        "DARF": {"taxPayer", "expireDate", "principalAmount", "baselinePeriodDate", "treasuryRevenueCode", "feeAmount", "fineAmount", "referenceNumber"},
    }
    require(set(detail) <= allowed[kind], "invalid_request", "Campo detail fora do escopo.")
    if kind in {"BANKSLIP", "UTILITIES"}:
        require(len(detail) == 1, "invalid_request", "Informe somente uma linha digitável ou código de barras.")
        key = next(iter(detail))
        code = text(detail[key], "Código de pagamento", 48)
        require(code.isascii() and code.isdigit() and len(code) in ({47} if kind == "BANKSLIP" else {44, 48}), "invalid_barcode", "Código de pagamento inválido.")
        require(code.startswith("8") == (kind == "UTILITIES"), "invalid_barcode", "Modalidade incompatível com código iniciado por 8.")
    elif kind in {"PIX_KEY", "PIX_QR_CODE", "TED", "PIX_MANUAL"}:
        detail["creditParty"] = beneficiary(detail.get("creditParty"), kind in {"TED", "PIX_MANUAL"})
        if kind == "PIX_KEY":
            require(isinstance(detail.get("key"), dict) and set(detail["key"]) == {"value"}, "invalid_request", "Informe key.value.")
            detail["key"]["value"] = text(detail["key"]["value"], "Chave Pix", 200)
        if kind == "PIX_QR_CODE":
            detail["emv"] = text(detail.get("emv"), "Pix copia e cola", 4096)
    else:
        payer = detail.get("taxPayer")
        require(isinstance(payer, dict) and set(payer) == {"id", "name"}, "invalid_request", "Informe taxPayer.id e name.")
        beneficiary({"name": payer["name"], "taxId": payer["id"]})
        require(payer["id"] == bank_company_id(profile), "context_mismatch", "Pagador DARF diverge da empresa autorizada no BTG.")
        for key in ("expireDate", "baselinePeriodDate"):
            detail[key] = date(detail.get(key))
        detail["treasuryRevenueCode"] = text(detail.get("treasuryRevenueCode"), "Código da Receita", 20)
        for key in ("principalAmount", "feeAmount", "fineAmount"):
            if key == "principalAmount" or key in detail:
                detail[key] = money(detail.get(key), positive=False)
        if "referenceNumber" in detail:
            detail["referenceNumber"] = text(detail["referenceNumber"], "Referência", 80)
        require(sum((detail.get(k, Decimal(0)) for k in ("principalAmount", "feeAmount", "fineAmount")), Decimal(0)) == amount, "amount_mismatch", "Total DARF diverge dos componentes.")
    item = {"type": kind, "amount": amount, "paymentDate": date(body.get("paymentDate")), "detail": detail,
            "debitParty": {"branchCode": profile["branch_code"], "number": profile["account_number"]}, "tags": {"externalId": identifier}}
    for key in ("description", "internalDescription"):
        if key in body:
            item[key] = text(body[key], key)
    metadata = {k: text(body[k], k) for k in ("document", "reference") if k in body}
    metadata["dueDate"] = date(body["dueDate"]) if body.get("dueDate") else None
    return item, metadata
