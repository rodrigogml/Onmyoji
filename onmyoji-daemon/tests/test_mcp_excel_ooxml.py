"""Contratos de leitura direta Excel com pacotes sintéticos, sem Excel instalado."""
from __future__ import annotations

import hashlib
from pathlib import Path
import struct
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from onmyoji_daemon.mcp.excel.component.ooxml import (
    CFB_SIGNATURE, SpreadsheetPackage, cell_coordinates, column_name, column_number,
    excel_date, formula_errors, inspect_package, path_sensitive_formulas, preflight,
    range_coordinates, translate_shared_formula,
)
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.inspection import execute_direct


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"


def workbook_parts(*, body=None, namespace=MAIN, date1904=False, kind="xlsx", extras=None):
    """Produz pacote pequeno com fórmulas, cache, tabela, estilos e validações."""
    main_type = {"xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                 "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                 "xlam": "application/vnd.ms-excel.addin.macroEnabled.main+xml",
                 "xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.main"}[kind]
    main_part = "xl/workbook.bin" if kind == "xlsb" else "xl/workbook.xml"
    if body is None:
        body = '''<cols><col min="8" max="8" hidden="1"/></cols><sheetData>
          <row r="1"><c r="A1" t="inlineStr"><is><t>Nome</t></is></c><c r="B1" t="inlineStr"><is><t>Valor</t></is></c></row>
          <row r="2"><c r="A2" t="s"><v>0</v></c><c r="B2"><v>2</v></c><c r="C2"><f>SUM(B2,3)</f><v>5</v></c>
          <c r="D2" t="e"><f>1/0</f><v>#DIV/0!</v></c><c r="E2" t="b"><v>1</v></c><c r="F2" s="1"><v>60</v></c>
          <c r="G2" t="str"><f>IF(B2=2,"",B2)</f><v/></c><c r="H2" t="inlineStr"><is><t>=texto</t></is></c></row>
          <row r="3" hidden="1"><c r="A3" t="inlineStr"><is><t>Outro</t></is></c><c r="B3"><v>8</v></c><c r="C3"><f>CELL("filename",A1)</f><v>1</v></c></row>
          <row r="10"><c r="J10" s="1"/></row></sheetData>
          <mergeCells count="1"><mergeCell ref="A5:B5"/></mergeCells>
          <autoFilter ref="A1:B3"><filterColumn colId="1"><filters><filter val="2"/></filters></filterColumn></autoFilter>
          <dataValidations count="2"><dataValidation type="whole" operator="between" allowBlank="1" sqref="B2:B3"><formula1>1</formula1><formula2>5</formula2></dataValidation>
          <dataValidation type="custom" sqref="A2"><formula1>LEN(A2)&gt;0</formula1></dataValidation></dataValidations>'''
    parts = {
        "[Content_Types].xml": f'''<Types xmlns="{CONTENT_TYPES}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/>
        <Override PartName="/{main_part}" ContentType="{main_type}"/>
        <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        <Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/></Types>''',
        "_rels/.rels": f'<Relationships xmlns="{PACKAGE_REL}"><Relationship Id="r1" Type="{REL}/officeDocument" Target="{main_part}"/></Relationships>',
        main_part: b"binary-workbook" if kind == "xlsb" else f'''<workbook xmlns="{namespace}" xmlns:r="{REL}"><workbookPr date1904="{1 if date1904 else 0}"/>
        <sheets><sheet name="Dados" sheetId="1" r:id="s1"/></sheets><definedNames><definedName name="Selecao">Dados!$A$1:$B$3</definedName><definedName name="Local" localSheetId="0">Dados!$B$2</definedName></definedNames><calcPr calcMode="manual" fullCalcOnLoad="0"/></workbook>''',
        f"xl/_rels/{Path(main_part).name}.rels": f'''<Relationships xmlns="{PACKAGE_REL}">
          <Relationship Id="s1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>
          <Relationship Id="styles" Type="{REL}/styles" Target="styles.xml"/>
          <Relationship Id="strings" Type="{REL}/sharedStrings" Target="sharedStrings.xml"/>
          <Relationship Id="external" Type="{REL}/externalLinkPath" Target="file:///C:/dados/fonte.xlsx" TargetMode="External"/></Relationships>''',
        "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{namespace}" xmlns:r="{REL}">{body}<tableParts count="1"><tablePart r:id="table1"/></tableParts></worksheet>',
        "xl/worksheets/_rels/sheet1.xml.rels": f'<Relationships xmlns="{PACKAGE_REL}"><Relationship Id="table1" Type="{REL}/table" Target="../tables/table1.xml"/></Relationships>',
        "xl/tables/table1.xml": f'<table xmlns="{namespace}" id="1" name="Vendas" displayName="Vendas" ref="A1:B3"><tableColumns count="2"><tableColumn id="1" name="Nome"/><tableColumn id="2" name="Valor"/></tableColumns></table>',
        "xl/sharedStrings.xml": f'<sst xmlns="{namespace}" count="1" uniqueCount="1"><si><r><t>Olá </t></r><r><t>Mundo</t></r><rPh sb="0" eb="1"><t>fonética</t></rPh></si></sst>',
        "xl/styles.xml": f'<styleSheet xmlns="{namespace}"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>',
    }
    parts.update(extras or {})
    return parts


def write_package(path, parts=None, **kwargs):
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in (parts or workbook_parts(**kwargs)).items():
            archive.writestr(name, content)
    return path


def context_for(path, defaults=None):
    def input_path(value):
        candidate = Path(value)
        if not candidate.is_file():
            raise ExcelExceptions("file_not_found", "Arquivo sintético não encontrado.")
        return candidate
    return SimpleNamespace(input_path=input_path, defaults=defaults or {}, profile={}, job_dir=path.parent)


def execute(path, operation, **arguments):
    return execute_direct(operation, {"input_path": str(path), **arguments}, context_for(path))


def test_inventory_and_content_detection_ignore_filename_extension(tmp_path):
    path = write_package(tmp_path / "arquivo.dat")
    original = path.read_bytes()
    result = inspect_package(path)
    assert result["format"] == "xlsx"
    assert result["calculation_mode"] == "manual"
    assert result["calc_properties"] == {"calcMode": "manual", "fullCalcOnLoad": "0"}
    assert result["snapshot_id"] == hashlib.sha256(original).hexdigest()
    assert result["sheets"][0]["data_range"] == "A1:H3"
    assert result["sheets"][0]["cell_count"] == 14
    assert result["features"]["tables"] == 1
    assert result["tables"][0]["displayName"] == "Vendas"
    assert result["defined_names"][1]["scope"] == "Dados"
    assert result["external_relationships"][0]["external"] is True
    assert result["path_sensitive_formulas"] == [{"sheet": "Dados", "address": "C3", "reason": "workbook_location"}]
    assert result["formula_errors"][0]["error"] == "#DIV/0!"
    assert formula_errors(path) == result["formula_errors"]
    assert path_sensitive_formulas(path) == result["path_sensitive_formulas"]
    assert len(result["package_manifest"]) == len(workbook_parts())
    assert path.read_bytes() == original


def test_read_distinguishes_formula_cache_blank_string_and_literal_text(tmp_path):
    path = write_package(tmp_path / "read.xlsx")
    result = execute(path, "read_range", sheet="Dados", range="A2:I2", limit=20)
    cells = {item["address"]: item for item in result["cells"]}
    assert cells["A2"]["value"] == "Olá Mundo"
    assert cells["B2"]["value"] == 2
    assert cells["C2"]["value"] is None and cells["C2"]["formula"] == "=SUM(B2,3)"
    assert cells["C2"]["cached_value"] == 5 and cells["C2"]["freshness"] == "saved_cache"
    assert cells["D2"]["error"] == "#DIV/0!"
    assert cells["E2"]["value"] is True
    assert cells["F2"]["value"] == 60 and cells["F2"]["date_value"] == "1900-02-29"
    assert cells["G2"]["cached_value"] == "" and cells["G2"]["cached_type"] == "string"
    assert cells["H2"]["value"] == "=texto" and cells["H2"]["formula"] is None
    assert cells["H2"]["hidden_column"] is True
    assert cells["I2"]["type"] == "blank" and cells["I2"]["value"] is None
    assert all(item["display_text"] is None for item in cells.values())


def test_pagination_fields_static_names_tables_and_snapshot_conflict(tmp_path):
    path = write_package(tmp_path / "pages.xlsx")
    first = execute(path, "read_range", sheet="dados", range="Selecao", fields=["value"], limit=2)
    assert first["total"] == 6 and first["next_offset"] == 2
    assert set(first["cells"][0]) == {"sheet", "address", "row", "column", "value"}
    second = execute(path, "read_range", sheet="Dados", range="Vendas", offset=2, limit=2, snapshot_id=first["snapshot_id"])
    assert [item["address"] for item in second["cells"]] == ["A2", "B2"]
    local = execute(path, "read_range", sheet="Dados", range="Local")
    assert local["cells"][0]["value"] == 2
    sparse = execute(path, "read_range", sheet="Dados", range="I1:J10", include_empty=False)
    assert sparse["total"] == 0 and sparse["cells"] == []
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "read_range", sheet="Dados", range="A1", snapshot_id="0" * 64)
    assert caught.value.code == "snapshot_changed"


def test_search_and_formula_analysis_are_static_and_paged(tmp_path):
    path = write_package(tmp_path / "search.xlsx")
    search = execute(path, "search", query="olá", scope="values")
    assert search["matches"][0]["address"] == "A2"
    assert execute(path, "search", query="olá", case_sensitive=True)["total"] == 0
    cached = execute(path, "search", query="^5$", regex=True, scope="values")
    assert cached["matches"][0]["field"] == "cached_value"
    assert execute(path, "search", query="SUM", scope="formulas", range="C2", sheet="Dados")["total"] == 1
    analysis = execute(path, "analyze_formulas", limit=1)
    assert analysis["summary"]["formulas"] == 4 and analysis["next_offset"] == 1
    assert analysis["formulas"][0]["observed_a1_references"] == ["B2"]
    assert analysis["formulas"][0]["dependencies_complete"] is False
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "search", query="[", regex=True)
    assert caught.value.code == "invalid_regex"


def test_navigation_and_inspect_include_cells(tmp_path):
    path = write_package(tmp_path / "navigation.xlsx")
    assert execute(path, "navigate")["items"][0]["name"] == "Dados"
    assert execute(path, "navigate", kind="tables")["items"][0]["name"] == "Vendas"
    assert execute(path, "navigate", kind="names", sheet="Dados")["total"] == 2
    assert execute(path, "navigate", kind="objects")["items"][0]["kind"] == "table"
    assert execute(path, "navigate", kind="regions")["items"][0]["merged_ranges"] == ["A5:B5"]
    assert len(execute(path, "inspect", include_cells=True)["sheets"][0]["cells"]) == 14
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "navigate", kind="queries")
    assert caught.value.code == "direct_read_unavailable"


def test_validates_existing_rules_and_explicit_data_contracts(tmp_path):
    path = write_package(tmp_path / "validation.xlsx")
    result = execute(path, "validate", checks=["data"], rules=[
        {"type": "required", "sheet": "Dados", "range": "I2"},
        {"type": "range", "sheet": "Dados", "range": "B2:B3", "minimum": 0, "maximum": 10},
        {"type": "type", "sheet": "Dados", "range": "B2:B3", "value_type": "integer"},
        {"type": "regex", "sheet": "Dados", "range": "A2", "pattern": "Olá.*"},
        {"type": "allowed_values", "sheet": "Dados", "range": "E2", "values": [True]},
    ])
    assert result["status"] == "failed"
    assert {item["code"] for item in result["issues"]} == {"data_validation_violation", "data_rule_violation"}
    assert any(item["address"] == "B3" for item in result["issues"])
    assert any(item["address"] == "I2" for item in result["issues"])
    assert result["unverified"][0]["code"] == "custom_validation_requires_calculation"
    formulas = execute(path, "validate", checks=["formulas"])
    assert formulas["issues"][0]["code"] == "saved_formula_error"
    assert any(item["code"] == "calculation_not_performed" for item in formulas["unverified"])
    structure = execute(path, "validate", checks=["structure"])
    assert structure["valid"] is True and structure["status"] == "passed"


def test_data_rule_type_distinctions_and_duplicates(tmp_path):
    body = '<sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1" t="b"><v>1</v></c><c r="C1"><v>1</v></c><c r="D1" t="str"><v>1</v></c><c r="E1"><f>SUM(A1)</f></c></row></sheetData>'
    path = write_package(tmp_path / "rules.xlsx", body=body)
    result = execute(path, "validate", checks=["data"], rules=[
        {"type": "unique", "sheet": "Dados", "range": "A1:D1"},
        {"type": "allowed_values", "sheet": "Dados", "range": "B1", "values": [1]},
        {"type": "type", "sheet": "Dados", "range": "D1", "value_type": "number"},
        {"type": "required", "sheet": "Dados", "range": "E1"},
    ])
    assert [(item["rule"], item["address"]) for item in result["issues"]] == [(0, "C1"), (1, "B1"), (2, "D1")]
    assert result["unverified"][0]["code"] == "formula_without_saved_result"
    assert execute(path, "validate", checks=["data"], rules=[])["valid"] is True


def test_compare_semantics_manifest_and_preservation(tmp_path):
    original = write_package(tmp_path / "before.xlsx")
    copy = tmp_path / "copy.xlsx"; copy.write_bytes(original.read_bytes())
    same = execute(original, "compare", other_path=str(copy))
    assert same["equal_in_scope"] is True and same["identical_bytes"] is True
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace("<v>2</v>", "<v>3</v>")
    changed = write_package(tmp_path / "after.xlsx", parts)
    differences = execute(original, "compare", other_path=str(changed), scope="cells", limit=1)
    assert differences["total"] == 1 and differences["differences"][0]["address"] == "B2"
    assert differences["package_changes"][0]["part"] == "xl/worksheets/sheet1.xml"
    checked = execute(changed, "validate", checks=["preservation"], other_path=str(original))
    assert checked["status"] == "unverified" and checked["unverified"][0]["code"] == "changed_parts_require_review"
    with pytest.raises(ExcelExceptions) as caught:
        execute(changed, "validate", checks=["preservation"])
    assert caught.value.code == "invalid_argument"


def test_strict_namespace_shared_formulas_and_date_system(tmp_path):
    strict = "http://purl.oclc.org/ooxml/spreadsheetml/main"
    body = '<sheetData><row r="1"><c r="A1"><f t="shared" si="0" ref="A1:A2">B1+$C$1</f><v>3</v></c><c r="B1" s="1"><v>0</v></c></row><row r="2"><c r="A2"><f t="shared" si="0"/><v>4</v></c></row></sheetData>'
    path = write_package(tmp_path / "strict.xlsx", namespace=strict, body=body, date1904=True)
    with SpreadsheetPackage(path) as package:
        cells = package.cells("Dados")
        assert cells["B1"]["date_value"] == "1904-01-01"
        assert package.date_system == "1904"
        assert cells["A2"]["shared_formula_master"] == "A1"
        assert cells["A2"]["formula"] == "=B2+$C$1"


def test_shared_formula_translation_preserves_literals_qualifiers_and_structured_refs():
    formula = '=SUM(A1,$B1,C$1,$D$1,A:C,$A:$B,1:3,$1:$2,"A1",\'A1\'!A1,[1]Q1!A1,Table1[A1],LOG10(A1))'
    assert translate_shared_formula(formula, "A1", "B2") == '=SUM(B2,$B2,D$1,$D$1,B:D,$A:$B,2:4,$1:$2,"A1",\'A1\'!B2,[1]Q1!B2,Table1[A1],LOG10(B2))'
    assert translate_shared_formula('=A1', 'B2', 'A1') == '=#REF!'
    assert translate_shared_formula('=ZZZ1+A1048577+A1', 'A1', 'B2') == '=ZZZ1+A1048577+B2'


@pytest.mark.parametrize("kind,extras,blocked", [
    ("xlsm", {"xl/vbaProject.bin": b"vba"}, []),
    ("xlsm", {"xl/macrosheets/sheet2.xml": f'<macrosheet xmlns="{MAIN}"/>'}, ["xlm_macros"]),
    ("xlam", {}, ["addin_loading_disabled"]),
    ("xlsb", {}, ["active_content_unverified"]),
])
def test_preflight_vba_xlm_addin_and_binary_policies(tmp_path, kind, extras, blocked):
    path = write_package(tmp_path / ("test." + kind), kind=kind, extras=extras)
    report = preflight(path)
    assert report["blocked_reasons"] == blocked
    assert report["native_open_allowed"] is (not blocked)
    if "xl/vbaProject.bin" in extras:
        assert report["has_vba"] is True
    if kind == "xlsb":
        assert preflight(path, {"allow_unverified_active_content": True})["native_open_allowed"] is True
        with pytest.raises(ExcelExceptions) as caught:
            execute(path, "read_range", sheet="Dados", range="A1")
        assert caught.value.code == "direct_read_unavailable"
    if kind == "xlam":
        assert preflight(path, {"allow_addins": True})["native_open_allowed"] is True


def test_signatures_missing_relationships_and_hashes_are_reported(tmp_path):
    parts = workbook_parts(extras={"_xmlsignatures/sig1.xml": b"<Signature/>"})
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = parts["xl/worksheets/_rels/sheet1.xml.rels"].replace("../tables/table1.xml", "../tables/missing.xml")
    path = write_package(tmp_path / "invalid.xlsx", parts)
    # The broken table relationship is a structural error; reading its missing
    # required metadata also produces a stable package error, never Excel repair.
    with pytest.raises(ExcelExceptions) as caught:
        preflight(path)
    assert caught.value.code == "invalid_package"
    parts = workbook_parts(extras={"_xmlsignatures/sig1.xml": b"<Signature/>"})
    path = write_package(tmp_path / "signed.xlsx", parts)
    report = inspect_package(path)
    assert report["has_signatures"] is True and report["signatures"] == ["_xmlsignatures/sig1.xml"]
    signature = next(item for item in report["package_manifest"] if item["name"] == "_xmlsignatures/sig1.xml")
    assert signature["sha256"] == hashlib.sha256(b"<Signature/>").hexdigest()


def test_xlm_in_defined_names_is_blocked_without_a_macrosheet(tmp_path):
    parts = workbook_parts()
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace('</definedNames>', '<definedName name="Legacy">EVALUATE(Dados!A1)</definedName></definedNames>')
    path = write_package(tmp_path / "named-xlm.xlsx", parts)
    report = preflight(path)
    assert report["has_xlm"] is True and report["native_open_allowed"] is False
    assert report["xlm_expressions"] == [{"scope": "workbook", "name": "Legacy"}]
    from onmyoji_daemon.mcp.excel.component.ooxml import contains_xlm_formula
    assert contains_xlm_formula('=CALL("dll","function","J")')
    assert contains_xlm_formula('=_xlfn.REGISTER.ID("x")')
    assert not contains_xlm_formula('="CALL("')
    assert not contains_xlm_formula("='CALL(backup)'!A1")


@pytest.mark.parametrize("mutation,code", [
    (lambda parts: parts.update({"../escape.xml": "<x/>"}), "invalid_package"),
    (lambda parts: parts.update({"/absolute.xml": "<x/>"}), "invalid_package"),
    (lambda parts: parts.update({"XL/WORKBOOK.XML": "<x/>"}), "invalid_package"),
    (lambda parts: parts.update({"xl/styles.xml": '<!DOCTYPE root [<!ENTITY a "danger">]><x>&a;</x>'}), "unsafe_xml"),
    (lambda parts: parts.update({"xl/styles.xml": "<notclosed>"}), "invalid_package"),
    (lambda parts: parts.update({"xl/styles.xml": "<x>".encode("utf-16") + "<!DOCTYPE r>".encode("utf-16")}), "unsafe_xml"),
])
def test_rejects_unsafe_package_names_and_xml(tmp_path, mutation, code):
    parts = workbook_parts(); mutation(parts)
    path = write_package(tmp_path / "malformed.xlsx", parts)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code == code


def test_rejects_backslash_name_even_when_zip_writer_normalizes_windows_paths(tmp_path):
    path = write_package(tmp_path / "backslash.xlsx")
    info = ZipInfo("placeholder.xml")
    info.filename = "xl\\windows.xml"
    with ZipFile(path, "a") as archive:
        archive.writestr(info, "<x/>")
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code == "invalid_package"


@pytest.mark.parametrize("target", ["../../../outside.xml", "%2e%2e/%2e%2e/%2e%2e/outside.xml", "file:///C:/outside.xml", "\\server\\x.xml"])
def test_relationship_traversal_is_rejected_without_accessing_external_paths(tmp_path, target):
    parts = workbook_parts()
    parts["xl/worksheets/_rels/sheet1.xml.rels"] = f'<Relationships xmlns="{PACKAGE_REL}"><Relationship Id="x" Type="{REL}/table" Target="{target}"/></Relationships>'
    path = write_package(tmp_path / "traversal.xlsx", parts)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code == "invalid_relationship"


@pytest.mark.parametrize("limits", [{"max_package_entries": 1}, {"max_uncompressed_bytes": 20}, {"max_xml_bytes": 10}, {"max_cells": 2}, {"max_compression_ratio": 1}, {"max_package_bytes": 20}, {"max_zip_entries": 1}])
def test_package_limits_are_enforced(tmp_path, limits):
    parts = workbook_parts()
    if "max_compression_ratio" in limits:
        parts["large.bin"] = b"A" * 200000
    path = write_package(tmp_path / "large.xlsx", parts)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path, limits)
    assert caught.value.code == "package_limit"


@pytest.mark.parametrize("reference", ["A0", "A1048577", "XFE1", "A1:B0", "B2:A1", "A1,B2", "Dados!A1", "", None])
def test_rejects_invalid_a1_references(reference):
    with pytest.raises(ExcelExceptions) as caught:
        range_coordinates(reference)
    assert caught.value.code == "invalid_range"


def test_date_serials_keep_excel_semantics():
    assert excel_date(1, "1900", "mm-dd-yy") == "1900-01-01"
    assert excel_date(61, "1900", "mm-dd-yy") == "1900-03-01"
    assert excel_date(60.5, "1900", "mm-dd-yy h:mm") == "1900-02-29T12:00:00"
    assert excel_date(0.5, "1900", "h:mm") == "12:00:00"
    assert excel_date(1.5, "1900", "[h]:mm:ss") == "PT129600S"
    assert excel_date(1, "1900", '"data"0.00') is None
    assert excel_date(True, "1900", "mm-dd-yy") is None
    assert excel_date(10 ** 15, "1900", "mm-dd-yy") is None
    assert column_number("XFD") == 16384 and column_name(16384) == "XFD"
    assert cell_coordinates("$AA$10") == (10, 27)


def compound_file(path, stream_names):
    """Monta apenas diretório CFB suficiente para inspeção de streams, sem BIFF."""
    header = bytearray(512)
    header[:8] = CFB_SIGNATURE
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<I", header, 44, 1)
    struct.pack_into("<I", header, 48, 1)
    struct.pack_into("<I", header, 68, 0xFFFFFFFE)
    struct.pack_into("<I", header, 72, 0)
    struct.pack_into("<109I", header, 76, 0, *([0xFFFFFFFF] * 108))
    fat = bytearray(b"\xff" * 512)
    struct.pack_into("<2I", fat, 0, 0xFFFFFFFD, 0xFFFFFFFE)
    directory = bytearray(512)
    for index, name in enumerate(stream_names):
        entry = index * 128
        encoded = name.encode("utf-16-le") + b"\x00\x00"
        directory[entry:entry + len(encoded)] = encoded
        struct.pack_into("<H", directory, entry + 64, len(encoded))
        directory[entry + 66] = 2
    path.write_bytes(header + fat + directory)
    return path


def test_compound_encryption_and_legacy_require_explicit_unverified_policy(tmp_path):
    encrypted = compound_file(tmp_path / "encrypted.xlsx", ["EncryptionInfo", "EncryptedPackage"])
    report = preflight(encrypted)
    assert report["format"] == "encrypted_ooxml" and report["encrypted"] is True
    assert report["active_content_verified"] is False and report["native_open_allowed"] is False
    legacy = compound_file(tmp_path / "legacy.xls", ["Workbook", "_VBA_PROJECT"])
    assert inspect_package(legacy)["has_vba"] is True
    assert preflight(legacy, {"allow_unverified_active_content": True})["native_open_allowed"] is True
    invalid = compound_file(tmp_path / "word.doc", ["WordDocument"])
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(invalid)
    assert caught.value.code == "unsupported_format"


def test_invalid_argument_and_unsupported_format_errors_are_stable(tmp_path):
    path = write_package(tmp_path / "stable.xlsx")
    for operation, arguments, code in [
        ("unsupported", {}, "operation_unavailable"),
        ("read_range", {"sheet": "Dados", "range": "A1", "offset": -1}, "invalid_pagination"),
        ("read_range", {"sheet": "Dados", "range": "A1", "fields": ["bad"]}, "invalid_argument"),
        ("read_range", {"sheet": "Missing", "range": "A1"}, "sheet_not_found"),
        ("read_range", {"sheet": "Dados", "range": "INDIRECT(\"A1\")"}, "direct_read_unavailable"),
        ("validate", {"checks": ["unknown"]}, "invalid_argument"),
        ("compare", {"other_path": str(path), "scope": "unknown"}, "invalid_argument"),
    ]:
        with pytest.raises(ExcelExceptions) as caught:
            execute(path, operation, **arguments)
        assert caught.value.code == code
    text = tmp_path / "text.xlsx"; text.write_text("not an excel file", encoding="utf-8")
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(text)
    assert caught.value.code == "unsupported_format"


@pytest.mark.parametrize('body', [
    '<cols><col min="bad" max="2" hidden="1"/></cols><sheetData/>',
    '<sheetData><row r="bad"/></sheetData>',
    '<sheetData><row r="1"><c r="A1" s="bad"><v>1</v></c></row></sheetData>',
    '<sheetData><row r="1"><c r="A1" t="s"><v>bad</v></c></row></sheetData>',
    '<sheetData><row r="1"><c r="A1"><v>bad</v></c></row></sheetData>',
    '<sheetData><row r="1"><c r="A1"><v>NaN</v></c></row></sheetData>',
    '<sheetData><row r="1"><c r="A1" t="unknown"><v>1</v></c></row></sheetData>',
    '<sheetData><row r="1"><c r="A1" t="b"><v>2</v></c></row></sheetData>',
])
def test_malformed_cells_and_metadata_are_rejected_before_reading(tmp_path, body):
    path = write_package(tmp_path / 'invalid.xlsx', body=body)
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, 'inspect')
    assert caught.value.code == 'invalid_package'


@pytest.mark.parametrize('styles', [
    '<numFmts><numFmt numFmtId="bad" formatCode="0"/></numFmts><cellXfs><xf numFmtId="0"/></cellXfs>',
    '<cellXfs><xf numFmtId="bad"/></cellXfs>',
])
def test_invalid_custom_number_format_or_xf_fails_with_package_error(tmp_path, styles):
    values = workbook_parts()
    values['xl/styles.xml'] = f'<styleSheet xmlns="{MAIN}">{styles}</styleSheet>'
    path = write_package(tmp_path / 'badstyles.xlsx', parts=values)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code == 'invalid_package'


def test_custom_dates_iso_dates_empty_and_implicit_cells_keep_their_types(tmp_path):
    values = workbook_parts(body='<sheetData><ignore/><row r="1"><ignore/><c s="1"><v>1.5</v></c><c t="d"><v>2026-11-07T12:34:56</v></c><c t="inlineStr"/><c><v></v></c></row></sheetData>')
    values['xl/styles.xml'] = f'<styleSheet xmlns="{MAIN}"><numFmts><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm:ss"/></numFmts><cellXfs><xf numFmtId="0"/><xf numFmtId="164"/></cellXfs></styleSheet>'
    path = write_package(tmp_path / 'dates.xlsx', parts=values)
    cells = execute(path, 'read_range', sheet='Dados', range='A1:D1')['cells']
    assert cells[0]['date_value'] == '1900-01-01T12:00:00'
    assert cells[1]['type'] == 'date' and cells[1]['date_value'] == '2026-11-07T12:34:56'
    assert cells[2]['type'] == 'string' and cells[2]['value'] == ''
    assert cells[3]['type'] == 'blank' and cells[3]['value'] is None
    with SpreadsheetPackage(path) as package:
        assert package.sheet(None)['name'] == 'Dados'


def test_structure_diagnostics_prevent_native_open_and_allow_readonly_audit(tmp_path):
    values = workbook_parts()
    values['[Content_Types].xml'] = values['[Content_Types].xml'].replace('</Types>', '<Override PartName="/missing.xml" ContentType="application/xml"/></Types>')
    values['orphan/_rels/source.xml.rels'] = f'<Relationships xmlns="{PACKAGE_REL}"><ignored/><Relationship Id="missing" Type="{REL}/image" Target="missing.xml"/></Relationships>'
    path = write_package(tmp_path / 'structural.xlsx', parts=values)
    report = preflight(path)
    assert report['native_open_allowed'] is False and 'invalid_structure' in report['blocked_reasons']
    assert {issue['code'] for issue in report['structure_errors']} >= {'missing_content_part','orphan_relationship_part','missing_relationship_target'}


def test_defined_names_prefer_local_static_reference_and_diagnose_invalid_scope(tmp_path):
    values = workbook_parts()
    values['xl/workbook.xml'] = values['xl/workbook.xml'].replace('</definedNames>', '<definedName name="Selecao" localSheetId="0">\'Dados\'!$B$2</definedName><definedName name="Outra">Outra!$A$1</definedName></definedNames>')
    path = write_package(tmp_path / 'names.xlsx', parts=values)
    assert execute(path,'read_range',sheet='Dados',range='Selecao')['total'] == 1
    with pytest.raises(ExcelExceptions) as caught:
        execute(path,'read_range',sheet='Dados',range='Outra')
    assert caught.value.code == 'invalid_range'
    values['xl/workbook.xml'] = values['xl/workbook.xml'].replace('localSheetId="0"','localSheetId="bad"')
    path = write_package(tmp_path / 'scope.xlsx', parts=values)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code == 'invalid_package'


@pytest.mark.parametrize('kind,operator,first,second,value,violates', [
    ('whole','between','1','5','2',False),('whole','notBetween','1','5','2',True),
    ('decimal','equal','2',None,'2',False),('decimal','notEqual','2',None,'2',True),
    ('decimal','lessThan','2',None,'1',False),('decimal','lessThanOrEqual','2',None,'2',False),
    ('decimal','greaterThan','2',None,'3',False),('decimal','greaterThanOrEqual','2',None,'1',True),
    ('whole','equal','2.5',None,'2.5',True),('date','between','1','61','60',False),
    ('time','between','0','1','0.5',False),('textLength','equal','3',None,'123',False),
])
def test_builtin_validation_numeric_date_time_and_operator_contracts(tmp_path,kind,operator,first,second,value,violates):
    formula2 = f'<formula2>{second}</formula2>' if second is not None else ''
    body=f'<sheetData><row r="1"><c r="A1"><v>{value}</v></c></row></sheetData><dataValidations><dataValidation type="{kind}" operator="{operator}" sqref="A1"><formula1>{first}</formula1>{formula2}</dataValidation></dataValidations>'
    path=write_package(tmp_path/'validation.xlsx',body=body)
    result=execute(path,'validate',checks=['data'])
    assert result['status'] == ('failed' if violates else 'passed'), result


@pytest.mark.parametrize('validation,code', [
    ('<dataValidation type="whole"><formula1>1</formula1></dataValidation>','invalid_validation_reference'),
    ('<dataValidation type="list" sqref="A1"><formula1>INDIRECT("A1")</formula1></dataValidation>','list_source_not_resolved'),
    ('<dataValidation type="whole" sqref="A1"><formula1>INDIRECT("A1")</formula1></dataValidation>','validation_formula_not_evaluated'),
    ('<dataValidation type="whole" sqref="A1"><formula1>1</formula1></dataValidation>','validation_formula_not_evaluated'),
    ('<dataValidation type="whole" operator="unknown" sqref="A1"><formula1>1</formula1></dataValidation>','validation_operator_unknown'),
    ('<dataValidation type="future" operator="equal" sqref="A1"><formula1>1</formula1></dataValidation>','validation_type_unknown'),
])
def test_unresolved_or_malformed_validations_have_explicit_findings(tmp_path,validation,code):
    body=f'<sheetData><row r="1"><c r="A1"><v>2</v></c></row></sheetData><dataValidations>{validation}</dataValidations>'
    path=write_package(tmp_path/'unverified.xlsx',body=body)
    result=execute(path,'validate',checks=['data'])
    assert any(item['code']==code for item in result['issues']+result['unverified']), result


@pytest.mark.parametrize('source', ['"A,B"','A2:A3'])
def test_list_validation_resolves_literal_or_static_range(tmp_path,source):
    body=f'<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>B</t></is></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>A</t></is></c></row><row r="3"><c r="A3" t="inlineStr"><is><t>B</t></is></c></row></sheetData><dataValidations><dataValidation type="list" sqref="A1"><formula1>{source}</formula1></dataValidation></dataValidations>'
    path=write_package(tmp_path/'list.xlsx',body=body)
    assert execute(path,'validate',checks=['data'])['status']=='passed'


def test_static_formula_analysis_reports_dynamic_external_and_service_references(tmp_path):
    body='<sheetData><row r="1"><c r="A1"><f>INDIRECT("A2")+OFFSET(B1,0,0)+[Fonte.xlsx]A1+RTD("",,"x")+#REF!</f></c></row></sheetData>'
    path=write_package(tmp_path/'analysis.xlsx',body=body)
    item=execute(path,'analyze_formulas')['formulas'][0]
    assert set(item['unresolved'])=={'dynamic_reference','external_or_structured_reference','external_service_or_model'}
    assert item['broken_reference_literal'] is True
    validation=execute(path,'validate',checks=['data'],rules=[{'type':'required','sheet':'Dados','range':'A1'}])
    assert validation['status']=='unverified' and validation['unverified'][0]['code']=='formula_without_saved_result'


@pytest.mark.parametrize('rule,code', [
    ({'type':'type','value_type':'unknown'},'invalid_argument'),({'type':'range'},'invalid_argument'),
    ({'type':'regex','pattern':'['},'invalid_regex'),({'type':'regex'},'invalid_argument'),
    ({'type':'allowed_values'},'invalid_argument'),({'type':'unknown'},'invalid_argument'),
])
def test_invalid_data_rules_fail_with_stable_contract_errors(tmp_path,rule,code):
    path=write_package(tmp_path/'rules.xlsx')
    with pytest.raises(ExcelExceptions) as caught:
        execute(path,'validate',checks=['data'],rules=[{'sheet':'Dados','range':'A2',**rule}])
    assert caught.value.code==code


def test_structure_validation_detects_duplicate_names_tables_and_invalid_columns(tmp_path):
    values=workbook_parts()
    values['xl/workbook.xml']=values['xl/workbook.xml'].replace('</definedNames>','<definedName name="Selecao">Dados!A1</definedName></definedNames>')
    values['xl/tables/table1.xml']=values['xl/tables/table1.xml'].replace('ref="A1:B3"','ref="bad"').replace('name="Valor"','name="Nome"')
    values['xl/worksheets/_rels/sheet1.xml.rels']=values['xl/worksheets/_rels/sheet1.xml.rels'].replace('</Relationships>',f'<Relationship Id="table2" Type="{REL}/table" Target="../tables/table2.xml"/></Relationships>')
    values['xl/tables/table2.xml']=values['xl/tables/table1.xml']
    values['xl/worksheets/sheet1.xml']=values['xl/worksheets/sheet1.xml'].replace('</tableParts>','<tablePart r:id="table2"/></tableParts>')
    path=write_package(tmp_path/'structure.xlsx',parts=values)
    result=execute(path,'validate',checks=['structure'])
    assert {item['code'] for item in result['issues']} >= {'duplicate_defined_name','duplicate_table_name','invalid_table_range','invalid_table_columns'}


def test_referenced_validation_operands_blank_skipping_and_extended_rules_are_reported(tmp_path):
    body=f'''<sheetData><row r="1"><c r="A1"><v>3</v></c><c r="B1"><v>4</v></c></row><row r="2"><c r="A2"><v>2</v></c><c r="B2" t="inlineStr"><is><t>texto</t></is></c></row></sheetData>
    <dataValidations><dataValidation type="whole" operator="greaterThan" sqref="A1"><formula1>A2</formula1></dataValidation>
    <dataValidation type="whole" operator="greaterThan" sqref="B1"><formula1>B2</formula1></dataValidation>
    <dataValidation type="whole" operator="greaterThan" allowBlank="1" sqref="C1"><formula1>0</formula1></dataValidation></dataValidations>
    <extLst xmlns:x14="http://schemas.microsoft.com/office/spreadsheetml/2009/9/main"><x14:dataValidations><x14:dataValidation/></x14:dataValidations></extLst>'''
    path=write_package(tmp_path/'operands.xlsx',body=body)
    result=execute(path,'validate',checks=['data'],rules=[{'type':'unique','sheet':'Dados','range':'C1:C2'}])
    assert result['status']=='unverified' and not result['issues']
    assert {item['code'] for item in result['unverified']}=={'extended_validations_not_evaluated','validation_operand_type_unverified'}


def test_preservation_audit_reports_structural_changes_and_changed_parts(tmp_path):
    source=write_package(tmp_path/'before.xlsx')
    values=workbook_parts()
    values['xl/workbook.xml']=values['xl/workbook.xml'].replace('name="Selecao"','name="Renomeado"')
    output=write_package(tmp_path/'after.xlsx',parts=values)
    result=execute(output,'validate',checks=['preservation'],other_path=str(source))
    assert result['status']=='failed' and result['issues'][0]['code']=='structure_changed'
    assert result['unverified'][0]['code']=='changed_parts_require_review'
    with pytest.raises(ExcelExceptions) as caught:
        execute(source,'navigate',kind='invalid')
    assert caught.value.code=='invalid_argument'


def test_nonworksheet_parts_are_navigable_but_not_cell_readers(tmp_path):
    values=workbook_parts()
    values['xl/_rels/workbook.xml.rels']=values['xl/_rels/workbook.xml.rels'].replace(f'Type="{REL}/worksheet"',f'Type="{REL}/chartsheet"')
    path=write_package(tmp_path/'chart.xlsx',parts=values)
    assert execute(path,'search',query='anything')['total']==0
    assert execute(path,'validate',checks=['data'])['status']=='passed'
    assert execute(path,'compare',other_path=str(path))['equal_in_scope'] is True


@pytest.mark.parametrize('target', ['[Content_Types].xml','custom.bin'])
def test_corrupt_zip_payload_is_rejected_by_xml_or_manifest_integrity(tmp_path,target):
    values=workbook_parts(extras={'custom.bin':b'unique-payload-for-crc-check'})
    path=write_package(tmp_path/'crc.xlsx',parts=values)
    with ZipFile(path) as archive:
        entry=archive.getinfo(target)
    content=bytearray(path.read_bytes())
    name_length,extra_length=struct.unpack_from('<HH',content,entry.header_offset+26)
    start=entry.header_offset+30+name_length+extra_length
    content[start+entry.compress_size//2]^=0xFF
    path.write_bytes(content)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code=='invalid_package'


def test_truncated_zip_and_missing_optional_parts_have_explicit_behavior(tmp_path):
    invalid=tmp_path/'truncated.xlsx';invalid.write_bytes(b'PK\x03\x04truncated')
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(invalid)
    assert caught.value.code=='invalid_package'
    values=workbook_parts(body='')
    values['xl/_rels/workbook.xml.rels']=f'<Relationships xmlns="{PACKAGE_REL}"><Relationship Id="s1" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'
    values['xl/workbook.xml']=values['xl/workbook.xml'].replace('<definedNames>','<empty/><definedNames>')
    values['xl/styles.xml']=f'<styleSheet xmlns="{MAIN}"/>'
    values['xl/sharedStrings.xml']=f'<sst xmlns="{MAIN}"><ignored/></sst>'
    path=write_package(tmp_path/'optional.xlsx',parts=values)
    with SpreadsheetPackage(path) as package:
        assert package.cells('Dados')=={} and package.shared_strings()==[]
        assert package.styles()[0]['number_format']=='General'


def test_shared_formula_translation_preserves_escaped_literals_and_handles_bounds():
    assert translate_shared_formula('=SUM(ZZZ:ZZZ,XFD:XFD,1048576:1048576,"a""b",\'O\'\'Brien\'!A1)', 'A1','B2') == '=SUM(ZZZ:ZZZ,#REF!,#REF!,"a""b",\'O\'\'Brien\'!B2)'
    assert translate_shared_formula('="unterminated A1', 'A1','B2')=='="unterminated A1'


def test_compound_directory_can_use_difat_and_rejects_invalid_stream_encoding(tmp_path):
    path=compound_file(tmp_path/'difat.xls',['Workbook'])
    data=bytearray(path.read_bytes())
    struct.pack_into('<109I',data,76,*([0xFFFFFFFF]*109))
    struct.pack_into('<II',data,68,2,1)
    data.extend(struct.pack('<128I',0,*([0xFFFFFFFF]*126),0xFFFFFFFE))
    path.write_bytes(data)
    assert inspect_package(path)['format']=='xls'
    struct.pack_into('<I',data,72,2)
    struct.pack_into('<I',data,len(data)-4,2)
    path.write_bytes(data)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code=='invalid_package'
    path=compound_file(tmp_path/'badname.xls',['Workbook'])
    data=bytearray(path.read_bytes());data[1024:1028]=b'\x00\xd8\x00\x00';struct.pack_into('<H',data,1088,4)
    path.write_bytes(data)
    with pytest.raises(ExcelExceptions) as caught:
        inspect_package(path)
    assert caught.value.code=='invalid_package'


def test_internal_fragment_relationship_retains_its_source_part(tmp_path):
    values=workbook_parts()
    values['xl/worksheets/_rels/sheet1.xml.rels']=values['xl/worksheets/_rels/sheet1.xml.rels'].replace('</Relationships>',f'<Relationship Id="fragment" Type="{REL}/hyperlink" Target="#anchor"/></Relationships>')
    path=write_package(tmp_path/'fragment.xlsx',parts=values)
    with SpreadsheetPackage(path) as package:
        assert next(item['target'] for item in package.relationships['xl/worksheets/sheet1.xml'] if item['id']=='fragment')=='xl/worksheets/sheet1.xml'
