"""Substitui categorias de contas a pagar e confere título e todas as baixas.

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
_PAYMENT_NUMBERS = ("nValorMovCC", "nDesconto", "nJuros", "nMulta")
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


def _cause(error: Exception) -> dict[str, Any]:
    result: dict[str, Any] = {"cause_code": error.code if isinstance(error, OmieError) else "unexpected_error"}
    if isinstance(error, OmieError) and isinstance(error.details, Mapping):
        for field, low, high in (("retry_after_seconds", 1, 86400), ("http_status", 100, 599)):
            value = error.details.get(field)
            if type(value) is int and low <= value <= high:
                result[field] = value
    return result


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
    try:
        if _number(record.get("valor_documento")) <= 0:
            raise ValueError("invalid_title_amount")
    except ValueError:
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


def _financial_state(service: Any, profile: str, title: Mapping[str, Any], processed: bool) -> tuple[dict[str, Any], dict[int, dict[str, Any]]]:
    titles = _movements(service, profile, title, "CP", processed)
    if len(titles) != 1:
        _invalid_observation(processed, "financial_title_not_unique")
    financial = titles[0]
    summary = financial.get("resumo")
    try:
        if not isinstance(summary, Mapping) or summary.get("cLiquidado") not in ("S", "N"):
            raise ValueError("invalid_payment_summary")
        amounts = {key: _number(summary.get(key)) for key in _SUMMARY_NUMBERS}
        if any(value < 0 for value in amounts.values()):
            raise ValueError("invalid_payment_summary")
        if abs(amounts["nValPago"] - amounts["nDesconto"] + amounts["nJuros"] + amounts["nMulta"] - amounts["nValLiquido"]) > _CENT:
            raise ValueError("inconsistent_payment_summary")
        if summary["cLiquidado"] == "S" and amounts["nValAberto"] != 0:
            raise ValueError("inconsistent_open_balance")
        if abs(_number(financial["detalhes"].get("nValorTitulo")) - _number(title["valor_documento"])) > _CENT:
            raise ValueError("financial_title_amount_mismatch")
    except ValueError as error:
        _invalid_observation(processed, str(error))
    payments: dict[int, dict[str, Any]] = {}
    totals = {key: Decimal(0) for key in _PAYMENT_NUMBERS}
    for row in _movements(service, profile, title, "BXCP", processed):
        details = row["detalhes"]
        try:
            if any(type(details.get(key)) is not int or details[key] < 1 for key in ("nCodBaixa", "nCodMovCC", "nCodCC")):
                raise ValueError("invalid_payment_identity")
            identifier = details["nCodBaixa"]
            if identifier in payments:
                raise ValueError("duplicate_payment")
            if not _date(details.get("dDtPagamento")):
                raise ValueError("invalid_payment_date")
            numbers = {key: _number(details.get(key)) for key in _PAYMENT_NUMBERS}
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
        except ValueError as error:
            _invalid_observation(processed, str(error))
        payments[identifier] = row
    pairs = {"nValorMovCC": "nValLiquido", "nDesconto": "nDesconto", "nJuros": "nJuros", "nMulta": "nMulta"}
    if any(abs(totals[key] - amounts[target]) > _CENT for key, target in pairs.items()):
        _invalid_observation(processed, "payment_population_incomplete")
    if not payments and (amounts["nValPago"] != 0 or summary["cLiquidado"] == "S"):
        _invalid_observation(processed, "paid_title_without_payments")
    return financial, payments


def _snapshot(record: Mapping[str, Any], *, title: bool = False) -> dict[str, Any]:
    result = deepcopy(dict(record))
    result.pop("categorias", None)
    if title:
        result.pop("codigo_categoria", None)
        result.pop("info", None)
    else:
        for key in ("cCodCateg", *_AUDIT_FIELDS):
            result["detalhes"].pop(key, None)
    return result


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _observed(rows: Any, total: Decimal, *, payment: bool, processed: bool) -> list[dict[str, Any]]:
    code_key, percent_key, value_key = ("cCodCateg", "nDistrPercentual", "nDistrValor") if payment else ("codigo_categoria", "percentual", "valor")
    if not isinstance(rows, list) or not rows:
        _invalid_observation(processed, "missing_categories")
    result = []
    codes: set[str] = set()
    try:
        for row in rows:
            if not isinstance(row, Mapping):
                raise ValueError("invalid_category")
            code = row.get(code_key)
            if not isinstance(code, str) or not code.strip() or len(code) > 20 or code in codes:
                raise ValueError("invalid_category_code")
            percent = _number(row.get(percent_key))
            if not 0 < percent <= 100:
                raise ValueError("invalid_category_percentage")
            item: dict[str, Any] = {"codigo": code, "percentual": float(percent)}
            if payment or value_key in row:
                amount = _number(row.get(value_key))
                if amount < 0 or abs(total * percent / 100 - amount) > _CENT:
                    raise ValueError("inconsistent_category_amount")
                item["valor"] = float(amount)
            codes.add(code)
            result.append(item)
        if abs(sum((_number(item["percentual"]) for item in result), Decimal(0)) - 100) > _PERCENT_TOLERANCE:
            raise ValueError("incomplete_category_percentages")
        if result and all("valor" in item for item in result) and abs(sum((_number(item["valor"]) for item in result), Decimal(0)) - total) > _CENT:
            raise ValueError("incomplete_category_amounts")
    except ValueError as error:
        _invalid_observation(processed, str(error))
    return result


def _matches(expected: list[dict[str, Any]], observed: list[dict[str, Any]]) -> bool:
    actual = {row["codigo"]: _number(row["percentual"]) for row in observed}
    return set(actual) == {row["codigo_categoria"] for row in expected} and all(
        abs(actual[row["codigo_categoria"]] - _number(row["percentual"])) <= _PERCENT_TOLERANCE for row in expected
    )


def _payment_result(identifier: int, row: Mapping[str, Any], categories: list[dict[str, Any]]) -> dict[str, Any]:
    details = row["detalhes"]
    return {
        "id_baixa": identifier, "lancamento_conta_id": details["nCodMovCC"], "conta_corrente_id": details["nCodCC"],
        "data": details["dDtPagamento"], "valor": float(_number(details["nValorMovCC"])),
        "desconto": float(_number(details["nDesconto"])), "juros": float(_number(details["nJuros"])), "multa": float(_number(details["nMulta"])),
        "conciliacao": {"conciliado": bool(details.get("dDtConcilia")), "data": details.get("dDtConcilia"),
                        "hora": details.get("cHrConcilia"), "usuario": details.get("cUsConcilia")},
        "categorias": categories,
    }


def define_categories(service: Any, profile: str, identifier: Mapping[str, Any], categories: list[dict[str, Any]], *, include_native: bool = False) -> dict[str, Any]:
    """Substitui categorias e confirma título, todas as baixas e seus valores.

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
    """
    if not isinstance(identifier, Mapping) or len(identifier) != 1 or not set(identifier) <= {_TITLE_ID, _INTEGRATION_ID}:
        raise _error("invalid_parameter", "Informe exatamente um identificador do título.", field="dados")
    key, value = next(iter(identifier.items()))
    valid = type(value) is int and value > 0 if key == _TITLE_ID else isinstance(value, str) and bool(value.strip()) and len(value) <= 60
    if not valid or type(include_native) is not bool:
        raise _error("invalid_parameter", "Identificador ou controle de auditoria inválido.", field="dados")
    if not isinstance(categories, list) or any(not isinstance(row, Mapping) or set(row) != {"codigo_categoria", "percentual"} for row in categories):
        raise _error("invalid_parameter", "Categorias nativas inválidas.", field="dados.categorias")
    expected = normalize_categories([{"codigo": row["codigo_categoria"], "percentual": row["percentual"]} for row in categories])
    before = _title(service, profile, identifier, False)
    query = {_TITLE_ID: before[_TITLE_ID]}
    financial_before, payments_before = _financial_state(service, profile, before, False)
    body = {**query, "categorias": expected}
    if len(expected) == 1:
        body["codigo_categoria"] = expected[0]["codigo_categoria"]
    audit = {"call": "AlterarContaPagar", "param": [body]}
    target = {"recurso": "contas_pagar", "id": before[_TITLE_ID]}
    flags = {"titulo_confirmado": False, "titulo_financeiro_confirmado": False, "baixas_confirmadas": False, "campos_preservados": False}
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
        observed = _observed(after.get("categorias"), _number(after["valor_documento"]), payment=False, processed=True)
        flags["titulo_confirmado"] = _matches(expected, observed) and (
            len(expected) != 1 or after.get("codigo_categoria") == expected[0]["codigo_categoria"]
        )
        financial_after, payments_after = _financial_state(service, profile, after, True)
        changed = []
        if not _same(_snapshot(before, title=True), _snapshot(after, title=True)):
            changed.append("titulo")
        if not _same(_snapshot(financial_before), _snapshot(financial_after)):
            changed.append("resumo_financeiro")
        if set(payments_before) != set(payments_after):
            changed.append("identificadores_baixas")
        for payment_id in payments_before.keys() & payments_after.keys():
            if not _same(_snapshot(payments_before[payment_id]), _snapshot(payments_after[payment_id])):
                changed.append(f"baixa:{payment_id}")
        if changed:
            raise _error("category_allocation_side_effect_detected", "A conferência detectou alterações fora das categorias. Confira o estado atual; nenhuma compensação foi executada.", escrita_processada=True, campos_alterados=changed)
        flags["campos_preservados"] = True
        financial_categories = _observed(financial_after.get("categorias"), _number(after["valor_documento"]), payment=True, processed=True)
        flags["titulo_financeiro_confirmado"] = _matches(expected, financial_categories)
        payments = []
        for payment_id, row in payments_after.items():
            allocation = _observed(row.get("categorias"), abs(_number(row["detalhes"]["nValorMovCC"])), payment=True, processed=True)
            payments.append(_payment_result(payment_id, row, allocation))
        flags["baixas_confirmadas"] = all(_matches(expected, row["categorias"]) for row in payments)
        if not flags["titulo_confirmado"]:
            raise _error("category_allocation_not_persisted", "A Omie aceitou a escrita, mas o título mantém categorias diferentes das solicitadas. Não repita a escrita para conferir.", escrita_processada=True, categorias_titulo=observed, baixas=payments)
        if not flags["titulo_financeiro_confirmado"] or not flags["baixas_confirmadas"]:
            raise _error("category_allocation_propagation_not_confirmed", "O título foi corrigido, mas o rateio não foi confirmado no movimento financeiro do título ou em todas as baixas. Não cancele nem refaça pagamentos.", escrita_processada=True, categorias_titulo=observed, categorias_financeiro=financial_categories, baixas=payments)
        result: dict[str, Any] = {"alvo": target, "categorias": observed, "baixas": payments,
                                  "categorias_financeiro": financial_categories, "verificacao": {**flags, "quantidade_baixas": len(payments)}, "versao_mcp": SETUP_VERSION}
        if include_native:
            result["dados_omie"] = {"requisicao": audit, "processamento": ack, "registro": after,
                                    "financeiro": financial_after, "baixas": list(payments_after.values())}
        return result
    except OmieError as error:
        details = {**error.details, "alvo": target, "verificacao": dict(flags), "retryable": False, "versao_mcp": SETUP_VERSION}
        if include_native:
            details["requisicao_omie"] = audit
        raise OmieError(error.code, str(error), details) from error
