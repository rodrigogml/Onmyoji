"""Contratos de baixas com ajustes monetários, sem cofre ou API reais."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie import finance
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade
from onmyoji_daemon.mcp.omie.service import OmieService


OPERATIONS = [
    ("omie.financeiro.contas_pagar.registrar_pagamento", "payables", "LancarPagamento"),
    ("omie.financeiro.contas_receber.registrar_recebimento", "receivables", "LancarRecebimento"),
]
FIELDS = {"id", "codigo_integracao", "conta_corrente_id", "valor", "data", "observacao", "desconto", "juros", "multa"}
ACK = {"codigo_status": "0", "codigo_baixa": 78}


def payment_data():
    """Os valores sintéticos não pressupõem a liquidação de um título real."""
    return {"id": 34, "conta_corrente_id": 56, "valor": 1000, "data": "17/11/2026"}


def facade(root):
    return OmieFinanceFacade(SimpleNamespace(root=root))


def schema_for(root, name):
    return next(item["input_schema"] for item in facade(root).definitions([{"name": "empresa"}]) if item["name"] == name)


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
def test_payment_schemas_publish_optional_nonnegative_amounts_without_defaults(tmp_path, name, endpoint, method):
    schema = schema_for(tmp_path, name)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    data_schema = schema["properties"]["dados"]

    assert set(data_schema["properties"]) == FIELDS
    assert set(data_schema["required"]) == {"conta_corrente_id", "valor", "data"}
    assert data_schema["additionalProperties"] is False
    for field in ("desconto", "juros", "multa"):
        assert data_schema["properties"][field]["type"] == "number"
        assert data_schema["properties"][field]["minimum"] == 0
        assert "default" not in data_schema["properties"][field]
    data = payment_data()
    validator.validate({"confirmar": True, "dados": data})
    validator.validate({"confirmar": True, "dados": {**data, "desconto": 0, "juros": 0.5, "multa": 2}})
    validator.validate({"confirmar": True, "dados": {**data, "codigo_integracao": "TEST-TITLE-34"}})
    validator.validate({"confirmar": True, "dados": {key: value for key, value in {**data, "codigo_integracao": "T" * 60}.items() if key != "id"}})
    assert not validator.is_valid({"confirmar": True, "dados": {**data, "desconto": -0.01}})
    assert not validator.is_valid({"confirmar": True, "dados": {**data, "desconto": None}})
    assert not validator.is_valid({"confirmar": True, "dados": {**data, "desconto": True}})


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
@pytest.mark.parametrize("identifier", [{"id": 34}, {"codigo_integracao": "000-TEST-TITLE"}, {"id": 34, "codigo_integracao": "000-TEST-TITLE"}])
@pytest.mark.parametrize("adjustments", [
    {}, {"desconto": 0}, {"desconto": 50}, {"juros": 2.5}, {"multa": 3.25},
    {"desconto": 50, "juros": 2.5, "multa": 3.25},
])
def test_payment_adapter_preserves_requested_values_and_omits_unsupplied_adjustments(tmp_path, monkeypatch, name, endpoint, method, identifier, adjustments):
    """Preserva fachada, serviço e build_params; apenas perfil e HTTP são simulados."""
    profile = Mock(return_value=({}, {}))
    request = Mock(return_value=ACK)
    monkeypatch.setattr(OmieService, "_profile", profile)
    monkeypatch.setattr(OmieService, "_request", request)
    data = {**payment_data(), **identifier, **adjustments, "observacao": "Baixa sintética"}
    if "id" not in identifier:
        data.pop("id")
    original = deepcopy(data)
    expected = {
        "codigo_conta_corrente": 56, "valor": 1000, "data": "17/11/2026",
        "observacao": "Baixa sintética", **adjustments,
    }
    if "id" in identifier:
        expected["codigo_lancamento"] = identifier["id"]
    if "codigo_integracao" in identifier:
        expected["codigo_lancamento_integracao"] = identifier["codigo_integracao"]

    result = facade(tmp_path).call(name, {"confirmar": True, "dados": data}, "empresa")

    assert result["dados"] == [ACK]
    profile.assert_called_once_with("empresa")
    request.assert_called_once_with({}, {}, endpoint, method, expected, retries=0)
    assert data == original
    assert "conciliar_documento" not in expected
    assert "codigo_baixa" not in expected
    for field in {"desconto", "juros", "multa"} - set(adjustments):
        assert field not in request.call_args.args[4]


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
def test_payment_amount_and_discount_are_forwarded_separately_without_recalculation(tmp_path, monkeypatch, name, endpoint, method):
    profile = Mock(return_value=({}, {}))
    request = Mock(return_value=ACK)
    monkeypatch.setattr(OmieService, "_profile", profile)
    monkeypatch.setattr(OmieService, "_request", request)
    data = {**payment_data(), "valor": 125.25, "desconto": 5.25}

    facade(tmp_path).call(name, {"confirmar": True, "dados": data, "incluir_dados_omie": True}, "empresa")

    assert request.call_count == 1
    assert request.call_args.args[4] == {
        "codigo_lancamento": 34, "codigo_conta_corrente": 56,
        "valor": 125.25, "desconto": 5.25, "data": "17/11/2026",
    }
    assert request.call_args.kwargs == {"retries": 0}


INVALID_FIELDS = [
    ("id", None), ("id", True), ("id", 0), ("id", -1), ("id", "34"),
    ("conta_corrente_id", None), ("conta_corrente_id", True), ("conta_corrente_id", 0),
    ("conta_corrente_id", -1), ("conta_corrente_id", "56"),
    ("codigo_integracao", None), ("codigo_integracao", True), ("codigo_integracao", ""),
    ("codigo_integracao", "  "), ("codigo_integracao", "T" * 61),
    ("data", None), ("data", True), ("data", "31/02/2026"), ("data", "2026-11-17"),
    ("observacao", None), ("observacao", True), ("observacao", []),
] + [
    (field, value)
    for field in ("valor", "desconto", "juros", "multa")
    for value in (None, True, False, "10.00", [], {}, -0.01, float("nan"), float("inf"), float("-inf"))
] + [("valor", 0)]


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
@pytest.mark.parametrize("field,value", INVALID_FIELDS)
def test_invalid_payment_fields_fail_before_service_access(tmp_path, monkeypatch, name, endpoint, method, field, value):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)
    data = {**payment_data(), field: value}

    with pytest.raises(OmieError) as raised:
        facade(tmp_path).call(name, {"confirmar": True, "dados": data}, "empresa")

    assert raised.value.code == "invalid_parameter"
    assert raised.value.details["field"] == "dados." + field
    assert raised.value.details["retryable"] is False
    constructor.assert_not_called()


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
@pytest.mark.parametrize("field", ["conta_corrente_id", "valor", "data"])
def test_required_payment_fields_fail_before_service_access(tmp_path, monkeypatch, name, endpoint, method, field):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)
    data = payment_data()
    del data[field]

    with pytest.raises(OmieError) as raised:
        facade(tmp_path).call(name, {"confirmar": True, "dados": data}, "empresa")

    assert raised.value.code == "invalid_parameter"
    assert raised.value.details["field"] == "dados." + field
    constructor.assert_not_called()


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
def test_payment_requires_a_title_identifier_before_service_access(tmp_path, monkeypatch, name, endpoint, method):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)
    data = payment_data()
    del data["id"]

    with pytest.raises(OmieError) as raised:
        facade(tmp_path).call(name, {"confirmar": True, "dados": data}, "empresa")

    assert raised.value.code == "invalid_parameter"
    assert raised.value.details["field"] == "dados"
    constructor.assert_not_called()


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
@pytest.mark.parametrize("alias", ["valor_desconto", "desconto_valor", "percentual_desconto"])
def test_discount_aliases_point_to_the_canonical_field_without_service_access(tmp_path, monkeypatch, name, endpoint, method, alias):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)
    data = {**payment_data(), alias: 5}
    assert not Draft202012Validator(schema_for(tmp_path, name)).is_valid({"confirmar": True, "dados": data})

    with pytest.raises(OmieError) as raised:
        facade(tmp_path).call(name, {"confirmar": True, "dados": data}, "empresa")

    assert raised.value.code == "invalid_parameter"
    assert raised.value.details["field"] == "dados." + alias
    assert "dados.desconto" in raised.value.details["use_instead"]
    constructor.assert_not_called()


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
@pytest.mark.parametrize("field,value", [("conciliar_documento", "S"), ("id_baixa", 78), ("codigo_baixa_integracao", "TEST-78"), ("outro", 1)])
def test_unpublished_payment_fields_remain_rejected(tmp_path, monkeypatch, name, endpoint, method, field, value):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)

    with pytest.raises(OmieError) as raised:
        facade(tmp_path).call(name, {"confirmar": True, "dados": {**payment_data(), field: value}}, "empresa")

    assert raised.value.code == "invalid_parameter"
    assert raised.value.details["field"] == "dados." + field
    constructor.assert_not_called()


@pytest.mark.parametrize("name,endpoint,method", OPERATIONS)
@pytest.mark.parametrize("confirmation", [None, False, "true", 1])
def test_payment_still_requires_explicit_boolean_confirmation(tmp_path, monkeypatch, name, endpoint, method, confirmation):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)

    with pytest.raises(OmieError) as raised:
        facade(tmp_path).call(name, {"confirmar": confirmation, "dados": {**payment_data(), "desconto": 50}}, "empresa")

    assert raised.value.code == "confirmation_required"
    constructor.assert_not_called()
