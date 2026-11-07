"""Contratos nativos isoláveis, sem exigir instalação do Excel."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import zipfile

import pytest

from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.native import _canonical_date_value, _capture_vba_semantics, _model_inventory, _object, _patch_calculation, _pivot_source_range, _refresh, _refresh_pivots, _resolve_object, _same_native_objects, _verify_final_pivot_sources, _verify_vba_semantics, portable
from onmyoji_daemon.mcp.excel.service.operations import assert_range_owner, check_calculation_sources, check_formula, matrix, require_pivot_or_slicer_policy, write_values


@pytest.mark.parametrize("value,expected", [(None, None), (True, True), (12, 12), (1.5, 1.5), ("ação", "ação"),
                                           (datetime(2026, 11, 7), "2026-11-07T00:00:00"), (((1, 2), (3, None)), [[1, 2], [3, None]])])
def test_portable_supported_values(value, expected):
    assert portable(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), object()])
def test_portable_rejects_nonportable_values(value):
    with pytest.raises(ExcelExceptions):
        portable(value)


@pytest.mark.parametrize("values", [None, [], [[]], [[1], [2, 3]], [[1, 2, 3]]])
def test_matrix_rejects_null_empty_ragged_and_limit(values):
    with pytest.raises(ExcelExceptions):
        matrix(values, SimpleNamespace(defaults={"max_cells": 2}))


def test_matrix_keeps_declared_strings_literal_and_nulls():
    context = SimpleNamespace(defaults={"max_cells": 20})
    assert matrix([["=1+1", "00123", "'texto", "", None, False, 1]], context) == (("'=1+1", "'00123", "''texto", "'", None, False, 1),)


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf"), 5e-324, 9007199254740993, "a" * 32768, 10 ** 1000],
                         ids=["object", "nan", "infinity", "subnormal", "large_integer", "long_text", "overflow"])
def test_matrix_refuses_values_excel_cannot_represent_without_loss(value):
    with pytest.raises(ExcelExceptions):
        matrix([[value]], SimpleNamespace(defaults={"max_cells": 1}))


def test_write_values_rejects_shape_mismatch_and_resizes_single_cell(monkeypatch):
    context = SimpleNamespace(defaults={"max_cells": 20})
    resize = Mock()
    resize.return_value.EntireRow.Hidden = resize.return_value.EntireColumn.Hidden = False
    monkeypatch.setattr("onmyoji_daemon.mcp.excel.service.operations.resize_range", resize)
    target = Mock(CountLarge=1)
    write_values(target, [[1, 2], [3, 4]], context)
    resize.assert_called_once_with(target, 2, 2)
    assert resize.return_value.Value2 == ((1, 2), (3, 4))
    target = Mock(CountLarge=4)
    target.Rows.Count = target.Columns.Count = 2
    with pytest.raises(ExcelExceptions, match="dimensões"):
        write_values(target, [[1, 2, 3]], context)


@pytest.mark.parametrize("formula", [None, "SUM(A1:A2)", '=WEBSERVICE("https://example.com")', '=RTD("server",,"x")', "='[externo.xlsx]Dados'!A1"])
def test_formula_contract_blocks_missing_equals_and_external_sources(formula):
    with pytest.raises(ExcelExceptions):
        check_formula(formula, SimpleNamespace(profile={}))


def test_formula_allows_local_structured_references_and_authorized_sources():
    check_formula("=SUM(Vendas[Valor])", SimpleNamespace(profile={}))
    check_formula('="WEBSERVICE("', SimpleNamespace(profile={}))
    check_formula('="[book.xlsx]"', SimpleNamespace(profile={}))
    check_formula('=WEBSERVICE("https://example.com")', SimpleNamespace(profile={"allow_refresh": True}))
    with pytest.raises(ExcelExceptions):
        check_formula('=INDIRECT("\'[book.xlsx]Dados\'!A1")', SimpleNamespace(profile={}))


@pytest.mark.parametrize("formula", ['=WEBSERVICE("https://example.invalid")', '=RTD("server",,"topic")', '=PY("1+1")'])
def test_existing_external_formula_is_blocked_before_calculation(formula):
    workbook = Mock()
    workbook.Worksheets.Count = 1
    used = workbook.Worksheets.Item.return_value.UsedRange
    used.HasFormula = True
    used.SpecialCells.return_value.CountLarge = 1
    used.SpecialCells.return_value.Areas.Count = 1
    used.SpecialCells.return_value.Areas.Item.return_value.Formula2 = formula
    used.SpecialCells.return_value.Areas.Item.return_value.Row = 1
    used.SpecialCells.return_value.Areas.Item.return_value.Column = 1
    with pytest.raises(ExcelExceptions) as caught:
        check_calculation_sources(workbook, SimpleNamespace(profile={}, defaults={"max_cells":100}))
    assert caught.value.code == "external_data_denied"
    workbook.Application.Calculate.assert_not_called()


def test_calculation_checks_external_defined_names_and_workbook_links():
    workbook = Mock()
    workbook.Worksheets.Count = 0
    workbook.Names.Count = 1
    workbook.Names.Item.return_value.RefersTo = '=WEBSERVICE("https://example.invalid")'
    with pytest.raises(ExcelExceptions):
        check_calculation_sources(workbook, SimpleNamespace(profile={}, defaults={}))
    workbook.Names.Count = 0
    workbook.LinkSources.return_value = ("C:/Work/externo.xlsx",)
    with pytest.raises(ExcelExceptions):
        check_calculation_sources(workbook, SimpleNamespace(profile={}, defaults={}))


def test_pivot_and_slicer_policy_distinguishes_local_model_from_external_olap():
    model = SimpleNamespace(WorkbookConnection=SimpleNamespace(Type=7), OLAP=True, SourceType=2)
    require_pivot_or_slicer_policy(model, SimpleNamespace(profile={}))
    external = SimpleNamespace(WorkbookConnection=SimpleNamespace(Type=1), OLAP=True, SourceType=2)
    with pytest.raises(ExcelExceptions) as caught:
        require_pivot_or_slicer_policy(external, SimpleNamespace(profile={}))
    assert caught.value.code == "external_data_denied"
    require_pivot_or_slicer_policy(external, SimpleNamespace(profile={"allow_refresh": True}))


@pytest.mark.parametrize("selector", [[{"collection": "Application", "name": "x"}], [{"collection": "Parent", "index": 1}],
                                       [{"collection": "Worksheets", "name": "Dados", "index": 1}], [{"collection": "Worksheets"}]])
def test_object_selector_rejects_escape_and_ambiguous_steps(selector):
    with pytest.raises(ExcelExceptions):
        _resolve_object(Mock(), selector)


def test_object_adapter_requires_permission_and_rejects_unlisted_member():
    with pytest.raises(ExcelExceptions, match="perfil"):
        _object(Mock(), "get_object", {"member": "Name"}, SimpleNamespace(profile={}))
    with pytest.raises(ExcelExceptions, match="membro"):
        _object(Mock(), "call_object", {"member": "Run"}, SimpleNamespace(profile={"allow_object_api": True}))


def test_calculation_patch_changes_only_workbook_part_and_preserves_extensions(tmp_path):
    path = tmp_path / "pasta.xlsx"
    document = b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:x15="urn:x15" xmlns:mc="urn:mc" mc:Ignorable="x15"><calcPr calcMode="manual" calcOnSave="0" calcId="1"/><extLst><ext uri="opaque"><x15:item/></ext></extLst></workbook>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", document)
        archive.writestr("xl/vbaProject.bin", b"opaque binary")
    _patch_calculation(path, {"calc_properties": {"calcMode": "auto", "calcOnSave": "1"}}, {"output_mode": "preserve", "calculate_on_open": False})
    with zipfile.ZipFile(path) as archive:
        changed = archive.read("xl/workbook.xml")
        assert b'calcMode="auto"' in changed
        assert b'calcOnSave="1"' in changed
        assert b'fullCalcOnLoad="0"' in changed
        assert b'mc:Ignorable="x15"' in changed
        assert b"<x15:item/>" in changed
        assert archive.read("xl/vbaProject.bin") == b"opaque binary"


def test_calculation_patch_refuses_unverified_binary_preserve(tmp_path):
    with pytest.raises(ExcelExceptions) as caught:
        _patch_calculation(tmp_path / "pasta.xlsb", {}, {})
    assert caught.value.code == "calculation_mode_unverified"


def test_native_object_comparison_tolerates_only_known_geometry_rounding():
    assert _same_native_objects({"shapes": [{"Left":400,"Top":80,"Width":100,"Height":20}]},
                                {"shapes": [{"Left":399.75,"Top":80.25,"Width":100.375,"Height":19.625}]})
    assert not _same_native_objects({"Left":0}, {"Left":0.376})
    assert not _same_native_objects({"Value2":1}, {"Value2":1.001})
    assert not _same_native_objects({"Left":1}, {"Top":1})


def test_range_owner_rejects_named_range_in_another_sheet_workbook_or_instance():
    workbook = SimpleNamespace(FullName="C:/Work/pasta.xlsx", Application=SimpleNamespace(Hwnd=100))
    sheet = SimpleNamespace(Name="Dados", Parent=workbook)
    assert_range_owner(SimpleNamespace(Worksheet=sheet), workbook, sheet)
    others = [SimpleNamespace(Name="Outra", Parent=workbook),
              SimpleNamespace(Name="Dados", Parent=SimpleNamespace(FullName="C:/Work/outra.xlsx", Application=SimpleNamespace(Hwnd=100))),
              SimpleNamespace(Name="Dados", Parent=SimpleNamespace(FullName="C:/Work/pasta.xlsx", Application=SimpleNamespace(Hwnd=200)))]
    for other in others:
        with pytest.raises(ExcelExceptions) as caught:
            assert_range_owner(SimpleNamespace(Worksheet=other), workbook, sheet)
        assert caught.value.code == "range_context_mismatch"


@pytest.mark.parametrize("serial,date1904,expected", [(60,False,"1900-02-29"),(61,False,"1900-03-01"),
                                                       (60,True,"1904-03-01"),(61,True,"1904-03-02")])
def test_native_date_value_uses_excel_calendar_instead_of_ole_datetime(serial, date1904, expected):
    dispatch = SimpleNamespace(GetIDsOfNames=Mock(return_value=193), Invoke=Mock(return_value="yyyy-mm-dd"))
    cell = SimpleNamespace(_oleobj_=dispatch, Worksheet=SimpleNamespace(Parent=SimpleNamespace(Date1904=date1904)))
    assert _canonical_date_value(cell, serial) == expected


def test_vba_proof_requires_all_three_phases_and_keeps_hashes_private():
    before = {"status":"completed","modules_sha256":"a"*64,"references_sha256":"b"*64,"design_sha256":"c"*64,"module_count":3,"reference_count":4}
    proof = _verify_vba_semantics(before, dict(before), dict(before), "d"*64, "e"*64)
    assert proof["status"] == "completed"
    assert proof["modules_verified"] and proof["references_verified"] and proof["design_verified"]
    assert proof["source_snapshot_id"] == "d"*64 and proof["output_snapshot_id"] == "e"*64
    assert "modules_sha256" not in proof and "references_sha256" not in proof and "design_sha256" not in proof
    changed = {**before,"references_sha256":"f"*64}
    assert _verify_vba_semantics(before, changed, dict(before), "d"*64, "e"*64)["status"] == "failed"
    assert _verify_vba_semantics(before, dict(before), None, "d"*64, "e"*64)["status"] == "unverified"
    assert _verify_vba_semantics(before, {"status":"unverified"}, dict(before), "d"*64, "e"*64)["status"] == "unverified"


def test_vba_userform_proof_includes_designer_frx_and_reference_metadata(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    form_resource = [b"designer-resource-A"]
    def export(destination):
        path = __import__("pathlib").Path(destination)
        path.write_bytes(b'VERSION 5.00\r\nOleObjectBlob = "component-0000.frx":0000\r\nAttribute VB_Name = "Form1"\r\n')
        path.with_suffix(".frx").write_bytes(form_resource[0])
    component = SimpleNamespace(Name="Form1",Type=3,CodeModule=SimpleNamespace(CountOfLines=2,Lines=lambda *args:"Private Sub Teste()\r\nEnd Sub"),Export=export)
    reference = SimpleNamespace(GUID="{00020813-0000-0000-C000-000000000046}",Major=1,Minor=9,FullPath=str(tmp_path / "EXCEL.EXE"),IsBroken=False,Name="Excel")
    project = SimpleNamespace(VBComponents=SimpleNamespace(Count=1,Item=lambda index:component),References=SimpleNamespace(Count=1,Item=lambda index:reference))
    monkeypatch.setattr(native,"_project",lambda *args:project)
    directories = iter([tmp_path / "before",tmp_path / "after",tmp_path / "changed"])
    context = SimpleNamespace(profile={"allow_vba_source":True},defaults={},temporary_path=lambda:next(directories))
    workbook = SimpleNamespace(HasVBProject=True)
    before = _capture_vba_semantics(workbook,context)
    after = _capture_vba_semantics(workbook,context)
    assert before["status"] == after["status"] == "completed"
    assert _verify_vba_semantics(before,after,after,"a"*64,"b"*64)["status"] == "completed"
    form_resource[0] = b"designer-resource-B"
    changed = _capture_vba_semantics(workbook,context)
    proof = _verify_vba_semantics(before,changed,changed,"a"*64,"b"*64)
    assert proof["status"] == "failed" and proof["design_verified"] is False
    assert proof["modules_verified"] is True and proof["references_verified"] is True


def test_vba_snapshot_unavailable_does_not_claim_source_loss(monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    workbook = SimpleNamespace(HasVBProject=True)
    assert _capture_vba_semantics(workbook,SimpleNamespace(profile={})) == {"status":"unverified","reason":"vba_source_policy_disabled"}
    monkeypatch.setattr(native,"_project",Mock(side_effect=ExcelExceptions("vba_access_denied","private path")))
    result = _capture_vba_semantics(workbook,SimpleNamespace(profile={"allow_vba_source":True}))
    assert result == {"status":"unverified","reason":"vba_semantics_not_accessible"}
    assert "private" not in str(result)


def test_protected_formula_snapshot_keeps_physical_cells_and_literal_text():
    from onmyoji_daemon.mcp.excel.service.native import _path_sensitive, _value_snapshot
    from onmyoji_daemon.mcp.excel.service.operations import formula_records
    used = SimpleNamespace(HasFormula=None, CountLarge=3, Row=4, Column=2,
                           Formula2=(("=1+1", '=WEBSERVICE("literal")', '=CELL("filename")'),),
                           Value2=((2, '=WEBSERVICE("literal")', ""),), SpecialCells=Mock(side_effect=RuntimeError("protected")))
    sheet = SimpleNamespace(Name="Protegida", ProtectContents=True, UsedRange=used,
                            Cells=lambda row,column:SimpleNamespace(HasFormula=column != 3))
    workbook = SimpleNamespace(Worksheets=SimpleNamespace(Count=1,Item=lambda index:sheet),Names=SimpleNamespace(Count=0))
    context = SimpleNamespace(defaults={"max_cells":3},profile={})
    assert list(formula_records(sheet,context)) == [(4,2,"=1+1"),(4,4,'=CELL("filename")')]
    assert _value_snapshot(workbook,context) == {"Protegida":{"B4":{"value":2,"formula":"=1+1"},
        "C4":{"value":'=WEBSERVICE("literal")',"formula":None},"D4":{"value":"","formula":'=CELL("filename")'}}}
    assert _path_sensitive(workbook,context) is True
    workbook.LinkSources = lambda kind:None
    check_calculation_sources(workbook,context)
    used.Formula2 = (('=WEBSERVICE("blocked")',"literal",""),)
    with pytest.raises(ExcelExceptions) as caught:
        check_calculation_sources(workbook,context)
    assert caught.value.code == "external_data_denied"
    context.defaults["max_cells"] = 2
    with pytest.raises(ExcelExceptions) as caught:
        list(formula_records(sheet,context))
    assert caught.value.code == "cell_limit_exceeded"
    used.SpecialCells.assert_not_called()


def test_protected_error_audit_distinguishes_hresult_number_and_validation_is_unverified(monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    code = -2146826281
    cells = {1:SimpleNamespace(Value2=code,Formula2="=1/0",Text="#DIV/0!"),
             2:SimpleNamespace(Value2=code,Formula2="=-2146826281",Text=str(code))}
    for column,cell in cells.items():
        cell.column = column
        cell.Application = SimpleNamespace(WorksheetFunction=SimpleNamespace(IsError=lambda target:target.column == 1))
    used = SimpleNamespace(HasFormula=True,CountLarge=2,Row=1,Column=1,Formula2=(("=1/0","=-2146826281"),))
    for cell in cells.values():
        cell.HasFormula = True
    class PhysicalCells:
        def __call__(self,row,column):
            return cells[column]
        def SpecialCells(self,*args):
            raise RuntimeError("protected")
    sheet = SimpleNamespace(Name="Protegida",ProtectContents=True,UsedRange=used,Cells=PhysicalCells())
    workbook = SimpleNamespace(Worksheets=SimpleNamespace(Count=1,Item=lambda index:sheet))
    monkeypatch.setattr(native,"range_address",lambda cell:"A1" if cell.column == 1 else "B1")
    errors = native._native_errors(workbook,SimpleNamespace(defaults={}))
    assert len(errors) == 1 and errors[0]["error"] == "#DIV/0!"
    validation = native._validate_native(workbook,{"checks":["data"]},None,SimpleNamespace(defaults={}))
    assert validation["status"] == "unverified" and validation["valid"] is None
    assert validation["unverified"] == [{"code":"data_validations_not_accessible","sheet":"Protegida",
        "message":"O Excel impede SpecialCells na aba protegida; as validações não foram certificadas."}]


def test_model_inventory_does_not_initialize_model_on_empty_workbook():
    workbook = Mock()
    workbook.Connections.Count = 0
    assert _model_inventory(workbook) is None
    workbook.Model.ModelTables.Item.assert_not_called()


def test_export_materializes_only_after_refresh_and_calculation(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"synthetic source")
    context = SimpleNamespace(profile={"allow_write":True,"allow_refresh":True}, defaults={},
                              input_path=Mock(return_value=source), resolve_password=Mock(return_value=None), progress=Mock())
    session = MagicMock()
    session.__enter__.return_value = session
    session._visible.is_set.return_value = False
    session.assert_hidden = Mock()
    session.application.Version = "16"
    session.application.Build = "test"
    session.open.return_value.Worksheets.Count = 0
    monkeypatch.setattr(native, "ExcelSession", Mock(return_value=session))
    monkeypatch.setattr(native, "preflight", Mock(return_value={"native_open_allowed":True,"has_signatures":False,"format":"xlsx"}))
    monkeypatch.setattr(native, "_copy_workbook", Mock(return_value=source))
    monkeypatch.setattr(native, "_native_errors", Mock(return_value=[]))
    monkeypatch.setattr(native, "_path_sensitive", Mock(return_value=False))
    monkeypatch.setattr(native, "check_calculation_sources", Mock())
    events = []
    monkeypatch.setattr(native, "_refresh", lambda *args: events.append("refresh") or {"connections":[]})
    monkeypatch.setattr(native, "_calculate", lambda *args: events.append("calculate") or {"status":"completed"})
    monkeypatch.setattr(native, "_export", lambda *args: events.append("export") or {"format":"csv"})
    result = native.execute_native("export", {"input_path":str(source),"output_path":str(tmp_path / "output.csv"),"format":"csv",
                                             "calculation":{"mode":"full_rebuild","refresh_connections":True}}, context)
    assert events == ["refresh","calculate","export"]
    assert result["freshness"] == "calculated"


def test_export_new_formula_error_prevents_materializing_artifact(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"synthetic source")
    context = SimpleNamespace(profile={"allow_write":True}, defaults={}, input_path=Mock(return_value=source),
                              resolve_password=Mock(return_value=None), progress=Mock())
    session = MagicMock()
    session.__enter__.return_value = session
    session._visible.is_set.return_value = False
    session.assert_hidden = Mock()
    session.open.return_value.Worksheets.Count = 0
    monkeypatch.setattr(native, "ExcelSession", Mock(return_value=session))
    monkeypatch.setattr(native, "preflight", Mock(return_value={"native_open_allowed":True,"has_signatures":False,"format":"xlsx"}))
    monkeypatch.setattr(native, "_copy_workbook", Mock(return_value=source))
    monkeypatch.setattr(native, "_native_errors", Mock(side_effect=[[],[{"sheet":"Dados","address":"A1","error":"#REF!"}]]))
    monkeypatch.setattr(native, "_calculate", Mock(return_value={"status":"not_requested"}))
    exporting = Mock()
    monkeypatch.setattr(native, "_export", exporting)
    with pytest.raises(ExcelExceptions) as caught:
        native.execute_native("export", {"input_path":str(source),"output_path":str(tmp_path / "output.csv"),"format":"csv"}, context)
    assert caught.value.code == "formula_validation_failed"
    exporting.assert_not_called()


def test_refresh_pipeline_calculates_sources_before_pivots_and_dependents_afterward(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    source = tmp_path / "source.xlsx"
    source.write_bytes(b"synthetic source")
    context = SimpleNamespace(profile={"allow_write":True,"allow_refresh":True}, defaults={},
                              input_path=Mock(return_value=source), resolve_password=Mock(return_value=None), progress=Mock())
    session = MagicMock()
    session.__enter__.return_value = session
    session._visible.is_set.return_value = False
    session.assert_hidden = Mock()
    monkeypatch.setattr(native, "ExcelSession", Mock(return_value=session))
    monkeypatch.setattr(native, "preflight", Mock(return_value={"native_open_allowed":True,"has_signatures":False,"format":"xlsx"}))
    monkeypatch.setattr(native, "_copy_workbook", Mock(return_value=source))
    monkeypatch.setattr(native, "_native_errors", Mock(return_value=[]))
    monkeypatch.setattr(native, "_path_sensitive", Mock(return_value=False))
    monkeypatch.setattr(native, "check_calculation_sources", Mock())
    monkeypatch.setattr(native, "_pivot_targets", Mock(return_value=[(Mock(),Mock())]))
    events = []
    refresh = Mock(side_effect=lambda *args: events.append("sources") or {"connections":[]})
    pivots = Mock(side_effect=lambda *args,**kwargs: events.append("pivots") or [{"status":"completed"}])
    monkeypatch.setattr(native, "_refresh", refresh)
    monkeypatch.setattr(native, "_refresh_pivots", pivots)
    monkeypatch.setattr(native, "_calculate", lambda *args: events.append("calculate") or {"status":"completed"})
    monkeypatch.setattr(native, "_export", lambda *args: events.append("export") or {"format":"csv"})
    result = native.execute_native("export", {"input_path":str(source),"output_path":str(tmp_path / "output.csv"),"format":"csv",
        "calculation":{"mode":"full_rebuild","refresh_connections":True,"refresh_require_verified":False}}, context)
    assert events == ["sources","calculate","pivots","calculate","export"]
    refresh.assert_called_once()
    assert refresh.call_args.args[1]["require_verified"] is False
    assert pivots.call_args.kwargs["sources_calculated"] is True
    assert result["calculation"]["before_pivots"]["status"] == "completed"


@pytest.mark.parametrize("with_properties", [True, False])
def test_calculation_patch_preserves_prefixed_workbook_namespace(tmp_path, with_properties):
    from xml.etree import ElementTree as ET
    path = tmp_path / "pasta.xlsx"
    document = '<s:workbook xmlns:s="http://purl.oclc.org/ooxml/spreadsheetml/main">' + ('<s:calcPr calcMode="manual"/>' if with_properties else '') + '</s:workbook>'
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", document)
    _patch_calculation(path, {}, {"output_mode": "automatic"})
    with zipfile.ZipFile(path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        properties = workbook.find('{http://purl.oclc.org/ooxml/spreadsheetml/main}calcPr')
        assert properties is not None and properties.get('calcMode') == 'auto'


def test_querytable_refresh_failure_does_not_become_success_with_old_data():
    query = Mock(Name="Consulta", Refreshing=False)
    query.WorkbookConnection.Name = "Fonte"
    query.Refresh.side_effect = RuntimeError("private connection information")
    table = Mock(Name="Tabela", QueryTable=query)
    sheet = Mock(Name="Dados")
    sheet.ListObjects.Count = 1
    sheet.ListObjects.Item.return_value = table
    sheet.QueryTables.Count = 1
    sheet.QueryTables.Item.return_value = query
    sheet.PivotTables.return_value.Count = 0
    workbook = Mock()
    workbook.Worksheets.Count = 1
    workbook.Worksheets.Item.return_value = sheet
    connection = Mock(Name="Fonte")
    workbook.Connections.Count = 1
    workbook.Connections.Item.return_value = connection
    with pytest.raises(ExcelExceptions) as caught:
        _refresh(workbook, {}, Mock(), SimpleNamespace(profile={"allow_refresh": True}))
    assert caught.value.code == "refresh_failed"
    assert "private" not in str(caught.value)
    query.Refresh.assert_called_once_with(BackgroundQuery=False)
    connection.Refresh.assert_not_called()
    workbook.RefreshAll.assert_not_called()


def test_connection_only_refresh_requires_explicit_acceptance_of_unverified_state():
    workbook = Mock()
    workbook.Worksheets.Count = 0
    connection = Mock(Name="Modelo")
    workbook.Connections.Count = 1
    workbook.Connections.Item.return_value = connection
    context = SimpleNamespace(profile={"allow_refresh": True})
    with pytest.raises(ExcelExceptions) as caught:
        _refresh(workbook, {}, Mock(), context)
    assert caught.value.code == "refresh_unverified"
    result = _refresh(workbook, {"require_verified": False}, Mock(), context)
    assert result["connections"] == [{"name": "Modelo", "status": "unverified"}]


def test_pivot_with_formula_source_requires_calculation_before_refresh(monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    cache = SimpleNamespace(SourceType=1, Index=1, Refresh=Mock())
    pivot = SimpleNamespace(Name="Resumo", PivotCache=lambda:cache, RefreshTable=Mock(return_value=True))
    source = SimpleNamespace(HasFormula=True, Address="$A$1:$B$2", Value2=(("Produto","Valor"),("A",20)))
    monkeypatch.setattr(native, "_pivot_targets", lambda workbook:[(SimpleNamespace(Name="Dados"),pivot)])
    monkeypatch.setattr(native, "_pivot_source_range", lambda *args:source)
    context = SimpleNamespace(profile={"allow_refresh":True}, defaults={})
    session = SimpleNamespace(assert_hidden=Mock())
    with pytest.raises(ExcelExceptions) as caught:
        _refresh_pivots(Mock(), {}, session, context)
    assert caught.value.code == "refresh_unverified"
    pending = _refresh_pivots(Mock(), {"require_verified":False}, session, context)
    assert pending[0]["status"] == "unverified" and pending[0]["attempted"] is False
    cache.Refresh.assert_not_called()
    pivot.RefreshTable.assert_not_called()
    completed = _refresh_pivots(Mock(), {}, session, context, sources_calculated=True)
    assert completed[0]["status"] == "completed"
    cache.Refresh.assert_called_once_with()
    pivot.RefreshTable.assert_called_once_with()


def test_final_calculation_changing_pivot_source_is_not_reported_as_fresh(monkeypatch):
    from onmyoji_daemon.mcp.excel.service import native
    source = SimpleNamespace(Address="$A$1:$B$2", Value2=(("Produto","Valor"),("A",60)))
    report = {"sheet":"Dados","name":"Resumo","status":"completed"}
    session = SimpleNamespace(pivot_source_evidence={1:{"cache":Mock(),"address":source.Address,
                                                     "values":(("Produto","Valor"),("A",20)),"reports":[report]}})
    monkeypatch.setattr(native, "_pivot_source_range", lambda *args:source)
    with pytest.raises(ExcelExceptions) as caught:
        _verify_final_pivot_sources(Mock(), {}, session, Mock())
    assert caught.value.code == "refresh_unverified"
    _verify_final_pivot_sources(Mock(), {"require_verified":False}, session, Mock())
    assert report["status"] == "unverified" and report["reason"] == "source_changed_during_final_calculation"


def test_pivot_r1c1_source_is_resolved_inside_its_own_workbook(tmp_path):
    workbook = SimpleNamespace(Name="book.xlsx", FullName=str(tmp_path / "book.xlsx"), Application=SimpleNamespace(Hwnd=100))
    sheet = SimpleNamespace(Name="Dados", Parent=workbook)
    target = SimpleNamespace(Worksheet=sheet, Areas=SimpleNamespace(Count=1), CountLarge=6)
    sheet.Range = Mock(return_value=target)
    workbook.Worksheets = SimpleNamespace(Item=Mock(return_value=sheet))
    cache = SimpleNamespace(SourceData="'[book.xlsx]Dados'!R1C1:R3C2")
    assert _pivot_source_range(workbook, cache, SimpleNamespace(defaults={})) is target
    sheet.Range.assert_called_once_with("A1:B3")
    cache.SourceData = "'[other.xlsx]Dados'!R1C1:R3C2"
    assert _pivot_source_range(workbook, cache, SimpleNamespace(defaults={})) is None
