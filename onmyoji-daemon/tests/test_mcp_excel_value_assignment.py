"""Preserva posições e tipos quando linhas ou colunas do destino estão ocultas."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.operations import rule_formula,validation_formula,write_values


@pytest.mark.parametrize("hidden_rows,hidden_columns", [(True, False), (None, False), (False, True), (False, None)])
def test_hidden_destination_uses_physical_cells_without_changing_visibility(monkeypatch, hidden_rows, hidden_columns):
    grid = {}
    class Cell:
        def __init__(self, row, column):
            self.position = row, column
        @property
        def Value2(self):
            return grid.get(self.position)
        @Value2.setter
        def Value2(self, value):
            grid[self.position] = value[1:] if isinstance(value, str) and value.startswith("'") else value
    class Destination:
        Row, Column = 2, 3
        EntireRow = SimpleNamespace(Hidden=hidden_rows)
        EntireColumn = SimpleNamespace(Hidden=hidden_columns)
        Worksheet = SimpleNamespace(Cells=lambda row, column: Cell(row, column))
        @property
        def Value2(self):
            return tuple(tuple(grid.get((row, column)) for column in range(3, 6)) for row in range(2, 4))
        @Value2.setter
        def Value2(self, value):
            pytest.fail("O Excel pode deslocar matrizes em destinos ocultos; a atribuição deve usar células físicas.")
    destination = Destination()
    monkeypatch.setattr("onmyoji_daemon.mcp.excel.service.operations.resize_range", lambda *arguments: destination)
    values = [[False, "=literal", None], [3, "00123", 4.5]]
    write_values(Mock(CountLarge=1), values, SimpleNamespace(defaults={"max_cells": 10}))
    assert destination.Value2 == ((False, "=literal", None), (3, "00123", 4.5))
    assert destination.EntireRow.Hidden is hidden_rows and destination.EntireColumn.Hidden is hidden_columns
    assert set(grid) == {(row, column) for row in range(2, 4) for column in range(3, 6)}


@pytest.mark.parametrize("separator,values,expected", [(";", ["Sim", "Nao"], "Sim;Nao"), (";", ["A,B", "C"], "A,B;C"), (",", ["A;B", "C"], "A;B,C")])
def test_typed_validation_list_uses_regional_separator_without_rewriting_items(monkeypatch, separator, values, expected):
    regional = Mock(return_value=separator)
    monkeypatch.setattr("onmyoji_daemon.mcp.excel.service.operations.parameterized_property", regional)
    session = SimpleNamespace(application=object())
    assert validation_formula(session, {"validation_type": "list", "list_values": values}, SimpleNamespace(profile={})) == expected
    regional.assert_called_once_with(session.application, "International", 5)


@pytest.mark.parametrize("operation,code", [
    ({"validation_type": "custom", "list_values": ["A"]}, "invalid_validation_list"),
    ({"validation_type": "list", "list_values": ["A"], "formula1": "A"}, "invalid_validation_list"),
    ({"validation_type": "list", "list_values": ["A;B"]}, "validation_list_unrepresentable"),
    ({"validation_type": "list", "list_values": ["A" * 128, "B" * 128]}, "validation_formula_too_long"),
])
def test_validation_lists_reject_unrepresentable_content_with_stable_codes(monkeypatch, operation, code):
    monkeypatch.setattr("onmyoji_daemon.mcp.excel.service.operations.parameterized_property", lambda *arguments: ";")
    with pytest.raises(ExcelExceptions) as failure:
        validation_formula(SimpleNamespace(application=object()), operation, SimpleNamespace(profile={}))
    assert failure.value.code == code


def test_rule_formula_uses_excel_parser_and_always_clears_private_cell():
    cell = Mock(Formula2Local='=COUNTIF(A1:A3;"x,y")>0')
    session = Mock()
    session.bootstrap.Worksheets.Item.return_value.Range.return_value = cell
    formula = '=COUNTIF(A1:A3,"x,y")>0'
    assert rule_formula(session, formula, SimpleNamespace(profile={})) == '=COUNTIF(A1:A3;"x,y")>0'
    assert cell.Formula2 == formula
    cell.ClearContents.assert_called_once_with()


def test_rule_formula_clears_private_cell_when_excel_parser_fails():
    from unittest.mock import PropertyMock
    cell = Mock()
    type(cell).Formula2 = PropertyMock(side_effect=RuntimeError("Parser unavailable"))
    session = Mock()
    session.bootstrap.Worksheets.Item.return_value.Range.return_value = cell
    with pytest.raises(RuntimeError, match="Parser unavailable"):
        rule_formula(session, "=AND(A1>0,A1<10)", SimpleNamespace(profile={}))
    cell.ClearContents.assert_called_once_with()
