"""Solicita o rateio de categorias e confere título e todas as baixas.

A única escrita é ``AlterarContaPagar``. Consultas remotas antes e depois
comprovam a classificação e a preservação financeira; não há compensação nem
repetição automática quando a Omie aceita a escrita sem persistir o rateio.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Any, Mapping

from ...state_layout import SETUP_VERSION
from .exception import OmieError


_PERCENT_TOLERANCE = Decimal("0.000001")
_CENT = Decimal("0.01")
_TITLE_ID = "codigo_lancamento_omie"
_INTEGRATION_ID = "codigo_lancamento_integracao"
_SUMMARY_NUMBERS = ("nValPago", "nValLiquido", "nValAberto", "nDesconto", "nJuros", "nMulta")
_PAYMENT_ADJUSTMENTS = ("nDesconto", "nJuros", "nMulta")
_PAYMENT_NUMBERS = ("nValorMovCC", *_PAYMENT_ADJUSTMENTS)
_AUDIT_FIELDS = ("dDtAlt", "cHrAlt", "cUsAlt")


def _error(code: str, message: str, **details: Any) -> OmieError:
    return OmieError(code, message, {"retryable": False, "versao_mcp": SETUP_VERSION, **details})


def _number(value: Any) -> Decimal:
    if type(value) not in (int, float, str):
        raise ValueError("invalid_number")
    try:
        number = Decimal(str(value))
    except DecimalException as error:
        raise ValueError("invalid_number") from error
    if not number.is_finite() or abs(number) > Decimal("1e18"):
        raise ValueError("invalid_number")
    return number


def normalize_categories(value: Any) -> list[dict[str, Any]]:
    """Valida a lista pública completa e traduz códigos e percentuais para a API.

    Exige códigos textuais únicos de até 20 caracteres e percentuais numéricos
    positivos somando 100, com tolerância de 0,000001. ``invalid_parameter``
    recusa lista vazia, números não finitos e campos adicionais antes de HTTP.
    Para consolidar o rateio, informe uma categoria com percentual 100.
    """
    def invalid(field: str) -> None:
        raise _error("invalid_parameter", "Informe categorias não vazias, com códigos únicos e percentuais positivos somando 100.", field=field)

    if not isinstance(value, list) or not value:
        invalid("dados.categorias")
    result = []
    codes: set[str] = set()
    total = Decimal(0)
    for index, row in enumerate(value):
        field = f"dados.categorias[{index}]"
        if not isinstance(row, Mapping) or set(row) != {"codigo", "percentual"}:
            invalid(field)
        code, percent = row["codigo"], row["percentual"]
        if not isinstance(code, str) or not code.strip() or len(code) > 20 or code in codes:
            invalid(field + ".codigo")
        if type(percent) not in (int, float):
            invalid(field + ".percentual")
        try:
            number = _number(percent)
        except ValueError:
            invalid(field + ".percentual")
        if not 0 < number <= 100:
            invalid(field + ".percentual")
        codes.add(code)
        total += number
        result.append({"codigo_categoria": code, "percentual": percent})
    if abs(total - 100) > _PERCENT_TOLERANCE:
        invalid("dados.categorias")
    return result


def _invalid_observation(processed: bool, reason: str, **details: Any) -> None:
    code = "category_allocation_verification_failed" if processed else "category_allocation_preflight_failed"
    message = (
        "A escrita foi processada, mas a conferência ficou inconclusiva; consulte o estado atual sem repetir a escrita."
        if processed else "Não foi possível conferir título e baixas com segurança; nenhuma escrita foi enviada."
    )
    raise _error(code, message, escrita_processada=processed, reason=reason, **details)


def _remote_number(record: Mapping[str, Any], key: str, processed: bool, *, field: str,
                   operation: str = "financial_movements_list", movement_kind: str | None = None) -> Decimal:
    """Identifica números ausentes, nulos ou inválidos sem expor o valor remoto.

    Ausência e ``null`` não comprovam zero. A falha conserva ``invalid_number``
    e informa campo, operação, tipo JSON e motivo específico. Antes da escrita
    lança ``category_allocation_preflight_failed``; depois do aceite lança
    ``category_allocation_verification_failed``, sem autorizar repetição.
    """
    present = key in record
    value = record.get(key)
    if not present:
        received_type = "missing"
    elif value is None:
        received_type = "null"
    elif type(value) is bool:
        received_type = "boolean"
    elif type(value) in (int, float):
        received_type = "number"
    elif isinstance(value, str):
        received_type = "string"
    elif isinstance(value, list):
        received_type = "array"
    elif isinstance(value, Mapping):
        received_type = "object"
    else:
        received_type = "unsupported"
    try:
        return _number(value)
    except ValueError:
        issue = received_type if received_type in {"missing", "null"} else (
            "invalid_value" if received_type in {"number", "string"} else "invalid_type"
        )
        context = {"tipo_movimento": movement_kind} if movement_kind is not None else {}
        _invalid_observation(processed, "invalid_number", field=field, operation=operation,
                             number_issue=issue, received_type=received_type, **context)


def _cause(error: Exception) -> dict[str, Any]:
    result: dict[str, Any] = {"cause_code": error.code if isinstance(error, OmieError) else "unexpected_error"}
    if isinstance(error, OmieError) and isinstance(error.details, Mapping):
        for field, low, high in (("retry_after_seconds", 1, 86400), ("http_status", 100, 599)):
            value = error.details.get(field)
            if type(value) is int and low <= value <= high:
                result[field] = value
    return result


def _payment_number(record: Mapping[str, Any], key: str, processed: bool) -> Decimal:
    """Interpreta os três ajustes opcionais de BXCP omitidos como zero.

    Desconto, juros e multa ausentes não alteram o payload nativo. Valores
    presentes continuam sujeitos ao diagnóstico numérico estrito, inclusive
    ``null``. O valor principal da baixa permanece obrigatório. A conferência
    com o resumo CP deve comprovar a soma dos ajustes antes de autorizar escrita.
    """
    if key in _PAYMENT_ADJUSTMENTS and key not in record:
        return Decimal(0)
    return _remote_number(record, key, processed, field="detalhes." + key, movement_kind="BXCP")


def _read(service: Any, profile: str, operation: str, params: Mapping[str, Any], processed: bool) -> Any:
    try:
        return service.invoke_financial(profile, operation, {"params": dict(params), "atualizar_cache": True}, writes=False)
    except Exception as error:
        _invalid_observation(processed, "query_failed", operation=operation, **_cause(error))


def _date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return datetime.strptime(value, "%d/%m/%Y").strftime("%d/%m/%Y") == value
    except ValueError:
        return False


def _title(service: Any, profile: str, query: Mapping[str, Any], processed: bool) -> dict[str, Any]:
    record = _read(service, profile, "payables_get", query, processed)
    if not isinstance(record, Mapping) or type(record.get(_TITLE_ID)) is not int or record[_TITLE_ID] < 1:
        _invalid_observation(processed, "invalid_title_identity")
    if any(type(record.get(key)) is not type(value) or record[key] != value for key, value in query.items()):
        _invalid_observation(processed, "title_identity_mismatch")
    if not _date(record.get("data_emissao")) or not _date(record.get("data_vencimento")):
        _invalid_observation(processed, "invalid_title_dates")
    amount = _remote_number(record, "valor_documento", processed, field="valor_documento", operation="payables_get")
    if amount <= 0:
        _invalid_observation(processed, "invalid_title_amount")
    if type(record.get("codigo_cliente_fornecedor")) is not int or record["codigo_cliente_fornecedor"] < 1:
        _invalid_observation(processed, "invalid_supplier")
    return deepcopy(dict(record))


def _counter(value: Any) -> int:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 12:
        return int(value)
    raise ValueError("invalid_pagination")


def _movements(service: Any, profile: str, title: Mapping[str, Any], kind: str, processed: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    expected_totals = None
    page_number = 1
    while True:
        params = {
            "nCodTitulo": title[_TITLE_ID], "cNatureza": "P", "cTpLancamento": kind,
            "dDtEmisDe": title["data_emissao"], "dDtEmisAte": title["data_emissao"],
            "nPagina": page_number, "nRegPorPagina": 50, "lDadosCad": True,
        }
        response = _read(service, profile, "financial_movements_list", params, processed)
        try:
            if not isinstance(response, Mapping):
                raise ValueError("invalid_page")
            current, pages, count, total = (_counter(response.get(key)) for key in ("nPagina", "nTotPaginas", "nRegistros", "nTotRegistros"))
            batch = response.get("movimentos")
            empty = page_number == 1 and current in (0, 1) and pages in (0, 1) and count == total == 0 and batch == []
            if not isinstance(batch, list) or len(batch) != count or count > 50:
                raise ValueError("invalid_page_size")
            if not empty and (current != page_number or pages < current or total < count or not batch):
                raise ValueError("invalid_page_metadata")
            if expected_totals is not None and expected_totals != (pages, total):
                raise ValueError("pagination_changed")
            expected_totals = (pages, total)
            for row in batch:
                details = row.get("detalhes") if isinstance(row, Mapping) else None
                if (not isinstance(details, Mapping) or type(details.get("nCodTitulo")) is not int
                        or details["nCodTitulo"] != title[_TITLE_ID] or details.get("cNatureza") != "P"):
                    raise ValueError("movement_identity_mismatch")
                rows.append(deepcopy(dict(row)))
            if len(rows) > total:
                raise ValueError("invalid_total")
            if empty or current == pages:
                if len(rows) != total:
                    raise ValueError("incomplete_pages")
                return rows
        except ValueError as error:
            _invalid_observation(processed, str(error), tipo_movimento=kind)
        page_number += 1


def _financial_state(service: Any, profile: str, title: Mapping[str, Any], processed: bool,
                     observations: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    titles = _movements(service, profile, title, "CP", processed)
    if len(titles) != 1:
        _invalid_observation(processed, "financial_title_not_unique")
    financial = titles[0]
    if observations is not None:
        observations["financeiro"] = financial
    summary = financial.get("resumo")
    try:
        if not isinstance(summary, Mapping) or summary.get("cLiquidado") not in ("S", "N"):
            raise ValueError("invalid_payment_summary")
        amounts = {key: _remote_number(summary, key, processed, field="resumo." + key, movement_kind="CP") for key in _SUMMARY_NUMBERS}
        if any(value < 0 for value in amounts.values()):
            raise ValueError("invalid_payment_summary")
        if abs(amounts["nValPago"] - amounts["nDesconto"] + amounts["nJuros"] + amounts["nMulta"] - amounts["nValLiquido"]) > _CENT:
            raise ValueError("inconsistent_payment_summary")
        if summary["cLiquidado"] == "S" and amounts["nValAberto"] != 0:
            raise ValueError("inconsistent_open_balance")
        amount = _remote_number(financial["detalhes"], "nValorTitulo", processed, field="detalhes.nValorTitulo", movement_kind="CP")
        if abs(amount - _number(title["valor_documento"])) > _CENT:
            raise ValueError("financial_title_amount_mismatch")
    except OmieError:
        raise
    except ValueError as error:
        _invalid_observation(processed, str(error))
    payments: dict[int, dict[str, Any]] = {}
    totals = {key: Decimal(0) for key in _PAYMENT_NUMBERS}
    payment_rows = _movements(service, profile, title, "BXCP", processed)
    if observations is not None:
        observations["baixas"] = payment_rows
    for row in payment_rows:
        details = row["detalhes"]
        try:
            if any(type(details.get(key)) is not int or details[key] < 1 for key in ("nCodBaixa", "nCodMovCC", "nCodCC")):
                raise ValueError("invalid_payment_identity")
            identifier = details["nCodBaixa"]
            if identifier in payments:
                raise ValueError("duplicate_payment")
            if not _date(details.get("dDtPagamento")):
                raise ValueError("invalid_payment_date")
            numbers = {key: _payment_number(details, key, processed) for key in _PAYMENT_NUMBERS}
            if any(numbers[key] < 0 for key in ("nDesconto", "nJuros", "nMulta")):
                raise ValueError("invalid_payment_amounts")
            if "dDtConcilia" not in details:
                raise ValueError("missing_reconciliation_state")
            date = details["dDtConcilia"]
            if date not in (None, "") and not _date(date):
                raise ValueError("invalid_reconciliation_date")
            if date and any(not isinstance(details.get(key), str) or not details[key].strip() for key in ("cHrConcilia", "cUsConcilia")):
                raise ValueError("incomplete_reconciliation")
            for key, number in numbers.items():
                totals[key] += abs(number) if key == "nValorMovCC" else number
        except OmieError:
            raise
        except ValueError as error:
            _invalid_observation(processed, str(error))
        payments[identifier] = row
    pairs = {"nValorMovCC": "nValLiquido", "nDesconto": "nDesconto", "nJuros": "nJuros", "nMulta": "nMulta"}
    if any(abs(totals[key] - amounts[target]) > _CENT for key, target in pairs.items()):
        _invalid_observation(processed, "payment_population_incomplete")
    if not payments and (amounts["nValPago"] != 0 or summary["cLiquidado"] == "S"):
        _invalid_observation(processed, "paid_title_without_payments")
    return financial, payments


def _snapshot(record: Mapping[str, Any], *, title: bool = False, payment: bool = False) -> dict[str, Any]:
    """Compara ajustes opcionais já validados por valor, preservando a origem."""
    result = deepcopy(dict(record))
    result.pop("categorias", None)
    if title:
        result.pop("codigo_categoria", None)
        result.pop("info", None)
    else:
        for key in ("cCodCateg", *_AUDIT_FIELDS):
            result["detalhes"].pop(key, None)
        if payment:
            for key in _PAYMENT_ADJUSTMENTS:
                result["detalhes"][key] = _number(result["detalhes"].get(key, 0))
    return result


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _observed(rows: Any, total: Decimal, *, payment: bool, processed: bool,
              movement_kind: str | None = None, payment_id: int | None = None,
              present: bool = True) -> list[dict[str, Any]]:
    operation = "financial_movements_list" if payment else "payables_get"
    context: dict[str, Any] = {"field": "categorias", "operation": operation}
    if movement_kind is not None:
        context["tipo_movimento"] = movement_kind
    if payment_id is not None:
        context["id_baixa"] = payment_id
    code_key, percent_key, value_key = ("cCodCateg", "nDistrPercentual", "nDistrValor") if payment else ("codigo_categoria", "percentual", "valor")
    if not isinstance(rows, list) or not rows:
        issue = "missing" if not present else "null" if rows is None else "empty" if isinstance(rows, list) else "invalid_type"
        _invalid_observation(processed, "missing_categories", collection_issue=issue, **context)
    result = []
    codes: set[str] = set()
    try:
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise ValueError("invalid_category")
            code = row.get(code_key)
            if not isinstance(code, str) or not code.strip() or len(code) > 20 or code in codes:
                raise ValueError("invalid_category_code")
            percent = _remote_number(row, percent_key, processed, field=f"categorias[{index}].{percent_key}", operation=operation, movement_kind=movement_kind)
            if not 0 < percent <= 100:
                raise ValueError("invalid_category_percentage")
            item: dict[str, Any] = {"codigo": code, "percentual": float(percent)}
            if payment or value_key in row:
                amount = _remote_number(row, value_key, processed, field=f"categorias[{index}].{value_key}", operation=operation, movement_kind=movement_kind)
                if amount < 0 or abs(total * percent / 100 - amount) > _CENT:
                    raise ValueError("inconsistent_category_amount")
                item["valor"] = float(amount)
            codes.add(code)
            result.append(item)
        if abs(sum((_number(item["percentual"]) for item in result), Decimal(0)) - 100) > _PERCENT_TOLERANCE:
            raise ValueError("incomplete_category_percentages")
        if result and all("valor" in item for item in result) and abs(sum((_number(item["valor"]) for item in result), Decimal(0)) - total) > _CENT:
            raise ValueError("incomplete_category_amounts")
    except OmieError as error:
        raise OmieError(error.code, str(error), {**context, **error.details}) from error
    except ValueError as error:
        _invalid_observation(processed, str(error), **context)
    return result


def _matches(expected: list[dict[str, Any]], observed: list[dict[str, Any]]) -> bool:
    actual = {row["codigo"]: row for row in observed}
    if set(actual) != {row["codigo_categoria"] for row in expected}:
        return False
    value_only = any("percentual" not in row for row in observed)
    total = sum((_number(row["valor"]) for row in observed), Decimal(0)) if value_only else Decimal(0)
    if value_only and total == 0 and len(observed) > 1:
        return False
    for row in expected:
        found = actual[row["codigo_categoria"]]
        if "percentual" in found:
            if abs(_number(found["percentual"]) - _number(row["percentual"])) > _PERCENT_TOLERANCE:
                return False
        elif abs(_number(found["valor"]) - total * _number(row["percentual"]) / 100) > _CENT:
            return False
    return True


def _categories_omitted(record: Mapping[str, Any]) -> bool:
    return "categorias" not in record or record["categorias"] is None or record["categorias"] == []


def _linked_categories(service: Any, profile: str, title_id: int, payment_id: int,
                       payment: Mapping[str, Any], processed: bool,
                       observations: dict[str, Any]) -> list[dict[str, Any]]:
    """Lê o rateio do lançamento vinculado quando BXCP não fornece a coleção.

    Confere movimento, título de origem, conta e magnitude do valor contra a
    baixa. A data do lançamento precisa ser válida, sem presumir igualdade com
    a data do pagamento. Dados de conciliação presentes precisam concordar com
    BXCP. ``linked_movement_mismatch`` recusa contradições; coleção ausente
    continua inconclusiva, sem inferência pela categoria escalar.
    Percentuais omitidos podem ser conferidos por valores até um centavo.
    Não altera o lançamento nem o pagamento.
    """
    details = payment["detalhes"]
    movement_id = details["nCodMovCC"]
    context = {"operation": "account_transactions_get", "tipo_movimento": "BXCP",
               "id_baixa": payment_id, "lancamento_conta_id": movement_id}

    def invalid(reason: str, field: str) -> None:
        _invalid_observation(processed, reason, field=field, **context)

    try:
        record = _read(service, profile, "account_transactions_get", {"nCodLanc": movement_id}, processed)
        if not isinstance(record, Mapping) or type(record.get("nCodLanc")) is not int or record["nCodLanc"] != movement_id:
            invalid("linked_movement_mismatch", "nCodLanc")
        header, source, classification = record.get("cabecalho"), record.get("diversos"), record.get("detalhes")
        if not isinstance(header, Mapping) or not isinstance(source, Mapping) or not isinstance(classification, Mapping):
            invalid("invalid_linked_movement", "lancamento")
        if type(source.get("nCodLancCP")) is not int or source["nCodLancCP"] != title_id:
            invalid("linked_movement_mismatch", "diversos.nCodLancCP")
        if source.get("cNatureza") not in (None, "", "P"):
            invalid("linked_movement_mismatch", "diversos.cNatureza")
        receivable = source.get("nCodLancCR")
        if receivable is not None and not (type(receivable) is int and receivable == 0):
            invalid("linked_movement_mismatch", "diversos.nCodLancCR")
        if type(header.get("nCodCC")) is not int or header["nCodCC"] != details["nCodCC"]:
            invalid("linked_movement_mismatch", "cabecalho.nCodCC")
        total = abs(_number(details["nValorMovCC"]))
        amount = _remote_number(header, "nValorLanc", processed, field="cabecalho.nValorLanc", operation="account_transactions_get", movement_kind="BXCP")
        if abs(abs(amount) - total) > _CENT:
            invalid("linked_movement_mismatch", "cabecalho.nValorLanc")
        if not _date(header.get("dDtLanc")):
            invalid("invalid_linked_movement_date", "cabecalho.dDtLanc")
        for account_key, payment_key in (("dDtConc", "dDtConcilia"), ("cHrConc", "cHrConcilia"), ("cUsConc", "cUsConcilia")):
            if account_key in source and (source[account_key] or None) != (details.get(payment_key) or None):
                invalid("linked_movement_mismatch", "diversos." + account_key)
        observations.setdefault("lancamentos", []).append(deepcopy(dict(record)))
        rows = classification.get("aCodCateg")
        if not isinstance(rows, list) or not rows:
            invalid("missing_categories", "detalhes.aCodCateg")
        result: list[dict[str, Any]] = []
        codes: set[str] = set()
        for index, row in enumerate(rows):
            path = f"detalhes.aCodCateg[{index}]"
            if not isinstance(row, Mapping):
                invalid("invalid_category", path)
            code = row.get("cCodCateg")
            if not isinstance(code, str) or not code.strip() or len(code) > 20 or code in codes:
                invalid("invalid_category_code", path + ".cCodCateg")
            item: dict[str, Any] = {"codigo": code}
            if "nPerc" in row:
                percent = _remote_number(row, "nPerc", processed, field=path + ".nPerc", operation="account_transactions_get", movement_kind="BXCP")
                if not 0 < percent <= 100:
                    invalid("invalid_category_percentage", path + ".nPerc")
                item["percentual"] = float(percent)
            if "nValor" in row:
                value = _remote_number(row, "nValor", processed, field=path + ".nValor", operation="account_transactions_get", movement_kind="BXCP")
                if value < 0 or "percentual" in item and abs(total * _number(item["percentual"]) / 100 - value) > _CENT:
                    invalid("inconsistent_category_amount", path + ".nValor")
                item["valor"] = float(value)
            if len(item) == 1:
                invalid("missing_category_values", path)
            codes.add(code)
            result.append(item)
        if all("valor" in row for row in result):
            if abs(sum((_number(row["valor"]) for row in result), Decimal(0)) - total) > _CENT:
                invalid("incomplete_category_amounts", "detalhes.aCodCateg")
        elif any("percentual" not in row for row in result):
            invalid("incomplete_category_values", "detalhes.aCodCateg")
        if all("percentual" in row for row in result) and abs(sum((_number(row["percentual"]) for row in result), Decimal(0)) - 100) > _PERCENT_TOLERANCE:
            invalid("incomplete_category_percentages", "detalhes.aCodCateg")
        return result
    except OmieError as error:
        raise OmieError(error.code, str(error), {**context, **error.details}) from error


def _payment_categories(service: Any, profile: str, title_id: int, payment_id: int,
                        payment: Mapping[str, Any], processed: bool,
                        observations: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if _categories_omitted(payment):
        return _linked_categories(service, profile, title_id, payment_id, payment, processed, observations), "account_transactions_get"
    return _observed(payment["categorias"], abs(_number(payment["detalhes"]["nValorMovCC"])),
                     payment=True, processed=processed, movement_kind="BXCP", payment_id=payment_id), "financial_movements_list"


def _payment_result(identifier: int, row: Mapping[str, Any], categories: list[dict[str, Any]] | None,
                    source: str = "financial_movements_list") -> dict[str, Any]:
    details = row["detalhes"]
    return {
        "id_baixa": identifier, "lancamento_conta_id": details["nCodMovCC"], "conta_corrente_id": details["nCodCC"],
        "data": details["dDtPagamento"], "valor": float(_number(details["nValorMovCC"])),
        "desconto": float(_payment_number(details, "nDesconto", True)), "juros": float(_payment_number(details, "nJuros", True)), "multa": float(_payment_number(details, "nMulta", True)),
        "conciliacao": {"conciliado": bool(details.get("dDtConcilia")), "data": details.get("dDtConcilia"),
                        "hora": details.get("cHrConcilia"), "usuario": details.get("cUsConcilia")},
        "categorias": categories, "fonte_categorias": source,
    }


def _category_request(identifier: Mapping[str, Any], categories: list[dict[str, Any]], include_native: bool) -> list[dict[str, Any]]:
    if not isinstance(identifier, Mapping) or len(identifier) != 1 or not set(identifier) <= {_TITLE_ID, _INTEGRATION_ID}:
        raise _error("invalid_parameter", "Informe exatamente um identificador do título.", field="dados")
    key, value = next(iter(identifier.items()))
    valid = type(value) is int and value > 0 if key == _TITLE_ID else isinstance(value, str) and bool(value.strip()) and len(value) <= 60
    if not valid or type(include_native) is not bool:
        raise _error("invalid_parameter", "Identificador ou controle de auditoria inválido.", field="dados")
    if not isinstance(categories, list) or any(not isinstance(row, Mapping) or set(row) != {"codigo_categoria", "percentual"} for row in categories):
        raise _error("invalid_parameter", "Categorias nativas inválidas.", field="dados.categorias")
    return normalize_categories([{"codigo": row["codigo_categoria"], "percentual": row["percentual"]} for row in categories])


def check_categories(service: Any, profile: str, identifier: Mapping[str, Any], categories: list[dict[str, Any]], *, include_native: bool = False) -> dict[str, Any]:
    """Confere o estado atual sem gravar nem repetir uma alteração anterior.

    Lê título, resumo financeiro e baixas. Quando BXCP omite categorias, lê o
    lançamento de conta corrente e comprova o vínculo antes de usar seu rateio.
    A ausência da coleção em CP fica explícita, sem inventar sua classificação.
    ``conforme_esperado`` depende do título, de todas as baixas e das coleções
    adicionais efetivamente retornadas. Divergências não provocam escrita.
    Erros de classificação ficam em ``falhas_conferencia``; leituras financeiras
    essenciais inconclusivas lançam ``category_allocation_preflight_failed``.
    Esta leitura não atesta preservação entre dois instantes.
    """
    expected = _category_request(identifier, categories, include_native)
    title = _title(service, profile, identifier, False)
    observations: dict[str, Any] = {"registro": title}
    financial, payments = _financial_state(service, profile, title, False, observations)
    failures: list[dict[str, Any]] = []

    def observe(record: Mapping[str, Any], total: Decimal, *, payment: bool = False, kind: str | None = None) -> list[dict[str, Any]] | None:
        try:
            return _observed(record.get("categorias"), total, payment=payment, processed=False,
                             movement_kind=kind, present="categorias" in record)
        except OmieError as error:
            failures.append(error.as_dict())
            return None

    observed = observe(title, _number(title["valor_documento"]))
    available = not _categories_omitted(financial)
    financial_categories = observe(financial, _number(title["valor_documento"]), payment=True, kind="CP") if available else None
    payment_results = []
    for payment_id, row in payments.items():
        source = "account_transactions_get" if _categories_omitted(row) else "financial_movements_list"
        try:
            allocation, source = _payment_categories(service, profile, title[_TITLE_ID], payment_id, row, False, observations)
        except OmieError as error:
            failures.append(error.as_dict())
            allocation = None
        payment_results.append(_payment_result(payment_id, row, allocation, source))
    title_matches = observed is not None and _matches(expected, observed) and (
        len(expected) != 1 or title.get("codigo_categoria") == expected[0]["codigo_categoria"]
    )
    financial_matches = financial_categories is not None and _matches(expected, financial_categories)
    payments_match = all(row["categorias"] is not None and _matches(expected, row["categorias"]) for row in payment_results)
    result: dict[str, Any] = {
        "alvo": {"recurso": "contas_pagar", "id": title[_TITLE_ID]}, "categorias": observed,
        "categorias_financeiro": financial_categories, "baixas": payment_results,
        "verificacao": {"titulo_confirmado": title_matches, "titulo_financeiro_confirmado": financial_matches,
                         "categorias_financeiro_disponiveis": available, "baixas_confirmadas": payments_match},
        "conforme_esperado": title_matches and payments_match and (not available or financial_matches) and not failures,
        "falhas_conferencia": failures, "somente_leitura": True, "versao_mcp": SETUP_VERSION,
    }
    if include_native:
        result["dados_omie"] = observations
    return result


def define_categories(service: Any, profile: str, identifier: Mapping[str, Any], categories: list[dict[str, Any]], *, include_native: bool = False) -> dict[str, Any]:
    """Solicita o rateio e confirma título, todas as baixas e seus valores.

    Recebe identificador nativo único e categorias já traduzidas. Resolve a
    integração uma vez e usa o ID numérico nas operações posteriores. Exige
    data de emissão para evitar a janela implícita de 30 dias das consultas.
    Pré e pós-consultas exigem leitura remota, respeitando o cooldown da Omie.

    ``category_allocation_preflight_failed`` impede escrita sem observação
    completa. Depois do aceite, ``category_allocation_verification_failed``
    indica consulta inconclusiva; ``category_allocation_not_persisted`` indica
    divergência no título; ``category_allocation_propagation_not_confirmed``
    indica divergência em baixa; ``category_allocation_side_effect_detected``
    informa mudança financeira ou de outro campo fora da classificação e dos
    metadados de alteração. Retornos e erros distinguem cada confirmação.
    Nenhuma falha autoriza repetição automática, cancelamento ou nova baixa.
    Falhas de classificação não interrompem a leitura financeira posterior:
    todas as coleções são conferidas independentemente. Os erros preservam as
    falhas em ``falhas_conferencia``; com auditoria, expõem os registros já
    consultados em ``dados_omie``, sem inferir coleções ausentes pela categoria
    escalar. Divergências observadas e efeitos colaterais têm prioridade sobre
    a ausência de classificação em outro objeto.
    """
    expected = _category_request(identifier, categories, include_native)
    before = _title(service, profile, identifier, False)
    query = {_TITLE_ID: before[_TITLE_ID]}
    financial_before, payments_before = _financial_state(service, profile, before, False)
    body = {**query, "categorias": expected}
    if len(expected) == 1:
        body["codigo_categoria"] = expected[0]["codigo_categoria"]
    audit = {"call": "AlterarContaPagar", "param": [body]}
    target = {"recurso": "contas_pagar", "id": before[_TITLE_ID]}
    flags = {"titulo_confirmado": False, "titulo_financeiro_confirmado": False, "categorias_financeiro_disponiveis": False,
             "baixas_confirmadas": False, "campos_preservados": False}
    category_errors: list[OmieError] = []
    observations: dict[str, Any] = {}
    observed = None
    financial_categories = None
    payments: list[dict[str, Any]] = []

    def observe(record: Mapping[str, Any], total: Decimal, *, payment: bool = False,
                kind: str | None = None, payment_id: int | None = None) -> list[dict[str, Any]] | None:
        try:
            return _observed(record.get("categorias"), total, payment=payment, processed=True,
                             movement_kind=kind, payment_id=payment_id, present="categorias" in record)
        except OmieError as error:
            category_errors.append(error)
            return None

    try:
        try:
            ack = service.invoke_financial(profile, "payables_update", {"body": body}, writes=True)
        except OmieError:
            raise
        except Exception as error:
            raise _error("outcome_unknown", "A escrita pode ter sido recebida; consulte antes de qualquer nova gravação.", **_cause(error)) from error
        if (not isinstance(ack, Mapping) or str(ack.get("codigo_status", ack.get("cCodStatus"))) != "0"
                or _TITLE_ID in ack and (type(ack[_TITLE_ID]) is not int or ack[_TITLE_ID] != before[_TITLE_ID])):
            raise _error("outcome_unknown", "O retorno da escrita não confirmou o processamento do título; consulte antes de repetir.", reason="invalid_acknowledgement")
        after = _title(service, profile, query, True)
        observations["registro"] = after
        observed = observe(after, _number(after["valor_documento"]))
        flags["titulo_confirmado"] = observed is not None and _matches(expected, observed) and (
            len(expected) != 1 or after.get("codigo_categoria") == expected[0]["codigo_categoria"]
        )
        financial_after, payments_after = _financial_state(service, profile, after, True, observations)
        flags["categorias_financeiro_disponiveis"] = not _categories_omitted(financial_after)
        financial_categories = observe(financial_after, _number(after["valor_documento"]), payment=True, kind="CP") if flags["categorias_financeiro_disponiveis"] else None
        flags["titulo_financeiro_confirmado"] = financial_categories is not None and _matches(expected, financial_categories)
        for payment_id, row in payments_after.items():
            source = "account_transactions_get" if _categories_omitted(row) else "financial_movements_list"
            try:
                allocation, source = _payment_categories(service, profile, after[_TITLE_ID], payment_id, row, True, observations)
            except OmieError as error:
                category_errors.append(error)
                allocation = None
            payments.append(_payment_result(payment_id, row, allocation, source))
        flags["baixas_confirmadas"] = all(row["categorias"] is not None and _matches(expected, row["categorias"]) for row in payments)
        changed = []
        if not _same(_snapshot(before, title=True), _snapshot(after, title=True)):
            changed.append("titulo")
        if not _same(_snapshot(financial_before), _snapshot(financial_after)):
            changed.append("resumo_financeiro")
        if set(payments_before) != set(payments_after):
            changed.append("identificadores_baixas")
        for payment_id in payments_before.keys() & payments_after.keys():
            if not _same(_snapshot(payments_before[payment_id], payment=True), _snapshot(payments_after[payment_id], payment=True)):
                changed.append(f"baixa:{payment_id}")
        if changed:
            raise _error("category_allocation_side_effect_detected", "A conferência detectou alterações fora das categorias. Confira o estado atual; nenhuma compensação foi executada.", escrita_processada=True, campos_alterados=changed)
        flags["campos_preservados"] = True
        if observed is not None and not flags["titulo_confirmado"]:
            raise _error("category_allocation_not_persisted", "A Omie aceitou a escrita, mas o título mantém categorias diferentes das solicitadas. Não repita a escrita para conferir.", escrita_processada=True, categorias_titulo=observed, baixas=payments)
        movement_mismatch = (financial_categories is not None and not flags["titulo_financeiro_confirmado"]) or any(
            row["categorias"] is not None and not _matches(expected, row["categorias"]) for row in payments
        )
        if flags["titulo_confirmado"] and movement_mismatch:
            raise _error("category_allocation_propagation_not_confirmed", "O título foi corrigido, mas o rateio não foi confirmado no movimento financeiro do título ou em todas as baixas. Não cancele nem refaça pagamentos.", escrita_processada=True, categorias_titulo=observed, categorias_financeiro=financial_categories, baixas=payments)
        if category_errors:
            raise category_errors[0]
        result: dict[str, Any] = {"alvo": target, "categorias": observed, "baixas": payments,
                                  "categorias_financeiro": financial_categories, "verificacao": {**flags, "quantidade_baixas": len(payments)}, "versao_mcp": SETUP_VERSION}
        if include_native:
            result["dados_omie"] = {"requisicao": audit, "processamento": ack, **observations}
        return result
    except OmieError as error:
        details = {**error.details, "alvo": target, "verificacao": dict(flags), "retryable": False, "versao_mcp": SETUP_VERSION}
        if observations:
            details.update(categorias_titulo=observed, categorias_financeiro=financial_categories, baixas=payments)
        if category_errors:
            details["falhas_conferencia"] = [failure.as_dict() for failure in category_errors]
        if include_native:
            details["requisicao_omie"] = audit
            if observations:
                details["dados_omie"] = observations
        raise OmieError(error.code, str(error), details) from error
