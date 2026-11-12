"""Leitura navegável, comparação e auditoria de snapshots Excel salvos.

O backend direto não calcula fórmulas, acessa conexões nem executa VBA. Os
resultados distinguem verificações conclusivas de regras que exigem Excel.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re

from ..component.ooxml import (
    SpreadsheetPackage, cell_coordinates, column_name, in_range, inspect_package,
    range_coordinates,
)
from ..exception import ExcelExceptions, require


READ_FIELDS = {"value", "formula", "cached_value", "display_text", "type", "number_format", "error", "date_value"}
DIRECT_OPERATIONS = {"inspect", "navigate", "read_range", "search", "analyze_formulas", "validate", "compare"}


def _pagination(arguments: dict) -> tuple[int, int]:
    offset, limit = arguments.get("offset", 0), arguments.get("limit", 100)
    require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 1000,
            "invalid_pagination", "Use offset não negativo e limit entre 1 e 1000.")
    return offset, limit


def _snapshot(path, expected: str | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(data)
    identifier = digest.hexdigest()
    require(expected is None or expected == identifier, "snapshot_changed", "A fonte mudou; reinicie a leitura paginada com o novo snapshot.")
    return identifier


def _page(items: list, arguments: dict, **metadata) -> dict:
    offset, limit = _pagination(arguments)
    return {**metadata, "offset": offset, "limit": limit, "total": len(items),
            "next_offset": offset + limit if offset + limit < len(items) else None,
            "items": items[offset:offset + limit]}


def _blank(address: str, metadata: dict | None = None) -> dict:
    row, column = cell_coordinates(address)
    metadata = metadata or {}
    return {"address": address, "row": row, "column": column, "type": "blank", "value": None,
            "formula": None, "cached_value": None, "raw_value": None, "cached_type": None,
            "error": None, "style_index": 0, "number_format": "General", "date_value": None,
            "display_text": None, "freshness": "unknown", "hidden_row": row in metadata.get("hidden_rows", []),
            "hidden_column": any(first <= column <= last for first, last in metadata.get("hidden_columns", []))}


def _resolve_range(package: SpreadsheetPackage, sheet_name: str, reference: str) -> tuple[str, tuple[int, int, int, int]]:
    try:
        return reference, range_coordinates(reference)
    except ExcelExceptions as error:
        if error.code != "invalid_range":
            raise
    # A simple table/name can be resolved without evaluating an Excel expression.
    tables = package.tables()
    for table in tables:
        if table["sheet"].casefold() == sheet_name.casefold() and reference.casefold() in {table.get("name", "").casefold(), table.get("displayName", "").casefold()}:
            return table["ref"], range_coordinates(table["ref"])
    definitions = [item for item in package.names_defined() if item["name"].casefold() == reference.casefold() and item["scope"].casefold() in {"workbook", sheet_name.casefold()}]
    definitions.sort(key=lambda item: item["scope"] == "workbook")
    if definitions:
        formula = definitions[0]["formula"].removeprefix("=")
        match = re.fullmatch(r"(?:'((?:[^']|'')+)'|([^'!]+))!(\$?[A-Za-z]{1,3}\$?[1-9]\d*(?::\$?[A-Za-z]{1,3}\$?[1-9]\d*)?)", formula)
        if match:
            referenced_sheet = (match.group(1) or match.group(2)).replace("''", "'")
            require(referenced_sheet.casefold() == sheet_name.casefold(), "invalid_range", "O nome definido pertence a outra aba.")
            return match.group(3), range_coordinates(match.group(3))
    raise ExcelExceptions("direct_read_unavailable", "O backend direto exige A1, nome estático ou nome simples de tabela; use Excel para outras referências.")


def _selected_cells(package: SpreadsheetPackage, arguments: dict, *, include_empty: bool = False):
    selected = [package.sheet(arguments["sheet"])] if arguments.get("sheet") else list(package.sheets)
    for sheet in selected:
        if sheet["kind"].casefold() != "worksheet":
            continue
        cells = package.cells(sheet["name"])
        area = None
        if arguments.get("range"):
            _, area = _resolve_range(package, sheet["name"], arguments["range"])
        if include_empty and area:
            count = (area[2] - area[0] + 1) * (area[3] - area[1] + 1)
            require(count <= package.limits["max_cells"], "range_too_large", "O intervalo excede o limite de células; selecione blocos menores.")
            metadata = package.sheet_metadata(sheet["name"])
            for row in range(area[0], area[2] + 1):
                for column in range(area[1], area[3] + 1):
                    address = f"{column_name(column)}{row}"
                    yield sheet["name"], cells.get(address) or _blank(address, metadata)
        else:
            for entry in sorted(cells.values(), key=lambda item: (item["row"], item["column"])):
                if area is None or in_range(entry["address"], area):
                    yield sheet["name"], entry


def _read_range(package: SpreadsheetPackage, arguments: dict, snapshot: str) -> dict:
    require(isinstance(arguments.get("sheet"), str) and isinstance(arguments.get("range"), str), "invalid_argument", "Informe aba e intervalo para leitura.")
    offset, limit = _pagination(arguments)
    fields = arguments.get("fields")
    require(fields is None or isinstance(fields, list) and fields and all(isinstance(field, str) for field in fields)
            and len(set(fields)) == len(fields) and set(fields) <= READ_FIELDS,
            "invalid_argument", "Os campos solicitados não pertencem ao contrato de leitura.")
    package.sheet(arguments["sheet"])
    records, total = [], 0
    for sheet, entry in _selected_cells(package, arguments, include_empty=arguments.get("include_empty", True)):
        if not arguments.get("include_empty", True) and entry["type"] == "blank":
            continue
        if offset <= total < offset + limit:
            selected = dict(entry) if fields is None else {"address": entry["address"], "row": entry["row"], "column": entry["column"], **{field: entry.get(field) for field in fields}}
            selected["sheet"] = sheet
            records.append(selected)
        total += 1
    return {"backend": "direct", "snapshot_id": snapshot, "freshness": "saved_cache", "sheet": package.sheet(arguments["sheet"])["name"],
            "range": arguments["range"], "offset": offset, "limit": limit, "total": total,
            "next_offset": offset + limit if offset + limit < total else None, "cells": records,
            "limitations": ["Cálculo e renderização não executados; display_text não está disponível no backend direto."]}


def _navigate(package: SpreadsheetPackage, arguments: dict, snapshot: str) -> dict:
    kind = arguments.get("kind", "sheets")
    selected = package.sheet(arguments["sheet"])["name"] if arguments.get("sheet") else None
    if kind == "sheets":
        items = [dict(sheet) for sheet in package.sheets if selected is None or selected == sheet["name"]]
    elif kind == "names":
        items = [item for item in package.names_defined() if selected is None or item["scope"] in {"workbook", selected}]
    elif kind == "tables":
        items = [item for item in package.tables() if selected is None or item["sheet"] == selected]
    elif kind == "regions":
        inventory = package.inventory()
        items = [{"sheet": item["name"], "data_range": item.get("data_range"), "merged_ranges": item.get("merged_ranges", []), "auto_filter": item.get("auto_filter")}
                 for item in inventory["sheets"] if selected is None or item["name"] == selected]
    elif kind == "objects":
        items = []
        sheets = [item for item in package.sheets if selected is None or item["name"] == selected]
        for sheet in sheets:
            for relation in package.relationships.get(sheet["part"], []):
                items.append({"sheet": sheet["name"], "kind": relation["type"].rsplit("/", 1)[-1], "part": relation["target"], "external": relation["external"]})
    elif kind in {"pivots", "charts", "queries", "connections", "vba"}:
        raise ExcelExceptions("direct_read_unavailable", "Essa categoria exige inventário do modelo de objetos no backend Excel.")
    else:
        raise ExcelExceptions("invalid_argument", "Categoria de navegação inválida.")
    return {"backend": "direct", "snapshot_id": snapshot, "kind": kind, "items": items, "total": len(items)}


def _search(package: SpreadsheetPackage, arguments: dict, snapshot: str) -> dict:
    query = arguments.get("query")
    require(isinstance(query, str) and 0 < len(query) <= 10000, "invalid_argument", "Informe um texto de busca com até 10000 caracteres.")
    scope = arguments.get("scope", "all")
    require(scope in {"values", "formulas", "all"}, "invalid_argument", "Escopo de busca inválido.")
    flags = 0 if arguments.get("case_sensitive", False) else re.I
    try:
        matcher = re.compile(query if arguments.get("regex", False) else re.escape(query), flags)
    except re.error as error:
        raise ExcelExceptions("invalid_regex", "A expressão regular de busca está inválida.") from error
    offset, limit = _pagination(arguments)
    matches, total = [], 0
    for sheet, entry in _selected_cells(package, arguments):
        values = []
        if scope in {"values", "all"}:
            value = entry["cached_value"] if entry["type"] == "formula" else entry["value"]
            if value is not None:
                values.append(("cached_value" if entry["type"] == "formula" else "value", str(value)))
        if scope in {"formulas", "all"} and entry["formula"] is not None:
            values.append(("formula", entry["formula"]))
        for field, value in values:
            match = matcher.search(value)
            if match is not None:
                if offset <= total < offset + limit:
                    matches.append({"sheet": sheet, "address": entry["address"], "field": field,
                                    "value": value, "match_start": match.start(), "match_end": match.end()})
                total += 1
    return {"backend": "direct", "snapshot_id": snapshot, "freshness": "saved_cache", "matches": matches,
            "offset": offset, "limit": limit, "total": total, "next_offset": offset + limit if offset + limit < total else None}


def _analyze(package: SpreadsheetPackage, arguments: dict, snapshot: str) -> dict:
    entries = []
    counts = Counter()
    for sheet, cell in _selected_cells(package, arguments):
        if cell["type"] != "formula":
            continue
        formula = cell["formula"]
        source = re.sub(r'"(?:[^\"]|\"\")*"', '""', formula or "")
        unresolved = []
        if cell.get("formula_status") == "shared_unexpanded":
            unresolved.append("shared_formula_requires_translator")
        if re.search(r"\b(?:INDIRECT|OFFSET)\s*\(", source, re.I):
            unresolved.append("dynamic_reference")
        if "[" in source:
            unresolved.append("external_or_structured_reference")
        if re.search(r"\b(?:RTD|WEBSERVICE|STOCKHISTORY|PY|CUBEVALUE|CUBEMEMBER)\s*\(", source, re.I):
            unresolved.append("external_service_or_model")
        references = re.findall(r"(?<![A-Za-z0-9_])\$?[A-Za-z]{1,3}\$?[1-9]\d{0,6}(?::\$?[A-Za-z]{1,3}\$?[1-9]\d{0,6})?(?![A-Za-z0-9_!(])", source)
        entry = {"sheet": sheet, "address": cell["address"], "formula": formula, "cached_value": cell["cached_value"],
                 "cached_error": cell["error"], "formula_kind": cell.get("formula_kind"),
                 "broken_reference_literal": "#REF!" in source.upper(), "observed_a1_references": references,
                 "unresolved": unresolved, "dependencies_complete": False}
        entries.append(entry)
        counts["formulas"] += 1
        counts["cached_errors"] += cell["error"] is not None
        counts["unresolved"] += bool(unresolved)
    result = _page(entries, arguments, backend="direct", snapshot_id=snapshot, freshness="saved_cache", summary=dict(counts),
                   limitations=["A análise é estática; referências dinâmicas, nomes, UDFs e dependências completas exigem o Excel ou análise adicional."])
    result["formulas"] = result.pop("items")
    return result


def _cell_value(cell: dict):
    return cell["cached_value"] if cell["type"] == "formula" else cell["value"]


def _blank_value(value) -> bool:
    return value is None or value == ""


def _typed_key(value) -> str:
    return type(value).__name__ + ":" + json.dumps(value, ensure_ascii=False, sort_keys=True)


def _apply_rule(rule: dict, cell: dict, seen: set) -> bool:
    value = _cell_value(cell)
    kind = rule["type"]
    if kind == "required":
        return not _blank_value(value)
    if _blank_value(value) and rule.get("allow_blank", True):
        return True
    if kind == "unique":
        key = _typed_key(value)
        valid = key not in seen
        seen.add(key)
        return valid
    if kind == "type":
        value_type = rule.get("value_type")
        require(value_type in {"string", "number", "boolean", "date", "integer"}, "invalid_argument", "A regra de tipo exige value_type válido.")
        return {"string": isinstance(value, str), "number": type(value) in {int, float}, "boolean": type(value) is bool,
                "date": cell.get("date_value") is not None, "integer": type(value) is int or type(value) is float and value.is_integer()}[value_type]
    if kind == "range":
        require("minimum" in rule or "maximum" in rule, "invalid_argument", "A regra de intervalo exige minimum ou maximum.")
        return type(value) in {int, float} and ("minimum" not in rule or value >= rule["minimum"]) and ("maximum" not in rule or value <= rule["maximum"])
    if kind == "regex":
        require(isinstance(rule.get("pattern"), str), "invalid_argument", "A regra regex exige pattern.")
        try:
            return isinstance(value, str) and re.fullmatch(rule["pattern"], value) is not None
        except re.error as error:
            raise ExcelExceptions("invalid_regex", "Uma regra de validação possui expressão regular inválida.") from error
    if kind == "allowed_values":
        require(isinstance(rule.get("values"), list), "invalid_argument", "A regra de valores permitidos exige values.")
        return _typed_key(value) in {_typed_key(item) for item in rule["values"]}
    raise ExcelExceptions("invalid_argument", "Tipo de regra de dados inválido.")


def _validation_operand(package: SpreadsheetPackage, sheet: str, value: str | None):
    if value is None:
        return None, False
    candidate = value.removeprefix("=")
    try:
        return float(candidate), True
    except ValueError:
        pass
    try:
        reference, area = _resolve_range(package, sheet, candidate)
    except ExcelExceptions:
        return None, False
    if area[0] == area[2] and area[1] == area[3]:
        target = package.cells(sheet).get(reference.replace("$", "").upper())
        return (_cell_value(target) if target else None), True
    return None, False


def _audit_builtin_validations(package: SpreadsheetPackage) -> tuple[list[dict], list[dict], int]:
    issues, unverified, checked = [], [], 0
    for sheet in package.sheets:
        if sheet["kind"].casefold() != "worksheet":
            continue
        metadata = package.sheet_metadata(sheet["name"])
        if metadata["extended_validation_count"]:
            unverified.append({"sheet": sheet["name"], "code": "extended_validations_not_evaluated", "count": metadata["extended_validation_count"]})
        for validation in metadata["data_validations"]:
            kind = validation.get("type", "none")
            references = validation.get("sqref", "").split()
            if not references:
                issues.append({"sheet": sheet["name"], "code": "invalid_validation_reference"})
                continue
            if kind in {"custom"}:
                unverified.append({"sheet": sheet["name"], "range": validation.get("sqref"), "code": "custom_validation_requires_calculation"})
                continue
            first, first_known = _validation_operand(package, sheet["name"], validation.get("formula1"))
            second, second_known = _validation_operand(package, sheet["name"], validation.get("formula2"))
            allowed = None
            if kind == "list":
                literal = validation.get("formula1") or ""
                if len(literal) >= 2 and literal.startswith('"') and literal.endswith('"'):
                    allowed = literal[1:-1].replace('""', '"').split(",")
                else:
                    try:
                        _, area = _resolve_range(package, sheet["name"], literal.removeprefix("="))
                        allowed = [_cell_value(cell) for _, cell in _selected_cells(package, {"sheet": sheet["name"], "range": literal.removeprefix("=")}, include_empty=True)]
                    except ExcelExceptions:
                        pass
                if allowed is None:
                    unverified.append({"sheet": sheet["name"], "range": validation.get("sqref"), "code": "list_source_not_resolved"})
                    continue
            elif kind not in {"none"} and not first_known:
                unverified.append({"sheet": sheet["name"], "range": validation.get("sqref"), "code": "validation_formula_not_evaluated"})
                continue
            operator = validation.get("operator", "between")
            if kind not in {"none", "list"} and operator in {"between", "notBetween"} and not second_known:
                unverified.append({"sheet": sheet["name"], "range": validation.get("sqref"), "code": "validation_formula_not_evaluated"})
                continue
            for reference in references:
                for _, cell in _selected_cells(package, {"sheet": sheet["name"], "range": reference}, include_empty=True):
                    checked += 1
                    value = _cell_value(cell)
                    if _blank_value(value) and validation.get("allowBlank", "0").casefold() in {"1", "true"}:
                        continue
                    valid = True
                    if kind == "list":
                        valid = str(value).casefold() in {str(item).casefold() for item in allowed}
                    elif kind in {"whole", "decimal", "date", "time", "textLength"}:
                        operand = value
                        if kind == "textLength":
                            representation = "" if value is None else str(value).upper() if type(value) is bool else format(value, ".15g") if type(value) in {int, float} else str(value)
                            operand = len(representation)
                        valid = type(operand) in {int, float}
                        if kind == "whole":
                            valid = valid and float(operand).is_integer()
                        if valid:
                            comparisons = {"between": lambda: first <= operand <= second, "notBetween": lambda: not first <= operand <= second,
                                           "equal": lambda: operand == first, "notEqual": lambda: operand != first,
                                           "lessThan": lambda: operand < first, "lessThanOrEqual": lambda: operand <= first,
                                           "greaterThan": lambda: operand > first, "greaterThanOrEqual": lambda: operand >= first}
                            if operator not in comparisons:
                                unverified.append({"sheet": sheet["name"], "range": reference, "code": "validation_operator_unknown"})
                                break
                            try:
                                valid = comparisons[operator]()
                            except TypeError:
                                unverified.append({"sheet": sheet["name"], "range": reference, "code": "validation_operand_type_unverified"})
                                break
                    elif kind != "none":
                        unverified.append({"sheet": sheet["name"], "range": reference, "code": "validation_type_unknown"})
                        break
                    if not valid:
                        issues.append({"sheet": sheet["name"], "address": cell["address"], "code": "data_validation_violation", "validation_type": kind})
    return issues, unverified, checked


def _structure_view(inventory: dict) -> dict:
    return {
        "format": inventory["format"], "date_system": inventory.get("date_system"),
        "sheets": [{key: item.get(key) for key in ("name", "kind", "state", "merged_ranges", "auto_filter", "data_validations", "protection")} for item in inventory.get("sheets", [])],
        "defined_names": inventory.get("defined_names", []), "tables": inventory.get("tables", []),
        "features": inventory.get("features", {}), "has_vba": inventory.get("has_vba"), "has_xlm": inventory.get("has_xlm"),
        "external_relationships": [{key: item.get(key) for key in ("part", "type", "target", "external")} for item in inventory.get("external_relationships", [])],
    }


def _compare(package: SpreadsheetPackage, other: SpreadsheetPackage, arguments: dict, snapshot: str, other_snapshot: str) -> dict:
    scope = arguments.get("scope", "all")
    require(scope in {"all", "cells", "structure"}, "invalid_argument", "Escopo de comparação inválido.")
    offset, limit = _pagination(arguments)
    differences, total = [], 0

    def record(item):
        nonlocal total
        if offset <= total < offset + limit:
            differences.append(item)
        total += 1

    left_inventory, right_inventory = package.inventory(), other.inventory()
    if scope in {"all", "structure"}:
        left, right = _structure_view(left_inventory), _structure_view(right_inventory)
        for key in left:
            if left[key] != right[key]:
                record({"kind": "structure", "field": key, "before": left[key], "after": right[key]})
    if scope in {"all", "cells"}:
        left_sheets = {item["name"]: item for item in package.sheets if item["kind"].casefold() == "worksheet"}
        right_sheets = {item["name"]: item for item in other.sheets if item["kind"].casefold() == "worksheet"}
        for sheet in sorted(set(left_sheets) | set(right_sheets)):
            left_cells = package.cells(sheet) if sheet in left_sheets else {}
            right_cells = other.cells(sheet) if sheet in right_sheets else {}
            for address in sorted(set(left_cells) | set(right_cells), key=cell_coordinates):
                before, after = left_cells.get(address), right_cells.get(address)
                fields = ("value", "formula", "cached_value", "error", "type", "number_format", "style_index")
                left_value = {key: before.get(key) for key in fields} if before else None
                right_value = {key: after.get(key) for key in fields} if after else None
                if left_value != right_value:
                    record({"kind": "cell", "sheet": sheet, "address": address, "before": left_value, "after": right_value})
    left_parts = {item["name"]: item for item in left_inventory["package_manifest"]}
    right_parts = {item["name"]: item for item in right_inventory["package_manifest"]}
    manifest_changes = []
    for name in sorted(set(left_parts) | set(right_parts)):
        left_hash, right_hash = left_parts.get(name, {}).get("sha256"), right_parts.get(name, {}).get("sha256")
        if left_hash != right_hash:
            manifest_changes.append({"part": name, "before_sha256": left_hash, "after_sha256": right_hash})
    return {"backend": "direct", "snapshot_id": snapshot, "other_snapshot_id": other_snapshot,
            "identical_bytes": snapshot == other_snapshot, "scope": scope, "equal_in_scope": total == 0,
            "total": total, "offset": offset, "limit": limit, "next_offset": offset + limit if offset + limit < total else None,
            "differences": differences, "package_changes": manifest_changes,
            "limitations": ["Equivalência de XML/valores não comprova equivalência visual nem valida a semântica de partes opacas."]}


def _validate(package: SpreadsheetPackage, arguments: dict, snapshot: str, context) -> dict:
    checks = arguments.get("checks", ["structure", "formulas", "data"])
    require(isinstance(checks, list) and checks and all(isinstance(check, str) for check in checks)
            and set(checks) <= {"structure", "formulas", "data", "preservation"}, "invalid_argument", "Selecione verificações de validação válidas.")
    issues, unverified, checked = [], [], 0
    inventory = package.inventory()
    if "structure" in checks:
        issues.extend(inventory["structure_errors"])
        duplicates = [name for name, count in Counter((item["scope"].casefold(), item["name"].casefold()) for item in package.names_defined()).items() if count > 1]
        issues.extend({"code": "duplicate_defined_name", "scope": name[0], "name": name[1]} for name in duplicates)
        tables = package.tables()
        duplicated_tables = [name for name, count in Counter(item.get("name", "").casefold() for item in tables).items() if count > 1]
        issues.extend({"code": "duplicate_table_name", "name": name} for name in duplicated_tables)
        for table in tables:
            try:
                range_coordinates(table.get("ref", ""))
            except ExcelExceptions:
                issues.append({"code": "invalid_table_range", "name": table.get("name"), "sheet": table.get("sheet")})
            columns = [item.get("name", "").casefold() for item in table["columns"]]
            if len(columns) != len(set(columns)) or any(not name for name in columns):
                issues.append({"code": "invalid_table_columns", "name": table.get("name"), "sheet": table.get("sheet")})
    if "formulas" in checks:
        issues.extend({**item, "code": "saved_formula_error"} for item in inventory["formula_errors"])
        unverified.append({"code": "calculation_not_performed", "message": "A ausência de erro nos caches não comprova fórmulas atualizadas ou corretas."})
    if "data" in checks:
        builtin_issues, builtin_unverified, builtin_checked = _audit_builtin_validations(package)
        issues.extend(builtin_issues); unverified.extend(builtin_unverified); checked += builtin_checked
        rules = arguments.get("rules", [])
        require(isinstance(rules, list), "invalid_argument", "As regras devem ser uma lista.")
        for index, rule in enumerate(rules):
            require(isinstance(rule, dict) and rule.get("type") and rule.get("sheet") and rule.get("range"), "invalid_argument", "Cada regra exige tipo, aba e intervalo.")
            seen = set()
            for sheet, cell in _selected_cells(package, rule, include_empty=True):
                checked += 1
                if cell["type"] == "formula" and cell["cached_type"] == "blank":
                    unverified.append({"code": "formula_without_saved_result", "sheet": sheet, "address": cell["address"], "rule": index})
                elif not _apply_rule(rule, cell, seen):
                    issues.append({"code": "data_rule_violation", "rule": index, "rule_type": rule["type"], "sheet": sheet, "address": cell["address"]})
    if "preservation" in checks:
        require(arguments.get("other_path"), "invalid_argument", "A verificação de preservação exige other_path como referência.")
        other_path = context.input_path(arguments["other_path"])
        with SpreadsheetPackage(other_path, context.defaults) as other:
            comparison = _compare(other, package, {"scope": "structure", "limit": 1000}, _snapshot(other_path), snapshot)
        if not comparison["equal_in_scope"]:
            issues.append({"code": "structure_changed", "differences": comparison["differences"], "total": comparison["total"]})
        if comparison["package_changes"]:
            unverified.append({"code": "changed_parts_require_review", "parts": [item["part"] for item in comparison["package_changes"]], "message": "Sem um envelope de alterações esperado, mudanças em partes do pacote não comprovam preservação integral."})
    return {"backend": "direct", "snapshot_id": snapshot, "checks": checks,
            "status": "failed" if issues else "unverified" if unverified else "passed",
            "valid": False if issues else None if unverified else True,
            "issue_count": len(issues), "issues": issues, "unverified": unverified,
            "data_cells_checked": checked, "calculation": {"status": "not_requested"},
            "limitations": inventory["limitations"]}


def execute_direct(operation: str, arguments: dict, context) -> dict:
    """Executa leitura/auditoria dentro das permissões do contexto documental.

    ``snapshot_changed`` impede combinar páginas de versões diferentes. Formatos
    binários são inventariados, mas leitura de células exige backend nativo.
    Nenhuma ação desta função modifica a fonte ou abre Microsoft Excel.
    """
    require(operation in DIRECT_OPERATIONS, "operation_unavailable", "A operação não pertence ao backend de leitura direta.")
    require(isinstance(arguments, dict) and isinstance(arguments.get("input_path"), str), "invalid_argument", "Informe o arquivo Excel de entrada.")
    path = context.input_path(arguments["input_path"])
    snapshot = _snapshot(path, arguments.get("snapshot_id"))
    if operation == "inspect":
        inventory = inspect_package(path, context.defaults)
        if arguments.get("include_cells") and inventory["format"] not in {"xls", "encrypted_ooxml", "xlsb"}:
            with SpreadsheetPackage(path, context.defaults) as package:
                inventory = package.inventory(include_cells=True)
        return inventory
    with SpreadsheetPackage(path, context.defaults) as package:
        require(package.format != "xlsb", "direct_read_unavailable", "O formato binário exige o backend Excel para essa operação.")
        if operation == "navigate":
            return _navigate(package, arguments, snapshot)
        if operation == "read_range":
            return _read_range(package, arguments, snapshot)
        if operation == "search":
            return _search(package, arguments, snapshot)
        if operation == "analyze_formulas":
            return _analyze(package, arguments, snapshot)
        if operation == "validate":
            return _validate(package, arguments, snapshot, context)
        other_path = context.input_path(arguments.get("other_path", ""))
        with SpreadsheetPackage(other_path, context.defaults) as other:
            require(other.format != "xlsb", "direct_read_unavailable", "Comparação de células binárias exige o backend Excel.")
            return _compare(package, other, arguments, snapshot, _snapshot(other_path))
