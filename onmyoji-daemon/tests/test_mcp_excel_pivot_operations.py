"""Contratos de pivôs avançados e filtros sem depender de Excel instalado."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service import pivot_operations as operations


class Collection:
    def __init__(self, values):
        self.values = values
        self.Count = len(values)

    def Item(self, identifier):
        if isinstance(identifier, int):
            return self.values[identifier - 1]
        return next(item for item in self.values if item.Name == identifier)


def area(row=1, column=1, rows=1, columns=1, value=None):
    return SimpleNamespace(Row=row, Column=column, Rows=SimpleNamespace(Count=rows), Columns=SimpleNamespace(Count=columns),
                           Areas=SimpleNamespace(Count=1), CountLarge=rows * columns, Value2=value, _properties={},
                           AdvancedFilter=Mock(), Group=Mock(), Ungroup=Mock(), HasFormula=False)


@pytest.fixture
def environment(monkeypatch):
    cache = SimpleNamespace(OLAP=False, SourceType=1)
    calculated = Collection([]); calculated.Add = Mock()
    members = Collection([SimpleNamespace(Name="A", Visible=True), SimpleNamespace(Name="B", Visible=True), SimpleNamespace(Name="C", Visible=True)])
    field = SimpleNamespace(Name="Produto", Orientation=1, ClearAllFilters=Mock(), PivotItems=lambda: members, PivotFilters=SimpleNamespace(Add=Mock()))
    data = area(row=2, column=1, rows=3)
    cell = area(row=2, column=1)
    data._properties[("Cells", 1, 1)] = cell
    field.DataRange = data
    pivot = SimpleNamespace(Name="Resumo", PivotCache=lambda: cache, PivotFields=lambda name: field, ChangePivotCache=Mock(), RefreshTable=Mock(return_value=True),
                            CalculatedFields=lambda: calculated, DataFields=Collection([SimpleNamespace(Name="Soma de Valor")]), DataBodyRange=area(row=2, column=2, rows=3))
    sheet = SimpleNamespace(Name="Dados", PivotTables=lambda name: pivot)
    source = area(rows=4, columns=2)
    source._properties[("Rows", 1)] = area(columns=2, value=(("Produto", "Valor"),))
    ranges = {("Dados", "A1:B4"): source}
    sheets = Collection([sheet])
    replacement = SimpleNamespace(Name="NovoCache")
    caches = SimpleNamespace(Create=Mock(return_value=replacement))
    workbook = SimpleNamespace(Worksheets=sheets, Sheets=sheets, PivotCaches=lambda: caches)
    context = SimpleNamespace(profile={"allow_refresh": False}, defaults={"max_cells": 1000})
    session = SimpleNamespace(assert_hidden=Mock())
    missing = object()
    monkeypatch.setattr(operations, "_missing", lambda: missing)
    monkeypatch.setattr(operations, "worksheet", lambda book, name: sheets.Item(name))
    monkeypatch.setattr(operations, "target_range", lambda book, args, ctx: ranges[(args["sheet"], args["range"])])
    monkeypatch.setattr(operations, "parameterized_property", lambda owner, member, *args: owner._properties[(member, *args)])
    monkeypatch.setattr(operations, "range_address", lambda value: "A1:B4")
    monkeypatch.setattr(operations, "check_calculation_sources", Mock())
    monkeypatch.setattr(operations, "resize_range", lambda target, rows, columns: area(target.Row, target.Column, rows, columns))
    monkeypatch.setattr(operations, "write_values", Mock())
    # Source resolution has independent native tests. These operation tests
    # supply a local constant source while exercising the calculation gate.
    from onmyoji_daemon.mcp.excel.service import native
    monkeypatch.setattr(native, "_pivot_source_range", lambda book, pivot_cache, ctx: source)
    return SimpleNamespace(workbook=workbook, context=context, session=session, pivot=pivot, cache=cache, field=field, members=members,
                           source=source, ranges=ranges, caches=caches, replacement=replacement, cell=cell, calculated=calculated, missing=missing)


def apply(environment, **operation):
    return operations.apply_pivot_operation(environment.workbook, {"sheet": "Dados", "name": "Resumo", **operation}, environment.session, environment.context)


def test_change_source_is_verified_and_external_cache_is_rejected(environment):
    assert apply(environment, type="pivot_source", source_sheet="Dados", source_range="A1:B4")
    environment.caches.Create.assert_called_once_with(1, environment.source)
    environment.pivot.ChangePivotCache.assert_called_once_with(environment.replacement)
    assert environment.session.operation_evidence[0]["status"] == "completed"
    environment.cache.SourceType = 2
    environment.cache.OLAP = True
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_source", source_sheet="Dados", source_range="A1:B4")
    assert caught.value.code == "external_data_denied"
    environment.context.profile["allow_refresh"] = True
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_source", source_sheet="Dados", source_range="A1:B4")
    assert caught.value.code == "olap_operation_unavailable"


@pytest.mark.parametrize("headers", [(("", "Value"),), (("Value", "value"),), ((None, "Value"),)])
def test_source_headers_must_be_nonempty_and_unique(environment, headers):
    environment.source._properties[("Rows", 1)].Value2 = headers
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_source", source_sheet="Dados", source_range="A1:B4")
    assert caught.value.code == "invalid_pivot_source"
    assert not environment.pivot.ChangePivotCache.called


def test_field_items_and_page_fields_use_explicit_members(environment):
    apply(environment, type="pivot_field", field="Produto", action="items", items=["A", "C"])
    assert [item.Visible for item in environment.members.values] == [True, False, True]
    environment.field.Orientation = 3
    apply(environment, type="pivot_field", field="Produto", action="items", items=["A"])
    assert environment.field.EnableMultiplePageItems is False and environment.field.CurrentPage == "A"
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_field", field="Produto", action="items", items=["missing"])
    assert caught.value.code == "invalid_pivot_items"


def test_olap_item_filters_require_policy_and_use_member_unique_names(environment):
    environment.cache.OLAP = True
    environment.cache.SourceType = 2
    environment.cache.WorkbookConnection = SimpleNamespace(Type=7)
    apply(environment, type="pivot_field", field="Produto", action="items", items=["[Produto].&[A]"])
    assert environment.field.VisibleItemsList == ("[Produto].&[A]",)


@pytest.mark.parametrize("action,operator,criteria,expected", [("label", "contains", "Prod", 21), ("value", "greater_than", 100, 9),
                                                           ("date", "greater_or_equal", "2026-01-01", 34), ("value", "top_count", 10, 1)])
def test_filter_arguments_match_official_pivotfilter_types(environment, action, operator, criteria, expected):
    operation = {"type": "pivot_field", "field": "Produto", "action": action, "operator": operator, "criteria1": criteria}
    if action == "value":
        operation["data_field"] = "Soma de Valor"
    apply(environment, **operation)
    arguments = environment.field.PivotFilters.Add.call_args.args
    assert arguments[0] == expected
    assert arguments[2] == (datetime(2026, 1, 1) if action == "date" else criteria)
    assert arguments[3] is environment.missing
    if action == "value":
        assert arguments[1].Name == "Soma de Valor"


def test_between_and_value_filters_reject_missing_or_wrong_criteria(environment):
    for specification, code in [
        ({"action": "label", "operator": "between", "criteria1": "A"}, "invalid_pivot_filter"),
        ({"action": "value", "operator": "greater_than", "criteria1": "100", "data_field": "Soma de Valor"}, "invalid_pivot_filter"),
        ({"action": "value", "operator": "top_percent", "criteria1": 101, "data_field": "Soma de Valor"}, "invalid_pivot_filter"),
        ({"action": "value", "operator": "greater_than", "criteria1": 10}, "data_field_required"),
    ]:
        with pytest.raises(ExcelExceptions) as caught:
            apply(environment, type="pivot_field", field="Produto", **specification)
        assert caught.value.code == code


def test_calculated_field_add_update_delete_and_olap_limit(environment):
    apply(environment, type="pivot_calculated_field", field="Dobro", action="set", formula="=Valor*2")
    environment.calculated.Add.assert_called_once_with("Dobro", "=Valor*2", True)
    existing = SimpleNamespace(Name="Dobro", Delete=Mock())
    environment.calculated.values = [existing]; environment.calculated.Count = 1
    apply(environment, type="pivot_calculated_field", field="Dobro", action="set", formula="=Valor*3")
    assert existing.StandardFormula == "=Valor*3"
    apply(environment, type="pivot_calculated_field", field="Dobro", action="delete")
    assert existing.Delete.called


def test_numeric_and_date_grouping_use_a_single_field_cell(environment):
    apply(environment, type="pivot_group", field="Produto", action="group", start=0, end=100, interval=10)
    environment.cell.Group.assert_called_once_with(0, 100, 10, environment.missing)
    periods = [False, False, False, False, True, False, True]
    apply(environment, type="pivot_group", field="Produto", action="group", start="2026-01-01", end="2026-12-31", periods=periods)
    assert environment.cell.Group.call_args.args == (datetime(2026, 1, 1), datetime(2026, 12, 31), environment.missing, tuple(periods))
    apply(environment, type="pivot_group", field="Produto", action="ungroup")
    environment.cell.Ungroup.assert_called_once()


@pytest.mark.parametrize("parameters", [{"interval": 0}, {"start": False}, {"start": 20, "end": 1}, {"periods": [True]},
                                         {"periods": [False] * 7}, {"start": "2026-01-01T00:00:00+03:00"}])
def test_invalid_grouping_does_not_call_group(environment, parameters):
    with pytest.raises(ExcelExceptions):
        apply(environment, type="pivot_group", field="Produto", action="group", **parameters)
    assert not environment.cell.Group.called


def test_advanced_filter_in_place_and_copy_use_explicit_ranges(environment):
    criteria = area(row=1, column=8, rows=2)
    destination = area(row=1, column=5)
    environment.ranges[("Dados", "H1:H2")] = criteria
    environment.ranges[("Dados", "E1")] = destination
    apply(environment, type="advanced_filter", range="A1:B4", action="in_place", criteria_range="H1:H2", unique=True)
    environment.source.AdvancedFilter.assert_called_once_with(1, criteria, environment.missing, True)
    apply(environment, type="advanced_filter", range="A1:B4", action="copy", criteria_range="H1:H2", copy_sheet="Dados", copy_range="E1")
    arguments = environment.source.AdvancedFilter.call_args.args
    assert arguments[0] == 2 and arguments[2].CountLarge == 2
    operations.write_values.assert_called_once()


def test_filter_copy_overlap_and_cross_sheet_are_rejected(environment):
    for target, sheet, expected in [(area(row=1, column=2), "Dados", "overlapping_filter_destination"), (area(row=1, column=5), "Other", "advanced_filter_copy_unavailable")]:
        environment.ranges[(sheet, "B1")] = target
        with pytest.raises(ExcelExceptions) as caught:
            apply(environment, type="advanced_filter", range="A1:B4", action="copy", copy_sheet=sheet, copy_range="B1")
        assert caught.value.code == expected
    assert not environment.source.AdvancedFilter.called


def test_drilldown_rejects_label_cells_or_existing_destination(environment):
    environment.ranges[("Dados", "A2")] = area(row=2, column=1)
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_drilldown", range="A2", destination_sheet="Detail")
    assert caught.value.code == "invalid_drilldown_cell"
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_drilldown", range="A2", destination_sheet="Dados")
    assert caught.value.code == "sheet_exists"


def test_unrelated_operation_returns_false_and_com_failures_are_sanitized(environment):
    assert operations.apply_pivot_operation(environment.workbook, {"type": "other"}, environment.session, environment.context) is False
    environment.field.ClearAllFilters.side_effect = RuntimeError("sensitive remote detail")
    with pytest.raises(ExcelExceptions) as caught:
        apply(environment, type="pivot_field", field="Produto", action="clear")
    assert caught.value.code == "pivot_operation_failed" and "sensitive" not in str(caught.value)
