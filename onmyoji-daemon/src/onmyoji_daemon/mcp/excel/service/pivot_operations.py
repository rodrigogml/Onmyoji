"""Pivôs avançados e filtros de critérios por APIs explícitas do Excel.

Não seleciona abas/células. Operações que dependem de OLAP externo verificam
política antes de consultar o cache; agrupamento/campos calculados/drill-down
clássicos recusam OLAP quando a API pública utilizada não o suporta.
"""
from __future__ import annotations

from datetime import datetime
import math

from ..exception import ExcelExceptions, require
from .operations import (
    check_calculation_sources, check_formula, defer_pivot_refresh, parameterized_property, range_address,
    require_pivot_or_slicer_policy, resize_range, target_range, worksheet, write_values,
)


KINDS = frozenset({"pivot_source", "pivot_field", "pivot_calculated_field", "pivot_group", "pivot_drilldown", "advanced_filter"})
CAPTION_FILTERS = {"equals": 15, "not_equals": 16, "begins_with": 17, "not_begins_with": 18, "ends_with": 19,
                   "not_ends_with": 20, "contains": 21, "not_contains": 22, "greater_than": 23, "greater_or_equal": 24,
                   "less_than": 25, "less_or_equal": 26, "between": 27, "not_between": 28}
VALUE_FILTERS = {"top_count": 1, "bottom_count": 2, "top_percent": 3, "bottom_percent": 4, "top_sum": 5, "bottom_sum": 6,
                 "equals": 7, "not_equals": 8, "greater_than": 9, "greater_or_equal": 10, "less_than": 11,
                 "less_or_equal": 12, "between": 13, "not_between": 14}
DATE_FILTERS = {"equals": 29, "not_equals": 30, "less_than": 31, "less_or_equal": 32, "greater_than": 33,
                "greater_or_equal": 34, "between": 35, "not_between": 36}


def _missing():
    import pythoncom
    return pythoncom.Missing


def _items(collection):
    return [collection.Item(index) for index in range(1, int(collection.Count) + 1)]


def _date(value, *, automatic=False):
    if automatic and value is True:
        return True
    if type(value) in {int, float}:
        require(math.isfinite(value), "invalid_grouping", "Limites de agrupamento devem ser finitos.")
        return value
    require(isinstance(value, str), "invalid_date", "Use data ISO sem timezone ou limite numérico explícito.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as error:
        raise ExcelExceptions("invalid_date", "Use uma data ISO válida para o filtro ou agrupamento.") from error
    require(result.tzinfo is None, "invalid_date", "Datas de agrupamento/filtro não aceitam timezone.")
    return result


def _pivot(workbook, operation, session, context):
    pivot = worksheet(workbook, operation["sheet"]).PivotTables(operation["name"])
    cache = pivot.PivotCache()
    require_pivot_or_slicer_policy(cache, context)
    from .native import ensure_pivot_source_ready
    ensure_pivot_source_ready(workbook, session, context, cache=cache)
    defer_pivot_refresh(session, operation["sheet"], operation["name"])
    return pivot, cache


def _classic(cache, operation):
    require(not bool(cache.OLAP), "olap_operation_unavailable", f"{operation} por esta API exige tabela dinâmica tradicional; OLAP/Data Model requer outra operação.")


def _evidence(session, entry):
    if not hasattr(session, "operation_evidence"):
        session.operation_evidence = []
    session.operation_evidence.append(entry)


def _change_source(workbook, operation, session, context):
    pivot, cache = _pivot(workbook, operation, session, context)
    _classic(cache, "Alterar origem")
    require(int(cache.SourceType) == 1, "pivot_source_unavailable", "ChangePivotCache exige origem de worksheet.")
    source = target_range(workbook, {"sheet": operation["source_sheet"], "range": operation["source_range"]}, context)
    require(int(source.Rows.Count) >= 2, "invalid_pivot_source", "A origem exige cabeçalho e pelo menos uma linha de dados.")
    headers = parameterized_property(source, "Rows", 1).Value2
    headers = [item for row in headers for item in row] if isinstance(headers, tuple) else [headers]
    require(all(item is not None and str(item).strip() for item in headers) and len({str(item).casefold() for item in headers}) == len(headers),
            "invalid_pivot_source", "Os cabeçalhos da origem devem ser preenchidos e distintos.")
    check_calculation_sources(workbook, context)
    from .native import ensure_pivot_source_ready
    ensure_pivot_source_ready(workbook, session, context, source=source)
    replacement = workbook.PivotCaches().Create(1, source)
    pivot.ChangePivotCache(replacement)
    require(bool(pivot.RefreshTable()), "refresh_failed", "O pivô não concluiu a mudança de origem.")
    _evidence(session, {"type": "pivot_source", "sheet": operation["sheet"], "name": operation["name"],
                        "source_sheet": operation["source_sheet"], "source_range": range_address(source), "status": "completed"})


def _field_filter(workbook, operation, session, context):
    pivot, cache = _pivot(workbook, operation, session, context)
    field = pivot.PivotFields(operation["field"])
    action = operation["action"]
    if action == "clear":
        field.ClearAllFilters()
    elif action == "items":
        selected = operation.get("items", [])
        require(isinstance(selected, list) and selected and all(isinstance(item, str) and item for item in selected),
                "invalid_pivot_items", "Informe ao menos um item de filtro válido.")
        if bool(cache.OLAP):
            field.VisibleItemsList = tuple(selected)
        else:
            members = _items(field.PivotItems())
            names = {str(item.Name) for item in members}
            require(set(selected) <= names, "invalid_pivot_items", "A seleção contém item inexistente no campo do pivô.")
            field.ClearAllFilters()
            if int(field.Orientation) == 3 and len(set(selected)) == 1:
                field.EnableMultiplePageItems = False
                field.CurrentPage = selected[0]
            else:
                if int(field.Orientation) == 3:
                    field.EnableMultiplePageItems = True
                for item in members:
                    if str(item.Name) in selected:
                        item.Visible = True
                for item in members:
                    if str(item.Name) not in selected:
                        item.Visible = False
    else:
        types = {"label": CAPTION_FILTERS, "value": VALUE_FILTERS, "date": DATE_FILTERS}
        require(action in types and operation.get("operator") in types[action], "invalid_pivot_filter", "O operador não corresponde ao tipo de filtro.")
        require("criteria1" in operation, "invalid_pivot_filter", "O filtro exige criteria1.")
        operator, first = operation["operator"], operation["criteria1"]
        second = operation.get("criteria2", _missing())
        require(operator not in {"between", "not_between"} or "criteria2" in operation,
                "invalid_pivot_filter", "O filtro entre limites exige criteria2.")
        data_field = _missing()
        if action == "value":
            require(operation.get("data_field"), "data_field_required", "Filtro de valores exige nome da medida/campo de dados agregado.")
            require(type(first) in {int, float} and math.isfinite(first), "invalid_pivot_filter", "Filtro de valores exige critério numérico finito.")
            if "criteria2" in operation:
                require(type(second) in {int, float} and math.isfinite(second), "invalid_pivot_filter", "O segundo critério deve ser numérico finito.")
            if operator in {"top_count", "bottom_count", "top_percent", "bottom_percent", "top_sum", "bottom_sum"}:
                require(first > 0 and (operator not in {"top_percent", "bottom_percent"} or first <= 100), "invalid_pivot_filter", "O limite top/bottom deve ser positivo; percentuais não podem exceder 100.")
            data_field = pivot.DataFields.Item(operation["data_field"])
        elif action == "date":
            first = _date(first)
            if "criteria2" in operation:
                second = _date(second)
        else:
            require(isinstance(first, str) and ("criteria2" not in operation or isinstance(second, str)),
                    "invalid_pivot_filter", "Filtro de rótulo exige critérios textuais.")
        field.ClearAllFilters()
        field.PivotFilters.Add(types[action][operator], data_field, first, second)
    _evidence(session, {"type": "pivot_field", "sheet": operation["sheet"], "name": operation["name"],
                        "field": operation["field"], "action": action, "status": "completed"})


def _calculated(workbook, operation, session, context):
    pivot, cache = _pivot(workbook, operation, session, context)
    _classic(cache, "Campo calculado")
    calculated = pivot.CalculatedFields()
    existing = next((item for item in _items(calculated) if str(item.Name).casefold() == operation["field"].casefold()), None)
    if operation["action"] == "delete":
        require(existing is not None, "pivot_field_not_found", "O campo calculado não existe.")
        existing.Delete()
    else:
        formula = operation["formula"]
        check_formula(formula, context)
        if existing is None:
            calculated.Add(operation["field"], formula, True)
        else:
            existing.StandardFormula = formula
    _evidence(session, {"type": "pivot_calculated_field", "sheet": operation["sheet"], "name": operation["name"],
                        "field": operation["field"], "action": operation["action"], "status": "completed"})


def _group(workbook, operation, session, context):
    pivot, cache = _pivot(workbook, operation, session, context)
    _classic(cache, "Agrupamento")
    field = pivot.PivotFields(operation["field"])
    require(int(field.Orientation) in {1, 2}, "invalid_grouping", "Agrupamento exige campo de linha ou coluna.")
    data = field.DataRange
    require(data is not None and int(data.CountLarge) > 0, "invalid_grouping", "O campo não possui valores agrupáveis.")
    cell = parameterized_property(data, "Cells", 1, 1)
    if operation["action"] == "ungroup":
        cell.Ungroup()
    else:
        start, end = _date(operation.get("start", True), automatic=True), _date(operation.get("end", True), automatic=True)
        interval = operation.get("interval", _missing())
        if "interval" in operation:
            require(type(interval) in {int, float} and math.isfinite(interval) and interval > 0, "invalid_grouping", "O intervalo de agrupamento deve ser positivo e finito.")
        periods = operation.get("periods", _missing())
        if "periods" in operation:
            require(isinstance(periods, list) and len(periods) == 7 and all(type(item) is bool for item in periods) and any(periods),
                    "invalid_grouping", "Periods exige sete booleanos, de segundos a anos, com ao menos um período ativo.")
            periods = tuple(periods)
        if type(start) in {int, float} and type(end) in {int, float} or isinstance(start, datetime) and isinstance(end, datetime):
            require(start <= end, "invalid_grouping", "O início do agrupamento deve anteceder o fim.")
        cell.Group(start, end, interval, periods)
    _evidence(session, {"type": "pivot_group", "sheet": operation["sheet"], "name": operation["name"], "field": operation["field"],
                        "action": operation["action"], "status": "completed"})


def _overlap(first, rows, columns, second):
    return int(first.Row) < int(second.Row) + int(second.Rows.Count) and int(second.Row) < int(first.Row) + rows \
        and int(first.Column) < int(second.Column) + int(second.Columns.Count) and int(second.Column) < int(first.Column) + columns


def _advanced_filter(workbook, operation, session, context):
    source = target_range(workbook, operation, context)
    require(int(source.Rows.Count) >= 2, "invalid_filter_source", "Filtro avançado exige cabeçalho e dados em intervalo explícito.")
    check_calculation_sources(workbook, context)
    criteria = _missing()
    if operation.get("criteria_range"):
        criteria = target_range(workbook, {"sheet": operation.get("criteria_sheet", operation["sheet"]), "range": operation["criteria_range"]}, context)
        require(int(criteria.Rows.Count) >= 2, "invalid_filter_criteria", "O intervalo de critérios deve incluir cabeçalho e critérios.")
    elif operation.get("criteria_sheet"):
        require(False, "invalid_filter_criteria", "criteria_sheet exige criteria_range.")
    action = operation["action"]
    destination = _missing()
    if action == "copy":
        require(operation.get("copy_sheet") == operation["sheet"], "advanced_filter_copy_unavailable", "A cópia por esta rota exige origem e destino na mesma aba; não há fallback por ativação de janela.")
        destination = target_range(workbook, {"sheet": operation["copy_sheet"], "range": operation["copy_range"]}, context)
        require(int(destination.Rows.Count) == 1, "invalid_filter_destination", "O destino deve indicar uma célula inicial ou uma linha de cabeçalhos.")
        rows = int(source.Rows.Count)
        columns = int(source.Columns.Count) if int(destination.CountLarge) == 1 else int(destination.Columns.Count)
        require(int(destination.Row) + rows - 1 <= 1048576 and int(destination.Column) + columns - 1 <= 16384
                and rows * columns <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A saída potencial do filtro excede os limites de células.")
        require(not _overlap(destination, rows, columns, source), "overlapping_filter_destination", "A saída potencial do filtro não pode sobrepor a origem.")
        if operation.get("criteria_range") and operation.get("criteria_sheet", operation["sheet"]) == operation["copy_sheet"]:
            require(not _overlap(destination, rows, columns, criteria), "overlapping_filter_destination", "A saída potencial do filtro não pode sobrepor os critérios.")
        if int(destination.CountLarge) == 1:
            destination = resize_range(destination, 1, columns)
            values = parameterized_property(source, "Rows", 1).Value2
            write_values(destination, [list(values[0])] if isinstance(values, tuple) else [[values]], context)
        require(int(destination.Columns.Count) <= int(source.Columns.Count), "invalid_filter_destination", "A linha de cabeçalhos de saída excede as colunas da origem.")
    else:
        require(action == "in_place", "invalid_filter_action", "Use in_place ou copy para o filtro avançado.")
    source.AdvancedFilter(2 if action == "copy" else 1, criteria, destination, operation.get("unique", False))
    _evidence(session, {"type": "advanced_filter", "sheet": operation["sheet"], "range": range_address(source),
                        "action": action, "status": "completed"})


def _drilldown(workbook, operation, session, context):
    pivot, cache = _pivot(workbook, operation, session, context)
    _classic(cache, "Drill-down")
    destination_name = operation["destination_sheet"]
    require(destination_name.casefold() not in {str(sheet.Name).casefold() for sheet in _items(workbook.Sheets)},
            "sheet_exists", "A aba de destino do drill-down já existe.")
    target = target_range(workbook, operation, context)
    require(int(target.CountLarge) == 1, "single_cell_required", "Drill-down exige uma única célula de valor do pivô.")
    body = pivot.DataBodyRange
    require(body is not None and int(body.Row) <= int(target.Row) < int(body.Row) + int(body.Rows.Count)
            and int(body.Column) <= int(target.Column) < int(body.Column) + int(body.Columns.Count),
            "invalid_drilldown_cell", "A célula deve pertencer à área de dados do pivô selecionado.")
    before = {str(sheet.Name) for sheet in _items(workbook.Sheets)}
    target.ShowDetail = True
    added = [sheet for sheet in _items(workbook.Worksheets) if str(sheet.Name) not in before]
    require(len(added) == 1, "drilldown_unverified", "O Excel não produziu exatamente uma aba de detalhes.")
    detail = added[0]
    require(int(detail.UsedRange.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "Os detalhes do pivô excedem o limite de células.")
    detail.Name = destination_name
    _evidence(session, {"type": "pivot_drilldown", "sheet": operation["sheet"], "name": operation["name"],
                        "destination_sheet": destination_name, "rows": int(detail.UsedRange.Rows.Count), "status": "completed"})


def apply_pivot_operation(workbook, operation: dict, session, context) -> bool:
    """Aplica um tipo reconhecido e verifica política/limites antes da mutação.

    Agrupamento segue ``Range.Group(Start, End, By, Periods)`` em célula única;
    filtros usam ``PivotFilters.Add`` com argumentos opcionais Missing. As
    operações não simulam o editor visual nem usam ActiveSheet/Select.
    """
    kind = operation.get("type")
    if kind not in KINDS:
        return False
    handlers = {"pivot_source": _change_source, "pivot_field": _field_filter, "pivot_calculated_field": _calculated,
                "pivot_group": _group, "pivot_drilldown": _drilldown, "advanced_filter": _advanced_filter}
    try:
        handlers[kind](workbook, operation, session, context)
        session.assert_hidden()
        return True
    except ExcelExceptions:
        raise
    except Exception as error:
        raise ExcelExceptions("pivot_operation_failed", "O Excel recusou a operação de pivô/filtro; confira origem, tipos, proteção e suporte do cache.") from error
