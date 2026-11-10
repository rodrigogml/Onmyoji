"""Serviço de leitura e autoria com o motor Excel desktop em processo privado."""
from __future__ import annotations

import csv
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from ..component.native import ExcelSession
from ..component.ooxml import SpreadsheetPackage, excel_date, preflight, formula_errors
from ..exception import ExcelExceptions, require
from .operations import apply_operation, assert_range_owner, check_calculation_sources, check_formula, defer_pivot_refresh, formula_records, get_number_format, range_address, require_pivot_or_slicer_policy, set_number_format, target_range, worksheet, write_formula


FORMAT_IDS = {"xlsx": 51, "xlsm": 52, "xlsb": 50, "xls": 56, "xltx": 54, "xltm": 53, "xlam": 55, "xla": 18, "xlt": 17}
WRITING = {"create", "apply_changes", "recalculate", "refresh", "export", "edit_vba", "run_macro", "set_object", "call_object"}
READ_FIELDS = {"value", "formula", "cached_value", "display_text", "type", "number_format", "error", "date_value"}
ERROR_CODES = {2000: "#NULL!", 2007: "#DIV/0!", 2015: "#VALUE!", 2023: "#REF!", 2029: "#NAME?", 2036: "#NUM!", 2042: "#N/A", 2043: "#GETTING_DATA", 2045: "#SPILL!"}
COLLECTIONS = {"Worksheets", "Sheets", "Range", "Names", "ListObjects", "ListColumns", "PivotTables", "PivotFields", "ChartObjects", "Shapes", "Queries", "Connections", "Model", "ModelTables", "ModelMeasures", "ModelTableColumns", "ModelRelationships", "SlicerCaches", "Slicers"}
OBJECT_MEMBERS = {
    "Workbook": {"get": {"Name", "FullName", "FileFormat", "Date1904", "ReadOnly", "HasVBProject", "ForceFullCalculation"}, "set": {"Date1904", "ForceFullCalculation"}, "call": set()},
    "Worksheets": {"get": {"Name", "Index", "Visible", "ProtectContents", "StandardWidth", "StandardHeight"}, "set": {"Name", "Visible", "StandardWidth", "StandardHeight"}, "call": {"Delete"}},
    "Sheets": {"get": {"Name", "Index", "Visible"}, "set": {"Name", "Visible"}, "call": {"Delete"}},
    "Range": {"get": {"Address", "Value2", "Formula2", "Formula", "FormulaR1C1", "NumberFormat", "Text", "HasFormula", "CountLarge", "Row", "Column", "ColumnWidth", "RowHeight", "WrapText", "MergeCells"},
              "set": {"Value2", "Formula2", "Formula", "FormulaR1C1", "NumberFormat", "ColumnWidth", "RowHeight", "WrapText"}, "call": {"Clear", "ClearContents", "ClearFormats", "Merge", "UnMerge"}},
    "Names": {"get": {"Name", "RefersTo", "Visible"}, "set": {"RefersTo", "Visible"}, "call": {"Delete"}},
    "ListObjects": {"get": {"Name", "DisplayName", "TableStyle", "ShowTotals", "ShowHeaders", "ShowAutoFilter"}, "set": {"Name", "TableStyle", "ShowTotals", "ShowHeaders", "ShowAutoFilter"}, "call": {"Delete", "Unlist"}},
    "ListColumns": {"get": {"Name", "Index", "TotalsCalculation"}, "set": {"Name", "TotalsCalculation"}, "call": {"Delete"}},
    "PivotTables": {"get": {"Name", "TableStyle2", "RowGrand", "ColumnGrand", "DisplayFieldCaptions", "RefreshDate"}, "set": {"TableStyle2", "RowGrand", "ColumnGrand", "DisplayFieldCaptions"}, "call": {"RefreshTable"}},
    "PivotFields": {"get": {"Name", "Orientation", "Position", "Function", "NumberFormat"}, "set": {"Orientation", "Position", "Function", "NumberFormat"}, "call": {"ClearAllFilters"}},
    "ChartObjects": {"get": {"Name", "Left", "Top", "Width", "Height", "Visible"}, "set": {"Name", "Left", "Top", "Width", "Height", "Visible"}, "call": {"Delete"}},
    "Shapes": {"get": {"Name", "Type", "Left", "Top", "Width", "Height", "Visible", "Rotation", "AlternativeText"}, "set": {"Name", "Left", "Top", "Width", "Height", "Visible", "Rotation", "AlternativeText"}, "call": {"Delete"}},
    "Queries": {"get": {"Name", "Formula", "Description"}, "set": {"Name", "Formula", "Description"}, "call": {"Delete"}},
    "Connections": {"get": {"Name", "Type", "Description", "RefreshWithRefreshAll", "InModel"}, "set": {"Description", "RefreshWithRefreshAll"}, "call": {"Delete"}},
    "ModelMeasures": {"get": {"Name", "Formula", "Description"}, "set": {"Formula", "Description"}, "call": {"Delete"}},
    "ModelTables": {"get": {"Name", "RecordCount"}, "set": set(), "call": set()},
    "ModelTableColumns": {"get": {"Name", "DataType"}, "set": set(), "call": set()},
    "ModelRelationships": {"get": {"Active"}, "set": {"Active"}, "call": {"Delete"}},
    "SlicerCaches": {"get": {"Name", "OLAP", "FilterCleared", "CrossFilterType", "SortItems"}, "set": {"CrossFilterType", "SortItems"}, "call": {"ClearManualFilter", "Delete"}},
    "Slicers": {"get": {"Name", "Caption", "Left", "Top", "Width", "Height", "NumberOfColumns"}, "set": {"Caption", "Left", "Top", "Width", "Height", "NumberOfColumns"}, "call": {"Delete"}},
}


def portable(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        require(math.isfinite(value), "invalid_result", "O Excel retornou número não finito.")
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [portable(item) for item in value]
    require(False, "unsupported_result", "O membro retornou objeto COM não serializável; selecione uma propriedade explícita.")


def _collection(collection):
    return [collection.Item(index) for index in range(1, int(collection.Count) + 1)]


def _copy_workbook(source, information, context):
    actual = information.get("format")
    suffix = "." + actual if actual in FORMAT_IDS else source.suffix
    require(suffix.lower().lstrip(".") in FORMAT_IDS, "unsupported_format", "O formato detectado não possui rota nativa de abertura homologada.")
    copied = context.temporary_path(suffix)
    shutil.copyfile(source, copied)
    return copied


def _names(collection):
    return [str(item.Name) for item in _collection(collection)]


def _inventory(workbook):
    sheets = []
    for sheet in _collection(workbook.Worksheets):
        sheets.append({"name": str(sheet.Name), "index": int(sheet.Index), "visible": int(sheet.Visible),
                       "used_range": str(sheet.UsedRange.Address), "tables": _names(sheet.ListObjects),
                       "pivots": _names(sheet.PivotTables()), "charts": _names(sheet.ChartObjects()), "shapes": _names(sheet.Shapes)})
    result = {"sheets": sheets, "names": [{"name": str(name.Name), "refers_to": str(name.RefersTo)} for name in _collection(workbook.Names)],
            "queries": _names(workbook.Queries), "connections": [{"name": str(item.Name), "type": int(item.Type)} for item in _collection(workbook.Connections)],
            "has_vba": bool(workbook.HasVBProject), "date_1904": bool(workbook.Date1904), "format_id": int(workbook.FileFormat)}
    model = _model_inventory(workbook)
    if model is not None:
        result["model"] = model
    return result


def _model_inventory(workbook):
    connections = _collection(workbook.Connections)
    loaded = False
    for connection in connections:
        try:
            loaded = loaded or bool(connection.InModel) or int(connection.Type) == 7
        except Exception:
            pass
    if not loaded:
        return None
    model = workbook.Model
    return {"tables": [{"name": str(table.Name), "record_count": int(table.RecordCount),
                        "columns": [{"name": str(column.Name), "data_type": portable(column.DataType)} for column in _collection(table.ModelTableColumns)]} for table in _collection(model.ModelTables)],
            "measures": [{"name": str(measure.Name), "associated_table": str(measure.AssociatedTable.Name), "formula": str(measure.Formula), "description": portable(measure.Description)} for measure in _collection(model.ModelMeasures)],
            "relationships": [{"foreign_table": str(item.ForeignKeyTable.Name), "foreign_column": str(item.ForeignKeyColumn.Name),
                                "primary_table": str(item.PrimaryKeyTable.Name), "primary_column": str(item.PrimaryKeyColumn.Name),
                                "active": bool(item.Active)} for item in _collection(model.ModelRelationships)]}


def _same_native_objects(expected, actual, member=None):
    """Aceita somente o arredondamento geométrico observado no Excel.

    Controles podem ajustar geometria em meio pixel ao salvar. A tolerância
    de 0,375 ponto se aplica exclusivamente a Left/Top/Width/Height; valores,
    fórmulas, contagens e os demais membros continuam com igualdade exata.
    """
    if member in {"Left", "Top", "Width", "Height"} and type(expected) in {int, float} and type(actual) in {int, float}:
        return abs(expected - actual) <= 0.375
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_same_native_objects(expected[key], actual[key], key) for key in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_same_native_objects(left, right, member) for left, right in zip(expected, actual))
    return expected == actual


def _capture_vba_semantics(workbook, context):
    """Captura fonte, atributos exportados, referências e desenho sem publicá-los.

    A evidência serve para distinguir recompilação de p-code de alteração do
    projeto durante cálculo UDF. Exportações ficam somente no trabalho do job;
    hashes e conteúdo nunca são copiados para o resultado MCP ou para logs.
    """
    if context.profile.get("allow_vba_source") is not True:
        return {"status": "unverified", "reason": "vba_source_policy_disabled"}
    if not bool(workbook.HasVBProject):
        return {"status": "failed", "reason": "vba_project_not_available"}
    try:
        project = _project(workbook, context)
        components = sorted(_collection(project.VBComponents), key=lambda item: (str(item.Name).casefold(), int(item.Type)))
        directory = context.temporary_path()
        directory.mkdir()
        modules, designs, references = [], [], []
        maximum = context.defaults.get("max_package_bytes", 1073741824)
        total = 0
        for index, component in enumerate(components):
            kind = int(component.Type)
            suffix = {1: ".bas", 2: ".cls", 3: ".frm", 100: ".cls"}.get(kind)
            if suffix is None:
                return {"status": "unverified", "reason": "vba_component_design_not_exportable"}
            code = component.CodeModule
            count = int(code.CountOfLines)
            source_digest = hashlib.sha256()
            source_digest.update(str(count).encode("ascii"))
            for line in range(1, count + 1, 1000):
                chunk = str(code.Lines(line, min(1000, count - line + 1))).encode("utf-8")
                total += len(chunk)
                require(total <= maximum, "vba_snapshot_limit", "A fonte VBA excede o limite de verificação do job.")
                source_digest.update(len(chunk).to_bytes(8, "big"))
                source_digest.update(chunk)
            # A mesma base em diretórios distintos evita diferenças artificiais
            # em OleObjectBlob="component-NNNN.frx" entre os três snapshots.
            exported = directory / ("component-" + str(index).zfill(4) + suffix)
            component.Export(str(exported))
            require(exported.is_file(), "vba_export_unverified", "O VBIDE não produziu o componente para verificação.")
            from ...document_processing.paths import file_digest
            total += exported.stat().st_size
            require(total <= maximum, "vba_snapshot_limit", "As exportações VBA excedem o limite de verificação do job.")
            modules.append({"name": str(component.Name), "type": kind, "source": source_digest.hexdigest(), "export": file_digest(exported)})
            if kind == 3:
                resource = exported.with_suffix(".frx")
                blobs = re.findall(rb'OleObjectBlob\s*=\s*"([^\"]+)"', exported.read_bytes())
                require(not blobs or all(name == resource.name.encode("ascii") for name in blobs) and resource.is_file(),
                        "vba_export_unverified", "O desenho do formulário não foi exportado com seu recurso associado.")
                total += resource.stat().st_size if resource.is_file() else 0
                require(total <= maximum, "vba_snapshot_limit", "Os recursos VBA excedem o limite de verificação do job.")
                designs.append({"name": str(component.Name), "form": file_digest(exported), "resource": file_digest(resource) if resource.is_file() else None})
        for reference in _collection(project.References):
            references.append({"guid": str(reference.GUID), "major": int(reference.Major), "minor": int(reference.Minor),
                               "full_path": str(Path(str(reference.FullPath)).resolve()).casefold(),
                               "broken": bool(reference.IsBroken), "name": str(reference.Name)})
        references.sort(key=lambda item: (item["guid"], item["major"], item["minor"], item["full_path"]))
        def digest(value):
            return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return {"status": "completed", "modules_sha256": digest(modules), "references_sha256": digest(references),
                "design_sha256": digest(designs), "module_count": len(modules), "reference_count": len(references)}
    except Exception:
        # Não confundir indisponibilidade VBIDE/designer ou referência quebrada
        # com perda comprovada de fonte, nem expor erro com caminhos privados.
        return {"status": "unverified", "reason": "vba_semantics_not_accessible"}


def _verify_vba_semantics(before, after, reopened, source_snapshot_id, output_snapshot_id):
    """Produz somente a atestação privada vinculada ao par de arquivos correto."""
    snapshots = (before, after, reopened)
    proof = {"status": "unverified", "source_snapshot_id": source_snapshot_id, "output_snapshot_id": output_snapshot_id,
             "modules_verified": False, "references_verified": False, "design_verified": False}
    if not all(isinstance(snapshot, dict) for snapshot in snapshots):
        proof["reason"] = "vba_snapshot_missing"
        return proof
    if any(snapshot.get("status") == "failed" for snapshot in snapshots):
        proof.update(status="failed", reason="vba_project_changed")
        return proof
    if not all(snapshot.get("status") == "completed" for snapshot in snapshots):
        proof["reason"] = "vba_semantics_not_accessible"
        return proof
    for field, verified in (("modules_sha256", "modules_verified"), ("references_sha256", "references_verified"), ("design_sha256", "design_verified")):
        hashes = [snapshot.get(field) for snapshot in snapshots]
        proof[verified] = all(isinstance(value, str) and len(value) == 64 for value in hashes) and len(set(hashes)) == 1
    proof["status"] = "completed" if all(proof[field] for field in ("modules_verified", "references_verified", "design_verified")) else "failed"
    proof["reason"] = "source_references_and_design_unchanged" if proof["status"] == "completed" else "vba_semantics_changed"
    proof["module_count"] = before.get("module_count")
    proof["reference_count"] = before.get("reference_count")
    return proof


def _preservation_inventory(workbook, context):
    """Captura pós-condições de objetos antes de salvar, sem expor seu conteúdo.

    A evidência complementa o manifesto OOXML. Membros indisponíveis são
    registrados como tal e não passam a significar suporte completo ao objeto.
    """
    def members(owner, names):
        result = {}
        for name in names:
            try:
                value = portable(get_number_format(owner) if name == "NumberFormat" else getattr(owner, name))
                result[name] = round(value, 6) if isinstance(value, float) else value
            except Exception:
                result[name] = {"unavailable": True}
        return result
    def reference(value):
        return value.replace(str(workbook.FullName), "<workbook>").replace("[" + str(workbook.Name) + "]", "[<workbook>]") if isinstance(value, str) else value
    inventory = {"date1904": bool(workbook.Date1904), "has_vba": bool(workbook.HasVBProject), "sheets": [],
                 "names": [{"name": str(item.Name), "refers_to": reference(str(item.RefersTo)), "visible": bool(item.Visible)} for item in _collection(workbook.Names)],
                 "queries": [members(item, ("Name", "Formula", "Description")) for item in _collection(workbook.Queries)],
                 "connections": [members(item, ("Name", "Type", "Description", "InModel")) for item in _collection(workbook.Connections)]}
    for sheet in _collection(workbook.Worksheets):
        info = {"name": str(sheet.Name), "visible": int(sheet.Visible), "protect_contents": bool(sheet.ProtectContents),
                "tables": [], "pivots": [], "charts": [], "shapes": [], "validations": [], "conditional_formats": []}
        for table in _collection(sheet.ListObjects):
            info["tables"].append({**members(table, ("Name", "ShowTotals", "ShowHeaders", "ShowAutoFilter")),
                                   "range": str(table.Range.Address), "columns": _names(table.ListColumns), "style": str(table.TableStyle)})
        for pivot in _collection(sheet.PivotTables()):
            info["pivots"].append({**members(pivot, ("Name", "TableStyle2", "RowGrand", "ColumnGrand", "DisplayFieldCaptions")),
                                   "range": str(pivot.TableRange2.Address), "fields": [members(field, ("Name", "Orientation", "Position", "Function", "NumberFormat")) for field in _collection(pivot.PivotFields())]})
        for item in _collection(sheet.ChartObjects()):
            chart = item.Chart
            series = []
            for element in _collection(chart.SeriesCollection()):
                record = members(element, ("Name", "Formula", "ChartType", "AxisGroup", "HasDataLabels"))
                if isinstance(record.get("Formula"), str):
                    record["Formula"] = reference(record["Formula"])
                series.append(record)
            info["charts"].append({**members(item, ("Name", "Left", "Top", "Width", "Height")),
                                   "chart": members(chart, ("ChartType", "HasTitle", "HasLegend")), "series": series})
        for shape in _collection(sheet.Shapes):
            info["shapes"].append(members(shape, ("Name", "Type", "Left", "Top", "Width", "Height", "Visible", "AlternativeText")))
        try:
            targets = sheet.Cells.SpecialCells(-4174)
        except Exception:
            targets = None
        if targets is not None:
            require(int(targets.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A preservação de validações excede o limite de células.")
            for cell in targets:
                info["validations"].append({"address": range_address(cell), **members(cell.Validation, ("Type", "Operator", "Formula1", "Formula2", "IgnoreBlank", "InCellDropdown", "ShowError", "ErrorTitle", "ErrorMessage", "InputTitle", "InputMessage"))})
        for condition in _collection(sheet.UsedRange.FormatConditions):
            info["conditional_formats"].append({"range": str(condition.AppliesTo.Address), **members(condition, ("Type", "Operator", "Formula1", "Formula2", "Priority", "StopIfTrue"))})
        inventory["sheets"].append(info)
    inventory["slicers"] = []
    for cache in _collection(workbook.SlicerCaches):
        state = {**members(cache, ("Name", "OLAP", "FilterCleared", "CrossFilterType", "SortItems")),
                 "slicers": [members(item, ("Name", "Caption", "Left", "Top", "Width", "Height", "NumberOfColumns")) for item in _collection(cache.Slicers)]}
        try:
            if cache.OLAP:
                state["selected"] = sorted(portable(cache.VisibleSlicerItemsList))
            elif int(cache.SlicerCacheType) == 2:
                state["timeline"] = members(cache.TimelineState, ("StartDate", "EndDate", "FilterType"))
            else:
                state["selected"] = sorted(str(item.Name) for item in _collection(cache.SlicerItems) if item.Selected)
        except Exception:
            state["selection"] = {"unavailable": True}
        inventory["slicers"].append(state)
    if inventory["has_vba"] and context.profile.get("allow_vba_source"):
        try:
            project = _project(workbook, context)
            inventory["vba"] = [{key: value for key, value in _module_source(item).items() if key != "source"} for item in _collection(project.VBComponents)]
        except ExcelExceptions:
            inventory["vba"] = {"unavailable": True}
    if any(connection.get("InModel") is True for connection in inventory["connections"]):
        model = workbook.Model
        inventory["model"] = {"tables": [{**members(table, ("Name", "RecordCount")), "columns": [members(column, ("Name", "DataType")) for column in _collection(table.ModelTableColumns)]} for table in _collection(model.ModelTables)],
                              "measures": [members(measure, ("Name", "Formula", "Description")) for measure in _collection(model.ModelMeasures)],
                              "relationships": [{"foreign_table": str(item.ForeignKeyTable.Name), "foreign_column": str(item.ForeignKeyColumn.Name),
                                                 "primary_table": str(item.PrimaryKeyTable.Name), "primary_column": str(item.PrimaryKeyColumn.Name),
                                                 "active": bool(item.Active)} for item in _collection(model.ModelRelationships)]}
    return inventory


def _native_errors(workbook, context):
    result = []
    for sheet in _collection(workbook.Worksheets):
        if getattr(sheet, "ProtectContents", False) is True:
            cells = (sheet.Cells(row, column) for row, column, _ in formula_records(sheet, context))
            for cell in cells:
                value = cell.Value2
                if isinstance(value, int) and not isinstance(value, bool) and -2146827000 <= value <= -2146820000 and bool(cell.Application.WorksheetFunction.IsError(cell)):
                    result.append({"sheet": str(sheet.Name), "address": range_address(cell), "error": ERROR_CODES.get(value & 0xffff, str(cell.Text)), "formula": str(cell.Formula2)})
            continue
        try:
            cells = sheet.UsedRange.SpecialCells(-4123, 16)
        except Exception:
            continue
        require(int(cells.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A auditoria de erros excede o limite de células.")
        for cell in cells:
            value = cell.Value2
            canonical = ERROR_CODES.get(value & 0xffff, str(cell.Text)) if isinstance(value, int) else str(cell.Text)
            result.append({"sheet": str(sheet.Name), "address": range_address(cell), "error": canonical, "formula": str(cell.Formula2)})
    return result


def _selected_ranges(workbook, args, context):
    sheets = [worksheet(workbook, args["sheet"])] if args.get("sheet") else _collection(workbook.Worksheets)
    total = 0
    for sheet in sheets:
        target = sheet.Range(args["range"]) if args.get("range") else sheet.UsedRange
        assert_range_owner(target, workbook, sheet)
        total += int(target.CountLarge)
        require(int(target.Areas.Count) == 1 and total <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A seleção de auditoria excede o limite de células.")
        yield sheet, target


def _native_cell(cell):
    value = cell.Value2
    formula = str(cell.Formula2) if cell.HasFormula else None
    is_error = isinstance(value, int) and not isinstance(value, bool) and -2146827000 <= value <= -2146820000 and bool(cell.Application.WorksheetFunction.IsError(cell))
    if is_error:
        value = ERROR_CODES.get(value & 0xffff, str(cell.Text))
    return {"address": range_address(cell), "row": int(cell.Row), "column": int(cell.Column), "formula": formula,
            "type": "formula" if formula else "error" if is_error else "blank" if value is None else "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "string",
            "value": None if formula else portable(value), "cached_value": portable(value) if formula else None,
            "error": value if is_error else None, "number_format": str(get_number_format(cell)),
            "date_value": None if is_error else _canonical_date_value(cell, value)}


def _canonical_date_value(cell, value):
    """Usa o calendário Excel inclusive o serial fictício 60 de 1900."""
    if type(value) in {int, float}:
        return excel_date(value, "1904" if cell.Worksheet.Parent.Date1904 else "1900", str(get_number_format(cell)))
    native = cell.Value
    if isinstance(native, datetime):
        native = native.replace(tzinfo=None)
        return native.date().isoformat() if native.time() == datetime.min.time() else native.isoformat()
    return native.isoformat() if isinstance(native, date) else None


def _analyze(workbook, args, context, snapshot):
    records = []
    for sheet, target in _selected_ranges(workbook, args, context):
        for cell in target:
            if not cell.HasFormula:
                continue
            entry = _native_cell(cell)
            expression = re.sub(r'"(?:[^\"]|\"\")*"', '""', entry["formula"])
            unresolved = []
            if re.search(r"\b(?:INDIRECT|OFFSET)\s*\(", expression, re.I):
                unresolved.append("dynamic_reference")
            if "[" in expression:
                unresolved.append("external_or_structured_reference")
            if re.search(r"\b(?:RTD|WEBSERVICE|STOCKHISTORY|PY|CUBEVALUE|CUBEMEMBER)\s*\(", expression, re.I):
                unresolved.append("external_service_or_model")
            records.append({"sheet": str(sheet.Name), "address": entry["address"], "formula": entry["formula"],
                            "cached_value": entry["cached_value"], "cached_error": entry["error"],
                            "broken_reference_literal": "#REF!" in expression.upper(), "unresolved": unresolved,
                            "observed_a1_references": re.findall(r"(?<![A-Za-z0-9_])\$?[A-Za-z]{1,3}\$?[1-9]\d{0,6}(?::\$?[A-Za-z]{1,3}\$?[1-9]\d{0,6})?(?![A-Za-z0-9_!(])", expression),
                            "dependencies_complete": False})
    offset, limit = args.get("offset", 0), args.get("limit", 100)
    return {"formulas": records[offset:offset + limit], "total": len(records), "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(records) else None, "snapshot_id": snapshot,
            "summary": {"formulas": len(records), "cached_errors": sum(item["cached_error"] is not None for item in records)},
            "limitations": ["Referências observadas não constituem grafo completo; fontes externas, nomes, UDFs e referências dinâmicas exigem análise própria."]}


def _compare_native(workbook, other, args, context):
    scope = args.get("scope", "all")
    differences = []
    if scope in {"all", "structure"}:
        left, right = _inventory(workbook), _inventory(other)
        for key in left:
            if left[key] != right[key]:
                differences.append({"kind": "structure", "field": key, "before": left[key], "after": right[key]})
    if scope in {"all", "cells"}:
        left_sheets = {str(sheet.Name): sheet for sheet in _collection(workbook.Worksheets)}
        right_sheets = {str(sheet.Name): sheet for sheet in _collection(other.Worksheets)}
        for name in sorted(set(left_sheets) | set(right_sheets)):
            def capture(sheet):
                if sheet is None:
                    return {}
                require(sheet.UsedRange.CountLarge <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A comparação excede o limite de células.")
                return {range_address(cell): _native_cell(cell) for cell in sheet.UsedRange if cell.Value2 is not None or cell.HasFormula}
            before, after = capture(left_sheets.get(name)), capture(right_sheets.get(name))
            for address in sorted(set(before) | set(after)):
                if before.get(address) != after.get(address):
                    differences.append({"kind": "cell", "sheet": name, "address": address, "before": before.get(address), "after": after.get(address)})
    offset, limit = args.get("offset", 0), args.get("limit", 100)
    return {"equal_in_scope": not differences, "scope": scope, "total": len(differences), "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < len(differences) else None, "differences": differences[offset:offset + limit],
            "limitations": ["Comparação do modelo de objetos não comprova equivalência visual ou preservação de todas as partes opacas."]}


def _validate_native(workbook, args, session, context):
    from .inspection import _apply_rule
    checks = args.get("checks", ["structure", "formulas", "data"])
    issues, unverified, checked = [], [], 0
    if "structure" in checks:
        _inventory(workbook)
    if "formulas" in checks:
        issues.extend({**item, "code": "saved_formula_error"} for item in _native_errors(workbook, context))
        unverified.append({"code": "calculation_not_performed", "message": "Abertura manual não comprova resultados atuais."})
    if "data" in checks:
        for sheet in _collection(workbook.Worksheets):
            try:
                targets = sheet.Cells.SpecialCells(-4174)
            except Exception:
                if getattr(sheet, "ProtectContents", False) is True:
                    unverified.append({"code": "data_validations_not_accessible", "sheet": str(sheet.Name),
                                       "message": "O Excel impede SpecialCells na aba protegida; as validações não foram certificadas."})
                continue
            require(int(targets.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A auditoria de validação excede o limite de células.")
            for cell in targets:
                checked += 1
                try:
                    first = cell.Validation.Formula1
                    if isinstance(first, str) and first.startswith("="):
                        check_formula(first, context)
                    valid = bool(cell.Validation.Value)
                    if not valid:
                        issues.append({"code": "data_validation_violation", "sheet": str(sheet.Name), "address": range_address(cell)})
                except Exception:
                    unverified.append({"code": "data_validation_unverified", "sheet": str(sheet.Name), "address": range_address(cell)})
        for index, rule in enumerate(args.get("rules", [])):
            seen = set()
            for sheet, target in _selected_ranges(workbook, rule, context):
                for cell in target:
                    checked += 1
                    if not _apply_rule(rule, _native_cell(cell), seen):
                        issues.append({"code": "data_rule_violation", "rule": index, "rule_type": rule["type"], "sheet": str(sheet.Name), "address": range_address(cell)})
    if "preservation" in checks:
        require(args.get("other_path"), "invalid_argument", "Preservação exige other_path como referência.")
        source = context.input_path(args["other_path"])
        information = preflight(source, context.profile, context.defaults)
        require(information["native_open_allowed"], "active_content_blocked", "A referência de preservação não permite abertura nativa.")
        copied = _copy_workbook(source, information, context)
        other = session.open(copied)
        comparison = _compare_native(other, workbook, {"scope": "structure", "limit": 1000}, context)
        other.Close(SaveChanges=False)
        if not comparison["equal_in_scope"]:
            issues.append({"code": "structure_changed", "differences": comparison["differences"]})
        unverified.append({"code": "opaque_preservation_not_proven", "message": "O modelo de objetos não certifica todas as partes opacas."})
    return {"checks": checks, "issues": issues, "issue_count": len(issues), "unverified": unverified, "data_cells_checked": checked,
            "status": "failed" if issues else "unverified" if unverified else "passed", "valid": False if issues else None if unverified else True,
            "limitations": ["Validação nativa por abertura/objeto não substitui validação OOXML, cálculo solicitado ou auditoria visual."]}


def _value_snapshot(workbook, context):
    """Compara conteúdo sem atribuir significado aos limites de UsedRange.

    O Excel pode encolher UsedRange ao salvar a última célula removida. Mapas
    esparsos distinguem dados/fórmulas de formatação vazia e evitam rejeitar
    essas edições legítimas. Áreas grandes usam SpecialCells e spill explícito.
    """
    from ..component.ooxml import column_name
    result, total = {}, 0
    maximum = context.defaults.get("max_cells", 1000000)
    for sheet in _collection(workbook.Worksheets):
        used, formulas = sheet.UsedRange, {}
        if getattr(sheet, "ProtectContents", False) is True:
            require(int(used.CountLarge) <= maximum, "cell_limit_exceeded", "A inspeção física da aba protegida excede o limite configurado.")
        formulas.update({(row, column): formula for row, column, formula in formula_records(sheet, context)})
        occupied = {}
        def capture(area):
            require(int(area.CountLarge) <= maximum, "cell_limit_exceeded", "A verificação de persistência excede o limite de células.")
            values = area.Value2
            values = values if isinstance(values, tuple) else ((values,),)
            for row, items in enumerate(values):
                for column, value in enumerate(items):
                    coordinates = int(area.Row) + row, int(area.Column) + column
                    formula = formulas.get(coordinates)
                    if value is not None or formula is not None:
                        address = column_name(coordinates[1]) + str(coordinates[0])
                        occupied[address] = {"value": value, "formula": formula}
        if int(used.CountLarge) <= maximum:
            capture(used)
        else:
            try:
                constants = used.SpecialCells(2)
            except Exception:
                constants = None
            if constants is not None:
                require(int(constants.CountLarge) <= maximum, "cell_limit_exceeded", "A pasta excede o limite de dados configurado.")
                for area in _collection(constants.Areas):
                    capture(area)
            for coordinates in formulas:
                cell = sheet.Cells(coordinates[0], coordinates[1])
                capture(cell)
                try:
                    spilling = cell.SpillingToRange
                except Exception:
                    spilling = None
                if spilling is not None:
                    capture(spilling)
        total += len(occupied)
        require(total <= maximum, "cell_limit_exceeded", "A verificação de persistência excede o limite de células.")
        result[str(sheet.Name)] = occupied
    return result


def _verify_package_values(path, expected, context):
    checked = 0
    with SpreadsheetPackage(path, context.defaults) as package:
        for name, snapshot in expected.items():
            for cell in package.cells(name).values():
                if cell["error"] is not None:
                    continue
                actual = cell["cached_value"] if cell["type"] == "formula" else cell["value"]
                native = snapshot.get(cell["address"], {}).get("value")
                # Excel serializa números com sua precisão documentada; não
                # exigir que o último bit binário sobreviva ao texto XML.
                same = math.isclose(actual, native, rel_tol=1e-14, abs_tol=1e-300) if type(actual) in {int, float} and type(native) in {int, float} else actual == native
                require(same, "persistence_validation_failed", "Um valor/cache salvo diverge do estado validado no Excel.")
                checked += 1
    return checked


def _read(workbook, args, context, snapshot):
    requested = set(args.get("fields", READ_FIELDS))
    require(requested <= READ_FIELDS, "invalid_argument", "Campo de leitura Excel desconhecido.")
    target = target_range(workbook, args, context)
    offset, limit = args.get("offset", 0), args.get("limit", 100)
    row_count, column_count = int(target.Rows.Count), int(target.Columns.Count)
    values = target.Value2
    formulas = target.Formula2
    if not isinstance(values, tuple):
        values = ((values,),)
        formulas = ((formulas,),)
    cells, total = [], 0
    for row in range(row_count):
        for column in range(column_count):
            value, raw_formula = values[row][column], formulas[row][column]
            if value is None and not raw_formula and not args.get("include_empty", True):
                continue
            if not offset <= total < offset + limit:
                total += 1
                continue
            total += 1
            cell = target.Cells(row + 1, column + 1)
            formula = str(raw_formula) if isinstance(raw_formula, str) and raw_formula.startswith("=") and cell.HasFormula else None
            kind = "blank" if value is None else "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "string"
            is_error = isinstance(value, int) and not isinstance(value, bool) and -2146827000 <= value <= -2146820000 and bool(workbook.Application.WorksheetFunction.IsError(cell))
            if is_error:
                kind = "error"
            item = {"address": range_address(cell), "sheet": args["sheet"], "row": int(cell.Row), "column": int(cell.Column)}
            if "value" in requested:
                item["value"] = None if formula else ERROR_CODES.get(value & 0xffff, str(cell.Text)) if is_error else portable(value)
            if "cached_value" in requested:
                item["cached_value"] = (ERROR_CODES.get(value & 0xffff, str(cell.Text)) if is_error else portable(value)) if formula else None
            if "formula" in requested:
                item["formula"] = formula
            if "type" in requested:
                item["type"] = "formula" if formula else kind
                item["cached_type"] = kind if formula else None
            if "display_text" in requested:
                item["display_text"] = str(cell.Text)
            if "number_format" in requested:
                item["number_format"] = str(get_number_format(cell))
            if "error" in requested:
                item["error"] = ERROR_CODES.get(value & 0xffff, str(cell.Text)) if is_error else None
            if "date_value" in requested:
                item["date_value"] = None if is_error else _canonical_date_value(cell, value)
            cells.append(item)
    return {"cells": cells, "total": total, "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < total else None, "snapshot_id": snapshot,
            "freshness": "saved_cache", "backend": "excel_com", "sheet": args["sheet"], "range": args["range"]}


def _navigate(workbook, args, context):
    kind = args.get("kind", "sheets")
    if kind == "model":
        model = _model_inventory(workbook)
        return {"kind": "model", "model": model, "available": model is not None}
    inventory = _inventory(workbook)
    if kind in {"sheets", "names", "queries", "connections"}:
        return {kind: inventory[kind]}
    if kind == "vba":
        project = _project(workbook, context)
        return {"modules": [{"name": str(item.Name), "component_type": int(item.Type)} for item in _collection(project.VBComponents)]}
    sheets = [worksheet(workbook, args["sheet"])] if args.get("sheet") else _collection(workbook.Worksheets)
    if kind == "regions":
        return {"regions": [{"sheet": str(sheet.Name), "range": str(sheet.UsedRange.Address)} for sheet in sheets], "note": "UsedRange pode incluir apenas formatação."}
    collection = {"tables": "ListObjects", "objects": "Shapes", "pivots": "PivotTables", "charts": "ChartObjects"}.get(kind)
    require(collection is not None, "capability_unavailable", "Família de navegação não disponível neste adaptador.")
    rows = []
    for sheet in sheets:
        items = getattr(sheet, collection)
        items = items() if collection in {"PivotTables", "ChartObjects"} else items
        for item in _collection(items):
            rows.append({"sheet": str(sheet.Name), "name": str(item.Name)})
    return {kind: rows}


def _search(workbook, args, context, snapshot):
    flags = 0 if args.get("case_sensitive") else re.IGNORECASE
    try:
        query = re.compile(args["query"] if args.get("regex") else re.escape(args["query"]), flags)
    except re.error as error:
        raise ExcelExceptions("invalid_regex", "A expressão regular não é válida.") from error
    found = []
    sheets = [worksheet(workbook, args["sheet"])] if args.get("sheet") else _collection(workbook.Worksheets)
    scanned = 0
    for sheet in sheets:
        target = sheet.Range(args["range"]) if args.get("range") else sheet.UsedRange
        scanned += int(target.CountLarge)
        require(scanned <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A busca excede o limite de células configurado.")
        for cell in target:
            scope = args.get("scope", "all")
            texts = []
            if scope in {"values", "all"}:
                texts.append(("value", cell.Value2))
            if scope in {"formulas", "all"} and cell.HasFormula:
                texts.append(("formula", cell.Formula2))
            for field, value in texts:
                if value is not None and query.search(str(value)):
                    found.append({"sheet": str(sheet.Name), "address": range_address(cell), "field": field, "value": portable(value)})
    offset, limit = args.get("offset", 0), args.get("limit", 100)
    return {"matches": found[offset:offset + limit], "total": len(found), "next_offset": offset + limit if offset + limit < len(found) else None, "snapshot_id": snapshot}


def _project(workbook, context):
    require(context.profile.get("allow_vba_source") is True, "vba_access_denied", "O perfil não autoriza acesso à fonte VBA.")
    try:
        project = workbook.VBProject
        require(int(project.Protection) == 0, "vba_project_protected", "O projeto VBA está protegido e não será desbloqueado pelo MCP.")
        project.VBComponents.Count
        return project
    except ExcelExceptions:
        raise
    except Exception as error:
        raise ExcelExceptions("vba_access_denied", "O Excel bloqueou o acesso VBIDE; confira Trust access e as políticas administrativas.") from error


def _module_source(component):
    code = component.CodeModule
    source = str(code.Lines(1, code.CountOfLines)) if code.CountOfLines else ""
    return {"name": str(component.Name), "component_type": int(component.Type), "source": source,
            "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()}


def _vba(workbook, operation, args, context):
    project = _project(workbook, context)
    if operation == "get_vba_source":
        modules = [_module_source(item) for item in _collection(project.VBComponents) if not args.get("module") or item.Name == args["module"]]
        require(modules or not args.get("module"), "module_not_found", "O módulo VBA não foi encontrado.")
        references = [{"name": str(reference.Name), "broken": bool(reference.IsBroken)} for reference in _collection(project.References)]
        return {"modules": modules, "references": references, "compilation": "unverified"}
    action = args["action"]
    component = next((item for item in _collection(project.VBComponents) if item.Name == args["module"]), None)
    if args.get("source_sha256"):
        require(component is not None and _module_source(component)["source_sha256"] == args["source_sha256"], "source_changed", "A fonte VBA não corresponde ao hash informado.")
    if action == "set":
        require("source" in args, "invalid_argument", "A edição VBA exige source.")
        if component is None:
            component = project.VBComponents.Add(1)
            component.Name = args["module"]
        code = component.CodeModule
        if code.CountOfLines:
            code.DeleteLines(1, code.CountOfLines)
        if args["source"]:
            code.AddFromString(args["source"])
        return {"module": _module_source(component), "compilation": "unverified"}
    require(component is not None or action == "import", "module_not_found", "O módulo VBA não foi encontrado.")
    if action == "delete":
        require(int(component.Type) != 100, "vba_document_module", "Módulos de documento não podem ser removidos.")
        project.VBComponents.Remove(component)
    elif action == "import":
        source = context.input_path(args["input_module_path"])
        require(source.suffix.lower() in {".bas", ".cls", ".frm"}, "invalid_module", "Importação VBA aceita .bas, .cls ou .frm.")
        require(source.suffix.lower() != ".frm" or context.profile.get("trusted_content") is True,
                "active_content_blocked", "Importação de UserForm exige conteúdo confiável por poder carregar controles.")
        temporary = context.temporary_path()
        temporary.mkdir()
        copied = temporary / source.name
        shutil.copyfile(source, copied)
        if source.suffix.lower() == ".frm":
            raw = source.read_bytes()
            names = re.findall(rb'OleObjectBlob\s*=\s*"([^\"]+)"', raw)
            for name in names:
                name = name.decode("ascii", errors="strict")
                require(Path(name).name == name and name.lower() == source.with_suffix(".frx").name.lower(), "invalid_module", "O formulário referencia recurso fora de seu par .frx.")
                resource = context.input_path(str(source.parent / name))
                shutil.copyfile(resource, temporary / name)
        if component is not None:
            require(int(component.Type) != 100, "vba_document_module", "Importação não substitui módulo de documento.")
            project.VBComponents.Remove(component)
        component = project.VBComponents.Import(str(copied))
        component.Name = args["module"]
    elif action == "export":
        destination = args["output_module_path"]
        staged = context.stage_output(destination)
        expected = {1: ".bas", 2: ".cls", 3: ".frm", 100: ".cls"}.get(int(component.Type))
        require(Path(destination).suffix.lower() == expected, "invalid_module", "A extensão de exportação não corresponde ao tipo do módulo.")
        component.Export(str(staged))
        resource = staged.with_suffix(".frx")
        if resource.exists():
            final_resource = Path(destination).with_suffix(".frx")
            require(final_resource.name.isascii(), "invalid_module", "O nome de exportação de UserForm deve permitir referência ASCII ao .frx.")
            staged.write_bytes(staged.read_bytes().replace(resource.name.encode("ascii"), final_resource.name.encode("ascii")))
            shutil.move(str(resource), str(context.stage_output(str(final_resource))))
    return {"module": args["module"], "action": action, "compilation": "unverified"}


def _resolve_object(workbook, selector):
    current, kind = workbook, "Workbook"
    for step in selector:
        collection = step["collection"]
        require(collection in COLLECTIONS and collection in OBJECT_MEMBERS or collection == "Model", "object_member_denied", "Coleção não autorizada no adaptador COM.")
        require(not ("name" in step and "index" in step), "invalid_selector", "Use nome ou índice em cada etapa do seletor.")
        # Cada aresta é explícita: nunca permitir Parent/Application, que
        # ultrapassariam a pasta do job, mesmo que o membro final seja permitido.
        edges = {"Workbook": {"Worksheets", "Sheets", "Names", "Queries", "Connections", "Model", "SlicerCaches"},
                 "Worksheets": {"Range", "Names", "ListObjects", "PivotTables", "ChartObjects", "Shapes"},
                 "Sheets": {"Range", "Names", "ListObjects", "PivotTables", "ChartObjects", "Shapes"},
                 "ListObjects": {"ListColumns"}, "PivotTables": {"PivotFields"}, "Model": {"ModelTables", "ModelMeasures", "ModelRelationships"},
                 "ModelTables": {"ModelTableColumns"}, "SlicerCaches": {"Slicers"}}
        require(collection in edges.get(kind, set()), "object_member_denied", "A coleção não pertence ao contexto selecionado.")
        parent = current
        member = getattr(current, collection)
        if collection == "Model":
            require("name" not in step and "index" not in step, "invalid_selector", "Model não aceita índice ou nome.")
            current = member
        else:
            require("name" in step or "index" in step, "invalid_selector", "A coleção exige nome ou índice explícito.")
            current = member(step.get("name", step.get("index")))
            if collection == "Range":
                assert_range_owner(current, workbook, parent)
        kind = collection
    return current, kind


def _object(workbook, operation, args, context, session=None):
    require(context.profile.get("allow_object_api") is True, "object_api_denied", "O perfil não autoriza o adaptador de objetos COM.")
    target, kind = _resolve_object(workbook, args.get("selector", []))
    allowed = OBJECT_MEMBERS.get(kind, {"get": set(), "set": set(), "call": set()})
    if operation == "describe_object":
        return {"object_type": kind, "members": {key: sorted(value) for key, value in allowed.items()}}
    action = {"get_object": "get", "set_object": "set", "call_object": "call"}[operation]
    member = args["member"]
    require(member in allowed[action], "object_member_denied", "O membro não é autorizado para essa operação/contexto.")
    if kind == "Range":
        require(int(target.Areas.Count) == 1 and int(target.CountLarge) <= context.defaults.get("max_cells", 1000000),
                "cell_limit_exceeded", "O intervalo do adaptador excede o limite de células ou não é contínuo.")
    if action == "get":
        return {"value": portable(get_number_format(target) if member == "NumberFormat" else getattr(target, member)), "object_type": kind, "member": member}
    if kind in {"PivotTables", "PivotFields"}:
        require_pivot_or_slicer_policy(target.PivotCache() if kind == "PivotTables" else target.Parent.PivotCache(), context)
    if kind in {"SlicerCaches", "Slicers"}:
        require_pivot_or_slicer_policy(target if kind == "SlicerCaches" else target.SlicerCache, context)
    if action == "set":
        value = args["value"]
        if member in {"Formula2", "Formula", "FormulaR1C1", "RefersTo"}:
            check_formula(value, context)
        if member == "Value2":
            require(kind == "Range" and target.CountLarge == 1, "invalid_range", "Value2 genérico aceita apenas célula única; use set_values para matrizes.")
            value = "'" + value if isinstance(value, str) else value
        if kind == "Range":
            require(target.CountLarge <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "O intervalo excede o limite de células.")
        if member == "NumberFormat":
            set_number_format(target, value)
        elif kind == "Range" and member in {"Formula2", "Formula", "FormulaR1C1"}:
            write_formula(target, value, member)
        else:
            setattr(target, member, value)
        return {"object_type": kind, "member": member, "changed": True}
    if member == "RefreshTable":
        require(context.profile.get("allow_refresh") is True, "external_data_denied", "Atualização pelo adaptador exige permissão de fontes externas.")
        require(session is not None, "pivot_refresh_context_required", "A atualização do pivô exige o contexto de cálculo do job.")
        ensure_pivot_source_ready(workbook, session, context, cache=target.PivotCache())
        defer_pivot_refresh(session, target.Parent.Name, target.Name)
    require(not args.get("args"), "invalid_argument", "Os métodos atualmente autorizados não recebem argumentos.")
    return {"object_type": kind, "member": member, "value": portable(getattr(target, member)())}


def _refresh(workbook, args, session, context):
    """Atualiza somente fontes; pivôs são tratados após o cálculo das origens."""
    require(context.profile.get("allow_refresh") is True, "external_data_denied", "O perfil não autoriza atualização de fontes externas.")
    selected = set(args.get("connections", []))
    connections = _collection(workbook.Connections)
    known = {str(connection.Name) for connection in connections}
    require(not selected or selected <= known, "connection_not_found", "Uma conexão selecionada não existe na pasta.")
    result, handled = [], set()
    for sheet in _collection(workbook.Worksheets):
        targets, seen = [], set()
        for table in _collection(sheet.ListObjects):
            try:
                query = table.QueryTable
                key = str(query.Name)
            except Exception:
                continue
            targets.append((str(table.Name), query))
            seen.add(key)
        for query in _collection(sheet.QueryTables):
            if str(query.Name) not in seen:
                targets.append((str(query.Name), query))
                seen.add(str(query.Name))
        for target_name, query in targets:
            try:
                name = str(query.WorkbookConnection.Name)
            except Exception:
                name = str(query.Name)
            if selected and name not in selected:
                continue
            try:
                completed = query.Refresh(BackgroundQuery=False)
                require(completed is True and not query.Refreshing, "refresh_failed", "A consulta não concluiu a atualização.")
                result.append({"name": name, "target": str(sheet.Name) + "!" + target_name, "status": "completed"})
            except Exception:
                result.append({"name": name, "target": str(sheet.Name) + "!" + target_name, "status": "failed"})
            handled.add(name)
    for connection in connections:
        name = str(connection.Name)
        if name in handled or selected and name not in selected:
            continue
        try:
            connection.Refresh()
            session.application.CalculateUntilAsyncQueriesDone()
            # Connection-only/model refresh does not expose a universal success
            # proof. Its completion must not be inferred from Refresh returning.
            result.append({"name": name, "status": "unverified"})
        except Exception:
            result.append({"name": name, "status": "failed"})
    require(not any(item["status"] == "failed" for item in result), "refresh_failed", "Uma fonte falhou; a cópia não será publicada com dados antigos.")
    require(not args.get("require_verified", True) or all(item["status"] == "completed" for item in result),
            "refresh_unverified", "A API não comprovou conclusão de todas as conexões; solicite política explícita para aceitar atualização não verificada.")
    return {"connections": result, "pivots": []}


def _pivot_targets(workbook):
    return [(sheet, pivot) for sheet in _collection(workbook.Worksheets) for pivot in _collection(sheet.PivotTables())]


def _pivot_source_range(workbook, cache, context):
    """Resolve fonte local A1/R1C1/nome/tabela sem Evaluate ou seleção ativa."""
    from ..component.ooxml import column_name, range_coordinates
    try:
        source = cache.SourceData
        if not isinstance(source, str):
            return None
        target = None
        if "!" in source:
            qualifier, reference = source.rsplit("!", 1)
            qualifier = qualifier.strip("'").replace("''", "'")
            book = re.search(r"\[([^\]]+)\]", qualifier)
            if book:
                if book.group(1).casefold() != str(workbook.Name).casefold():
                    return None
                directory = qualifier[:book.start()].rstrip("/" + chr(92))
                if directory and str(Path(directory).resolve()).casefold() != str(Path(str(workbook.FullName)).resolve().parent).casefold():
                    return None
                qualifier = qualifier[book.end():]
            sheet = worksheet(workbook, qualifier)
            cells = re.fullmatch(r"R([1-9]\d*)C([1-9]\d*)(?::R([1-9]\d*)C([1-9]\d*))?", reference, re.I)
            if cells:
                reference = column_name(int(cells.group(2))) + cells.group(1)
                if cells.group(3):
                    reference += ":" + column_name(int(cells.group(4))) + cells.group(3)
            range_coordinates(reference)
            target = sheet.Range(reference)
            assert_range_owner(target, workbook, sheet)
        else:
            for sheet in _collection(workbook.Worksheets):
                for table in _collection(sheet.ListObjects):
                    if source.casefold() == str(table.Name).casefold() or source.casefold().startswith(str(table.Name).casefold() + "["):
                        target = sheet.Range(source)
                        assert_range_owner(target, workbook, sheet)
                        break
                if target is not None:
                    break
            if target is None:
                target = workbook.Names.Item(source).RefersToRange
                require(str(target.Worksheet.Parent.FullName).casefold() == str(workbook.FullName).casefold()
                        and int(target.Worksheet.Parent.Application.Hwnd) == int(workbook.Application.Hwnd),
                        "range_context_mismatch", "A origem do pivô pertence a outra pasta.")
        require(int(target.Areas.Count) == 1 and int(target.CountLarge) <= context.defaults.get("max_cells", 1000000),
                "cell_limit_exceeded", "A origem do pivô excede o limite de células.")
        return target
    except ExcelExceptions as error:
        if error.code == "cell_limit_exceeded":
            raise
        return None
    except Exception:
        return None


def _refresh_pivots(workbook, args, session, context, *, sources_calculated=False, targets=None):
    """Atualiza relatórios após cálculo autorizado das origens locais.

    Sem cálculo, somente origem local comprovadamente sem fórmulas pode ser
    anunciada como concluída. Fontes locais não resolvidas ou com fórmulas
    ficam pendentes; require_verified=False permite publicar essa pendência,
    não transforma caches antigos em atualização comprovada.
    """
    if not args.get("pivots", True):
        return []
    targets, refreshed, result = _pivot_targets(workbook) if targets is None else targets, set(), []
    for sheet, pivot in targets:
        cache = pivot.PivotCache()
        require_pivot_or_slicer_policy(cache, context)
        source_type = int(cache.SourceType)
        source = _pivot_source_range(workbook, cache, context) if source_type == 1 else None
        local_unknown = source_type == 1 and source is None
        formulas = source is not None and source.HasFormula is not False
        if source_type != 1:
            try:
                local_model = int(cache.WorkbookConnection.Type) == 7
            except Exception:
                local_model = False
            formulas = local_model and any(sheet.UsedRange.HasFormula is not False for sheet in _collection(workbook.Worksheets))
        if local_unknown or formulas and not sources_calculated:
            result.append({"sheet": str(sheet.Name), "name": str(pivot.Name), "status": "unverified", "attempted": False,
                           "reason": "worksheet_source_not_resolved" if local_unknown else "source_calculation_not_requested"})
            continue
        try:
            cache_refreshed = False
            if source_type == 1:
                index = int(cache.Index)
                if index not in refreshed:
                    cache.Refresh()
                    refreshed.add(index)
                    cache_refreshed = True
            completed = pivot.RefreshTable()
            session.assert_hidden()
            result.append({"sheet": str(sheet.Name), "name": str(pivot.Name), "status": "completed" if completed else "failed",
                           "cache_refreshed": cache_refreshed,
                           "source_calculation": "completed" if formulas else "not_required"})
            if completed and source is not None:
                evidence = getattr(session, "pivot_source_evidence", None)
                if not isinstance(evidence, dict):
                    evidence = session.pivot_source_evidence = {}
                key = int(cache.Index)
                if key not in evidence:
                    evidence[key] = {"cache": cache, "address": str(source.Address), "values": source.Value2, "reports": []}
                evidence[key]["reports"].append(result[-1])
        except ExcelExceptions:
            raise
        except Exception:
            result.append({"sheet": str(sheet.Name), "name": str(pivot.Name), "status": "failed"})
    require(not any(item["status"] == "failed" for item in result), "refresh_failed", "Um pivô falhou; a cópia não será publicada com dados antigos.")
    require(not args.get("require_verified", True) or all(item["status"] == "completed" for item in result),
            "refresh_unverified", "A atualização dos pivôs depende de cálculo autorizado da origem ou de evidência não disponível.")
    return result


def _verify_final_pivot_sources(workbook, args, session, context):
    """Recusa uma falsa atualização se o cálculo final mudar a própria fonte.

    Um segundo cálculo pode reavaliar RAND/NOW ou dados contínuos. Não repetir
    queries nem buscar convergência infinita: relatar o snapshot instável e
    aplicar require_verified antes de publicar.
    """
    evidence = getattr(session, "pivot_source_evidence", None)
    if not isinstance(evidence, dict):
        return
    changed = False
    for item in evidence.values():
        source = _pivot_source_range(workbook, item["cache"], context)
        if source is None or str(source.Address) != item["address"] or source.Value2 != item["values"]:
            changed = True
            for report in item["reports"]:
                report.update(status="unverified", reason="source_changed_during_final_calculation")
    require(not changed or args.get("require_verified", True) is False, "refresh_unverified", "O cálculo final alterou fontes dos pivôs; a consistência do snapshot não foi comprovada.")


def _calculate_checked(workbook, session, calculation, context):
    if calculation.get("mode", "none") != "none":
        require(not _path_sensitive(workbook, context), "path_sensitive_calculation", "A pasta contém cálculo dependente do caminho; finalização em destino novo ainda não foi comprovada para esse caso.")
        check_calculation_sources(workbook, context)
    return _calculate(session, calculation)


def ensure_pivot_source_ready(workbook, session, context, *, cache=None, source=None):
    """Calcula uma origem com fórmulas antes de criar/alterar o cache solicitado.

    Somente a política de cálculo explícita do pedido autoriza essa etapa.
    Sem ela, não usar cache antigo para apresentar uma mutação como atualizada.
    """
    if source is None:
        if cache is None or int(cache.SourceType) != 1:
            return
        source = _pivot_source_range(workbook, cache, context)
        require(source is not None, "pivot_source_unverified", "Não foi possível resolver a origem local do pivô para comprovar seu cálculo.")
    if source.HasFormula is False:
        return
    calculation = getattr(session, "calculation_options", None)
    require(isinstance(calculation, dict) and calculation.get("mode", "none") != "none", "pivot_calculation_required", "O pivô depende de fórmulas; autorize cálculo no pedido antes de atualizar sua origem.")
    _calculate_checked(workbook, session, calculation, context)
    if cache is not None:
        cache.Refresh()


def _calculate(session, calculation):
    mode = calculation.get("mode", "none")
    if mode == "none":
        return {"status": "not_requested"}
    getattr(session.application, {"incremental": "Calculate", "full": "CalculateFull", "full_rebuild": "CalculateFullRebuild"}[mode])()
    session.wait_calculation()
    return {"status": "completed", "mode": mode, "state": int(session.application.CalculationState)}


def _reject_new_formula_errors(before, after, arguments):
    if arguments.get("validation", {}).get("reject_new_formula_errors", True):
        baseline = {(item["sheet"], item["address"], item["error"]) for item in before or []}
        require(all((item["sheet"], item["address"], item["error"]) in baseline for item in after),
                "formula_validation_failed", "A pasta passou a conter erros de fórmula novos; o resultado não será publicado.")


def _path_sensitive(workbook, context):
    total = 0
    pattern = re.compile(r'\bCELL\s*\(\s*"filename"|\bINFO\s*\(\s*"directory"', re.I)
    for sheet in _collection(workbook.Worksheets):
        for _, _, formula in formula_records(sheet, context):
            total += 1
            require(total <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A inspeção de fórmulas excede o limite de células.")
            if pattern.search(formula):
                return True
    if any(pattern.search(str(item.RefersTo)) for item in _collection(workbook.Names)):
        return True
    return False


def _patch_calculation(path, source_info, calculation):
    """Restaura flags OOXML sem ligar cálculo automático no Excel aberto."""
    if path.suffix.lower() not in {".xlsx", ".xlsm", ".xltx", ".xltm", ".xlam"}:
        require(calculation.get("output_mode", "preserve") == "manual", "calculation_mode_unverified", "Neste formato, solicite output_mode=manual; preservar modo original ainda não foi comprovado.")
        return
    with zipfile.ZipFile(path) as archive:
        content = archive.read("xl/workbook.xml")
        workbook = ET.fromstring(content)
        namespace = workbook.tag.split("}", 1)[0].removeprefix("{")
        tag = "{" + namespace + "}calcPr"
        properties = workbook.find(tag)
        if properties is None:
            properties = ET.SubElement(workbook, tag)
        original = source_info.get("calc_properties", {})
        output_mode = calculation.get("output_mode", "preserve")
        for key in ("calcMode", "calcOnSave"):
            if output_mode == "preserve":
                if key in original:
                    properties.set(key, original[key])
                else:
                    properties.attrib.pop(key, None)
            else:
                properties.set(key, ("manual" if output_mode == "manual" else "auto") if key == "calcMode" else "0")
        if calculation.get("calculate_on_open") is not None:
            properties.set("fullCalcOnLoad", "1" if calculation["calculate_on_open"] else "0")
            properties.set("forceFullCalc", "1" if calculation["calculate_on_open"] else "0")
        # ElementTree would rename extension namespace prefixes and can leave
        # mc:Ignorable references dangling. Replace only the calcPr element.
        match = re.search(rb"<(?:\w+:)?calcPr\b[^>]*(?:/>|>.*?</(?:\w+:)?calcPr>)", content, flags=re.S)
        root_tag = re.search(rb"<((?:[A-Za-z_][\w.-]*:)?workbook)\b", content)
        require(root_tag is not None, "preservation_unverified", "A parte workbook não possui uma tag raiz reconhecível.")
        prefix = root_tag.group(1)[:-len(b"workbook")]
        if match:
            prefix_match = re.match(rb"<((?:[A-Za-z_][\w.-]*:)?)(?:calcPr)\b", match.group())
            prefix = prefix_match.group(1)
        element = b"<" + prefix + ("calcPr " + " ".join(key + "=" + quoteattr(value) for key, value in properties.attrib.items()) + "/>").encode("utf-8")
        if match:
            patched = content[:match.start()] + element + content[match.end():]
        else:
            closing = b"</" + root_tag.group(1) + b">"
            patched = content.replace(closing, element + closing)
            require(patched != content, "preservation_unverified", "Não foi possível restaurar as flags de cálculo OOXML sem normalizar o documento.")
        temporary = path.with_name(path.name + ".calculation.tmp")
        with zipfile.ZipFile(temporary, "w") as output:
            for item in archive.infolist():
                output.writestr(item, patched if item.filename == "xl/workbook.xml" else archive.read(item.filename))
    temporary.replace(path)


def _save(workbook, context, args, source_info, calculation, session):
    destination = args["output_path"]
    suffix = Path(destination).suffix.lower().lstrip(".")
    require(suffix in FORMAT_IDS, "unsupported_format", "O formato de saída nativo não é suportado.")
    source_format = source_info.get("format")
    if source_format not in FORMAT_IDS and args.get("input_path"):
        source_format = Path(args["input_path"]).suffix.lower().lstrip(".")
    if args.get("input_path") and source_format != suffix:
        require(args.get("allow_feature_loss") is True, "conversion_required", "Mudança de formato exige excel.export com allow_feature_loss=true e perdas explícitas.")
    require(not workbook.HasVBProject or suffix in {"xlsm", "xlsb", "xls", "xltm", "xlam", "xla", "xlt"} or args.get("allow_feature_loss") is True,
            "feature_loss_blocked", "O formato de destino remove VBA; autorize a perda explicitamente ou preserve o formato.")
    staged = context.stage_output(destination)
    encrypted = source_info.get("format") == "encrypted_ooxml"
    require(not encrypted or calculation.get("output_mode", "preserve") == "manual",
            "calculation_mode_unverified", "Saída criptografada exige output_mode=manual para impedir cálculo extra durante o salvamento.")
    require(not encrypted or not getattr(session, "deferred_views", []), "capability_unavailable", "A finalização de visualização OOXML não está disponível em saída criptografada.")
    file_format = 61 if suffix == "xlsx" and int(workbook.FileFormat) == 61 else FORMAT_IDS[suffix]
    import pythoncom
    missing = pythoncom.Missing
    password = context.resolve_password(args.get("password_ref")) if source_info.get("format") == "encrypted_ooxml" else missing
    session.application.EnableEvents = False
    session.application.DisplayAlerts = False
    session.application.Calculation = -4135
    session.application.CalculateBeforeSave = False
    workbook.SaveAs(str(staged), file_format, password, missing, False, False, 1, 2, False, missing, missing, False)
    workbook.Close(SaveChanges=False)
    if not encrypted:
        _patch_calculation(staged, source_info, calculation)
    from .advanced_operations import finalize_advanced
    finalize_advanced(staged, session, context)
    return staged


def _export(workbook, args, context):
    from .advanced_operations import export_advanced
    advanced = export_advanced(workbook, args, context)
    if advanced is not None:
        return advanced
    format_name = args["format"]
    owner = worksheet(workbook, args["sheet"]) if args.get("sheet") else workbook
    staged = context.stage_output(args["output_path"])
    if format_name == "pdf":
        import pythoncom
        owner.ExportAsFixedFormat(0, str(staged), 0, True, False, pythoncom.Missing, pythoncom.Missing, False)
    elif format_name in {"csv", "tsv"}:
        require(args.get("sheet") or workbook.Worksheets.Count == 1, "sheet_required", "Exportação tabular exige aba explícita quando a pasta contém várias abas.")
        sheet = owner if args.get("sheet") else workbook.Worksheets(1)
        values = sheet.UsedRange.Value2
        if not isinstance(values, tuple):
            values = ((values,),)
        require(sheet.UsedRange.CountLarge <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A exportação excede o limite de células.")
        exported = []
        area = sheet.UsedRange
        for row_index, row in enumerate(values, 1):
            converted = []
            for column_index, value in enumerate(row, 1):
                if isinstance(value, int) and not isinstance(value, bool) and -2146827000 <= value <= -2146820000:
                    cell = area.Cells(row_index, column_index)
                    if sheet.Application.WorksheetFunction.IsError(cell):
                        value = ERROR_CODES.get(value & 0xffff, str(cell.Text))
                converted.append(value)
            exported.append(converted)
        with staged.open("w", newline="", encoding=args.get("encoding", "utf-8-sig")) as stream:
            writer = csv.writer(stream, delimiter=args.get("delimiter", "\t" if format_name == "tsv" else ","))
            writer.writerows(exported)
    else:
        require(False, "unsupported_format", "Conversão de pasta deve passar pela finalização nativa.")
    return {"format": format_name, "losses": ["Somente valores da aba selecionada; fórmulas e objetos não fazem parte do texto tabular."] if format_name in {"csv", "tsv"} else []}


def execute_native(operation: str, arguments: dict, context) -> dict:
    """Executa operação nativa em cópia, calcula e salva resultados verificáveis.

    Erros preservam a fonte e impedem publicação. Requer permissões específicas
    para escrita, VBA, fontes externas e adaptador de objetos. O supervisor
    impõe deadline mesmo se uma chamada COM não devolver controle.
    """
    args = dict(arguments)
    deferred_export = operation == "export" and args.get("format") not in FORMAT_IDS
    require_preservation = args.get("validation", {}).get("require_preservation", True)
    writing = operation in WRITING and not (operation == "edit_vba" and args.get("action") == "export") and not (operation == "export" and args.get("format") not in FORMAT_IDS)
    require(operation not in WRITING or context.profile.get("allow_write", False) is True, "write_denied", "O perfil não autoriza produzir arquivos Excel ou exportações.")
    source_info, source, snapshot = {}, None, None
    preservation_before = None
    if operation == "create" and args.get("template_path"):
        source = context.input_path(args["template_path"])
    elif operation not in {"create", "native_probe"}:
        source = context.input_path(args["input_path"])
    if source is not None:
        source_info = preflight(source, context.profile, context.defaults)
        require(source_info["native_open_allowed"], "active_content_blocked", "O preflight não comprovou abertura nativa permitida para este arquivo.")
        from ...document_processing.paths import file_digest
        snapshot = file_digest(source)
        require(not args.get("snapshot_id") or args["snapshot_id"] == snapshot, "snapshot_changed", "O arquivo mudou desde a página anterior.")
        if writing and source_info.get("has_signatures") is not False:
            require(context.profile.get("allow_signature_changes") is True and args.get("allow_signature_invalidation") is True,
                    "signature_change_blocked", "Editar arquivo assinado exige perfil e aceite explícito da invalidação de assinatura.")
        if writing:
            from ..component.preservation import capture_preservation
            preservation_before = capture_preservation(source, context.defaults)
    calculation = dict(args.get("calculation", {}))
    if operation == "recalculate":
        calculation.setdefault("mode", "full_rebuild")
    if source_info.get("path_sensitive_formulas") and operation == "run_macro":
        raise ExcelExceptions("path_sensitive_calculation", "A pasta contém cálculo dependente do caminho; finalização em destino novo ainda não foi comprovada para esse caso.")
    if calculation.get("refresh_connections"):
        require(context.profile.get("allow_refresh") is True, "external_data_denied", "O perfil não autoriza atualizar fontes externas.")
    if operation in WRITING and not (operation == "edit_vba" and args.get("action") == "export"):
        require("output_path" in args, "output_required", "Informe um destino novo para a pasta resultante.")
    if operation == "edit_vba" and args.get("action") == "export":
        require("output_module_path" in args, "output_required", "Informe o destino do módulo exportado.")
    before_errors = None
    vba_before = None
    session = ExcelSession(context, allow_macros=operation == "run_macro" or calculation.get("enable_vba_udfs", False))
    try:
        with session:
            session.calculation_options = calculation
            environment = {"backend": "excel_com", "version": str(session.application.Version), "build": str(session.application.Build)}
            if operation == "native_probe":
                return session.probe()
            if operation == "create" and source is None:
                workbook = session.application.Workbooks.Add(-4167)
            else:
                copied = _copy_workbook(source, source_info, context)
                workbook = session.open(copied, context.resolve_password(args.get("password_ref")), template_mode="instantiate" if operation == "create" else "edit")
            if preservation_before is not None and source_info.get("has_vba") and calculation.get("enable_vba_udfs"):
                vba_before = _capture_vba_semantics(workbook, context)
            if writing or deferred_export:
                before_errors = _native_errors(workbook, context)
            context.progress(1, 4, "Excel aberto em cópia exclusiva")
            result = {}
            if operation == "inspect":
                result = _inventory(workbook)
                if args.get("include_cells"):
                    snapshot_cells = _value_snapshot(workbook, context)
                    for sheet_info in result["sheets"]:
                        sheet = worksheet(workbook, sheet_info["name"])
                        sheet_info["cells"] = [_native_cell(sheet.Range(address)) for address in snapshot_cells[sheet_info["name"]]]
            elif operation == "navigate":
                result = _navigate(workbook, args, context)
            elif operation == "read_range":
                result = _read(workbook, args, context, snapshot)
            elif operation == "search":
                result = _search(workbook, args, context, snapshot)
            elif operation == "analyze_formulas":
                result = _analyze(workbook, args, context, snapshot)
            elif operation == "validate":
                result = _validate_native(workbook, args, session, context)
            elif operation == "compare":
                other_source = context.input_path(args["other_path"])
                other_info = preflight(other_source, context.profile, context.defaults)
                require(other_info["native_open_allowed"], "active_content_blocked", "A outra pasta não permite abertura nativa.")
                other_copy = _copy_workbook(other_source, other_info, context)
                other = session.open(other_copy, context.resolve_password(args.get("password_ref")))
                result = _compare_native(workbook, other, args, context)
                other.Close(SaveChanges=False)
            elif operation in {"create", "apply_changes"}:
                operations = args.get("operations", [])
                require(len(operations) <= context.defaults.get("max_operations", 1000), "operation_limit_exceeded", "O lote excede o limite de operações configurado.")
                for index, item in enumerate(operations):
                    apply_operation(workbook, item, session, context)
                    context.progress(index + 1, len(operations), "Aplicando alterações Excel")
                result = {"operations_applied": len(operations)}
            elif operation in {"get_vba_source", "edit_vba"}:
                result = _vba(workbook, operation, args, context)
            elif operation == "run_macro":
                procedure = args["procedure"]
                require(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?", procedure) is not None,
                        "invalid_procedure", "Informe apenas módulo.procedimento da pasta selecionada.")
                result = {"macros": {"status": "completed", "procedure": procedure,
                                     "result": portable(session.application.Run("'" + workbook.Name.replace("'", "''") + "'!" + procedure, *args.get("args", [])))}}
            elif operation in {"describe_object", "get_object", "set_object", "call_object"}:
                result = _object(workbook, operation, args, context, session)
            elif operation == "refresh":
                result = {"connections": [], "pivots": []}
            elif operation == "export" and args["format"] not in FORMAT_IDS:
                result = {"format": args["format"]}
            elif operation not in {"recalculate", "export"}:
                raise ExcelExceptions("operation_unavailable", "A operação não é implementada pelo backend nativo.")
            before_pivots = None
            refresh_requested = operation == "refresh" or calculation.get("refresh_connections")
            pending_pivots = getattr(session, "pending_pivot_refresh", None)
            pending_pivots = pending_pivots if isinstance(pending_pivots, set) else set()
            if refresh_requested or pending_pivots:
                refresh_args = args if operation == "refresh" else {
                    "require_verified": calculation.get("refresh_require_verified", True),
                    "pivots": calculation.get("refresh_pivots", True)}
                if refresh_requested:
                    result.update(_refresh(workbook, refresh_args, session, context))
                targets = _pivot_targets(workbook)
                if not refresh_requested:
                    targets = [(sheet, pivot) for sheet, pivot in targets if (str(sheet.Name), str(pivot.Name)) in pending_pivots]
                has_pivots = refresh_args.get("pivots", True) and bool(targets)
                if has_pivots and calculation.get("mode", "none") != "none":
                    before_pivots = _calculate_checked(workbook, session, calculation, context)
                result["pivots"] = _refresh_pivots(workbook, refresh_args, session, context,
                    sources_calculated=before_pivots is not None and before_pivots["status"] == "completed", targets=targets)
            result["calculation"] = _calculate_checked(workbook, session, calculation, context)
            if refresh_requested or pending_pivots:
                _verify_final_pivot_sources(workbook, refresh_args, session, context)
            if before_pivots is not None:
                result["calculation"]["before_pivots"] = before_pivots
            session.assert_hidden()
            if deferred_export:
                _reject_new_formula_errors(before_errors, _native_errors(workbook, context), args)
                result.update(_export(workbook, args, context))
                result["freshness"] = "calculated" if result["calculation"]["status"] == "completed" else "saved_cache"
            if writing:
                errors = _native_errors(workbook, context)
                expected_values = _value_snapshot(workbook, context)
                expected_objects = _preservation_inventory(workbook, context)
                vba_after = _capture_vba_semantics(workbook, context) if vba_before is not None else None
                _reject_new_formula_errors(before_errors, errors, args)
                staged = _save(workbook, context, args, source_info, calculation, session)
                saved_info = preflight(staged, context.profile, context.defaults)
                require(saved_info["native_open_allowed"], "active_content_blocked", "A cópia produzida não passou no preflight antes da verificação nativa.")
                check = session.open(staged, context.resolve_password(args.get("password_ref")) if saved_info.get("format") == "encrypted_ooxml" else None)
                require(_value_snapshot(check, context) == expected_values, "persistence_validation_failed", "Valores ou fórmulas divergiram ao reabrir a pasta sem cálculo.")
                native_objects_preserved = _same_native_objects(expected_objects, _preservation_inventory(check, context))
                require(native_objects_preserved or require_preservation is False,
                        "preservation_validation_failed", "O inventário de objetos divergiu após salvar/reabrir; a cópia não será publicada.")
                result["persisted_cache"] = {"status": "completed", "reopened_without_calculation": True,
                                             "verification": "native_reopen", "native_objects_preserved": native_objects_preserved,
                                             "geometry_tolerance_points": 0.375,
                                             "formula_errors": _native_errors(check, context)}
                if vba_before is not None:
                    vba_reopened = _capture_vba_semantics(check, context)
                    preservation_before["_native_vba_verification"] = _verify_vba_semantics(vba_before, vba_after, vba_reopened,
                        preservation_before.get("snapshot_id"), saved_info.get("snapshot_id"))
                check.Close(SaveChanges=False)
                if saved_info.get("format") in {"xlsx", "xlsm", "xltx", "xltm", "xlam"}:
                    result["persisted_cache"]["verification"] = "native_reopen_and_package"
                    result["persisted_cache"]["checked_cells"] = _verify_package_values(staged, expected_values, context)
                    persisted_errors = formula_errors(staged, context.defaults)
                    require(len(persisted_errors) == len(errors), "persistence_validation_failed", "Os erros de fórmula persistidos divergem do estado validado.")
                if preservation_before is not None:
                    from ..component.preservation import validate_preservation
                    preservation = validate_preservation(preservation_before, staged, operation, args, context.defaults)
                    require(preservation["status"] != "failed", "preservation_validation_failed", "A cópia perdeu ou alterou conteúdo fora do envelope solicitado.")
                    require(preservation["status"] == "completed" or require_preservation is False,
                            "preservation_unverified", "A preservação integral não foi comprovada; a cópia não será publicada sem política explícita.")
                else:
                    preservation = {"status": "completed", "baseline": "new_workbook", "findings": []}
                result["preservation"] = {**preservation, "native_objects_preserved": native_objects_preserved}
            result.update(environment=environment, snapshot_id=snapshot)
            evidence = getattr(session, "operation_evidence", None)
            if isinstance(evidence, list):
                result["operation_evidence"] = evidence
            context.progress(4, 4, "Operação Excel validada")
            return result
    except ExcelExceptions:
        raise
    except Exception as error:
        if session._visible.is_set():
            raise ExcelExceptions("ui_required", "O Excel solicitou interface; a execução oculta foi interrompida.") from error
        raise ExcelExceptions("native_operation_failed", "O Excel recusou a operação; confira alvo, argumentos, proteção e pré-requisitos da capacidade.") from error
