"""Apresentação e exportação Excel por APIs públicas, sem ativar janelas."""
from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import zipfile
from xml.dom import minidom

from ..component.ooxml import SpreadsheetPackage, column_name
from ..exception import ExcelExceptions, require
from .operations import range_address, require_pivot_or_slicer_policy, set_number_format, target_range, worksheet


CONTROL_TYPES = {"button": 0, "checkbox": 1, "dropdown": 2, "groupbox": 4, "label": 5,
                 "listbox": 6, "optionbutton": 7, "scrollbar": 8, "spinner": 9}


def _position(operation):
    return [operation.get("left", 0), operation.get("top", 0), operation.get("width", 160), operation.get("height", 60)]


def _shape_properties(shape, operation):
    for field in ("left", "top", "width", "height", "rotation"):
        if field in operation:
            setattr(shape, field.title(), operation[field])
    if "text" in operation:
        shape.TextFrame2.TextRange.Text = operation["text"]
    if "fill_color" in operation:
        shape.Fill.ForeColor.RGB = operation["fill_color"]
    if "line_color" in operation:
        shape.Line.ForeColor.RGB = operation["line_color"]


def configure_chart(chart, operation, workbook, context):
    """Configura séries/eixos sem selecionar o gráfico ou tocar no clipboard."""
    for specification in operation.get("series", []):
        collection = chart.SeriesCollection()
        index = specification.get("index")
        if specification.get("action", "add") == "delete":
            require(index is not None and 1 <= index <= collection.Count, "series_not_found", "A série a remover não existe.")
            collection.Item(index).Delete()
            continue
        if specification.get("action", "add") == "update":
            require(index is not None and 1 <= index <= collection.Count, "series_not_found", "A série a alterar não existe.")
            series = collection.Item(index)
        else:
            series = collection.NewSeries()
        if "name" in specification:
            series.Name = specification["name"]
        for field, member in (("values_range", "Values"), ("x_values_range", "XValues")):
            if field in specification:
                selected = target_range(workbook, {"sheet": specification.get("source_sheet", operation["sheet"]), "range": specification[field]}, context)
                setattr(series, member, selected)
        if "chart_type" in specification:
            series.ChartType = specification["chart_type"]
        if "axis_group" in specification:
            series.AxisGroup = 1 if specification["axis_group"] == "primary" else 2
    for specification in operation.get("axes", []):
        axis = chart.Axes(Type={"category": 1, "value": 2, "series": 3}[specification["axis"]],
                          AxisGroup=1 if specification.get("group", "primary") == "primary" else 2)
        if "title" in specification:
            axis.HasTitle = bool(specification["title"])
            if specification["title"]:
                axis.AxisTitle.Text = specification["title"]
        if "minimum" in specification:
            axis.MinimumScale = specification["minimum"]
        if "maximum" in specification:
            axis.MaximumScale = specification["maximum"]
        if "number_format" in specification:
            set_number_format(axis.TickLabels, specification["number_format"])
    if "has_legend" in operation:
        chart.HasLegend = operation["has_legend"]
    if "legend_position" in operation:
        chart.HasLegend = True
        chart.Legend.Position = operation["legend_position"]
    if "data_labels" in operation:
        chart.ApplyDataLabels()
        for series in chart.SeriesCollection():
            labels = series.DataLabels()
            for key, member in (("show_value", "ShowValue"), ("show_series_name", "ShowSeriesName"),
                                ("show_category_name", "ShowCategoryName"), ("number_format", "NumberFormat")):
                if key in operation["data_labels"]:
                    if member == "NumberFormat":
                        set_number_format(labels, operation["data_labels"][key])
                    else:
                        setattr(labels, member, operation["data_labels"][key])


def apply_advanced_operation(workbook, operation, session, context) -> bool:
    """Aplica famílias adicionais; ``False`` significa tipo não reconhecido."""
    kind, action = operation["type"], operation.get("action", "add")
    if kind in {"shape", "image", "control"}:
        shapes = worksheet(workbook, operation["sheet"]).Shapes
        if action == "add":
            require(not any(item.Name == operation["name"] for item in shapes), "object_exists", "Já existe um objeto com o nome solicitado.")
            position = _position(operation)
            if kind == "shape":
                shape = shapes.AddShape(Type=operation.get("shape_type", 1), Left=position[0], Top=position[1], Width=position[2], Height=position[3])
            elif kind == "image":
                source = context.input_path(operation["input_path"])
                require(source.suffix.casefold() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".svg"},
                        "unsupported_image", "Use uma imagem em formato aceito pelo Excel.")
                shape = shapes.AddPicture(Filename=str(source), LinkToFile=False, SaveWithDocument=True,
                                          Left=position[0], Top=position[1], Width=position[2], Height=position[3])
            else:
                shape = shapes.AddFormControl(Type=CONTROL_TYPES[operation["control_type"]], Left=position[0], Top=position[1], Width=position[2], Height=position[3])
            shape.Name = operation["name"]
        else:
            shape = shapes.Item(operation["name"])
        if action == "delete":
            shape.Delete()
            return True
        _shape_properties(shape, operation)
        if kind == "control":
            if "caption" in operation:
                # TextFrame is unavailable on some control types; Excel reports
                # that limitation instead of falling back to UI.
                shape.TextFrame.Characters().Text = operation["caption"]
            properties = shape.ControlFormat
            for field, member in (("linked_cell", "LinkedCell"), ("list_fill_range", "ListFillRange")):
                if field in operation:
                    target_range(workbook, {"sheet": operation["sheet"], "range": operation[field]}, context)
                    setattr(properties, member, "'" + operation["sheet"].replace("'", "''") + "'!" + operation[field])
            for field, member in (("minimum", "Min"), ("maximum", "Max"), ("small_change", "SmallChange"),
                                  ("large_change", "LargeChange"), ("value", "Value")):
                if field in operation:
                    setattr(properties, member, operation[field])
        return True
    if kind == "sparkline":
        target = target_range(workbook, operation, context)
        if action == "delete":
            target.SparklineGroups.Clear()
        else:
            source_sheet = operation.get("source_sheet", operation["sheet"])
            source = target_range(workbook, {"sheet": source_sheet, "range": operation["source_range"]}, context)
            group = target.SparklineGroups.Add(Type={"line": 1, "column": 2, "win_loss": 3}[operation.get("sparkline_type", "line")],
                                               SourceData="'" + source_sheet.replace("'", "''") + "'!" + range_address(source))
            if "color" in operation:
                group.SeriesColor.Color = operation["color"]
        return True
    if kind == "timeline_filter":
        cache = workbook.SlicerCaches.Item(operation["cache"])
        require_pivot_or_slicer_policy(cache, context)
        if operation.get("clear", False):
            cache.ClearManualFilter()
        else:
            try:
                first, last = (datetime.fromisoformat(operation[key]) for key in ("start_date", "end_date"))
            except ValueError as error:
                raise ExcelExceptions("invalid_date", "Use datas ISO para o intervalo da timeline.") from error
            require(first <= last and first.tzinfo is None and last.tzinfo is None, "invalid_date", "O intervalo da timeline é inválido ou contém timezone.")
            cache.TimelineState.SetFilterDateRange(StartDate=first, EndDate=last)
        return True
    if kind == "view":
        worksheet(workbook, operation["sheet"])
        if not hasattr(session, "deferred_views"):
            session.deferred_views = []
        session.deferred_views.append(dict(operation))
        return True
    if kind in {"advanced_filter", "pivot_source", "pivot_field", "pivot_calculated_field", "pivot_group", "pivot_drilldown"}:
        from .pivot_operations import apply_pivot_operation
        return apply_pivot_operation(workbook, operation, session, context)
    from .advanced_analysis import apply_analysis_operation
    return apply_analysis_operation(workbook, operation, session, context)


def _json_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


def export_advanced(workbook, arguments, context) -> dict | None:
    """Exporta gráfico ou dados em JSON; não rende imagens de intervalo via clipboard."""
    selected_format = arguments["format"]
    if selected_format not in {"png", "jpeg", "json"}:
        return None
    require(arguments.get("sheet"), "sheet_required", "Selecione a aba para esta exportação.")
    sheet = worksheet(workbook, arguments["sheet"])
    staged = context.stage_output(arguments["output_path"])
    if selected_format == "json":
        target = target_range(workbook, {"sheet": arguments["sheet"], "range": arguments["range"]}, context) if arguments.get("range") else sheet.UsedRange
        require(target.CountLarge <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A exportação excede o limite de células.")
        values = target.Value2
        if target.CountLarge == 1:
            values = ((values,),)
        values = [list(row) for row in values]
        errors = []
        for row_index, row in enumerate(values):
            for column_index, value in enumerate(row):
                if type(value) is int and -2146827000 <= value <= -2146820000:
                    cell = target.Cells(row_index + 1, column_index + 1)
                    if bool(sheet.Application.WorksheetFunction.IsError(cell)):
                        row[column_index] = str(cell.Text)
                        errors.append({"address": range_address(cell), "error": str(cell.Text)})
        content = {"sheet": str(sheet.Name), "range": range_address(target), "values": _json_value(values),
                   "errors": errors, "freshness": "saved_cache"}
        serialized = json.dumps(content, ensure_ascii=False, allow_nan=False).encode("utf-8")
        require(len(serialized) <= context.defaults.get("max_result_bytes", 33554432), "result_too_large", "O JSON excede o limite de resultado configurado.")
        staged.write_bytes(serialized)
    else:
        require(arguments.get("chart_name"), "chart_required", "Selecione o gráfico a exportar.")
        chart = sheet.ChartObjects(arguments["chart_name"]).Chart
        require(bool(chart.Export(Filename=str(staged), FilterName="PNG" if selected_format == "png" else "JPG", Interactive=False)),
                "chart_export_failed", "O Excel não concluiu a exportação oculta do gráfico.")
        require(staged.is_file() and staged.stat().st_size > 0, "chart_export_failed", "A exportação não produziu uma imagem válida.")
    return {"format": selected_format, "losses": ["A exportação contém apenas o gráfico ou os valores selecionados; não conserva os demais recursos da pasta."]}


def _view_document(content: bytes, operations: list[dict]) -> bytes:
    document = minidom.parseString(content)
    root = document.documentElement
    prefix = root.prefix + ":" if root.prefix else ""
    namespace = root.namespaceURI
    def create(name):
        return document.createElementNS(namespace, prefix + name)
    def element_children(node, name):
        return [item for item in node.childNodes if item.nodeType == item.ELEMENT_NODE and item.localName == name]
    containers = element_children(root, "sheetViews")
    if containers:
        views = containers[0]
    else:
        views = create("sheetViews")
        anchor = next((item for item in root.childNodes if item.nodeType == item.ELEMENT_NODE and item.localName not in {"sheetPr", "dimension"}), None)
        root.insertBefore(views, anchor) if anchor is not None else root.appendChild(views)
    view_list = element_children(views, "sheetView")
    if not view_list:
        view = create("sheetView")
        view.setAttribute("workbookViewId", "0")
        views.appendChild(view)
        view_list = [view]
    for operation in operations:
        for view in view_list:
            for field, attribute in (("zoom", "zoomScale"), ("show_gridlines", "showGridLines"), ("show_headers", "showRowColHeaders"),
                                     ("right_to_left", "rightToLeft"), ("show_zeros", "showZeros")):
                if field in operation:
                    value = operation[field]
                    if field == "zoom":
                        require(type(value) is int and 10 <= value <= 400, "invalid_zoom", "Zoom deve estar entre 10 e 400.")
                    else:
                        require(type(value) is bool, "invalid_view", "Opções de exibição devem ser booleanas.")
                    view.setAttribute(attribute, str(value) if field == "zoom" else "1" if value else "0")
            if "freeze_rows" not in operation and "freeze_columns" not in operation:
                continue
            panes = element_children(view, "pane")
            old = panes[0] if panes else None
            frozen = old is not None and old.getAttribute("state") in {"frozen", "frozenSplit"}
            rows = operation.get("freeze_rows", int(float(old.getAttribute("ySplit") or 0)) if frozen else 0)
            columns = operation.get("freeze_columns", int(float(old.getAttribute("xSplit") or 0)) if frozen else 0)
            require(type(rows) is int and 0 <= rows < 1048576 and type(columns) is int and 0 <= columns < 16384,
                    "invalid_view", "O congelamento excede os limites da planilha.")
            for item in [*panes, *element_children(view, "selection")]:
                view.removeChild(item)
                item.unlink()
            if rows or columns:
                pane = create("pane")
                active = "bottomRight" if rows and columns else "bottomLeft" if rows else "topRight"
                corner = column_name(columns + 1) + str(rows + 1)
                for key, value in {"state": "frozen", "activePane": active, "topLeftCell": corner}.items():
                    pane.setAttribute(key, value)
                if rows:
                    pane.setAttribute("ySplit", str(rows))
                if columns:
                    pane.setAttribute("xSplit", str(columns))
                view.insertBefore(pane, view.firstChild)
                selection = create("selection")
                for key, value in {"pane": active, "activeCell": corner, "sqref": corner}.items():
                    selection.setAttribute(key, value)
                view.insertBefore(selection, pane.nextSibling)
    result = document.toxml(encoding="UTF-8")
    document.unlink()
    return result


def finalize_advanced(path: Path, session, context) -> None:
    """Persiste views no OOXML salvo, preservando namespaces e conteúdo das células.

    O ajuste ocorre antes da reabertura de verificação e não ativa uma aba no
    Excel. Formatos binários retornam ``capability_unavailable`` para esta rota.
    """
    operations = getattr(session, "deferred_views", [])
    if not operations:
        return
    require(path.suffix.casefold() in {".xlsx", ".xlsm", ".xltx", ".xltm"},
            "capability_unavailable", "Configuração de view sem ativação de aba exige saída OOXML XML.")
    with SpreadsheetPackage(path, context.defaults) as package:
        targets = {}
        for operation in operations:
            targets.setdefault(package.sheet(operation["sheet"])["part"], []).append(operation)
    temporary = context.temporary_path(".views.zip")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w") as output:
        output.comment = source.comment
        for item in source.infolist():
            content = source.read(item.filename)
            output.writestr(item, _view_document(content, targets[item.filename]) if item.filename in targets else content)
    temporary.replace(path)
