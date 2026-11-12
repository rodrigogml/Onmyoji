"""Evidências de preservação entre fonte e saída, com envelope de alterações.

Normalização XML, IDs de relações e nomes físicos de partes não definem perda.
O componente compara conteúdo conhecido semanticamente e conserva bytes de
conteúdo opaco não autorizado. Evidência insuficiente retorna ``unverified``.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from .ooxml import (
    SpreadsheetPackage, attribute, cell_coordinates, child, column_name, in_range,
    inspect_package, local_name, range_coordinates,
)


_BOOLS = {"hidden", "customWidth", "bestFit", "customHeight", "collapsed", "thickBot", "thickTop", "customFormat",
          "applyNumberFormat", "applyFont", "applyFill", "applyBorder", "applyAlignment", "applyProtection",
          "wrapText", "shrinkToFit", "locked", "hidden", "diagonalUp", "diagonalDown", "outline", "pivotButton", "quotePrefix",
          "allowBlank", "showDropDown", "showInputMessage", "showErrorMessage", "stopIfTrue", "showFirstColumn", "showLastColumn",
          "showRowStripes", "showColumnStripes", "showGridLines", "showRowColHeaders", "showZeros", "rightToLeft"}
_DEFAULTS = {"applyNumberFormat": "0", "applyFont": "0", "applyFill": "0", "applyBorder": "0", "applyAlignment": "0", "applyProtection": "0",
             "hidden": "0", "customWidth": "0", "bestFit": "0", "customHeight": "0", "collapsed": "0", "customFormat": "0",
             "wrapText": "0", "shrinkToFit": "0", "diagonalUp": "0", "diagonalDown": "0", "pivotButton": "0", "quotePrefix": "0"}
_CACHES = {"numCache", "strCache", "multiLvlStrCache"}
_KNOWN_PARTS = {"workbook", "worksheet", "styles", "strings", "table", "bookkeeping"}
_CELL_KINDS = {"set_values", "set_formula", "clear", "copy_range", "autofill", "goal_seek", "data_table", "hyperlink"}
_STRUCTURAL_KINDS = {"insert_range", "delete_range", "sort", "subtotal", "table"}
_DYNAMIC_ARRAY_METADATA = '''<metadata xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:xda="http://schemas.microsoft.com/office/spreadsheetml/2017/dynamicarray">
<metadataTypes count="1"><metadataType name="XLDAPR" minSupportedVersion="120000" copy="1" pasteAll="1" pasteValues="1" merge="1" splitFirst="1" rowColShift="1" clearFormats="1" clearComments="1" assign="1" coerce="1" cellMeta="1"/></metadataTypes>
<futureMetadata name="XLDAPR" count="1"><bk><extLst><ext uri="{bdbb8cdc-fa1e-496e-a857-3c3f30c029c3}"><xda:dynamicArrayProperties fDynamic="1" fCollapsed="0"/></ext></extLst></bk></futureMetadata>
<cellMetadata count="1"><bk><rc t="1" v="0"/></bk></cellMetadata></metadata>'''


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _family(name: str, content_type: str) -> str:
    lowered, content = name.casefold(), content_type.casefold()
    if lowered == "[content_types].xml" or lowered.endswith(".rels") or lowered.startswith("docprops/") or "calcchain" in lowered:
        return "bookkeeping"
    if "vbaprojectsignature" in lowered or lowered.startswith("_xmlsignatures/") or "digital-signature" in content:
        return "signature"
    if "vbaproject" in lowered or "vbaproject" in content:
        return "vba"
    if "sheet.main+xml" in content or "template.main+xml" in content or "sheet.macroenabled.main+xml" in content or "template.macroenabled.main+xml" in content or "addin.macroenabled.main+xml" in content:
        return "workbook"
    if ".worksheet+xml" in content:
        return "worksheet"
    if lowered.endswith("/styles.xml") or ".styles+xml" in content:
        return "styles"
    if lowered.endswith("/sharedstrings.xml") or ".sharedstrings+xml" in content:
        return "strings"
    if ".table+xml" in content:
        return "table"
    if "pivotcache" in content or "/pivotcache/" in lowered:
        return "pivot_cache"
    if "pivottable" in content or "/pivottables/" in lowered:
        return "pivot"
    if "threadedcomment" in content or "threadedcomments" in lowered:
        return "threaded_comments"
    if ".comments+xml" in content or re.search(r"/comments\d*\.xml$", lowered):
        return "comments"
    if "person" in content or "/persons/" in lowered:
        return "persons"
    if "timeline" in content or "timeline" in lowered:
        return "timeline"
    if "slicer" in content or "slicer" in lowered:
        return "slicer"
    if "model+data" in content or "/model/" in lowered:
        return "model"
    if "querytable" in content or "/querytables/" in lowered or "mashup" in content or "/customdata/" in lowered:
        return "query"
    if "connection" in content or lowered.endswith("/connections.xml"):
        return "connection"
    if lowered.startswith("customxml/"):
        return "custom_xml"
    if content.startswith("image/") or "/media/" in lowered:
        return "image"
    if "chart" in content or "/charts/" in lowered:
        return "chart"
    if "drawing" in content or "/drawings/" in lowered:
        return "drawing"
    if "printersettings" in content or "/printersettings/" in lowered:
        return "printer"
    if "activex" in content or "/activex/" in lowered or "ctrlprop" in lowered:
        return "control"
    if "oleobject" in content or "/embeddings/" in lowered:
        return "ole"
    if "theme" in content or "/theme/" in lowered:
        return "theme"
    return "unknown"


def _canonical(node, relationship=None, *, drop=(), depth=0):
    if node is None:
        return None
    if depth > 128:
        return {"opaque_subtree_sha256": hashlib.sha256(ET.tostring(node, encoding="utf-8")).hexdigest()}
    name = local_name(node.tag)
    attributes = {}
    for key, value in node.attrib.items():
        field = local_name(key)
        namespace = key.partition("}")[0]
        if field in drop or field == "Ignorable" or field in {"uid", "dyDescent", "creationId"} and "microsoft" in namespace:
            continue
        if name == "cNvPr" and field == "id":
            continue
        if field in {"id", "embed", "link"} and "relationships" in namespace and relationship:
            value = relationship(value)
        elif field in _BOOLS:
            value = "1" if value.casefold() in {"1", "true", "on"} else "0"
        if _DEFAULTS.get(field) == value:
            continue
        if field == "rgb":
            value = value.upper()
        attributes[key] = value
    if name in {"b", "i", "strike", "outline", "shadow", "condense", "extend"}:
        value = node.get("val", "1").casefold()
        if value in {"0", "false"}:
            return None
        attributes.pop("val", None)
    text = node.text
    if text is not None and not text.strip() and name not in {"t", "f", "formula1", "formula2"}:
        text = None
    descendants = [_canonical(item, relationship, drop=drop, depth=depth + 1) for item in node if local_name(item.tag) not in _CACHES]
    descendants = [item for item in descendants if item is not None]
    return {"tag": node.tag, "attributes": attributes, "text": text, "children": descendants}


def _ranges(value: str) -> tuple:
    result = set()
    for reference in (value or "").split():
        try:
            result.add(range_coordinates(reference.replace("$", "").upper()))
        except Exception:
            return (value,)
    # Equivalent adjacent ranges are merged without expanding whole columns.
    changed = True
    while changed:
        changed, merged = False, []
        for area in sorted(result):
            for index, previous in enumerate(merged):
                if area[1] == previous[1] and area[3] == previous[3] and area[0] <= previous[2] + 1 and previous[0] <= area[2] + 1:
                    merged[index] = (min(area[0], previous[0]), area[1], max(area[2], previous[2]), area[3]); changed = True; break
                if area[0] == previous[0] and area[2] == previous[2] and area[1] <= previous[3] + 1 and previous[1] <= area[3] + 1:
                    merged[index] = (area[0], min(area[1], previous[1]), area[2], max(area[3], previous[3])); changed = True; break
            else:
                merged.append(area)
        result = set(merged)
    return tuple(sorted(result))


def _view_signature(node):
    # Excel can update the active cell/scroll while ShowDetail creates a sheet.
    # They are transient navigation state; freeze geometry, zoom and display
    # options remain part of the preservation contract.
    result = _canonical(node, drop={"tabSelected", "workbookViewId", "topLeftCell", "activePane"})
    def discard_selection(value):
        if value is None:
            return
        value["children"] = [item for item in value["children"] if local_name(item["tag"]) != "selection"]
        for item in value["children"]:
            discard_selection(item)
    discard_selection(result)
    return result


def _cache_dirty_signature(root, relationship):
    value = _canonical(root, relationship)
    marker = "{http://schemas.microsoft.com/office/spreadsheetml/2026/pivotDataSourceClean}dataSourceClean"
    def discard_clean_flag(node):
        result = []
        for descendant in node["children"]:
            if descendant["tag"] == marker and not descendant["attributes"] and not descendant["children"] and descendant["text"] in {"0", "1"}:
                continue
            discard_clean_flag(descendant)
            if local_name(descendant["tag"]) == "ext" and descendant["attributes"].get("uri") == "{F4336242-56EE-4DD3-A5F7-E900642BF6E9}" and not descendant["children"]:
                continue
            result.append(descendant)
        node["children"] = result
    discard_clean_flag(value)
    return _hash(value)


def _dynamic_array_formula_cells(package, part):
    # MS-XLSX Metadata/CT_DynamicArrayProperties: recognize only the complete
    # single-record XLDAPR structure observed for Formula2. Other metadata,
    # rich values and future extensions remain opaque and must be reviewed.
    related = [item for item in package.relationships.get(package.workbook_part, [])
               if not item["external"] and item["type"].endswith("/sheetMetadata")]
    if len(related) != 1 or related[0]["target"] != part or package.relationships.get(part):
        return []
    if _canonical(package.xml(part)) != _canonical(ET.fromstring(_DYNAMIC_ARRAY_METADATA)):
        return []
    result = []
    for sheet in package.sheets:
        if sheet["kind"].casefold() != "worksheet":
            continue
        for node in package.xml(sheet["part"]).iter():
            if local_name(node.tag) != "c":
                continue
            if node.get("vm", "0") != "0":
                return []
            if node.get("cm", "0") == "0":
                continue
            formula = child(node, "f")
            if node.get("cm") != "1" or formula is None or formula.get("t") != "array":
                return []
            try:
                if not in_range(node.get("r", ""), range_coordinates(formula.get("ref"))):
                    return []
            except ExcelExceptions:
                return []
            result.append({"sheet": sheet["name"], "address": node.get("r")})
    return result


def _style_signatures(package):
    part = next((item["target"] for item in package.relationships.get(package.workbook_part, []) if item["type"].endswith("/styles")), None)
    if not part:
        return [None], {}
    root = package.xml(part)
    groups = {local_name(item.tag): list(item) for item in root}
    formats = {int(item.get("numFmtId")): item.get("formatCode") for item in groups.get("numFmts", [])}

    def definition(identifier, group):
        values = groups.get(group, [])
        try:
            return _canonical(values[int(identifier)])
        except (ValueError, IndexError, TypeError):
            return None

    def xf(node, *, base_allowed=True):
        attributes = {key: ("1" if value.casefold() in {"1", "true", "on"} else "0") if local_name(key) in _BOOLS else value for key, value in node.attrib.items()}
        value = {"font": definition(attributes.pop("fontId", "0"), "fonts"),
                 "fill": definition(attributes.pop("fillId", "0"), "fills"),
                 "border": definition(attributes.pop("borderId", "0"), "borders")}
        identifier = int(attributes.pop("numFmtId", "0"))
        value["number_format"] = formats.get(identifier, next((item["number_format"] for item in package.styles() if item["num_fmt_id"] == identifier), str(identifier)))
        base = attributes.pop("xfId", None)
        base_values = groups.get("cellStyleXfs", [])
        value["base"] = xf(base_values[int(base)], base_allowed=False) if base_allowed and base is not None and int(base) < len(base_values) else None
        value["attributes"] = {key: val for key, val in attributes.items() if _DEFAULTS.get(local_name(key)) != val}
        value["children"] = [_canonical(item) for item in node]
        if value["base"] is not None:
            # Excel can replace a pivot's explicit General override with the
            # identical inherited format on SaveAs. An apply flag is redundant
            # only when both definitions are demonstrably the same.
            for flag, component in (("applyNumberFormat", "number_format"), ("applyFont", "font"),
                                    ("applyFill", "fill"), ("applyBorder", "border")):
                if value[component] == value["base"][component]:
                    value["attributes"].pop(flag, None)
            for flag, component in (("applyAlignment", "alignment"), ("applyProtection", "protection")):
                own = [item for item in value["children"] if item and local_name(item["tag"]) == component]
                inherited = [item for item in value["base"]["children"] if item and local_name(item["tag"]) == component]
                if own == inherited:
                    value["attributes"].pop(flag, None)
        return value

    signatures = [_hash(xf(item)) for item in groups.get("cellXfs", [])]
    named = {}
    for item in groups.get("cellStyles", []):
        attributes = {key: value for key, value in item.attrib.items() if key not in {"xfId"}}
        index = int(item.get("xfId", "0"))
        bases = groups.get("cellStyleXfs", [])
        named[item.get("name", "")] = {"attributes": attributes, "style": xf(bases[index], base_allowed=False) if index < len(bases) else None}
    return signatures or [None], named


def _capture_xml(path, limits):
    with SpreadsheetPackage(path, limits) as package:
        inventory = package.inventory()
        manifest = {item["name"]: item for item in inventory["package_manifest"]}
        # A type-102 model connection identifies a worksheet range through a
        # hidden defined name (MS-XLSX CT_RangePr.sourceName). Excel can rename
        # this alias while retaining its definition and connection identity.
        # Resolve only such referenced hidden aliases; ordinary names retain
        # their exact identity and cannot disappear through this normalization.
        definitions = {item["name"]: item for item in inventory["defined_names"] if item["scope"] == "workbook"}
        connection_names = {}
        for part, item in manifest.items():
            if _family(part, item["content_type"]) != "connection" or not part.casefold().endswith(".xml"):
                continue
            for connection in package.xml(part):
                if local_name(connection.tag) != "connection" or connection.get("type") != "102" or not connection.get("name"):
                    continue
                for node in connection.iter():
                    identifier = node.get("sourceName") if local_name(node.tag) == "rangePr" else None
                    definition = definitions.get(identifier, {})
                    if identifier and identifier.startswith("_xlcn.") and definition.get("hidden") is True:
                        connection_names[identifier] = {"connection": connection.get("name"),
                            "definition": {key: value for key, value in definition.items() if key != "name"}}
        owners = defaultdict(set)
        for sheet in package.sheets:
            pending, seen = [sheet["part"]], set()
            while pending:
                part = pending.pop()
                if part in seen:
                    continue
                seen.add(part); owners[part].add(sheet["name"])
                pending.extend(item["target"] for item in package.relationships.get(part, []) if not item["external"])
        semantic_cache = {}

        def semantic(part, seen=()):
            if part in semantic_cache:
                return semantic_cache[part]
            item = manifest.get(part)
            if item is None:
                return "missing"
            if part in seen:
                return "cycle:" + item["content_type"]
            is_xml = part.casefold().endswith((".xml", ".rels", ".vml")) or item["content_type"].endswith("+xml")
            if not is_xml:
                return item["sha256"]
            relations = {relation["id"]: relation for relation in package.relationships.get(part, [])}
            def reference(identifier):
                relation = relations.get(identifier)
                if relation is None:
                    return {"unresolved_relationship": identifier}
                return {"type": relation["type"], "external": relation["external"],
                        "target": relation["target"] if relation["external"] else semantic(relation["target"], (*seen, part))}
            root = package.xml(part)
            family = _family(part, item["content_type"])
            value = _canonical(root, reference, drop={"cacheId"} if family == "pivot" else ())
            if family == "connection":
                def resolve_model_range(node):
                    if local_name(node["tag"]) == "rangePr":
                        identifier = node["attributes"].get("sourceName")
                        if identifier in connection_names:
                            node["attributes"]["sourceName"] = connection_names[identifier]
                    for descendant in node["children"]:
                        resolve_model_range(descendant)
                resolve_model_range(value)
            semantic_cache[part] = _hash(value)
            return semantic_cache[part]

        styles, named_styles = _style_signatures(package)
        known_sheets, objects = {}, {}
        for sheet in package.sheets:
            state = {"kind": sheet["kind"], "state": sheet["state"]}
            if sheet["kind"].casefold() == "worksheet":
                root = package.xml(sheet["part"])
                metadata = package.sheet_metadata(sheet["name"])
                array_ranges = []
                for node in root.iter():
                    if local_name(node.tag) != "c":
                        continue
                    formula = child(node, "f")
                    if formula is not None and formula.get("t") == "array" and formula.get("ref"):
                        try:
                            array_ranges.append((node.get("r"), range_coordinates(formula.get("ref"))))
                        except Exception:
                            pass
                validation = []
                for item in metadata["data_validations"]:
                    value = {key: val for key, val in item.items() if key not in {"sqref", "showErrorMessage", "showInputMessage", "showDropDown", "allowBlank", "errorStyle", "operator", "type"}}
                    value.update(type=item.get("type", "none"), operator=item.get("operator", "between"), errorStyle=item.get("errorStyle", "stop"),
                                 sqref=_ranges(item.get("sqref", "")))
                    value.update({key: item.get(key, "0").casefold() in {"1", "true"} for key in ("showErrorMessage", "showInputMessage", "showDropDown", "allowBlank")})
                    validation.append(value)
                cells = {}
                for address, item in package.cells(sheet["name"]).items():
                    style = styles[item["style_index"]] if item["style_index"] < len(styles) else None
                    if item["type"] != "blank" or style != styles[0]:
                        cells[address] = {"type": item["type"], "value": item["value"], "formula": item["formula"], "cached_value": item["cached_value"],
                                          "style": style, "default_style": style == styles[0],
                                          "array_cache": item["formula"] is None and any(address != anchor and in_range(address, area) for anchor, area in array_ranges)}
                state.update(cells=cells, merges=_ranges(" ".join(metadata["merged_ranges"])), validations=sorted(validation, key=_hash),
                             hyperlink_ranges=[item.get("ref", "") for item in root.iter() if local_name(item.tag) == "hyperlink"],
                             conditional_formats=[_canonical(item) for item in root if local_name(item.tag) == "conditionalFormatting"],
                             auto_filter=metadata["auto_filter"], protection=metadata["protection"],
                             columns=_canonical(child(root, "cols")), sheet_format=_canonical(child(root, "sheetFormatPr")),
                             row_layout=[{key: value for key, value in row.attrib.items() if key in {"r", "ht", "customHeight", "hidden", "outlineLevel", "collapsed"}} for row in (list(child(root, "sheetData")) if child(root, "sheetData") is not None else []) if any(key in row.attrib for key in ("ht", "hidden", "outlineLevel", "collapsed"))],
                             page=[_canonical(item) for item in root if local_name(item.tag) in {"pageMargins", "pageSetup", "headerFooter", "rowBreaks", "colBreaks", "printOptions"}],
                             views=[_view_signature(item) for item in root if local_name(item.tag) == "sheetViews"])
            known_sheets[sheet["name"]] = state
        parts, pivot_ranges = [], []
        for part, item in manifest.items():
            family = _family(part, item["content_type"])
            entry = {**item, "family": family, "owners": sorted(owners.get(part, [])),
                     "semantic_hash": semantic(part) if family not in _KNOWN_PARTS else None}
            if item["content_type"].casefold() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheetmetadata+xml":
                entry["dynamic_array_formula_cells"] = _dynamic_array_formula_cells(package, part)
            if family == "pivot_cache" and part.casefold().endswith(".xml"):
                root = package.xml(part)
                if local_name(root.tag) == "pivotCacheDefinition":
                    cache_source = child(root, "cacheSource")
                    source = child(cache_source, "worksheetSource") if cache_source is not None else None
                    if source is not None and source.get("sheet") and source.get("ref") and not any(local_name(key) == "id" for key in source.attrib):
                        entry["pivot_source"] = {"sheet": source.get("sheet"), "range": source.get("ref")}
                        relations = {relation["id"]: relation for relation in package.relationships.get(part, [])}
                        def cache_reference(identifier):
                            relation = relations.get(identifier)
                            if relation is None:
                                return {"unresolved_relationship": identifier}
                            return {"type": relation["type"], "external": relation["external"],
                                    "target": relation["target"] if relation["external"] else semantic(relation["target"])}
                        entry["source_dirty_metadata_hash"] = _cache_dirty_signature(root, cache_reference)
            parts.append(entry)
            if family == "pivot" and part.casefold().endswith(".xml"):
                root = package.xml(part)
                location = child(root, "location")
                if root.get("name") and location is not None and location.get("ref"):
                    for owner in entry["owners"]:
                        pivot_ranges.append({"sheet": owner, "name": root.get("name"), "range": location.get("ref")})
            if family == "drawing" and part.casefold().endswith(".xml"):
                root = package.xml(part)
                object_relations = {relation["id"]: relation for relation in package.relationships.get(part, [])}
                def object_reference(identifier):
                    relation = object_relations.get(identifier)
                    if relation is None:
                        return {"unresolved_relationship": identifier}
                    return {"type": relation["type"], "external": relation["external"],
                            "target": relation["target"] if relation["external"] else semantic(relation["target"])}
                for anchor in root:
                    properties = next((node for node in anchor.iter() if local_name(node.tag) == "cNvPr" and node.get("name")), None)
                    if properties is None:
                        continue
                    for owner in entry["owners"]:
                        objects[owner + "\0" + properties.get("name")] = {"name": properties.get("name"), "sheet": owner,
                            "kind": "chart" if any(local_name(node.tag) == "chart" for node in anchor.iter()) else "shape",
                            "signature": _hash(_canonical(anchor, object_reference, drop={"id"}))}
        tables = {}
        for item in inventory["tables"]:
            root = package.xml(item["part"])
            tables[item["sheet"] + "\0" + item["name"]] = {"sheet": item["sheet"], "name": item["name"],
                "definition": _canonical(root, drop={"id"})}
        return {"format": inventory["format"], "snapshot_id": inventory["snapshot_id"], "has_manifest": True,
                "manifest": manifest, "parts": parts, "sheets": known_sheets, "tables": tables,
                "names": inventory["defined_names"], "connection_names": connection_names, "named_styles": named_styles, "objects": objects, "pivot_ranges": pivot_ranges,
                "features": inventory["features"], "has_vba": inventory["has_vba"], "has_xlm": inventory["has_xlm"], "has_signatures": inventory["has_signatures"],
                "date_system": inventory["date_system"], "workbook_protection": _canonical(child(package.workbook, "workbookProtection")),
                "external_relationships": inventory["external_relationships"], "limitations": []}


def capture_preservation(path: Path, limits: dict | None = None) -> dict:
    """Captura evidência estrutural sem abrir Excel nem modificar a fonte.

    Formatos sem um manifesto XML observável retornam evidência parcial; não
    transformam ausência de parser em alegação de preservação comprovada.
    """
    inventory = inspect_package(Path(path), limits)
    if inventory["format"] in {"xls", "xlsb", "encrypted_ooxml"}:
        return {"format": inventory["format"], "snapshot_id": inventory["snapshot_id"], "has_manifest": False,
                "manifest": {item["name"]: item for item in inventory.get("package_manifest", [])}, "parts": [],
                "sheets": {}, "tables": {}, "names": [], "objects": {}, "features": inventory.get("features", {}),
                "has_vba": inventory.get("has_vba"), "has_signatures": inventory.get("has_signatures"),
                "limitations": ["A inspeção binária/criptografada não permite certificar preservação integral de conteúdo opaco."]}
    return _capture_xml(Path(path), limits)


def _envelope(operation: str, arguments: dict):
    result = {"all": operation == "run_macro", "global_cells": False, "global_styles": False, "ranges": [], "styles": [], "families": set(),
              "deleted_sheets": set(), "renames": {}, "sheet_fields": defaultdict(set), "names": set(), "tables": set(), "objects": set(), "new_sheets": False}
    if operation == "edit_vba":
        result["families"].update({"vba", "signature"})
    if operation == "refresh" or arguments.get("calculation", {}).get("refresh_connections"):
        result["global_cells"] = True
        result["families"].update({"query", "custom_xml", "connection", "model", "pivot", "pivot_cache", "chart", "slicer", "timeline"})
    operations = list(arguments.get("operations", []))
    if operation in {"set_object", "call_object"}:
        selector = arguments.get("selector", [])
        sheet = next((item.get("name") for item in selector if item.get("collection") in {"Worksheets", "Sheets"}), None)
        reference = next((item.get("name") for item in selector if item.get("collection") == "Range"), None)
        member = arguments.get("member")
        if sheet and reference:
            operations.append({"type": "format_range" if member in {"NumberFormat", "RowHeight", "ColumnWidth", "WrapText"} else "set_values", "sheet": sheet, "range": reference})
        else:
            mapping = {"Queries": "query", "Connections": "connection", "ModelMeasures": "model", "ModelRelationships": "model", "PivotFields": "pivot", "PivotTables": "pivot", "ChartObjects": "chart", "Shapes": "drawing", "Slicers": "slicer", "SlicerCaches": "slicer"}
            for item in selector:
                if item.get("collection") in mapping:
                    result["families"].add(mapping[item["collection"]])
            if member == "Name" and selector and selector[-1].get("collection") in {"Sheets", "Worksheets"} and sheet:
                result["renames"][sheet] = arguments.get("value")
    for item in operations:
        kind, action, sheet = item["type"], item.get("action"), item.get("sheet")
        if kind in {"pivot", "pivot_source", "pivot_field", "pivot_calculated_field", "pivot_group", "pivot_drilldown"}:
            result["global_cells"] = True
            result["global_styles"] = True
            result["families"].update({"pivot", "pivot_cache", "slicer", "timeline", "chart", "drawing"})
            if kind == "pivot_drilldown":
                result["new_sheets"] = True
        if kind == "advanced_filter":
            result["sheet_fields"][sheet].update({"row_layout", "auto_filter"})
            if action == "copy":
                try:
                    first_row, first_column, last_row, last_column = range_coordinates(item["range"])
                    top, left, _, _ = range_coordinates(item["copy_range"])
                    reference = f"{column_name(left)}{top}:{column_name(left + last_column - first_column)}{top + last_row - first_row}"
                except Exception:
                    reference = item.get("copy_range")
                result["ranges"].append((item.get("copy_sheet", sheet), reference))
        if kind in _CELL_KINDS:
            target_sheet = item.get("target_sheet", sheet) if kind == "copy_range" else sheet
            reference = item.get("target_range", item.get("range"))
            if kind == "autofill":
                reference = item["destination"]
            elif kind == "goal_seek":
                reference = item["changing_range"]
            elif kind == "set_values" and item.get("values"):
                try:
                    top, left, bottom, right = range_coordinates(reference)
                    if top == bottom and left == right:
                        reference = f"{column_name(left)}{top}:{column_name(left + len(item['values'][0]) - 1)}{top + len(item['values']) - 1}"
                except Exception:
                    pass
            elif kind == "copy_range":
                try:
                    top, left, _, _ = range_coordinates(reference)
                    first_row, first_column, last_row, last_column = range_coordinates(item["range"])
                    rows, columns = last_row - first_row + 1, last_column - first_column + 1
                    if item.get("transpose"):
                        rows, columns = columns, rows
                    reference = f"{column_name(left)}{top}:{column_name(left + columns - 1)}{top + rows - 1}"
                except Exception:
                    pass
            result["ranges"].append((target_sheet, reference))
        if kind == "format_range" or kind == "copy_range" and item.get("mode", "all") in {"all", "formats"}:
            result["styles"].append((item.get("target_sheet", sheet), item.get("target_range", item.get("range"))))
        if kind in _STRUCTURAL_KINDS or kind in {"scenario", "copy_sheet_from"}:
            result["global_cells"] = True
        if kind in {"insert_range", "delete_range", "sort", "subtotal"}:
            result["global_styles"] = True
        if kind in {"set_formula", "copy_range", "copy_sheet_from", "name", "hyperlink", "clear"}:
            result["families"].add("external_links")
        if kind == "sheet":
            result["new_sheets"] |= action in {"add", "copy"}
            if action == "rename":
                result["renames"][sheet] = item["name"]
            elif action == "delete":
                result["deleted_sheets"].add(sheet)
            elif action == "visibility":
                result["sheet_fields"][sheet].add("state")
            elif action == "move":
                pass
            result["global_cells"] = True
        if kind == "copy_sheet_from":
            result["new_sheets"] = True
        if kind == "name":
            result["names"].add((sheet or "workbook", item["name"]))
        if kind == "table":
            result["tables"].add((sheet, item["name"]))
            result["sheet_fields"][sheet].add("auto_filter")
        if kind in {"validation", "conditional_format", "filter", "merge", "unmerge", "resize", "outline", "view", "page_setup", "protect"}:
            fields = {"validation": {"validations"}, "conditional_format": {"conditional_formats"}, "filter": {"auto_filter"}, "merge": {"merges"}, "unmerge": {"merges"},
                      "resize": {"columns", "row_layout", "sheet_format"}, "outline": {"columns", "row_layout", "sheet_format"}, "view": {"views"}, "page_setup": {"page"}, "protect": {"protection"}}
            result["sheet_fields"][sheet].update(fields[kind])
        if kind in {"insert_range", "delete_range", "subtotal"}:
            result["sheet_fields"][sheet].update({"columns", "row_layout", "sheet_format", "merges", "validations", "conditional_formats", "auto_filter"})
        if kind in {"chart", "shape", "image", "control"}:
            result["objects"].add((sheet, item["name"]))
        family_changes = {"chart": {"chart", "drawing"}, "shape": {"drawing"}, "image": {"drawing", "image"}, "control": {"drawing", "control"},
                          "sparkline": set(), "pivot": {"pivot", "pivot_cache", "slicer", "chart"}, "slicer": {"slicer", "timeline", "drawing"},
                          "timeline_filter": {"slicer", "timeline"}, "query": {"query", "custom_xml", "connection"}, "query_load": {"query", "custom_xml", "connection", "model", "table"},
                          "connection": {"connection", "model", "query", "custom_xml"}, "model_measure": {"model"}, "model_relationship": {"model"},
                          "comment": {"comments", "threaded_comments", "persons", "drawing"}, "hyperlink": {"drawing"}, "page_setup": {"printer"}}
        result["families"].update(family_changes.get(kind, set()))
        if kind == "clear" and item.get("what", "contents") == "all":
            result["styles"].append((sheet, item["range"]))
            result["sheet_fields"][sheet].update({"validations", "conditional_formats"})
            result["families"].update({"comments", "threaded_comments", "persons", "drawing"})
    return result


def _renamed(name, envelope):
    seen = set()
    while name in envelope["renames"] and name not in seen:
        seen.add(name); name = envelope["renames"][name]
    return name


def _matches(sheet, address, ranges, envelope, before):
    for candidate_sheet, reference in ranges:
        if not reference or _renamed(candidate_sheet, envelope) != _renamed(sheet, envelope):
            continue
        try:
            area = range_coordinates(reference)
        except Exception:
            table = next((item for item in before["tables"].values() if item["name"] == reference and _renamed(item["sheet"], envelope) == _renamed(sheet, envelope)), None)
            if table:
                reference = table["definition"]["attributes"].get("ref", "")
            else:
                definition = next((item for item in before["names"] if item["name"] == reference and item["scope"] in {sheet, "workbook"}), None)
                reference = definition["formula"].split("!")[-1] if definition else ""
            try:
                area = range_coordinates(reference)
            except Exception:
                continue
        if in_range(address, area):
            return True
    return False


def _pivot_column_ranges(before, after, operation, arguments):
    kinds = {"pivot", "pivot_source", "pivot_field", "pivot_calculated_field", "pivot_group", "pivot_drilldown"}
    targets = {(item.get("sheet"), item.get("name")) for item in arguments.get("operations", []) if item.get("type") in kinds}
    refresh = operation == "refresh" and arguments.get("pivots", True)
    result = defaultdict(set)
    for snapshot in (before, after):
        for item in snapshot.get("pivot_ranges", []):
            if refresh or (item["sheet"], item["name"]) in targets:
                try:
                    _, first, _, last = range_coordinates(item["range"])
                    result[item["sheet"]].update(range(first, last + 1))
                except ExcelExceptions:
                    continue
    return result


def _column_geometry_equal(before, after, mutable_columns):
    def projection(value):
        result = {}
        if value is None:
            return result
        for node in value["children"]:
            if local_name(node["tag"]) != "col":
                return {"unknown_column_content": value}
            attributes = node["attributes"]
            try:
                first, last = int(attributes["min"]), int(attributes["max"])
                if not 1 <= first <= last <= 16384:
                    return {"invalid_column_content": value}
            except (KeyError, ValueError, TypeError):
                return {"invalid_column_content": value}
            for column in range(first, last + 1):
                mutable = {"width", "bestFit", "customWidth"} if column in mutable_columns else set()
                values = {key: val for key, val in attributes.items() if key not in {"min", "max"} | mutable}
                if values or node["children"] or node["text"]:
                    result[column] = {"attributes": values, "children": node["children"], "text": node["text"]}
        return result
    return projection(before) == projection(after)


def _pivot_source_was_edited(part, envelope):
    source = part.get("pivot_source")
    if not source:
        return False
    if envelope["global_cells"]:
        return True
    try:
        top, left, bottom, right = range_coordinates(source["range"])
    except ExcelExceptions:
        return False
    for sheet, reference in envelope["ranges"]:
        if _renamed(sheet, envelope) != _renamed(source["sheet"], envelope):
            continue
        try:
            first_row, first_column, last_row, last_column = range_coordinates(reference)
        except ExcelExceptions:
            continue
        if first_row <= bottom and last_row >= top and first_column <= right and last_column >= left:
            return True
    return False


def _vba_compilation_evidence(before, after):
    evidence = before.get("_native_vba_verification", {})
    if (evidence.get("source_snapshot_id") != before.get("snapshot_id")
            or evidence.get("output_snapshot_id") != after.get("snapshot_id")):
        return "unverified"
    if evidence.get("status") == "failed":
        return "failed"
    if evidence.get("status") == "completed" and all(evidence.get(key) is True for key in ("modules_verified", "references_verified", "design_verified")):
        return "completed"
    return "unverified"


def _table_geometry_ranges(before, after, arguments, envelope):
    targets = {(str(item.get("sheet", "")).casefold(), str(item.get("name", "")).casefold())
               for item in arguments.get("operations", []) if item.get("type") == "table"}
    result = defaultdict(set)
    for snapshot in (before, after):
        for table in snapshot["tables"].values():
            if (table["sheet"].casefold(), table["name"].casefold()) not in targets:
                continue
            reference = table["definition"]["attributes"].get("ref")
            try:
                first_row, _, last_row, _ = range_coordinates(reference)
            except ExcelExceptions:
                continue
            envelope["styles"].append((_renamed(table["sheet"], envelope), reference))
            result[_renamed(table["sheet"], envelope)].update(range(first_row, last_row + 1))
    return result


def _row_geometry_equal(before, after, mutable_rows, hidden_rows=()):
    def projection(values):
        result = []
        for item in values:
            try:
                row = int(item["r"])
            except (KeyError, ValueError, TypeError):
                return {"unresolved_rows": values}
            mutable = ({"ht", "customHeight"} if row in mutable_rows else set()) | ({"hidden"} if row in hidden_rows else set())
            attributes = {key: value for key, value in item.items() if key not in mutable}
            if set(attributes) != {"r"}:
                result.append(attributes)
        return result
    return projection(before) == projection(after)


def _filter_hidden_rows(arguments, envelope):
    result = defaultdict(set)
    for item in arguments.get("operations", []):
        if item.get("type") != "filter":
            continue
        try:
            first, _, last, _ = range_coordinates(item.get("range"))
        except ExcelExceptions:
            continue
        result[_renamed(item.get("sheet"), envelope)].update(range(first + 1, last + 1))
    return result


def _last_hyperlink_was_deleted(before, after, arguments, envelope):
    if any(sheet.get("hyperlink_ranges") for sheet in after["sheets"].values()):
        return False
    deletes = [item for item in arguments.get("operations", []) if item.get("type") == "hyperlink" and item.get("action") == "delete"]
    found = False
    for name, sheet in before["sheets"].items():
        for reference in sheet.get("hyperlink_ranges", []):
            found = True
            if name in envelope["deleted_sheets"]:
                continue
            try:
                top, left, bottom, right = range_coordinates(reference)
            except ExcelExceptions:
                return False
            covered = False
            for item in deletes:
                if _renamed(item.get("sheet"), envelope) != _renamed(name, envelope):
                    continue
                try:
                    first_row, first_column, last_row, last_column = range_coordinates(item.get("range"))
                except ExcelExceptions:
                    continue
                covered |= first_row <= top and first_column <= left and last_row >= bottom and last_column >= right
            if not covered:
                return False
    return found and bool(deletes)


def validate_preservation(before: dict, after_path: Path, operation: str, arguments: dict, limits: dict | None = None) -> dict:
    """Compara a saída com a fonte respeitando apenas alterações autorizadas.

    Perda conhecida não autorizada retorna ``failed``. Mudança opaca sem prova
    retorna ``unverified``. O chamador decide publicação segundo sua política;
    ``completed`` comprova somente as verificações discriminadas no relatório.
    """
    after = capture_preservation(after_path, limits)
    findings, changes = [], []
    before_manifest, after_manifest = before.get("manifest", {}), after.get("manifest", {})
    for name in sorted(set(before_manifest) | set(after_manifest)):
        left, right = before_manifest.get(name), after_manifest.get(name)
        if (left or {}).get("sha256") != (right or {}).get("sha256"):
            changes.append({"part": name, "before_sha256": (left or {}).get("sha256"), "after_sha256": (right or {}).get("sha256"),
                            "kind": "added" if left is None else "removed" if right is None else "changed"})
    def report(code, severity, **values):
        findings.append({"code": code, "severity": severity, **values})
    if not before.get("has_manifest") or not after.get("has_manifest"):
        report("binary_preservation_unverified", "unverified", message="A comparação nativa complementar não certifica bytes/semântica de todo conteúdo binário.")
        return {"status": "unverified", "findings": findings, "manifest_changes": changes, "checked": ["container"], "limitations": before.get("limitations", []) + after.get("limitations", [])}
    envelope = _envelope(operation, arguments)
    pivot_columns = _pivot_column_ranges(before, after, operation, arguments)
    table_rows = _table_geometry_ranges(before, after, arguments, envelope)
    filter_rows = _filter_hidden_rows(arguments, envelope)
    if envelope["all"]:
        report("macro_envelope_authorized", "note", message="O procedimento solicitado pode alterar toda a pasta; a preservação é aferida fora desse envelope amplo, sem provar a lógica do VBA.")
    allow_loss = operation == "export" and arguments.get("allow_feature_loss") is True
    if before.get("has_vba") and not after.get("has_vba") and not (allow_loss or envelope["all"] or "vba" in envelope["families"]):
        report("vba_removed", "failed")
    if not before.get("has_vba") and after.get("has_vba") and not (envelope["all"] or "vba" in envelope["families"]):
        report("vba_added", "failed")
    if not before.get("has_xlm") and after.get("has_xlm"):
        report("xlm_added", "failed")
    if not envelope["all"] and before.get("date_system") != after.get("date_system") and not (operation == "set_object" and arguments.get("member") == "Date1904"):
        report("date_system_changed", "failed")
    workbook_protection_allowed = any(item.get("type") == "protect" and "sheet" not in item for item in arguments.get("operations", []))
    if not envelope["all"] and not workbook_protection_allowed and before.get("workbook_protection") != after.get("workbook_protection"):
        report("workbook_protection_changed", "failed")
    expected_sheets = set()
    for original_name, source in before["sheets"].items():
        name = _renamed(original_name, envelope)
        if original_name in envelope["deleted_sheets"] or name in envelope["deleted_sheets"] or envelope["all"]:
            continue
        expected_sheets.add(name)
        target = after["sheets"].get(name)
        if target is None:
            report("sheet_removed", "failed", sheet=original_name); continue
        for field in ("kind", "state", "merges", "validations", "conditional_formats", "auto_filter", "protection", "columns", "sheet_format", "row_layout", "page", "views"):
            permitted = field in envelope["sheet_fields"].get(original_name, set()) or field in envelope["sheet_fields"].get(name, set())
            if field == "columns" and pivot_columns.get(name) and _column_geometry_equal(source.get(field), target.get(field), pivot_columns[name]):
                permitted = True
            if field == "row_layout" and (table_rows.get(name) or filter_rows.get(name)) and _row_geometry_equal(source.get(field, []), target.get(field, []), table_rows.get(name, ()), filter_rows.get(name, ())):
                permitted = True
            if field in source and source[field] != target.get(field) and not permitted:
                removed = bool(source[field]) and not target.get(field)
                report("sheet_property_removed" if removed else "sheet_property_changed", "failed" if removed else "unverified", sheet=name, field=field)
        for address, cell in source.get("cells", {}).items():
            current = target.get("cells", {}).get(address)
            mutable = envelope["global_cells"] or _matches(name, address, envelope["ranges"], envelope, before)
            formatted = envelope["global_styles"] or _matches(name, address, envelope["ranges"], envelope, before) or _matches(name, address, envelope["styles"], envelope, before)
            if current is None:
                if cell.get("array_cache"):
                    if not cell.get("default_style") and not formatted:
                        report("cell_style_changed", "unverified", sheet=name, address=address)
                    continue
                if not mutable and (cell["type"] != "blank" or cell.get("style")):
                    report("cell_removed", "failed", sheet=name, address=address)
                continue
            fields = ("type", "value", "formula")
            cache_only = cell.get("array_cache") and (current.get("array_cache") or current["type"] == "blank")
            cache_became_input = cell.get("array_cache") and not current.get("array_cache") and current["type"] != "blank"
            if not mutable and not cache_only and (cache_became_input or any(cell[key] != current[key] for key in fields)):
                report("cell_content_changed", "failed", sheet=name, address=address)
            if cell.get("style") is not None and cell["style"] != current.get("style") and not formatted:
                report("cell_style_changed", "unverified", sheet=name, address=address)
    if not envelope["all"] and not envelope["new_sheets"]:
        for name in after["sheets"]:
            if name not in expected_sheets:
                report("sheet_added", "failed", sheet=name)
    for key, table in before["tables"].items():
        name, sheet = table["name"], _renamed(table["sheet"], envelope)
        if envelope["all"] or "table" in envelope["families"] or table["sheet"] in envelope["deleted_sheets"] or (sheet, name) in envelope["tables"] or (table["sheet"], name) in envelope["tables"]:
            continue
        target = after["tables"].get(sheet + "\0" + name)
        if target is None:
            report("table_removed", "failed", sheet=sheet, name=name)
        elif table["definition"] != target["definition"] and not envelope["global_cells"]:
            report("table_changed", "unverified", sheet=sheet, name=name)
    if not envelope["all"] and not envelope["global_cells"]:
        current_names = {(item["scope"], item["name"]): item for item in after["names"]}
        for definition in before["names"]:
            scope = _renamed(definition["scope"], envelope)
            if (scope, definition["name"]) in envelope["names"]:
                continue
            target = current_names.get((scope, definition["name"]))
            alias = before.get("connection_names", {}).get(definition["name"]) if scope == "workbook" else None
            if target is None and alias and alias in after.get("connection_names", {}).values():
                continue
            if target is None:
                report("defined_name_removed", "failed", name=definition["name"], scope=scope)
            elif definition != target:
                report("defined_name_changed", "unverified", name=definition["name"], scope=scope)
    if not envelope["all"] and before.get("named_styles") != after.get("named_styles"):
        original_styles = dict(before.get("named_styles", {}))
        if _last_hyperlink_was_deleted(before, after, arguments, envelope):
            original_styles = {name: definition for name, definition in original_styles.items()
                               if name in after.get("named_styles", {}) or definition["attributes"].get("builtinId") != "8"}
        if original_styles != after.get("named_styles"):
            report("named_styles_changed", "unverified")
    if not envelope["all"] and "external_links" not in envelope["families"] and not ({"connection", "query"} & envelope["families"]):
        def links(snapshot):
            return Counter((item["type"], item["target"]) for item in snapshot.get("external_relationships", []))
        if links(before) != links(after):
            report("external_relationships_changed", "failed")
    for item in before["objects"].values():
        sheet, name = _renamed(item["sheet"], envelope), item["name"]
        if envelope["all"] or item["sheet"] in envelope["deleted_sheets"] or (sheet, name) in envelope["objects"] or (item["sheet"], name) in envelope["objects"]:
            continue
        current = after["objects"].get(sheet + "\0" + name)
        if current is None:
            report("visual_object_removed", "failed", sheet=sheet, name=name)
        elif item["signature"] != current["signature"] and item["kind"] not in envelope["families"]:
            report("visual_object_changed", "unverified", sheet=sheet, name=name)
    # Compare unedited resources by semantic payload/type/owner rather than
    # physical names. A package can renumber media/chart parts during SaveAs.
    available = list(after["parts"])
    for original in before["parts"]:
        family = original["family"]
        owners = {_renamed(name, envelope) for name in original["owners"]}
        owned_deleted = original["owners"] and all(name in envelope["deleted_sheets"] or _renamed(name, envelope) in envelope["deleted_sheets"] for name in original["owners"])
        if family in _KNOWN_PARTS or envelope["all"] or family in envelope["families"] or owned_deleted:
            continue
        if family == "signature" and arguments.get("allow_signature_invalidation"):
            continue
        def matching_owners(item):
            current = set(item["owners"])
            return current == owners or envelope["new_sheets"] and owners <= current and (current - owners) <= (set(after["sheets"]) - expected_sheets)
        candidates = [item for item in available if item["family"] == family and item["content_type"] == original["content_type"] and matching_owners(item)]
        matching = next((item for item in candidates if (item["sha256"] == original["sha256"] if family == "vba" else item["semantic_hash"] == original["semantic_hash"])), None)
        if matching is None and family == "pivot_cache" and original.get("source_dirty_metadata_hash") and _pivot_source_was_edited(original, envelope):
            matching = next((item for item in candidates if item.get("source_dirty_metadata_hash") == original["source_dirty_metadata_hash"]), None)
        if matching:
            available.remove(matching)
            continue
        if allow_loss:
            report("authorized_conversion_loss", "note", family=family, part=original["name"])
        elif not candidates:
            report("resource_removed", "failed", family=family, part=original["name"])
        else:
            if family == "vba" and arguments.get("calculation", {}).get("enable_vba_udfs") is True:
                proof = _vba_compilation_evidence(before, after)
                if proof == "completed":
                    report("vba_compiled_cache_normalized", "note", family=family, part=original["name"],
                           message="O binário mudou durante cálculo UDF; fonte, referências e desenho foram conferidos antes e após persistência pelo backend nativo.")
                elif proof == "unverified":
                    report("vba_compilation_preservation_unverified", "unverified", family=family, part=original["name"],
                           message="O cálculo UDF pode recompilar VBA; a igualdade da fonte, referências e desenho não foi comprovada para este par de arquivos.")
                else:
                    report("vba_bytes_changed", "failed", family=family, part=original["name"])
                available.remove(candidates[0])
                continue
            report("vba_bytes_changed" if family == "vba" else "opaque_resource_changed", "failed" if family == "vba" else "unverified", family=family, part=original["name"])
            available.remove(candidates[0])
    for item in available:
        if item["family"] in _KNOWN_PARTS or envelope["all"] or item["family"] in envelope["families"] or item["name"] in before_manifest:
            continue
        if item["owners"] and all(name not in expected_sheets for name in item["owners"]) and envelope["new_sheets"]:
            continue
        dynamic_cells = item.get("dynamic_array_formula_cells")
        calculated = arguments.get("calculation", {}).get("mode", "none") in {"incremental", "full", "full_rebuild"}
        if dynamic_cells and all(calculated or envelope["global_cells"] or _matches(cell["sheet"], cell["address"], envelope["ranges"], envelope, before) for cell in dynamic_cells):
            report("dynamic_array_metadata_added", "note", part=item["name"],
                   message="Metadados XLDAPR reconhecidos e vinculados somente a fórmulas afetadas ou calculadas pelo pedido.")
            continue
        report("new_resource_unverified", "unverified", family=item["family"], part=item["name"])
    severity = {item["severity"] for item in findings}
    return {"status": "failed" if "failed" in severity else "unverified" if "unverified" in severity else "completed",
            "findings": findings, "manifest_changes": changes,
            "checked": ["sheet_inventory", "known_cell_content", "cell_styles", "defined_names", "tables", "visual_object_inventory", "unedited_resource_payloads"],
            "limitations": ["A prova cobre o conteúdo discriminado e o envelope solicitado; não certifica equivalência visual nem semântica completa de extensões não interpretadas.",
                            "Caches de gráficos e metadados operacionais podem ser normalizados pelo Excel."]}
