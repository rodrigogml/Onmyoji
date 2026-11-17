"""Contrato da conferência de categorias sem nova escrita financeira."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.omie import finance
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade


TOOL = "omie.financeiro.contas_pagar.conferir_categorias"
CATEGORIES = [{"codigo": "2.01.03", "percentual": 100}]


def facade(root):
    return OmieFinanceFacade(SimpleNamespace(root=root))


def arguments():
    return {"filtros": {"id": 34}, "categorias_esperadas": CATEGORIES}


@pytest.mark.parametrize("multiple_profiles", [False, True])
def test_category_check_publishes_a_closed_readonly_contract(tmp_path, multiple_profiles):
    profiles = [{"name": "empresa", "description": "ERP"}]
    if multiple_profiles:
        profiles.append({"name": "filial", "description": "Outro ERP"})
    definition = next(item for item in facade(tmp_path).definitions(profiles) if item["name"] == TOOL)
    schema = definition["input_schema"]
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    expected = {"filtros", "categorias_esperadas", "incluir_dados_omie", "atualizar_cache"}
    if multiple_profiles:
        expected.add("profile")
    assert set(schema["properties"]) == expected
    assert set(schema["properties"]["filtros"]["properties"]) == {"id", "codigo_integracao"}
    assert {"filtros", "categorias_esperadas"} <= set(schema["required"])
    data = arguments()
    if multiple_profiles:
        assert not validator.is_valid(data)
        data["profile"] = "filial"
    validator.validate(data)
    validator.validate({**data, "filtros": {"codigo_integracao": "000034"}})
    assert "ConsultaLancCC" in definition["description"]
    assert "sem repetir a gravação" in definition["description"]
    assert "sempre remotas" in definition["description"]
    assert finance.BY_NAME[TOOL].writes is False
    assert finance.BY_NAME[TOOL].paginated is False


@pytest.mark.parametrize("filters", [
    None, [], True, "34", {}, {"id": 34, "codigo_integracao": "TITLE-34"},
    {"id": "34"}, {"id": True}, {"id": 0}, {"id": -1},
    {"codigo_integracao": ""}, {"codigo_integracao": " "},
    {"codigo_integracao": 34}, {"codigo_integracao": "X" * 61},
    {"codigo_lancamento_omie": 34}, {"id": 34, "conta_corrente_id": 56},
])
def test_invalid_filters_fail_schema_and_runtime_before_service_access(tmp_path, monkeypatch, filters):
    instance = facade(tmp_path)
    schema = next(item for item in instance.definitions([{"name": "empresa"}]) if item["name"] == TOOL)["input_schema"]
    data = {**arguments(), "filtros": filters}
    assert not Draft202012Validator(schema).is_valid(data)
    constructor = Mock()
    check = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)
    monkeypatch.setattr(finance, "check_categories", check)

    with pytest.raises(OmieError) as error:
        instance.call(TOOL, data, "empresa")

    assert error.value.code == "invalid_parameter"
    assert error.value.details["retryable"] is False
    constructor.assert_not_called()
    check.assert_not_called()


@pytest.mark.parametrize("categories", [
    None, [], {}, True, "categoria", [None], [{}],
    [{"codigo": "2.01.03"}], [{"percentual": 100}],
    [{"codigo": 20103, "percentual": 100}], [{"codigo": " ", "percentual": 100}],
    [{"codigo": "X" * 21, "percentual": 100}],
    [{"codigo": "2.01.03", "percentual": "100"}],
    [{"codigo": "2.01.03", "percentual": True}],
    [{"codigo": "2.01.03", "percentual": 0}],
    [{"codigo": "2.01.03", "percentual": 101}],
    [{"codigo": "2.01.03", "percentual": 100, "valor": 1000}],
    [{"codigo_categoria": "2.01.03", "percentual": 100}],
])
def test_invalid_expected_categories_use_the_public_readonly_field_name(tmp_path, monkeypatch, categories):
    instance = facade(tmp_path)
    schema = next(item for item in instance.definitions([{"name": "empresa"}]) if item["name"] == TOOL)["input_schema"]
    data = {**arguments(), "categorias_esperadas": categories}
    assert not Draft202012Validator(schema).is_valid(data)
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)

    with pytest.raises(OmieError) as error:
        instance.call(TOOL, data, "empresa")

    assert error.value.code == "invalid_parameter"
    assert error.value.details["field"].startswith("categorias_esperadas")
    assert "dados.categorias" not in str(error.value.details)
    constructor.assert_not_called()


@pytest.mark.parametrize("categories", [
    [{"codigo": "2.01.03", "percentual": float("nan")}],
    [{"codigo": "2.01.03", "percentual": float("inf")}],
    [{"codigo": "2.01.03", "percentual": 99}],
    [{"codigo": "2.01.03", "percentual": 50}, {"codigo": "2.01.03", "percentual": 50}],
])
def test_nonfinite_duplicate_or_incomplete_distribution_fails_before_service_access(tmp_path, monkeypatch, categories):
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)

    with pytest.raises(OmieError) as error:
        facade(tmp_path).call(TOOL, {**arguments(), "categorias_esperadas": categories}, "empresa")

    assert error.value.code == "invalid_parameter"
    assert error.value.details["field"].startswith("categorias_esperadas")
    constructor.assert_not_called()


@pytest.mark.parametrize("missing", ["filtros", "categorias_esperadas"])
def test_both_the_identifier_and_expected_categories_are_required(tmp_path, monkeypatch, missing):
    data = arguments()
    del data[missing]
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)

    with pytest.raises(OmieError) as error:
        facade(tmp_path).call(TOOL, data, "empresa")

    assert error.value.code == "invalid_parameter"
    constructor.assert_not_called()


@pytest.mark.parametrize("extra", [
    {"confirmar": True}, {"confirmar": False}, {"dados": {"id": 34}},
    {"pagina": 1}, {"tamanho_pagina": 50}, {"id": 34},
    {"incluir_dados_omie": "S"}, {"atualizar_cache": 1},
    {"atualizar_cache": None}, {"atualizar_cache": "true"},
])
def test_write_controls_and_invalid_read_controls_are_rejected(tmp_path, monkeypatch, extra):
    instance = facade(tmp_path)
    schema = next(item for item in instance.definitions([{"name": "empresa"}]) if item["name"] == TOOL)["input_schema"]
    data = {**arguments(), **extra}
    assert not Draft202012Validator(schema).is_valid(data)
    constructor = Mock()
    monkeypatch.setattr(finance, "OmieService", constructor)

    with pytest.raises(OmieError):
        instance.call(TOOL, data, "empresa")

    constructor.assert_not_called()


@pytest.mark.parametrize("identifier,native_identifier", [
    ({"id": 34}, {"codigo_lancamento_omie": 34}),
    ({"codigo_integracao": "000034"}, {"codigo_lancamento_integracao": "000034"}),
    ({"codigo_integracao": "X" * 60}, {"codigo_lancamento_integracao": "X" * 60}),
])
@pytest.mark.parametrize("include_native", [False, True])
@pytest.mark.parametrize("refresh", [None, False, True], ids=["omitted", "false", "true"])
def test_category_check_dispatches_only_to_the_readonly_core(tmp_path, monkeypatch, identifier, native_identifier, include_native, refresh):
    service = Mock()
    constructor = Mock(return_value=service)
    expected = {"alvo": {"recurso": "contas_pagar", "id": 34}, "categorias": CATEGORIES, "baixas": []}
    check = Mock(return_value=expected)
    monkeypatch.setattr(finance, "OmieService", constructor)
    monkeypatch.setattr(finance, "check_categories", check)
    data = {"filtros": identifier, "categorias_esperadas": CATEGORIES, "incluir_dados_omie": include_native}
    if refresh is not None:
        data["atualizar_cache"] = refresh

    result = facade(tmp_path).call(TOOL, data, "empresa")

    assert result == expected
    constructor.assert_called_once_with(tmp_path)
    check.assert_called_once_with(service, "empresa", native_identifier,
                                 [{"codigo_categoria": "2.01.03", "percentual": 100}],
                                 include_native=include_native)
    service.invoke_financial.assert_not_called()
