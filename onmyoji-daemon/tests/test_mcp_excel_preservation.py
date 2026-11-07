"""Preservação de conteúdo conhecido/opaco com envelope de alterações sintético."""
from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from onmyoji_daemon.mcp.excel.component.preservation import capture_preservation, validate_preservation


SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE = "http://schemas.openxmlformats.org/package/2006/relationships"
DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
MAIN_DRAWING = "http://schemas.openxmlformats.org/drawingml/2006/main"


def parts(*, vba=False):
    kind = "application/vnd.ms-excel.sheet.macroEnabled.main+xml" if vba else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
    values = {
        "[Content_Types].xml": f'''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
        <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
        <Default Extension="xml" ContentType="application/xml"/><Default Extension="bin" ContentType="application/octet-stream"/>
        <Default Extension="png" ContentType="image/png"/>
        <Override PartName="/xl/workbook.xml" ContentType="{kind}"/>
        <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
        <Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>
        <Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/></Types>''',
        "_rels/.rels": f'<Relationships xmlns="{PACKAGE}"><Relationship Id="book" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": f'''<workbook xmlns="{SHEET}" xmlns:r="{REL}"><sheets><sheet name="Dados" sheetId="1" r:id="sheet"/></sheets>
        <definedNames><definedName name="Valor">Dados!$A$1</definedName></definedNames><calcPr calcMode="manual"/></workbook>''',
        "xl/_rels/workbook.xml.rels": f'''<Relationships xmlns="{PACKAGE}"><Relationship Id="sheet" Type="{REL}/worksheet" Target="worksheets/sheet1.xml"/>
        <Relationship Id="styles" Type="{REL}/styles" Target="styles.xml"/></Relationships>''',
        "xl/worksheets/sheet1.xml": f'''<worksheet xmlns="{SHEET}" xmlns:r="{REL}"><sheetFormatPr defaultRowHeight="15"/>
        <sheetData><row r="1"><c r="A1"><v>1</v></c><c r="B1"><v>2</v></c></row><row r="2"><c r="A2"><v>3</v></c><c r="B2"><f>A1+A2</f><v>4</v></c></row></sheetData>
        <mergeCells count="1"><mergeCell ref="C1:D1"/></mergeCells>
        <dataValidations count="1"><dataValidation type="whole" operator="between" sqref="A1:A2"><formula1>1</formula1><formula2>10</formula2></dataValidation></dataValidations>
        <drawing r:id="drawing"/><tableParts count="1"><tablePart r:id="table"/></tableParts></worksheet>''',
        "xl/worksheets/_rels/sheet1.xml.rels": f'''<Relationships xmlns="{PACKAGE}"><Relationship Id="table" Type="{REL}/table" Target="../tables/table1.xml"/>
        <Relationship Id="drawing" Type="{REL}/drawing" Target="../drawings/drawing1.xml"/></Relationships>''',
        "xl/tables/table1.xml": f'<table xmlns="{SHEET}" id="1" name="Tabela" displayName="Tabela" ref="A1:B2"><tableColumns count="2"><tableColumn id="1" name="A"/><tableColumn id="2" name="B"/></tableColumns></table>',
        "xl/styles.xml": f'''<styleSheet xmlns="{SHEET}"><fonts count="1"><font><name val="Calibri"/><sz val="11"/></font></fonts>
        <fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders>
        <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
        <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
        <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>''',
        "xl/drawings/drawing1.xml": f'''<xdr:wsDr xmlns:xdr="{DRAWING}" xmlns:a="{MAIN_DRAWING}" xmlns:r="{REL}"><xdr:twoCellAnchor>
        <xdr:from><xdr:col>4</xdr:col><xdr:row>0</xdr:row></xdr:from><xdr:to><xdr:col>6</xdr:col><xdr:row>2</xdr:row></xdr:to>
        <xdr:pic><xdr:nvPicPr><xdr:cNvPr id="7" name="Logo"/><xdr:cNvPicPr/></xdr:nvPicPr><xdr:blipFill><a:blip r:embed="image"/></xdr:blipFill></xdr:pic>
        <xdr:clientData/></xdr:twoCellAnchor></xdr:wsDr>''',
        "xl/drawings/_rels/drawing1.xml.rels": f'<Relationships xmlns="{PACKAGE}"><Relationship Id="image" Type="{REL}/image" Target="../media/image1.png"/></Relationships>',
        "xl/media/image1.png": b"same-image-payload",
        "custom.bin": b"opaque-content",
        "custom.xml": '<a:record xmlns:a="urn:custom"><a:value>content</a:value></a:record>',
        "docProps/core.xml": '<core><modified>old</modified></core>',
    }
    if vba:
        values["xl/vbaProject.bin"] = b"unchanged-vba-bytes"
    return values


def save(tmp_path, name, values):
    path = tmp_path / name
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for part, content in values.items():
            archive.writestr(part, content)
    return path


def validate(tmp_path, after, operation="apply_changes", arguments=None, before=None):
    source = save(tmp_path, "before.xlsx", before or parts())
    output = save(tmp_path, "after.xlsx", after)
    return validate_preservation(capture_preservation(source), output, operation, arguments or {})


def test_identical_payload_and_expected_bookkeeping_normalization_are_completed(tmp_path):
    values = parts()
    values["docProps/core.xml"] = '<core><modified>new</modified></core>'
    values["custom.xml"] = '<z:record xmlns:z="urn:custom">\n <z:value>content</z:value>\n</z:record>'
    result = validate(tmp_path, values)
    assert result["status"] == "completed", result
    assert {item["part"] for item in result["manifest_changes"]} == {"docProps/core.xml", "custom.xml"}


def test_part_paths_relationship_ids_and_drawing_ids_are_not_object_identity(tmp_path):
    values = parts()
    values["xl/media/image9.png"] = values.pop("xl/media/image1.png")
    values["xl/drawings/_rels/drawing1.xml.rels"] = values["xl/drawings/_rels/drawing1.xml.rels"].replace('Id="image"', 'Id="rId999"').replace('image1.png', 'image9.png')
    values["xl/drawings/drawing1.xml"] = values["xl/drawings/drawing1.xml"].replace('r:embed="image"', 'r:embed="rId999"').replace('id="7"', 'id="27"')
    result = validate(tmp_path, values)
    assert result["status"] == "completed", result


def test_style_font_and_base_ids_are_not_semantic_identity(tmp_path):
    values = parts()
    values['xl/styles.xml'] = f'''<styleSheet xmlns="{SHEET}">
      <fonts count="2"><font><name val="Arial"/><sz val="9"/></font><font><name val="Calibri"/><sz val="11"/></font></fonts>
      <fills count="1"><fill><patternFill patternType="none"/></fill></fills><borders count="1"><border/></borders>
      <cellStyleXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0"/></cellStyleXfs>
      <cellXfs count="1"><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="1" applyFont="false"/></cellXfs>
      <cellStyles count="1"><cellStyle name="Normal" xfId="1" builtinId="0"/></cellStyles></styleSheet>'''
    result = validate(tmp_path, values)
    assert result["status"] == "completed", result


def test_style_change_outside_requested_range_is_not_silently_accepted(tmp_path):
    values = parts()
    values['xl/styles.xml'] = values['xl/styles.xml'].replace('<sz val="11"/>', '<sz val="20"/>')
    result = validate(tmp_path, values, arguments={"operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[1]]}]})
    assert result["status"] == "unverified"
    assert any(item["code"] == "cell_style_changed" and item["address"] == "B1" for item in result["findings"])


def test_redundant_apply_format_flags_are_normalized_but_actual_overrides_are_preserved(tmp_path):
    baseline = parts()
    baseline['xl/styles.xml'] = baseline['xl/styles.xml'].replace('xfId="0"/>', 'xfId="0" applyNumberFormat="1" applyFont="1" applyAlignment="1"/>')
    result = validate(tmp_path, parts(), before=baseline)
    assert result['status'] == 'completed', result
    changed = dict(baseline)
    changed['xl/styles.xml'] = changed['xl/styles.xml'].replace('<cellXfs count="1"><xf numFmtId="0"', '<cellXfs count="1"><xf numFmtId="2"')
    result = validate(tmp_path, changed, before=baseline)
    assert result['status'] == 'unverified'
    assert any(item['code'] == 'cell_style_changed' for item in result['findings'])


def test_empty_cell_with_duplicate_default_style_can_be_omitted_on_save(tmp_path):
    baseline = parts()
    baseline['xl/styles.xml'] = baseline['xl/styles.xml'].replace('<cellXfs count="1">', '<cellXfs count="2">').replace('</cellXfs>', '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>')
    baseline['xl/worksheets/sheet1.xml'] = baseline['xl/worksheets/sheet1.xml'].replace('<c r="B1"><v>2</v></c>', '<c r="B1"><v>2</v></c><c r="E1" s="1"/>')
    result = validate(tmp_path, parts(), before=baseline)
    assert result['status'] == 'completed', result


def model_connection_parts():
    values = parts()
    values['xl/workbook.xml'] = values['xl/workbook.xml'].replace('</definedNames>', '<definedName name="_xlcn.Linked" hidden="1">Tabela[]</definedName></definedNames>')
    values['[Content_Types].xml'] = values['[Content_Types].xml'].replace('</Types>', '<Override PartName="/xl/connections.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.connections+xml"/></Types>')
    values['xl/_rels/workbook.xml.rels'] = values['xl/_rels/workbook.xml.rels'].replace('</Relationships>', f'<Relationship Id="connection" Type="{REL}/connections" Target="connections.xml"/></Relationships>')
    values['xl/connections.xml'] = f'<connections xmlns="{SHEET}" xmlns:x15="http://schemas.microsoft.com/office/spreadsheetml/2010/11/main"><connection id="1" name="Linked" type="102"><extLst><ext uri="model"><x15:connection id="Tabela"><x15:rangePr sourceName="_xlcn.Linked"/></x15:connection></ext></extLst></connection></connections>'
    return values


def test_model_connection_alias_rename_resolves_to_same_hidden_definition(tmp_path):
    baseline = model_connection_parts()
    changed = {name: value.replace('_xlcn.Linked', '_xlcn.Linked1') if isinstance(value, str) else value for name, value in baseline.items()}
    result = validate(tmp_path, changed, before=baseline, arguments={'operations': [{'type': 'model_measure', 'action': 'set', 'name': 'Total', 'table': 'Tabela', 'formula': 'SUM(Tabela[A])'}]})
    assert result['status'] == 'completed', result


@pytest.mark.parametrize('change', ['different_range', 'different_connection', 'missing_definition', 'ordinary_name'])
def test_model_alias_normalization_never_hides_actual_name_or_source_loss(tmp_path, change):
    baseline = model_connection_parts()
    changed = {name: value.replace('_xlcn.Linked', '_xlcn.Linked1') if isinstance(value, str) else value for name, value in baseline.items()}
    if change == 'different_range':
        changed['xl/workbook.xml'] = changed['xl/workbook.xml'].replace('Tabela[]', 'Tabela[A]')
    elif change == 'different_connection':
        changed['xl/connections.xml'] = changed['xl/connections.xml'].replace('name="Linked"', 'name="Other"')
    elif change == 'missing_definition':
        changed['xl/workbook.xml'] = changed['xl/workbook.xml'].replace('<definedName name="_xlcn.Linked1" hidden="1">Tabela[]</definedName>', '')
    else:
        baseline['xl/workbook.xml'] = baseline['xl/workbook.xml'].replace('_xlcn.Linked', 'Linked')
        baseline['xl/connections.xml'] = baseline['xl/connections.xml'].replace('_xlcn.Linked', 'Linked')
    result = validate(tmp_path, changed, before=baseline)
    assert result['status'] == 'failed', result
    assert any(item['code'] == 'defined_name_removed' for item in result['findings'])


def test_cell_values_change_only_inside_declared_matrix_envelope(tmp_path):
    values = parts()
    values["xl/worksheets/sheet1.xml"] = values["xl/worksheets/sheet1.xml"].replace('<v>1</v>', '<v>7</v>').replace('<v>3</v>', '<v>8</v>')
    result = validate(tmp_path, values, arguments={"operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[7], [8]]}]})
    assert result["status"] == "completed", result
    outside = validate(tmp_path, values, arguments={"operations": [{"type": "set_values", "sheet": "Dados", "range": "A1", "values": [[7]]}]})
    assert outside["status"] == "failed" and any(item["code"] == "cell_content_changed" and item["address"] == "A2" for item in outside["findings"])


def test_saved_formula_cache_can_change_without_changing_formula_contract(tmp_path):
    values = parts()
    values["xl/worksheets/sheet1.xml"] = values["xl/worksheets/sheet1.xml"].replace('<f>A1+A2</f><v>4</v>', '<f>A1+A2</f><v>99</v>')
    result = validate(tmp_path, values, operation="recalculate", arguments={"calculation": {"mode": "full_rebuild"}})
    assert result["status"] == "completed", result


def test_array_children_are_derived_caches_and_can_change_or_disappear(tmp_path):
    baseline = parts()
    baseline['xl/worksheets/sheet1.xml'] = baseline['xl/worksheets/sheet1.xml'].replace('<f>A1+A2</f><v>4</v>', '<f t="array" ref="B2:C2">_xlfn.SEQUENCE(1,A1+1)</f><v>1</v>').replace('</row></sheetData>', '<c r="C2"><v>2</v></c></row></sheetData>')
    changed = dict(baseline)
    changed['xl/worksheets/sheet1.xml'] = changed['xl/worksheets/sheet1.xml'].replace('<c r="C2"><v>2</v></c>', '<c r="C2"><v>99</v></c>')
    assert validate(tmp_path, changed, before=baseline, operation='recalculate')['status'] == 'completed'
    changed['xl/worksheets/sheet1.xml'] = changed['xl/worksheets/sheet1.xml'].replace('<c r="C2"><v>99</v></c>', '').replace('ref="B2:C2"', 'ref="B2"')
    assert validate(tmp_path, changed, before=baseline, operation='recalculate')['status'] == 'completed'
    changed['xl/worksheets/sheet1.xml'] = changed['xl/worksheets/sheet1.xml'].replace('_xlfn.SEQUENCE(1,A1+1)', '999')
    result = validate(tmp_path, changed, before=baseline, operation='recalculate')
    assert result['status'] == 'failed'
    assert any(item['code'] == 'cell_content_changed' and item['address'] == 'B2' for item in result['findings'])


def test_array_cache_cannot_silently_turn_into_a_constant_input(tmp_path):
    baseline = parts()
    baseline['xl/worksheets/sheet1.xml'] = baseline['xl/worksheets/sheet1.xml'].replace('<f>A1+A2</f><v>4</v>', '<f t="array" ref="B2:C2">_xlfn.SEQUENCE(1,A1+1)</f><v>1</v>').replace('</row></sheetData>', '<c r="C2"><v>2</v></c></row></sheetData>')
    changed = dict(baseline)
    changed['xl/worksheets/sheet1.xml'] = changed['xl/worksheets/sheet1.xml'].replace('ref="B2:C2"', 'ref="B2"')
    result = validate(tmp_path, changed, before=baseline)
    assert result['status'] == 'failed'
    assert any(item['code'] == 'cell_content_changed' and item['address'] == 'C2' for item in result['findings'])


def test_deep_opaque_xml_change_never_collapses_to_an_equal_depth_marker(tmp_path):
    baseline = parts()
    baseline['custom.xml'] = '<root>' * 135 + '<value>old</value>' + '</root>' * 135
    changed = dict(baseline)
    changed['custom.xml'] = changed['custom.xml'].replace('old', 'new')
    result = validate(tmp_path, changed, before=baseline)
    assert result['status'] == 'unverified'
    assert any(item['code'] == 'opaque_resource_changed' for item in result['findings'])


def test_opaque_mutation_is_unverified_but_loss_is_failed(tmp_path):
    values = parts(); values["custom.bin"] = b"changed-unexplained-payload"
    changed = validate(tmp_path, values)
    assert changed["status"] == "unverified"
    assert any(item["code"] == "opaque_resource_changed" for item in changed["findings"])
    del values["custom.bin"]
    removed = validate(tmp_path, values)
    assert removed["status"] == "failed"
    assert any(item["code"] == "resource_removed" and item["part"] == "custom.bin" for item in removed["findings"])


def test_unrequested_new_opaque_payload_is_unverified_and_new_vba_is_failed(tmp_path):
    values = parts(); values['new.bin'] = b'new-opaque-data'
    result = validate(tmp_path, values)
    assert result['status'] == 'unverified'
    assert any(item['code'] == 'new_resource_unverified' for item in result['findings'])
    values = parts(vba=True)
    result = validate(tmp_path, values)
    assert result['status'] == 'failed'
    assert any(item['code'] == 'vba_added' for item in result['findings'])


def test_date_system_and_workbook_protection_are_preserved(tmp_path):
    values = parts()
    values['xl/workbook.xml'] = values['xl/workbook.xml'].replace('<sheets>', '<workbookPr date1904="1"/><sheets>')
    result = validate(tmp_path, values)
    assert result['status'] == 'failed'
    assert any(item['code'] == 'date_system_changed' for item in result['findings'])
    allowed = validate(tmp_path, values, operation='set_object', arguments={'member': 'Date1904', 'selector': [], 'value': True})
    assert allowed['status'] == 'completed', allowed


def test_vba_bytes_must_remain_identical_outside_explicit_vba_edit(tmp_path):
    baseline = parts(vba=True)
    changed = dict(baseline); changed["xl/vbaProject.bin"] = b"changed-project"
    result = validate(tmp_path, changed, before=baseline)
    assert result["status"] == "failed" and any(item["code"] == "vba_bytes_changed" for item in result["findings"])
    edited = validate(tmp_path, changed, operation="edit_vba", arguments={"action": "set", "module": "Module1", "source": "Option Explicit"}, before=baseline)
    assert edited["status"] == "completed", edited


def test_validation_or_merge_changes_require_their_declared_operation(tmp_path):
    values = parts()
    values["xl/worksheets/sheet1.xml"] = values["xl/worksheets/sheet1.xml"].replace('<formula2>10</formula2>', '<formula2>20</formula2>').replace('ref="C1:D1"', 'ref="C1:E1"')
    result = validate(tmp_path, values)
    assert result["status"] == "unverified"
    assert {item.get("field") for item in result["findings"]} >= {"validations", "merges"}
    allowed = validate(tmp_path, values, arguments={"operations": [
        {"type": "validation", "sheet": "Dados", "range": "A1:A2", "action": "set"},
        {"type": "merge", "sheet": "Dados", "range": "C1:E1"}]})
    assert allowed["status"] == "completed", allowed


def test_missing_table_and_visual_objects_are_detected_before_publication(tmp_path):
    values = parts()
    values["xl/worksheets/_rels/sheet1.xml.rels"] = f'<Relationships xmlns="{PACKAGE}"/>'
    values["xl/worksheets/sheet1.xml"] = values["xl/worksheets/sheet1.xml"].replace('<drawing r:id="drawing"/>', '').replace('<tableParts count="1"><tablePart r:id="table"/></tableParts>', '')
    for part in ('xl/tables/table1.xml', 'xl/drawings/drawing1.xml', 'xl/drawings/_rels/drawing1.xml.rels', 'xl/media/image1.png'):
        del values[part]
    values['[Content_Types].xml'] = values['[Content_Types].xml'].replace(f'<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>', '').replace(f'<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>', '')
    result = validate(tmp_path, values)
    assert result["status"] == "failed"
    assert any(item["code"] == "table_removed" for item in result["findings"])


def test_macro_envelope_is_explicit_without_pretending_to_validate_macro_logic(tmp_path):
    values = parts(); values["custom.bin"] = b"macro-authorized-change"
    result = validate(tmp_path, values, operation="run_macro", arguments={"procedure": "Module1.Update"})
    assert result["status"] == "completed"
    assert result["findings"][0]["code"] == "macro_envelope_authorized"


def test_template_clone_is_allowed_to_change_only_the_workbook_format(tmp_path):
    baseline = parts()
    baseline['[Content_Types].xml'] = baseline['[Content_Types].xml'].replace('spreadsheetml.sheet.main+xml', 'spreadsheetml.template.main+xml')
    result = validate(tmp_path, parts(), operation="create", arguments={"template_path": "synthetic.xltx", "operations": []}, before=baseline)
    assert result["status"] == "completed", result


def test_transient_selection_is_ignored_while_zoom_remains_protected(tmp_path):
    baseline = parts()
    baseline['xl/worksheets/sheet1.xml'] = baseline['xl/worksheets/sheet1.xml'].replace('<sheetFormatPr', '<sheetViews><sheetView workbookViewId="0" zoomScale="100"><selection activeCell="A1" sqref="A1"/></sheetView></sheetViews><sheetFormatPr')
    values = dict(baseline)
    values['xl/worksheets/sheet1.xml'] = values['xl/worksheets/sheet1.xml'].replace('activeCell="A1" sqref="A1"', 'activeCell="B9" sqref="B9"')
    assert validate(tmp_path, values, before=baseline)['status'] == 'completed'
    values['xl/worksheets/sheet1.xml'] = values['xl/worksheets/sheet1.xml'].replace('zoomScale="100"', 'zoomScale="90"')
    assert validate(tmp_path, values, before=baseline)['status'] == 'unverified'
    allowed = validate(tmp_path, values, before=baseline, arguments={'operations': [{'type': 'view', 'sheet': 'Dados', 'zoom': 90}]})
    assert allowed['status'] == 'completed', allowed


def test_binary_input_never_returns_completed_without_a_semantic_parser(tmp_path):
    before = {"format": "xls", "snapshot_id": "unknown", "manifest": {}, "has_manifest": False, "limitations": []}
    after = save(tmp_path, 'after.xlsx', parts())
    result = validate_preservation(before, after, 'export', {"allow_feature_loss": True})
    assert result["status"] == "unverified"
    assert result["findings"][0]["code"] == "binary_preservation_unverified"


@pytest.mark.parametrize('extra_column,hidden,expected', [(False,False,'completed'),(True,False,'unverified'),(False,True,'unverified')])
def test_pivot_autofit_changes_only_widths_within_its_report_columns(tmp_path, extra_column, hidden, expected):
    changed = parts()
    changed['[Content_Types].xml'] = changed['[Content_Types].xml'].replace('</Types>', '<Override PartName="/xl/pivotTables/pivotTable1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotTable+xml"/></Types>')
    changed['xl/worksheets/_rels/sheet1.xml.rels'] = changed['xl/worksheets/_rels/sheet1.xml.rels'].replace('</Relationships>', f'<Relationship Id="pivot" Type="{REL}/pivotTable" Target="../pivotTables/pivotTable1.xml"/></Relationships>')
    changed['xl/pivotTables/pivotTable1.xml'] = f'<pivotTableDefinition xmlns="{SHEET}" name="Resumo" cacheId="1"><location ref="E1:F3" firstHeaderRow="1" firstDataRow="1" firstDataCol="1"/></pivotTableDefinition>'
    columns = '<col min="5" max="6" width="13.4" bestFit="1" customWidth="1"' + (' hidden="1"' if hidden else '') + '/>'
    if extra_column:
        columns += '<col min="7" max="7" width="55" customWidth="1"/>'
    changed['xl/worksheets/sheet1.xml'] = changed['xl/worksheets/sheet1.xml'].replace('<sheetData>', '<cols>'+columns+'</cols><sheetData>')
    result = validate(tmp_path, changed, arguments={'operations':[{'type':'pivot','action':'create','sheet':'Dados','name':'Resumo','destination':'E1'}]})
    assert result['status'] == expected, result


@pytest.mark.parametrize('edit_source,change_cache,expected', [(True,False,'completed'),(False,False,'unverified'),(True,True,'unverified')])
def test_source_edit_can_dirty_pivot_metadata_without_authorizing_cache_data_changes(tmp_path,edit_source,change_cache,expected):
    baseline=parts()
    baseline['[Content_Types].xml']=baseline['[Content_Types].xml'].replace('</Types>','<Override PartName="/xl/pivotCache/pivotCacheDefinition1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.pivotCacheDefinition+xml"/></Types>')
    baseline['xl/pivotCache/pivotCacheDefinition1.xml']=f'<pivotCacheDefinition xmlns="{SHEET}" xmlns:clean="http://schemas.microsoft.com/office/spreadsheetml/2026/pivotDataSourceClean"><cacheSource type="worksheet"><worksheetSource ref="A1:B2" sheet="Dados"/></cacheSource><cacheFields count="1"><cacheField name="Valor"><sharedItems><n v="10"/></sharedItems></cacheField></cacheFields><extLst><ext uri="other"><metadata/></ext><ext uri="{{F4336242-56EE-4DD3-A5F7-E900642BF6E9}}"><clean:dataSourceClean>1</clean:dataSourceClean></ext></extLst></pivotCacheDefinition>'
    changed=dict(baseline)
    changed['xl/pivotCache/pivotCacheDefinition1.xml']=changed['xl/pivotCache/pivotCacheDefinition1.xml'].replace('<ext uri="{F4336242-56EE-4DD3-A5F7-E900642BF6E9}"><clean:dataSourceClean>1</clean:dataSourceClean></ext>','')
    if change_cache:
        changed['xl/pivotCache/pivotCacheDefinition1.xml']=changed['xl/pivotCache/pivotCacheDefinition1.xml'].replace('<n v="10"/>','<n v="20"/>')
    operations=[{'type':'set_values','sheet':'Dados','range':'A1' if edit_source else 'J10','values':[[1]]}]
    result=validate(tmp_path,changed,before=baseline,arguments={'operations':operations})
    assert result['status']==expected,result


@pytest.mark.parametrize('part,content_type,kind', [
    ('xl/comments1.xml','application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml','comment'),
    ('xl/threadedComments/threadedComment1.xml','application/vnd.ms-excel.threadedcomments+xml','comment'),
    ('xl/persons/person.xml','application/vnd.ms-excel.person+xml','comment'),
    ('xl/timelines/timeline1.xml','application/vnd.ms-excel.timeline+xml','timeline_filter'),
    ('xl/slicers/slicer1.xml','application/vnd.ms-excel.slicer+xml','slicer'),
    ('xl/model/item.data','application/vnd.ms-excel.model+data','model_measure'),
    ('xl/queryTables/query1.xml','application/vnd.openxmlformats-officedocument.spreadsheetml.queryTable+xml','query'),
    ('xl/connections.xml','application/vnd.openxmlformats-officedocument.spreadsheetml.connections+xml','connection'),
    ('customXml/item1.xml','application/xml','query'),
    ('xl/charts/chart1.xml','application/vnd.openxmlformats-officedocument.drawingml.chart+xml','chart'),
    ('xl/printerSettings/printer1.bin','application/vnd.openxmlformats-officedocument.spreadsheetml.printerSettings','page_setup'),
    ('xl/activeX/activeX1.bin','application/vnd.ms-office.activeX','control'),
])
def test_feature_payload_changes_require_their_explicit_operation(tmp_path,part,content_type,kind):
    baseline=parts()
    baseline['[Content_Types].xml']=baseline['[Content_Types].xml'].replace('</Types>',f'<Override PartName="/{part}" ContentType="{content_type}"/></Types>')
    baseline[part]='<resource><value>old</value></resource>' if part.endswith('.xml') else b'old-payload'
    changed=dict(baseline)
    changed[part]='<resource><value>new</value></resource>' if part.endswith('.xml') else b'new-payload'
    assert validate(tmp_path,changed,before=baseline)['status']=='unverified'
    result=validate(tmp_path,changed,before=baseline,arguments={'operations':[{'type':kind,'sheet':'Dados','name':'Recurso','action':'set'}]})
    assert result['status']=='completed',result


def test_allowed_conversion_reports_opaque_loss_and_vba_removal_without_hiding_it(tmp_path):
    baseline=parts(vba=True)
    changed=parts()
    del changed['custom.bin']
    blocked=validate(tmp_path,changed,before=baseline,operation='export')
    assert blocked['status']=='failed' and any(item['code']=='vba_removed' for item in blocked['findings'])
    result=validate(tmp_path,changed,before=baseline,operation='export',arguments={'allow_feature_loss':True})
    assert result['status']=='completed',result
    assert {item['family'] for item in result['findings'] if item['code']=='authorized_conversion_loss'}>={'vba','unknown'}


def test_signature_change_needs_acknowledgement_and_xlm_cannot_be_added(tmp_path):
    baseline=parts()
    baseline['_xmlsignatures/sig1.xml']='<signature>old</signature>'
    changed=dict(baseline);changed['_xmlsignatures/sig1.xml']='<signature>new</signature>'
    assert validate(tmp_path,changed,before=baseline)['status']=='unverified'
    assert validate(tmp_path,changed,before=baseline,arguments={'allow_signature_invalidation':True})['status']=='completed'
    changed=parts()
    changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('</definedNames>','<definedName name="Executar">EVALUATE("1+1")</definedName></definedNames>')
    result=validate(tmp_path,changed)
    assert result['status']=='failed' and any(item['code']=='xlm_added' for item in result['findings'])


def test_unrequested_external_link_and_visual_object_changes_are_detected(tmp_path):
    changed=parts()
    changed['xl/_rels/workbook.xml.rels']=changed['xl/_rels/workbook.xml.rels'].replace('</Relationships>',f'<Relationship Id="external" Type="{REL}/externalLinkPath" Target="file:///C:/fonte.xlsx" TargetMode="External"/></Relationships>')
    result=validate(tmp_path,changed)
    assert result['status']=='failed' and any(item['code']=='external_relationships_changed' for item in result['findings'])
    changed=parts()
    changed['xl/drawings/drawing1.xml']=changed['xl/drawings/drawing1.xml'].replace('<xdr:col>4</xdr:col>','<xdr:col>8</xdr:col>')
    result=validate(tmp_path,changed)
    assert result['status']=='unverified' and any(item['code']=='visual_object_changed' for item in result['findings'])
    allowed=validate(tmp_path,changed,arguments={'operations':[{'type':'image','action':'set','sheet':'Dados','name':'Logo'}]})
    assert allowed['status']=='completed',allowed


def test_sheet_rename_and_visibility_are_scoped_to_requested_actions(tmp_path):
    changed=parts()
    changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('name="Dados"','name="Nova"').replace('Dados!','Nova!')
    result=validate(tmp_path,changed)
    assert result['status']=='failed' and any(item['code']=='sheet_removed' for item in result['findings'])
    allowed=validate(tmp_path,changed,arguments={'operations':[{'type':'sheet','action':'rename','sheet':'Dados','name':'Nova'}]})
    assert allowed['status']=='completed',allowed
    changed=parts();changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('name="Dados" sheetId','name="Dados" state="hidden" sheetId')
    assert validate(tmp_path,changed)['status']=='unverified'
    assert validate(tmp_path,changed,arguments={'operations':[{'type':'sheet','action':'visibility','sheet':'Dados','state':'hidden'}]})['status']=='completed'


def test_defined_name_table_and_protection_changes_require_corresponding_action(tmp_path):
    changed=parts();changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('Dados!$A$1','Dados!$B$1')
    result=validate(tmp_path,changed)
    assert result['status']=='unverified' and any(item['code']=='defined_name_changed' for item in result['findings'])
    assert validate(tmp_path,changed,arguments={'operations':[{'type':'name','action':'set','name':'Valor'}]})['status']=='completed'
    changed=parts();changed['xl/tables/table1.xml']=changed['xl/tables/table1.xml'].replace('ref="A1:B2"','ref="A1:B3"')
    assert validate(tmp_path,changed)['status']=='unverified'
    assert validate(tmp_path,changed,arguments={'operations':[{'type':'table','action':'resize','sheet':'Dados','name':'Tabela','range':'A1:B3'}]})['status']=='completed'
    changed=parts();changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('<sheets>','<workbookProtection lockStructure="1"/><sheets>')
    assert validate(tmp_path,changed)['status']=='failed'
    assert validate(tmp_path,changed,arguments={'operations':[{'type':'protect','action':'set'}]})['status']=='completed'


@pytest.mark.parametrize('item', [
    {'type':'set_values','sheet':'Dados','range':'Tabela','values':[[7]]},
    {'type':'set_values','sheet':'Dados','range':'Valor','values':[[7]]},
    {'type':'autofill','sheet':'Dados','range':'B1','destination':'A1'},
    {'type':'goal_seek','sheet':'Dados','range':'B2','changing_range':'A1'},
    {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_range':'A1','transpose':True},
    {'type':'advanced_filter','sheet':'Dados','action':'copy','range':'B1:B2','copy_range':'A1'},
    {'type':'clear','sheet':'Dados','range':'A1','what':'all'},
    {'type':'sort','sheet':'Dados','range':'A1:B2'},
])
def test_cell_operation_envelopes_resolve_names_and_destinations(tmp_path,item):
    changed=parts();changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<c r="A1"><v>1</v></c>','<c r="A1"><v>7</v></c>')
    result=validate(tmp_path,changed,arguments={'operations':[item]})
    assert result['status']=='completed',result


def test_com_adapter_range_and_name_changes_use_the_same_preservation_contract(tmp_path):
    changed=parts();changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<c r="A1"><v>1</v></c>','<c r="A1"><v>7</v></c>')
    result=validate(tmp_path,changed,operation='set_object',arguments={'selector':[{'collection':'Worksheets','name':'Dados'},{'collection':'Range','name':'A1'}],'member':'Value2','value':7})
    assert result['status']=='completed',result


def test_equivalent_validation_regions_merge_adjacent_coordinates(tmp_path):
    baseline=parts()
    baseline['xl/worksheets/sheet1.xml']=baseline['xl/worksheets/sheet1.xml'].replace('sqref="A1:A2"','sqref="A1 A2 B1 B2"')
    changed=dict(baseline)
    changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('sqref="A1 A2 B1 B2"','sqref="A1:B2"')
    assert validate(tmp_path,changed,before=baseline)['status']=='completed'


def test_chart_cache_and_explicit_false_font_flags_are_normalized(tmp_path):
    baseline=parts()
    baseline['xl/styles.xml']=baseline['xl/styles.xml'].replace('<name val="Calibri"/>','<name val="Calibri"/><b val="false"/>')
    baseline['xl/charts/chart1.xml']='<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:numCache><c:pt idx="0"><c:v>10</c:v></c:pt></c:numCache><c:style val="1"/></c:chart></c:chartSpace>'
    changed=dict(baseline)
    changed['xl/charts/chart1.xml']=changed['xl/charts/chart1.xml'].replace('<c:v>10</c:v>','<c:v>20</c:v>')
    changed['xl/styles.xml']=changed['xl/styles.xml'].replace('<b val="false"/>','')
    assert validate(tmp_path,changed,before=baseline)['status']=='completed'
    changed['xl/charts/chart1.xml']=changed['xl/charts/chart1.xml'].replace('val="1"','val="2"')
    assert validate(tmp_path,changed,before=baseline)['status']=='unverified'


def test_binary_workbook_manifest_remains_explicitly_unverified(tmp_path):
    values=parts()
    values['[Content_Types].xml']=values['[Content_Types].xml'].replace('PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"','PartName="/xl/workbook.bin" ContentType="application/vnd.ms-excel.sheet.binary.macroEnabled.main"')
    values['_rels/.rels']=values['_rels/.rels'].replace('xl/workbook.xml','xl/workbook.bin')
    values['xl/workbook.bin']=b'binary-workbook'
    del values['xl/workbook.xml']
    values['xl/_rels/workbook.bin.rels']=values.pop('xl/_rels/workbook.xml.rels')
    path=save(tmp_path,'binary.xlsb',values)
    baseline=capture_preservation(path)
    assert baseline['has_manifest'] is False and baseline['format']=='xlsb'
    result=validate_preservation(baseline,path,'recalculate',{})
    assert result['status']=='unverified'


@pytest.mark.parametrize('kind', ['copy','add'])
def test_worksheet_copy_preserves_shared_image_resource_while_adding_an_owner(tmp_path,kind):
    changed=parts()
    changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('</sheets>','<sheet name="Copia" sheetId="2" r:id="copy"/></sheets>')
    changed['xl/_rels/workbook.xml.rels']=changed['xl/_rels/workbook.xml.rels'].replace('</Relationships>',f'<Relationship Id="copy" Type="{REL}/worksheet" Target="worksheets/sheet2.xml"/></Relationships>')
    changed['[Content_Types].xml']=changed['[Content_Types].xml'].replace('</Types>',f'<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    changed['xl/worksheets/sheet2.xml']=changed['xl/worksheets/sheet1.xml'].replace('<tableParts count="1"><tablePart r:id="table"/></tableParts>','')
    changed['xl/worksheets/_rels/sheet2.xml.rels']=f'<Relationships xmlns="{PACKAGE}"><Relationship Id="drawing" Type="{REL}/drawing" Target="../drawings/drawing1.xml"/></Relationships>'
    result=validate(tmp_path,changed,arguments={'operations':[{'type':'sheet','action':kind,'sheet':'Dados','name':'Copia'}]})
    assert result['status']=='completed',result


def test_cell_removal_requires_an_explicit_affected_range(tmp_path):
    changed=parts();changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<c r="B1"><v>2</v></c>','')
    result=validate(tmp_path,changed)
    assert result['status']=='failed' and any(item['code']=='cell_removed' and item['address']=='B1' for item in result['findings'])
    result=validate(tmp_path,changed,arguments={'operations':[{'type':'clear','sheet':'Dados','range':'B1','what':'contents'}]})
    assert result['status']=='completed',result


@pytest.mark.parametrize('kind,fragment', [
    ('resize','<cols><col min="1" max="2" width="20" customWidth="1"/></cols>'),
    ('outline','<cols><col min="1" max="2" hidden="1" outlineLevel="1"/></cols>'),
    ('filter','<autoFilter ref="A1:B2"/>'),
    ('conditional_format','<conditionalFormatting sqref="A1"><cfRule type="expression" priority="1"><formula>A1&gt;0</formula></cfRule></conditionalFormatting>'),
    ('page_setup','<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'),
    ('protect','<sheetProtection sheet="1"/>'),
])
def test_sheet_metadata_changes_require_the_specific_operation(tmp_path,kind,fragment):
    changed=parts();changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<sheetData>',fragment+'<sheetData>')
    assert validate(tmp_path,changed)['status']=='unverified'
    result=validate(tmp_path,changed,arguments={'operations':[{'type':kind,'sheet':'Dados','range':'A1:B2','action':'set'}]})
    assert result['status']=='completed',result


@pytest.mark.parametrize('part', ['xl/embeddings/oleObject1.bin','xl/theme/theme1.xml'])
def test_unedited_ole_and_theme_payloads_remain_protected(tmp_path,part):
    baseline=parts();baseline[part]='<theme>old</theme>' if part.endswith('.xml') else b'old-ole'
    changed=dict(baseline);changed[part]='<theme>new</theme>' if part.endswith('.xml') else b'new-ole'
    result=validate(tmp_path,changed,before=baseline)
    assert result['status']=='unverified'
    assert any(item['code']=='opaque_resource_changed' and item['family'] in {'theme','ole'} for item in result['findings'])


def test_font_color_case_and_explicit_true_have_the_same_effective_style(tmp_path):
    baseline=parts();baseline['xl/styles.xml']=baseline['xl/styles.xml'].replace('<name val="Calibri"/>','<name val="Calibri"/><b val="1"/><color rgb="ffaa0000"/>')
    changed=dict(baseline);changed['xl/styles.xml']=changed['xl/styles.xml'].replace('<b val="1"/>','<b/>').replace('ffaa0000','FFAA0000')
    assert validate(tmp_path,changed,before=baseline)['status']=='completed'


@pytest.mark.parametrize('proof,expected', [('verified','completed'),('missing','unverified'),('inaccessible','unverified'),('source_changed','failed'),('stale','unverified'),('design_unknown','unverified'),('client_argument','unverified')])
def test_udf_compilation_requires_bound_private_vba_evidence(tmp_path,proof,expected):
    source=save(tmp_path,'source.xlsm',parts(vba=True))
    changed=parts(vba=True);changed['xl/vbaProject.bin']=b'compiled-cache-and-identical-source'
    output=save(tmp_path,'output.xlsm',changed)
    before=capture_preservation(source);after=capture_preservation(output)
    evidence={'source_snapshot_id':before['snapshot_id'],'output_snapshot_id':after['snapshot_id'],
              'status':'completed','modules_verified':True,'references_verified':True,'design_verified':True}
    arguments={'calculation':{'mode':'full_rebuild','enable_vba_udfs':True}}
    if proof=='inaccessible': evidence['status']='unverified'
    if proof=='source_changed': evidence.update(status='failed',modules_verified=False)
    if proof=='stale': evidence['output_snapshot_id']='0'*64
    if proof=='design_unknown': evidence['design_verified']=False
    if proof=='client_argument': arguments['_native_vba_verification']=evidence
    elif proof!='missing': before['_native_vba_verification']=evidence
    result=validate_preservation(before,output,'apply_changes',arguments)
    assert result['status']==expected,result
    if proof=='verified':
        assert result['findings'][0]['code']=='vba_compiled_cache_normalized'
        ordinary=validate_preservation(before,output,'apply_changes',{})
        assert ordinary['status']=='failed' and ordinary['findings'][0]['code']=='vba_bytes_changed'


@pytest.mark.parametrize('outside,hidden,expected', [(False,False,'completed'),(True,False,'unverified'),(False,True,'unverified')])
def test_table_conversion_materializes_styles_and_height_only_inside_its_footprint(tmp_path,outside,hidden,expected):
    baseline=parts()
    baseline['xl/worksheets/sheet1.xml']=baseline['xl/worksheets/sheet1.xml'].replace('<c r="B1"><v>2</v></c>','<c r="B1"><v>2</v></c><c r="E1"><v>5</v></c>')
    changed=dict(baseline)
    changed['xl/styles.xml']=changed['xl/styles.xml'].replace('<cellXfs count="1">','<cellXfs count="2">').replace('</cellXfs>','<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>')
    changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<c r="A1">','<c r="A1" s="1">').replace('<row r="2">','<row r="2" ht="15.75"'+(' hidden="1"' if hidden else '')+'>')
    if outside:
        changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<c r="E1">','<c r="E1" s="1">')
    changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<tableParts count="1"><tablePart r:id="table"/></tableParts>','')
    changed['xl/worksheets/_rels/sheet1.xml.rels']=changed['xl/worksheets/_rels/sheet1.xml.rels'].replace(f'<Relationship Id="table" Type="{REL}/table" Target="../tables/table1.xml"/>','')
    del changed['xl/tables/table1.xml']
    changed['[Content_Types].xml']=changed['[Content_Types].xml'].replace('<Override PartName="/xl/tables/table1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"/>','')
    result=validate(tmp_path,changed,before=baseline,arguments={'operations':[{'type':'table','action':'convert','sheet':'Dados','name':'Tabela'}]})
    assert result['status']==expected,result


@pytest.mark.parametrize('variation,expected', [('known','completed'),('other_range','unverified'),('unknown_type','unverified'),('unknown_extension','unverified'),('unresolved_index','unverified'),('unused_metadata','unverified')])
def test_new_dynamic_array_metadata_is_bound_to_authorized_formulas_only(tmp_path,variation,expected):
    metadata=f'''<metadata xmlns="{SHEET}" xmlns:xda="http://schemas.microsoft.com/office/spreadsheetml/2017/dynamicarray"><metadataTypes count="1"><metadataType name="XLDAPR" minSupportedVersion="120000" copy="1" pasteAll="1" pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1" coerce="1" cellMeta="1"/></metadataTypes><futureMetadata name="XLDAPR" count="1"><bk><extLst><ext uri="{{bdbb8cdc-fa1e-496e-a857-3c3f30c029c3}}"><xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/></ext></extLst></bk></futureMetadata><cellMetadata count="1"><bk><rc t="1" v="0"/></bk></cellMetadata></metadata>'''
    changed=parts()
    changed['[Content_Types].xml']=changed['[Content_Types].xml'].replace('</Types>','<Override PartName="/xl/metadata.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheetMetadata+xml"/></Types>')
    changed['xl/_rels/workbook.xml.rels']=changed['xl/_rels/workbook.xml.rels'].replace('</Relationships>',f'<Relationship Id="metadata" Type="{REL}/sheetMetadata" Target="metadata.xml"/></Relationships>')
    changed['xl/metadata.xml']=metadata
    changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<c r="B2"><f>A1+A2</f>','<c r="B2" cm="1"><f t="array" ref="B2">A1+A2</f>')
    if variation=='unknown_type':changed['xl/metadata.xml']=metadata.replace('XLDAPR','XLRICHVALUE')
    if variation=='unknown_extension':changed['xl/metadata.xml']=metadata.replace('</metadata>','<extLst><unknown/></extLst></metadata>')
    if variation=='unresolved_index':changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('cm="1"','cm="2"')
    if variation=='unused_metadata':changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace(' cm="1"','')
    result=validate(tmp_path,changed,arguments={'operations':[{'type':'set_formula','sheet':'Dados','range':'A1' if variation=='other_range' else 'B2','formula':'=A1+A2'}]})
    assert result['status']==expected,result
    if variation=='known':assert result['findings'][0]['code']=='dynamic_array_metadata_added'


@pytest.mark.parametrize('outside,height,expected', [(False,False,'completed'),(True,False,'failed'),(False,True,'failed')])
def test_filter_clear_changes_hidden_rows_only_inside_filter_body(tmp_path,outside,height,expected):
    baseline=parts();baseline['xl/worksheets/sheet1.xml']=baseline['xl/worksheets/sheet1.xml'].replace('<row r="2">','<row r="2" hidden="1"'+(' ht="30"' if height else '')+'>')
    changed=dict(baseline);changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace(' hidden="1"','').replace(' ht="30"','')
    result=validate(tmp_path,changed,before=baseline,arguments={'operations':[{'type':'filter','action':'clear','sheet':'Dados','range':'A3:B5' if outside else 'A1:B2'}]})
    assert result['status']==expected,result


@pytest.mark.parametrize('extra_style,remaining_link,expected', [(False,False,'completed'),(True,False,'unverified'),(False,True,'unverified')])
def test_deleting_last_hyperlink_can_remove_only_its_builtin_named_style(tmp_path,extra_style,remaining_link,expected):
    baseline=parts()
    baseline['xl/styles.xml']=baseline['xl/styles.xml'].replace('</cellStyles>','<cellStyle name="Hyperlink" xfId="0" builtinId="8"/>'+('<cellStyle name="MinhaRegra" xfId="0"/>' if extra_style else '')+'</cellStyles>')
    links='<hyperlink ref="A1" location="Dados!B1"/>'+('<hyperlink ref="B1" location="Dados!A1"/>' if remaining_link else '')
    baseline['xl/worksheets/sheet1.xml']=baseline['xl/worksheets/sheet1.xml'].replace('</worksheet>','<hyperlinks>'+links+'</hyperlinks></worksheet>')
    changed=dict(baseline)
    changed['xl/styles.xml']=changed['xl/styles.xml'].replace('<cellStyle name="Hyperlink" xfId="0" builtinId="8"/>','').replace('<cellStyle name="MinhaRegra" xfId="0"/>','')
    changed['xl/worksheets/sheet1.xml']=changed['xl/worksheets/sheet1.xml'].replace('<hyperlink ref="A1" location="Dados!B1"/>','')
    result=validate(tmp_path,changed,before=baseline,arguments={'operations':[{'type':'hyperlink','sheet':'Dados','range':'A1','action':'delete'}]})
    assert result['status']==expected,result
    changed=parts();changed['xl/workbook.xml']=changed['xl/workbook.xml'].replace('name="Dados"','name="Nova"')
    result=validate(tmp_path,changed,operation='set_object',arguments={'selector':[{'collection':'Worksheets','name':'Dados'}],'member':'Name','value':'Nova'})
    assert result['status']=='completed',result
