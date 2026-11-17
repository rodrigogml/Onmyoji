"""Define o rateio de um objeto financeiro e devolve o estado conferido.

Lançamentos vinculados a títulos são recusados antes da escrita para evitar
editar a representação errada. A correção de um título pode conferir também
um movimento indicado pelo chamador, sem alterar nem refazer sua baixa.
"""
from __future__ import annotations

from datetime import datetime
from math import isfinite
import re
from typing import Any, Mapping

from ...state_layout import SETUP_VERSION
from .allocation import verify_department_allocation
from .contracts import build_params, normalize_percentage_departments
from .exception import OmieError


_RESOURCES = {
    "account_transactions": ("lancamentos_conta", "nCodLanc", "cCodIntLanc", "departamentos", 20),
    "payables": ("contas_pagar", "codigo_lancamento_omie", "codigo_lancamento_integracao", "distribuicao", 60),
    "receivables": ("contas_receber", "codigo_lancamento_omie", "codigo_lancamento_integracao", "distribuicao", 60),
}
_MISSING = object()
_UPDATE_METHODS = {"account_transactions": "AlterarLancCC", "payables": "AlterarContaPagar", "receivables": "AlterarContaReceber"}
_CORE_FIELDS = {
    "account_transactions": ("cabecalho.nCodCC", "cabecalho.dDtLanc", "cabecalho.nValorLanc", "detalhes"),
    "payables": ("valor_documento", "data_vencimento", "codigo_cliente_fornecedor"),
    "receivables": ("valor_documento", "data_vencimento", "codigo_cliente_fornecedor"),
}
_ACCOUNT_FIELDS = (
    "cCodIntLanc", "cabecalho.nCodCC", "cabecalho.dDtLanc", "cabecalho.nValorLanc",
    "detalhes.cCodCateg", "detalhes.aCodCateg", "detalhes.cTipo", "detalhes.cNumDoc",
    "detalhes.nCodCliente", "detalhes.nCodProjeto", "detalhes.cObs", "transferencia.nCodCCDestino",
    "diversos.cOrigem", "diversos.cNatureza", "diversos.nCodLancCP", "diversos.nCodLancCR",
    "diversos.dDtConc", "diversos.cHrConc", "diversos.cUsConc", "diversos.cIdentLanc",
    "diversos.nCodVendedor", "diversos.nCodComprador",
)
_TITLE_FIELDS = (
    "codigo_lancamento_integracao", "codigo_cliente_fornecedor", "codigo_cliente_fornecedor_integracao",
    "data_vencimento", "data_previsao", "data_emissao", "data_entrada", "valor_documento", "valor_pag",
    "codigo_categoria", "categorias", "codigo_projeto", "id_conta_corrente", "codigo_tipo_documento",
    "numero_documento", "numero_documento_fiscal", "numero_parcela", "numero_pedido", "chave_nfe",
    "observacao", "status_titulo", "id_origem", "pagamento", "pagamentos",
    "valor_cofins", "valor_csll", "valor_inss", "valor_ir", "valor_iss", "valor_pis",
    "retem_cofins", "retem_csll", "retem_inss", "retem_ir", "retem_iss", "retem_pis",
)


def _error(code: str, message: str, **details: Any) -> OmieError:
    return OmieError(code, message, {"retryable": False, "versao_mcp": SETUP_VERSION, **details})


def _safe_cause(error: Exception) -> dict[str, Any]:
    result: dict[str, Any] = {"cause_code": error.code if isinstance(error, OmieError) else "unexpected_error"}
    if not isinstance(error, OmieError) or not isinstance(error.details, Mapping):
        return result
    delay, status = error.details.get("retry_after_seconds"), error.details.get("http_status")
    if type(delay) is int and delay > 0:
        result["retry_after_seconds"] = delay
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    reason = error.details.get("cause_reason", error.details.get("reason"))
    if isinstance(reason, str) and reason.strip() and len(reason) <= 128 and reason.isprintable():
        result["cause_reason"] = reason
    return result


def _identifier(resource: str, identifier: Any) -> dict[str, Any]:
    _, id_key, integration_key, _, limit = _RESOURCES[resource]
    if not isinstance(identifier, Mapping) or len(identifier) != 1 or not set(identifier) <= {id_key, integration_key}:
        raise _error("invalid_parameter", "Informe exatamente um identificador do objeto financeiro.", field="dados")
    key, value = next(iter(identifier.items()))
    valid = type(value) is int and value > 0 if key == id_key else isinstance(value, str) and bool(value.strip()) and len(value) <= limit
    if not valid:
        raise _error("invalid_parameter", "O identificador financeiro é inválido.", field="dados.id" if key == id_key else "dados.codigo_integracao")
    return dict(identifier)


def _read(service: Any, profile: str, resource: str, identifier: Mapping[str, Any]) -> dict[str, Any]:
    try:
        record = service.invoke_financial(profile, resource + "_get", {"params": dict(identifier)}, writes=False)
    except Exception as error:
        raise _error(
            "allocation_preflight_failed", "Não foi possível conferir o objeto antes da alteração de rateio; nenhuma escrita foi enviada.",
            escrita_processada=False, **_safe_cause(error),
        ) from error
    id_key = _RESOURCES[resource][1]
    if (not isinstance(record, Mapping) or type(record.get(id_key)) is not int or record[id_key] < 1
            or any(key not in record or type(record[key]) is not type(value) or record[key] != value for key, value in identifier.items())):
        raise _error(
            "allocation_preflight_failed", "A consulta não confirmou a identidade do objeto financeiro; nenhuma escrita foi enviada.",
            escrita_processada=False, reason="invalid_response_identity",
        )
    _validate_core(record, resource)
    return dict(record)


def _links(record: Mapping[str, Any]) -> tuple[str, int | None, int | None]:
    source = record.get("diversos")
    if not isinstance(source, Mapping):
        raise _error("allocation_origin_unresolved", "A consulta não informou a origem do movimento; nenhuma escrita foi enviada.", escrita_processada=False)
    origin = source.get("cOrigem")
    if origin is None or origin == "":
        origin = ""
    elif not isinstance(origin, str) or len(origin) != 4 or not origin.isascii() or not origin.isalnum():
        raise _error("allocation_origin_unresolved", "A origem do movimento não pôde ser identificada; nenhuma escrita foi enviada.", escrita_processada=False)
    links = []
    for key in ("nCodLancCP", "nCodLancCR"):
        value = source.get(key)
        if value is None or type(value) is int and value == 0:
            links.append(None)
        elif type(value) is int and value > 0:
            links.append(value)
        else:
            raise _error("allocation_origin_unresolved", "O vínculo do movimento não é um ID válido; nenhuma escrita foi enviada.", escrita_processada=False, field="diversos." + key)
    payable, receivable = links
    if not origin and payable is None and receivable is None:
        raise _error("allocation_origin_unresolved", "A consulta não informou origem nem título vinculado; nenhuma escrita foi enviada.", escrita_processada=False)
    if payable is not None and receivable is not None or origin.upper() == "BAXP" and receivable is not None or origin.upper() == "BAXR" and payable is not None:
        raise _error("allocation_origin_unresolved", "O movimento tem vínculos de origem ambíguos ou inconsistentes; nenhuma escrita foi enviada.", escrita_processada=False)
    return origin.upper(), payable, receivable


def _semantic(departments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"codigo": row["cCodDep"], "percentual": row["nPerDep"]} for row in departments]


def _guard_direct(record: Mapping[str, Any], departments: list[dict[str, Any]]) -> None:
    origin, payable, receivable = _links(record)
    if payable is None and receivable is None:
        if origin not in {"BAXP", "BAXR"}:
            return
        raise _error("allocation_origin_unresolved", "O movimento é uma baixa, mas a consulta não informou o título de origem; nenhuma escrita foi enviada.", escrita_processada=False, origem=origin)
    resource, identifier = ("contas_pagar", payable) if payable is not None else ("contas_receber", receivable)
    tool = f"omie.financeiro.{resource}.definir_departamentos"
    raise _error(
        "allocation_requires_source_title",
        "Este método do MCP atende somente lançamentos diretos. Para este movimento vinculado, defina o rateio no título de origem e confira a propagação; nenhuma escrita foi enviada. Essa é uma proteção do MCP, não uma afirmação de proibição da API Omie.",
        escrita_processada=False, origem=origin, suggested_tool=tool,
        suggested_arguments={"confirmar": True, "dados": {"id": identifier, "departamentos": _semantic(departments), "lancamento_conta_id": record["nCodLanc"]}},
    )


def _at(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _validate_core(record: Mapping[str, Any], resource: str) -> None:
    missing = [path for path in _CORE_FIELDS[resource] if _at(record, path) is _MISSING]
    if missing:
        raise _error("allocation_preflight_failed", "A consulta não retornou os campos críticos necessários para preservar o objeto; nenhuma escrita foi enviada.", escrita_processada=False, reason="missing_core_fields", campos=missing)
    invalid = []
    for path in _CORE_FIELDS[resource]:
        value = _at(record, path)
        if path in {"cabecalho.nCodCC", "codigo_cliente_fornecedor"}:
            valid = type(value) is int and value > 0
        elif path in {"cabecalho.nValorLanc", "valor_documento"}:
            valid = type(value) is int and value > 0 or type(value) is float and isfinite(value) and value > 0
        elif path in {"cabecalho.dDtLanc", "data_vencimento"}:
            valid = isinstance(value, str) and re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", value) is not None
            if valid:
                try:
                    datetime.strptime(value, "%d/%m/%Y")
                except ValueError:
                    valid = False
        else:
            valid = isinstance(value, Mapping)
        if not valid:
            invalid.append(path)
    if invalid:
        raise _error("allocation_preflight_failed", "A consulta retornou campos críticos inválidos; nenhuma escrita foi enviada.", escrita_processada=False, reason="invalid_core_fields", campos=invalid)


def _same_value(left: Any, right: Any, *, unordered: bool = False) -> bool:
    """Compara snapshots JSON sem confundir booleanos com valores numéricos."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return isinstance(left, Mapping) and isinstance(right, Mapping) and set(left) == set(right) and all(_same_value(left[key], right[key]) for key in left)
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            return False
        if unordered:
            remaining = list(right)
            for value in left:
                index = next((index for index, candidate in enumerate(remaining) if _same_value(value, candidate)), None)
                if index is None:
                    return False
                remaining.pop(index)
            return True
        return all(_same_value(first, second) for first, second in zip(left, right))
    return left == right


def _changed(before: Mapping[str, Any], after: Mapping[str, Any], resource: str) -> list[dict[str, Any]]:
    result = []
    for path in _ACCOUNT_FIELDS if resource == "account_transactions" else _TITLE_FIELDS:
        old, new = _at(before, path), _at(after, path)
        if old is _MISSING:
            continue
        if not _same_value(old, new, unordered=path in {"categorias", "detalhes.aCodCateg"}):
            result.append({"campo": path, "antes": old, "depois": None if new is _MISSING else new, "presente_depois": new is not _MISSING})
    return result


def _check_preserved(before: Mapping[str, Any], after: Mapping[str, Any], resource: str, target: Mapping[str, Any], observed: list[Any]) -> None:
    changed = _changed(before, after, resource)
    if changed:
        raise _error(
            "allocation_side_effect_detected", "O rateio foi confirmado, mas outros campos diferem entre o estado inicialmente consultado e a conferência posterior. Confira os campos indicados; não foi feita compensação automática.",
            escrita_processada=True, alvo=dict(target), rateio_confirmado=True, departamentos=observed, campos_alterados=changed,
        )


def _apply(service: Any, profile: str, resource: str, body: Mapping[str, Any], native: Mapping[str, Any], before: Mapping[str, Any], target: Mapping[str, Any], movement_before: Mapping[str, Any] | None, movement_id: int | None, departments: list[dict[str, Any]], include_native: bool) -> dict[str, Any]:
    audit = {"call": _UPDATE_METHODS[resource], "param": [dict(native)]}
    acknowledged = False
    try:
        ack = service.invoke_financial(profile, resource + "_update", {"body": dict(body)}, writes=True)
        acknowledged = True
        id_key = _RESOURCES[resource][1]
        if not isinstance(ack, Mapping) or id_key in ack and (type(ack[id_key]) is not int or ack[id_key] != target["id"]):
            raise _error("outcome_unknown", "A resposta da escrita não confirmou a identidade do objeto solicitado; consulte o alvo antes de repetir.", escrita_processada=True, reason="write_response_identifier_mismatch", alvo=dict(target))
        verified = verify_department_allocation(service, profile, resource + "_update", native, include_observed=True)
        observed, after = verified["departamentos"], verified["registro"]
        _check_preserved(before, after, resource, target, observed)
        result: dict[str, Any] = {
            "alvo": dict(target), "departamentos": observed,
            "verificacao": {"rateio_confirmado": True, "campos_preservados": True,
                            "campos_verificados": [path for path in _ACCOUNT_FIELDS if _at(before, path) is not _MISSING] if resource == "account_transactions" else [path for path in _TITLE_FIELDS if _at(before, path) is not _MISSING]},
            "versao_mcp": SETUP_VERSION,
        }
        if movement_before is not None:
            try:
                movement = verify_department_allocation(service, profile, "account_transactions_update", {"nCodLanc": movement_id, "departamentos": departments}, include_observed=True)
            except Exception as error:
                source = error.details if isinstance(error, OmieError) and isinstance(error.details, Mapping) else {}
                raise _error(
                    "allocation_propagation_not_confirmed", "O rateio do título foi confirmado, mas não foi possível confirmar o mesmo rateio no movimento indicado. Nenhuma baixa foi cancelada, refeita ou alterada.",
                    escrita_processada=True, titulo_confirmado=True, alvo=dict(target), lancamento_conta_id=movement_id,
                    departamentos_titulo=observed, departamentos_movimento=source.get("departamentos_observados"), **_safe_cause(error),
                ) from error
            _check_preserved(movement_before, movement["registro"], "account_transactions", {"recurso": "lancamentos_conta", "id": movement_id}, movement["departamentos"])
            result["movimento"] = {"id": movement_id, "departamentos": movement["departamentos"]}
            result["verificacao"]["movimento_confirmado"] = True
        if include_native:
            result["dados_omie"] = {"requisicao": audit, "processamento": ack, "registro": after}
            if movement_before is not None:
                result["dados_omie"]["movimento"] = movement["registro"]
        return result
    except OmieError as error:
        details = {**(error.details if isinstance(error.details, Mapping) else {}), "versao_mcp": SETUP_VERSION}
        if include_native and (acknowledged or error.code in {"omie_api_error", "omie_http_error", "outcome_unknown"}):
            details["requisicao_omie"] = audit
        raise OmieError(error.code, str(error), details) from error
    except Exception as error:
        details: dict[str, Any] = {"cause_code": "unexpected_error"}
        if acknowledged:
            details["escrita_processada"] = True
            if include_native:
                details["requisicao_omie"] = audit
        raise _error("outcome_unknown", "A alteração de rateio não pôde ser concluída com segurança; consulte o objeto antes de repetir a escrita.", **details) from error


def define_departments(service: Any, profile: str, resource: str, identifier: Mapping[str, Any], departments: list[dict[str, Any]], *, movement_id: int | None = None, include_native: bool = False) -> dict[str, Any]:
    """Substitui o rateio de um objeto existente e confere o resultado observado.

    Lançamentos diretos usam ``AlterarLancCC``; títulos usam seu método de
    alteração com ``distribuicao``. Cada execução faz no máximo uma escrita e
    nunca cancela ou repete baixas. ``movement_id`` permite conferir também um
    movimento vinculado ao título, sem editá-lo.

    ``allocation_requires_source_title`` recusa movimentos vinculados antes da
    escrita e indica a tool correta. ``allocation_origin_unresolved`` e
    ``allocation_movement_mismatch`` recusam vínculos não comprovados.
    ``allocation_side_effect_detected`` informa alterações em outros campos;
    ``allocation_propagation_not_confirmed`` informa que o título foi confirmado
    mas a conferência do movimento não foi concluída. Nenhum desses resultados
    autoriza repetir automaticamente uma escrita já processada.
    A preservação compara os campos críticos presentes na pré-consulta, listados
    em ``verificacao.campos_verificados``; não atesta todos os campos do ERP.
    """
    if not isinstance(resource, str) or resource not in _RESOURCES:
        raise _error("invalid_parameter", "O recurso não oferece definição de departamentos.", field="recurso")
    query = _identifier(resource, identifier)
    normalized = normalize_percentage_departments(departments)
    if type(include_native) is not bool:
        raise _error("invalid_parameter", "incluir_dados_omie deve ser booleano.", field="incluir_dados_omie")
    if movement_id is not None and (resource == "account_transactions" or type(movement_id) is not int or movement_id < 1):
        raise _error("invalid_parameter", "lancamento_conta_id exige um ID positivo e só se aplica à conferência de títulos.", field="dados.lancamento_conta_id")
    public, id_key, _, _, _ = _RESOURCES[resource]
    before = _read(service, profile, resource, query)
    target = {"recurso": public, "id": before[id_key]}
    query = {id_key: before[id_key]}
    movement_before = None
    if resource == "account_transactions":
        _guard_direct(before, normalized)
    elif movement_id is not None:
        movement_before = _read(service, profile, "account_transactions", {"nCodLanc": movement_id})
        _, payable, receivable = _links(movement_before)
        linked = payable if resource == "payables" else receivable
        if linked != before[id_key]:
            raise _error("allocation_movement_mismatch", "O movimento indicado não está vinculado ao título consultado; nenhuma escrita foi enviada.", escrita_processada=False, alvo=target, lancamento_conta_id=movement_id)
    if resource == "account_transactions":
        body = {"id": before[id_key], "departments": normalized}
    else:
        body = {**query, "distribuicao": normalized}
    native = build_params(resource + "_update", {"body": body})
    return _apply(service, profile, resource, body, native, before, target, movement_before, movement_id, normalized, include_native)
