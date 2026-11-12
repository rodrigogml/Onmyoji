"""Contratos de análise e integração Excel, com permissões e limites explícitos."""
from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.advanced_analysis import apply_analysis_operation


def ranged(rows=1, columns=1, row=1, column=1, *, owner):
    target = Mock()
    target.Worksheet = owner
    target.Parent = owner
    target.CountLarge = rows * columns
    target.Areas.Count = 1
    target.Rows.Count, target.Columns.Count = rows, columns
    target.Row, target.Column = row, column
    return target


@pytest.fixture
def engine(monkeypatch):
    from onmyoji_daemon.mcp.excel.service import operations
    monkeypatch.setattr(operations, "check_calculation_sources", Mock(), raising=False)
    workbook, sheet, session = Mock(), Mock(), Mock()
    session.assert_hidden = Mock()
    workbook.FullName = "C:/Work/analysis-fixture.xlsx"
    workbook.Application.Hwnd = 1234
    sheet.Name = "Dados"
    sheet.Parent = workbook
    sheet.Application = workbook.Application
    workbook.Worksheets.Item.return_value = sheet
    context = SimpleNamespace(profile={}, defaults={"max_cells": 100}, resolve_password=Mock())
    return workbook, sheet, session, context


def test_unrecognized_operation_has_no_excel_effect(engine):
    workbook, _, session, context = engine
    assert apply_analysis_operation(workbook, {"type": "foreign"}, session, context) is False
    assert not workbook.mock_calls and not session.mock_calls


def test_autofill_requires_destination_containing_source(engine):
    workbook, sheet, session, context = engine
    source, destination = ranged(2, owner=sheet), ranged(5, owner=sheet)
    sheet.Range.side_effect = lambda name: {"A1:A2": source, "A1:A5": destination, "B1:B5": ranged(5, column=2, owner=sheet)}[name]
    operation = {"type": "autofill", "sheet": "Dados", "range": "A1:A2", "destination": "A1:A5", "fill_type": 2}
    assert apply_analysis_operation(workbook, operation, session, context)
    source.AutoFill.assert_called_once_with(Destination=destination, Type=2)
    with pytest.raises(ExcelExceptions, match="conter"):
        apply_analysis_operation(workbook, {**operation, "destination": "B1:B5"}, session, context)
    assert source.AutoFill.call_count == 1


@pytest.mark.parametrize("orientation,action,member", [("rows", "group", "Group"), ("columns", "ungroup", "Ungroup"), ("columns", "clear", "ClearOutline")])
def test_outline_changes_structure_without_scanning_full_rows_or_columns(engine, orientation, action, member):
    workbook, sheet, session, context = engine
    selected = ranged(1048576, 3, owner=sheet)
    sheet.Range.return_value = selected
    operation = {"type": "outline", "sheet": "Dados", "range": "A:C", "action": action, "orientation": orientation}
    assert apply_analysis_operation(workbook, operation, session, context)
    target = selected.EntireRow if orientation == "rows" else selected.EntireColumn
    getattr(target, member).assert_called_once_with()


def test_outline_can_hide_details_and_rejects_discontiguous_ranges(engine):
    workbook, sheet, session, context = engine
    selected = ranged(owner=sheet)
    sheet.Range.return_value = selected
    operation = {"type": "outline", "sheet": "Dados", "range": "A1:A4", "action": "show_detail", "orientation": "rows", "show": False}
    apply_analysis_operation(workbook, operation, session, context)
    assert selected.EntireRow.ShowDetail is False
    selected.Areas.Count = 2
    with pytest.raises(ExcelExceptions, match="contínuo"):
        apply_analysis_operation(workbook, operation, session, context)


def test_subtotals_reject_columns_outside_explicit_range(engine):
    workbook, sheet, session, context = engine
    target = ranged(4, 3, owner=sheet)
    sheet.Range.return_value = target
    operation = {"type": "subtotal", "sheet": "Dados", "range": "A1:C4", "group_by": 1, "function": "sum", "total_columns": [2, 3]}
    apply_analysis_operation(workbook, operation, session, context)
    assert target.Subtotal.call_args.kwargs["TotalList"] == (2, 3)
    with pytest.raises(ExcelExceptions, match="pertencer"):
        apply_analysis_operation(workbook, {**operation, "total_columns": [4]}, session, context)


def test_scenarios_match_values_to_changing_cells_and_show_without_ui(engine):
    workbook, sheet, session, context = engine
    sheet.Range.return_value = ranged(2, owner=sheet)
    operation = {"type": "scenario", "sheet": "Dados", "action": "add", "name": "Base", "changing_range": "A1:A2", "values": [10, 20]}
    apply_analysis_operation(workbook, operation, session, context)
    assert sheet.Scenarios.return_value.Add.call_args.kwargs["Values"] == (10, 20)
    with pytest.raises(ExcelExceptions, match="um valor"):
        apply_analysis_operation(workbook, {**operation, "values": [10]}, session, context)
    with pytest.raises(ExcelExceptions, match="texto"):
        apply_analysis_operation(workbook, {**operation, "values": [10, None]}, session, context)
    apply_analysis_operation(workbook, {"type": "scenario", "sheet": "Dados", "action": "show", "name": "Base"}, session, context)
    sheet.Scenarios.return_value.Item.return_value.Show.assert_called_once_with()
    apply_analysis_operation(workbook, {"type": "scenario", "sheet": "Dados", "action": "delete", "name": "Base"}, session, context)
    sheet.Scenarios.return_value.Item.return_value.Delete.assert_called_once_with()


def test_scenario_text_is_preserved_without_implicit_formula_or_number_conversion(engine):
    workbook, sheet, session, context = engine
    sheet.Range.return_value = ranged(2, owner=sheet)
    apply_analysis_operation(workbook, {"type": "scenario", "sheet": "Dados", "action": "add", "name": "Texto", "changing_range": "A1:A2", "values": ["=1+1", "00123"]}, session, context)
    assert sheet.Scenarios.return_value.Add.call_args.kwargs["Values"] == ("'=1+1", "'00123")


def test_data_table_requires_single_input_cells(engine):
    workbook, sheet, session, context = engine
    target, single, multiple = ranged(3, 3, owner=sheet), ranged(owner=sheet), ranged(2, owner=sheet)
    sheet.Range.side_effect = lambda name: {"A1:C3": target, "E1": single, "E1:E2": multiple}[name]
    operation = {"type": "data_table", "sheet": "Dados", "range": "A1:C3", "row_input": "E1"}
    apply_analysis_operation(workbook, operation, session, context)
    target.Table.assert_called_once_with(RowInput=single)
    with pytest.raises(ExcelExceptions, match="única"):
        apply_analysis_operation(workbook, {**operation, "row_input": "E1:E2"}, session, context)
    with pytest.raises(ExcelExceptions, match="entrada"):
        apply_analysis_operation(workbook, {key: value for key, value in operation.items() if key != "row_input"}, session, context)


def test_connections_require_trusted_external_policy_and_vault_reference(engine):
    workbook, _, session, context = engine
    operation = {"type": "connection", "action": "add", "name": "Consulta", "connection_ref": {"credential_profile": "vault", "credential_entry_path": "db"}, "command_text": "SELECT 1"}
    with pytest.raises(ExcelExceptions, match="confiável"):
        apply_analysis_operation(workbook, operation, session, context)
    workbook.Connections.Add2.assert_not_called()
    context.profile.update(allow_refresh=True, trusted_content=True)
    context.resolve_password.return_value = "OLEDB;Provider=Example"
    connection = workbook.Connections.Add2.return_value
    connection.Type = 1
    apply_analysis_operation(workbook, operation, session, context)
    context.resolve_password.assert_called_once_with(operation["connection_ref"])
    assert connection.OLEDBConnection.SavePassword is False
    assert connection.OLEDBConnection.RefreshOnFileOpen is False
    assert connection.OLEDBConnection.BackgroundQuery is False
    with pytest.raises(ExcelExceptions, match="combinar"):
        apply_analysis_operation(workbook, {**operation, "connection_string": "second"}, session, context)


def test_connection_update_and_delete_target_only_selected_provider(engine):
    workbook, _, session, context = engine
    context.profile.update(allow_refresh=True, trusted_content=True)
    connection = workbook.Connections.Item.return_value
    connection.Type = 2
    apply_analysis_operation(workbook, {"type": "connection", "action": "update", "name": "DB", "connection_string": "ODBC;DSN=x", "description": "Dados", "command_text": "SELECT 1", "refresh_with_refresh_all": False}, session, context)
    assert connection.ODBCConnection.Connection == "ODBC;DSN=x"
    assert connection.ODBCConnection.CommandText == "SELECT 1"
    assert connection.RefreshWithRefreshAll is False
    with pytest.raises(ExcelExceptions, match="CommandType"):
        apply_analysis_operation(workbook, {"type": "connection", "action": "update", "name": "DB", "command_type": 2}, session, context)
    apply_analysis_operation(workbook, {"type": "connection", "action": "delete", "name": "DB"}, session, context)
    connection.Delete.assert_called_once_with()


def test_local_model_connection_uses_worksheet_connection_without_oledb_provider(engine):
    workbook, _, session, context = engine
    context.profile.update(allow_refresh=True, trusted_content=True)
    connection = workbook.Connections.Add2.return_value
    connection.Type = 8
    operation = {"type": "connection", "action": "add", "name": "Modelo", "connection_string": "WORKSHEET;C:/Work/source.xlsx", "command_text": "Dados", "command_type": 7, "create_model_connection": True}
    apply_analysis_operation(workbook, operation, session, context)
    assert workbook.Connections.Add2.call_args.args[-2] is True
    assert not connection.OLEDBConnection.mock_calls


def test_query_loading_never_claims_refresh_without_completion(engine):
    workbook, sheet, session, context = engine
    operation = {"type": "query_load", "sheet": "Dados", "name": "Consulta", "destination": "A1", "table_name": "Tabela"}
    with pytest.raises(ExcelExceptions, match="fontes externas"):
        apply_analysis_operation(workbook, operation, session, context)
    context.profile["allow_refresh"] = True
    workbook.Queries.Item.return_value.Name = "Consulta"
    sheet.Range.return_value = ranged(owner=sheet)
    table = sheet.ListObjects.Add.return_value
    table.Range.CountLarge = 4
    table.QueryTable.Refresh.return_value = True
    table.QueryTable.Refreshing = False
    apply_analysis_operation(workbook, operation, session, context)
    table.QueryTable.Refresh.assert_not_called()
    apply_analysis_operation(workbook, {**operation, "refresh": True}, session, context)
    table.QueryTable.Refresh.assert_called_once_with(BackgroundQuery=False)
    table.QueryTable.Refresh.return_value = False
    with pytest.raises(ExcelExceptions, match="concluiu"):
        apply_analysis_operation(workbook, {**operation, "refresh": True}, session, context)


def test_copy_sheet_preserves_source_and_rejects_failed_preflight(engine, tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel.service import advanced_analysis
    workbook, _, session, context = engine
    source, temporary = tmp_path / "source.xlsx", tmp_path / "copy.xlsx"
    source.write_bytes(b"synthetic source")
    context.input_path = Mock(return_value=source)
    context.temporary_path = Mock(return_value=temporary)
    monkeypatch.setattr(advanced_analysis, "preflight", lambda *args: {"native_open_allowed": False})
    operation = {"type": "copy_sheet_from", "input_path": str(source), "sheet": "Dados", "name": "Copiada"}
    with pytest.raises(ExcelExceptions, match="preflight"):
        apply_analysis_operation(workbook, operation, session, context)
    session.open.assert_not_called()
    monkeypatch.setattr(advanced_analysis, "preflight", lambda *args: {"native_open_allowed": True})
    workbook.Sheets.Count = 1
    external = session.open.return_value
    original = external.Worksheets.Item.return_value
    monkeypatch.setitem(sys.modules, "pythoncom", SimpleNamespace(Missing=object()))
    original._oleobj_.InvokeTypes.side_effect = lambda *args: setattr(workbook.Sheets, "Count", 2)
    created = workbook.Sheets.Item.return_value
    created.UsedRange.CountLarge = 4
    apply_analysis_operation(workbook, operation, session, context)
    assert created.Name == "Copiada"
    assert source.read_bytes() == temporary.read_bytes() == b"synthetic source"
    external.Close.assert_called_once_with(SaveChanges=False)
