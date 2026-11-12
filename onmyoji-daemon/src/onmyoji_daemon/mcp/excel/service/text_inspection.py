"""Operações de consulta e auditoria para texto tabular sem interpretação Excel."""
from __future__ import annotations

import re

from ..component.tabular_text import TabularText
from ..component.ooxml import range_coordinates
from ..exception import ExcelExceptions, require
from .inspection import DIRECT_OPERATIONS, READ_FIELDS, _apply_rule, _pagination


def _load(path, arguments: dict, context, *, other: bool = False) -> TabularText:
    options = arguments
    if other:
        options = {}
        for option in ("encoding", "delimiter"):
            value = arguments.get("other_" + option, arguments.get(option))
            if value is not None:
                options[option] = value
    result = TabularText(path, options, context.defaults)
    expected = arguments.get("snapshot_id") if not other else None
    require(expected is None or expected == result.snapshot_id, "snapshot_changed", "A fonte mudou; reinicie a leitura paginada com o novo snapshot.")
    return result


def _metadata(document: TabularText) -> dict:
    return {"backend": "direct", "snapshot_id": document.snapshot_id, "format": document.format,
            "freshness": "not_applicable", "encoding": document.encoding, "delimiter": document.delimiter}


def _read(document: TabularText, arguments: dict) -> dict:
    require(isinstance(arguments.get("range"), str), "invalid_argument", "Informe um intervalo A1 para leitura.")
    offset, limit = _pagination(arguments)
    fields = arguments.get("fields")
    require(fields is None or isinstance(fields, list) and fields and all(isinstance(item, str) for item in fields)
            and len(set(fields)) == len(fields) and set(fields) <= READ_FIELDS,
            "invalid_argument", "Os campos solicitados não pertencem ao contrato de leitura.")
    result, total = [], 0
    for cell in document.selected_cells(arguments, include_empty=arguments.get("include_empty", True)):
        if not arguments.get("include_empty", True) and cell["type"] == "blank":
            continue
        if offset <= total < offset + limit:
            result.append(dict(cell) if fields is None else {"sheet": document.sheet_name, "address": cell["address"], "row": cell["row"], "column": cell["column"], **{field: cell.get(field) for field in fields}})
        total += 1
    return {**_metadata(document), "sheet": document.sheet_name, "range": arguments["range"], "cells": result,
            "offset": offset, "limit": limit, "total": total, "next_offset": offset + limit if offset + limit < total else None,
            "limitations": ["Campos são strings; prefixos de fórmula e formatos numéricos não são interpretados."]}


def _navigate(document: TabularText, arguments: dict) -> dict:
    document.require_sheet(arguments.get("sheet"))
    kind = arguments.get("kind", "sheets")
    status = "completed"
    if kind == "sheets":
        items = document.inventory()["sheets"]
    elif kind == "regions":
        items = [{"sheet": document.sheet_name, "data_range": document.data_range, "merged_ranges": [], "auto_filter": None}]
    elif kind in {"names", "tables", "objects", "pivots", "charts", "queries", "connections", "vba"}:
        items, status = [], "not_applicable"
    else:
        raise ExcelExceptions("invalid_argument", "Categoria de navegação inválida.")
    return {**_metadata(document), "kind": kind, "items": items, "total": len(items), "status": status}


def _search(document: TabularText, arguments: dict) -> dict:
    query, scope = arguments.get("query"), arguments.get("scope", "all")
    require(isinstance(query, str) and 0 < len(query) <= 10000, "invalid_argument", "Informe um texto de busca com até 10000 caracteres.")
    require(scope in {"values", "formulas", "all"}, "invalid_argument", "Escopo de busca inválido.")
    offset, limit = _pagination(arguments)
    try:
        matcher = re.compile(query if arguments.get("regex", False) else re.escape(query), 0 if arguments.get("case_sensitive", False) else re.I)
    except re.error as error:
        raise ExcelExceptions("invalid_regex", "A expressão regular de busca está inválida.") from error
    matches, total = [], 0
    if scope != "formulas":
        for cell in document.selected_cells(arguments):
            if cell["value"] is None:
                continue
            match = matcher.search(cell["value"])
            if match is not None:
                if offset <= total < offset + limit:
                    matches.append({"sheet": document.sheet_name, "address": cell["address"], "field": "value", "value": cell["value"], "match_start": match.start(), "match_end": match.end()})
                total += 1
    else:
        document.require_sheet(arguments.get("sheet"))
        if arguments.get("range"):
            range_coordinates(arguments["range"])
    return {**_metadata(document), "matches": matches, "offset": offset, "limit": limit, "total": total,
            "next_offset": offset + limit if offset + limit < total else None}


def _analyze(document: TabularText, arguments: dict) -> dict:
    offset, limit = _pagination(arguments)
    literal_prefixes = sum(cell["value"].startswith("=") for cell in document.selected_cells(arguments) if cell["value"] is not None)
    return {**_metadata(document), "offset": offset, "limit": limit, "total": 0, "next_offset": None,
            "formulas": [], "summary": {"formulas": 0, "cached_errors": 0, "unresolved": 0, "literal_equals_prefixes": literal_prefixes},
            "limitations": ["Texto iniciado por = permanece texto. O formato não contém fórmulas ou caches Excel."]}


def _representation(document: TabularText) -> dict:
    return {"format": document.format, "encoding": document.encoding, "delimiter": document.delimiter}


def _structure(document: TabularText) -> dict:
    return {"sheet": document.sheet_name, "row_count": document.row_count, "column_count": document.column_count,
            "row_widths": [len(row) for row in document.rows]}


def _compare(document: TabularText, other: TabularText, arguments: dict) -> dict:
    scope = arguments.get("scope", "all")
    require(scope in {"all", "cells", "structure"}, "invalid_argument", "Escopo de comparação inválido.")
    offset, limit = _pagination(arguments)
    differences, total = [], 0

    def record(item):
        nonlocal total
        if offset <= total < offset + limit:
            differences.append(item)
        total += 1

    if scope in {"all", "structure"}:
        before, after = _structure(document), _structure(other)
        for key in before:
            if before[key] != after[key]:
                record({"kind": "structure", "field": key, "before": before[key], "after": after[key]})
    if scope in {"all", "cells"}:
        for row in range(1, max(document.row_count, other.row_count) + 1):
            left = document.rows[row - 1] if row <= document.row_count else []
            right = other.rows[row - 1] if row <= other.row_count else []
            for column in range(1, max(len(left), len(right)) + 1):
                before, after = document.cell(row, column), other.cell(row, column)
                if (before["type"], before["value"]) != (after["type"], after["value"]):
                    fields = ("value", "formula", "cached_value", "error", "type", "number_format", "style_index")
                    record({"kind": "cell", "sheet": document.sheet_name, "address": before["address"],
                            "before": {key: before[key] for key in fields} if before["type"] != "blank" else None,
                            "after": {key: after[key] for key in fields} if after["type"] != "blank" else None})
    return {**_metadata(document), "other_snapshot_id": other.snapshot_id,
            "identical_bytes": document.snapshot_id == other.snapshot_id, "equal_in_scope": total == 0,
            "scope": scope, "offset": offset, "limit": limit, "total": total,
            "next_offset": offset + limit if offset + limit < total else None, "differences": differences,
            "representation_changed": _representation(document) != _representation(other),
            "representations": {"before": _representation(document), "after": _representation(other)},
            "limitations": ["A comparação preserva tipos texto e distingue campo vazio explícito de célula ausente."]}


def _validate(document: TabularText, arguments: dict, context) -> dict:
    checks = arguments.get("checks", ["structure", "formulas", "data"])
    require(isinstance(checks, list) and checks and all(isinstance(item, str) for item in checks)
            and set(checks) <= {"structure", "formulas", "data", "preservation"},
            "invalid_argument", "Selecione verificações de validação válidas.")
    issues, checked = [], 0
    if "data" in checks:
        rules = arguments.get("rules", [])
        require(isinstance(rules, list), "invalid_argument", "As regras devem ser uma lista.")
        for index, rule in enumerate(rules):
            require(isinstance(rule, dict) and rule.get("type") and rule.get("sheet") and rule.get("range"),
                    "invalid_argument", "Cada regra exige tipo, aba e intervalo.")
            seen = set()
            for cell in document.selected_cells(rule, include_empty=True):
                checked += 1
                if not _apply_rule(rule, cell, seen):
                    issues.append({"code": "data_rule_violation", "rule": index, "rule_type": rule["type"], "sheet": document.sheet_name, "address": cell["address"]})
    verification = None
    if "preservation" in checks:
        require(arguments.get("other_path"), "invalid_argument", "A verificação de preservação exige other_path como referência.")
        other_path = context.input_path(arguments["other_path"])
        other = _load(other_path, arguments, context, other=True)
        comparison = _compare(other, document, {"scope": "all", "limit": 1000})
        verification = {"method": "parsed_text_values", "identical_bytes": comparison["identical_bytes"], "equal_values_and_structure": comparison["equal_in_scope"]}
        if not comparison["equal_in_scope"]:
            issues.append({"code": "tabular_content_changed", "differences": comparison["differences"], "total": comparison["total"]})
    return {**_metadata(document), "checks": checks, "status": "failed" if issues else "passed", "valid": not issues,
            "issue_count": len(issues), "issues": issues, "unverified": [], "data_cells_checked": checked,
            "calculation": {"status": "not_applicable"}, "preservation": verification,
            "limitations": document.inventory()["limitations"]}


def execute_text(operation: str, arguments: dict, context) -> dict:
    """Consulta texto tabular autorizado sem abrir Excel ou inferir fórmulas.

    ``text_decode_failed`` indica encoding incompatível; ``snapshot_changed``
    impede combinar páginas de fontes distintas. TXT exige delimitador explícito.
    O original e seus finais de linha permanecem intactos.
    """
    require(operation in DIRECT_OPERATIONS, "operation_unavailable", "A operação não pertence ao backend de leitura de texto.")
    require(isinstance(arguments, dict) and isinstance(arguments.get("input_path"), str), "invalid_argument", "Informe o arquivo de texto tabular de entrada.")
    path = context.input_path(arguments["input_path"])
    document = _load(path, arguments, context)
    if operation == "inspect":
        return document.inventory(arguments.get("include_cells", False))
    if operation == "navigate":
        return _navigate(document, arguments)
    if operation == "read_range":
        return _read(document, arguments)
    if operation == "search":
        return _search(document, arguments)
    if operation == "analyze_formulas":
        return _analyze(document, arguments)
    if operation == "validate":
        return _validate(document, arguments, context)
    other_path = context.input_path(arguments.get("other_path", ""))
    other = _load(other_path, arguments, context, other=True)
    return _compare(document, other, arguments)
