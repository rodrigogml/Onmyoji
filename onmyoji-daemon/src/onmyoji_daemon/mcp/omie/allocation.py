"""Confirma a persistência do rateio após uma alteração financeira na Omie.

A confirmação usa uma consulta do mesmo objeto, sem repetir a escrita nem
redirecioná-la para um título vinculado. Um retorno de processamento bem-sucedido
não substitui a conferência do estado persistido.
"""
from __future__ import annotations

from decimal import Decimal, DecimalException
from typing import Any, Mapping

from .exception import OmieError


_OPERATIONS = {
    "account_transactions_update": ("account_transactions_get", "departamentos", "nCodLanc", "cCodIntLanc"),
    "payables_update": ("payables_get", "distribuicao", "codigo_lancamento_omie", "codigo_lancamento_integracao"),
    "receivables_update": ("receivables_get", "distribuicao", "codigo_lancamento_omie", "codigo_lancamento_integracao"),
}
_TOLERANCES = {"nPerDep": Decimal("0.000001"), "nValDep": Decimal("0.01")}


def _number(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("invalid_number")
    try:
        result = Decimal(str(value))
    except DecimalException as error:
        raise ValueError("invalid_number") from error
    if not result.is_finite() or result < 0:
        raise ValueError("invalid_number")
    return result


def _allocation(rows: Any) -> dict[str, dict[str, Decimal]]:
    if not isinstance(rows, list):
        raise ValueError("invalid_allocation_list")
    result: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("invalid_department")
        code = row.get("cCodDep")
        if not isinstance(code, str) or not code.strip() or len(code) > 40:
            raise ValueError("invalid_department_code")
        if code in result:
            raise ValueError("duplicate_department")
        numbers = {key: _number(row[key]) for key in _TOLERANCES if key in row}
        if not numbers or numbers.get("nPerDep", Decimal(0)) > 100:
            raise ValueError("invalid_department_values")
        result[code] = numbers
    return result


def _consistent_amounts(allocation: Mapping[str, Mapping[str, Decimal]], record: Mapping[str, Any], field: str) -> None:
    """Confere percentual e valor juntos somente quando a consulta informa o total."""
    source = record.get("cabecalho") if field == "departamentos" else record
    total_key = "nValorLanc" if field == "departamentos" else "valor_documento"
    if not isinstance(source, Mapping) or total_key not in source:
        return
    paired = [values for values in allocation.values() if {"nPerDep", "nValDep"} <= set(values)]
    if not paired:
        return
    total = _number(source[total_key])
    for values in paired:
        try:
            calculated = total * values["nPerDep"] / Decimal(100)
            difference = (calculated - values["nValDep"]).copy_abs()
        except DecimalException as error:
            raise ValueError("invalid_department_values") from error
        if difference > _TOLERANCES["nValDep"]:
            raise ValueError("inconsistent_department_values")


def _linked_diagnostics(record: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    source = record.get("diversos")
    if not isinstance(source, Mapping):
        return {}, ""
    details: dict[str, Any] = {}
    origin = source.get("cOrigem")
    if isinstance(origin, str) and len(origin) == 4 and origin.isascii() and origin.isalnum():
        details["origem"] = origin
    linked = []
    for native, public, resource, label in (
        ("nCodLancCP", "conta_pagar_id", "contas_pagar", "conta a pagar"),
        ("nCodLancCR", "conta_receber_id", "contas_receber", "conta a receber"),
    ):
        identifier = source.get(native)
        if type(identifier) is int and identifier > 0:
            details[public] = identifier
            linked.append((identifier, resource, label))
    if len(linked) == 1:
        identifier, resource, label = linked[0]
        tool = f"omie.financeiro.{resource}.alterar"
        details["suggested_tool"] = tool
        details["suggested_title_id"] = identifier
        return details, (
            f" O lançamento está vinculado à {label} {identifier}; consulte o título e use {tool} "
            f"com dados.id={identifier} para corrigir o rateio da origem. "
            "Não cancele nem repita a baixa para corrigir a classificação."
        )
    if linked:
        return details, " Confira os títulos vinculados antes de escolher a origem a alterar; não cancele nem repita a baixa."
    return details, ""


def verify_department_allocation(service: Any, profile: str, operation: str, native_parameters: Mapping[str, Any]) -> dict[str, bool]:
    """Confirma o rateio solicitado usando uma única consulta do objeto alterado.

    Recebe os parâmetros nativos já enviados pela escrita. Prefere o ID Omie ao
    código de integração, preserva os códigos textuais de departamento e compara
    percentuais com tolerância de 0,000001 e valores com tolerância de R$ 0,01.
    A ordem dos departamentos e os campos descritivos não afetam a comparação.
    Quando a consulta retorna percentual e valor juntos e informa o total do
    objeto, os dois precisam ser consistentes entre si até R$ 0,01.

    Lança ``allocation_not_persisted`` quando uma consulta válida diverge da
    solicitação. Lança ``write_verification_failed`` quando a consulta falha ou
    não permite conferir o rateio. Ambos indicam ``escrita_processada=true`` e
    ``retryable=false``: nenhuma escrita é repetida por este método. Na falha da
    consulta, preserva apenas o prazo positivo de espera, motivo curto e status
    HTTP válidos, para orientar uma nova consulta sem repetir a gravação.
    """
    details = {"operation": operation, "field": "dados.departamentos", "retryable": False, "escrita_processada": True}

    def inconclusive(reason: str, *, cause_code: str | None = None, cause_details: Mapping[str, Any] | None = None) -> None:
        diagnostics = {**details, "reason": reason}
        if cause_code is not None:
            diagnostics["cause_code"] = cause_code
        if cause_details is not None:
            delay = cause_details.get("retry_after_seconds")
            if type(delay) is int and delay > 0:
                diagnostics["retry_after_seconds"] = delay
            source_reason = cause_details.get("reason")
            if isinstance(source_reason, str) and 0 < len(source_reason) <= 128 and source_reason.strip() and source_reason.isprintable():
                diagnostics["cause_reason"] = source_reason
            status = cause_details.get("http_status")
            if type(status) is int and 100 <= status <= 599:
                diagnostics["http_status"] = status
        message = "A Omie confirmou o processamento da escrita, mas não foi possível conferir a persistência do rateio. "
        if "retry_after_seconds" in diagnostics:
            message += f"Aguarde o intervalo informado ({diagnostics['retry_after_seconds']} segundos) antes de consultar novamente. "
        message += "Consulte o estado atual; não repita a escrita para fazer esta conferência."
        raise OmieError(
            "write_verification_failed", message, diagnostics,
        )

    spec = _OPERATIONS.get(operation)
    if spec is None:
        inconclusive("unsupported_operation")
    read_operation, field, id_key, integration_key = spec
    if not isinstance(native_parameters, Mapping):
        inconclusive("invalid_write_parameters")
    try:
        expected = _allocation(native_parameters.get(field))
    except ValueError as error:
        inconclusive("invalid_expected_allocation", cause_code=str(error))
    identifier = native_parameters.get(id_key)
    if type(identifier) is int and identifier > 0:
        query = {id_key: identifier}
    else:
        identifier = native_parameters.get(integration_key)
        if not isinstance(identifier, str) or not identifier.strip():
            inconclusive("missing_identifier")
        query = {integration_key: identifier}
    try:
        record = service.invoke_financial(profile, read_operation, {"params": query}, writes=False)
    except Exception as error:
        code = error.code if isinstance(error, OmieError) else "unexpected_error"
        source_details = error.details if isinstance(error, OmieError) and isinstance(error.details, Mapping) else None
        inconclusive("query_failed", cause_code=code, cause_details=source_details)
    if not isinstance(record, Mapping):
        inconclusive("invalid_response")
    linked, guidance = _linked_diagnostics(record)
    details.update(linked)
    query_key, query_value = next(iter(query.items()))
    if query_key not in record:
        inconclusive("response_identifier_missing")
    if type(record[query_key]) is not type(query_value) or record[query_key] != query_value:
        inconclusive("response_identifier_mismatch")
    if field not in record:
        inconclusive("missing_allocation")
    try:
        actual = _allocation(record[field])
        _consistent_amounts(actual, record, field)
    except ValueError as error:
        inconclusive("invalid_response_allocation", cause_code=str(error))
    matches = set(expected) == set(actual)
    if matches:
        for code, numbers in expected.items():
            for key, value in numbers.items():
                if key not in actual[code]:
                    inconclusive("missing_allocation_value")
                try:
                    difference = (value - actual[code][key]).copy_abs()
                except DecimalException:
                    inconclusive("invalid_allocation_value")
                if difference > _TOLERANCES[key]:
                    matches = False
    if not matches:
        raise OmieError(
            "allocation_not_persisted",
            "A Omie confirmou o processamento da escrita, mas o rateio consultado não corresponde ao solicitado. "
            "Confira o estado atual antes de repetir qualquer escrita." + guidance,
            {**details, "reason": "allocation_mismatch"},
        )
    return {"rateio_confirmado": True}
