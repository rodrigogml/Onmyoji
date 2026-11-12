"""Views salvas e contratos de apresentação sem seleção de interface."""
from __future__ import annotations

from types import SimpleNamespace
from xml.dom import minidom

import pytest

from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.advanced_operations import _view_document, apply_advanced_operation, export_advanced


SOURCE = b'''<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
 xmlns:x14ac="http://schemas.microsoft.com/office/spreadsheetml/2009/9/ac" mc:Ignorable="x14ac">
 <dimension ref="A1:C4"/><sheetData><row r="1" x14ac:dyDescent="0.25"><c r="A1"><f>SUM(B1:C1)</f><v>12</v></c></row></sheetData>
</worksheet>'''


def document(content):
    return minidom.parseString(content)


def test_view_adds_frozen_panes_without_changing_cells_or_namespace_contract():
    result = _view_document(SOURCE, [{"sheet": "Dados", "freeze_rows": 2, "freeze_columns": 1, "zoom": 85, "show_gridlines": False}])
    parsed = document(result)
    view = parsed.getElementsByTagName("sheetView")[0]
    pane = parsed.getElementsByTagName("pane")[0]
    assert view.getAttribute("zoomScale") == "85" and view.getAttribute("showGridLines") == "0"
    assert pane.getAttribute("topLeftCell") == "B3" and pane.getAttribute("activePane") == "bottomRight"
    assert parsed.getElementsByTagName("f")[0].firstChild.data == "SUM(B1:C1)"
    assert parsed.documentElement.getAttribute("mc:Ignorable") == "x14ac"
    assert parsed.getElementsByTagName("row")[0].getAttribute("x14ac:dyDescent") == "0.25"
    assert [node.localName for node in parsed.documentElement.childNodes if node.nodeType == node.ELEMENT_NODE] == ["dimension", "sheetViews", "sheetData"]


def test_view_modifies_axes_independently_and_can_unfreeze():
    first = _view_document(SOURCE, [{"freeze_rows": 3, "freeze_columns": 2}])
    second = _view_document(first, [{"freeze_rows": 0, "show_headers": False, "right_to_left": True, "show_zeros": False}])
    parsed = document(second)
    pane = parsed.getElementsByTagName("pane")[0]
    assert pane.getAttribute("activePane") == "topRight" and pane.getAttribute("topLeftCell") == "C1"
    view = parsed.getElementsByTagName("sheetView")[0]
    assert view.getAttribute("rightToLeft") == "1" and view.getAttribute("showRowColHeaders") == "0"
    third = _view_document(second, [{"freeze_columns": 0}])
    assert not document(third).getElementsByTagName("pane")


def test_view_retains_prefixed_xml_and_view_without_freeze_changes():
    source = b'<s:worksheet xmlns:s="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><s:sheetViews><s:sheetView workbookViewId="0"><s:pane ySplit="1" state="frozen"/></s:sheetView></s:sheetViews><s:sheetData/></s:worksheet>'
    parsed = document(_view_document(source, [{"zoom": 125}]))
    assert parsed.getElementsByTagName("s:sheetView")[0].getAttribute("zoomScale") == "125"
    assert parsed.getElementsByTagName("s:pane")[0].getAttribute("ySplit") == "1"


@pytest.mark.parametrize("operation", [{"zoom": 0}, {"zoom": 401}, {"freeze_rows": -1}, {"freeze_columns": 16384}, {"show_headers": "no"}])
def test_view_rejects_invalid_contract_values(operation):
    with pytest.raises(ExcelExceptions):
        _view_document(SOURCE, [operation])


def test_unknown_export_format_does_not_allocate_output():
    assert export_advanced(None, {"format": "pdf"}, None) is None


def test_view_is_deferred_instead_of_activating_a_window():
    class Workbook:
        class Worksheets:
            @staticmethod
            def Item(name):
                assert name == "Dados"
                return object()
    session = SimpleNamespace()
    assert apply_advanced_operation(Workbook(), {"type": "view", "sheet": "Dados", "freeze_rows": 1}, session, None)
    assert session.deferred_views == [{"type": "view", "sheet": "Dados", "freeze_rows": 1}]
