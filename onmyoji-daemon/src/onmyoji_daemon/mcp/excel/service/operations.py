"""Alterações funcionais do Excel sem seleção, foco ou avaliação de código livre."""
from __future__ import annotations

import re
import math

from ..exception import require


AGGREGATIONS = {"sum": -4157, "count": -4112, "average": -4106, "min": -4139, "max": -4136,
                "product": -4149, "count_numbers": -4113, "stddev": -4155, "variance": -4164}
ORIENTATIONS = {"row": 1, "column": 2, "page": 3, "data": 4}
EXTERNAL_FORMULA = re.compile(r"(?i)(?:\b(?:WEBSERVICE|RTD|STOCKHISTORY|CUBE[A-Z]*|PY|IMAGE|TRANSLATE|DETECTLANGUAGE)\s*\(|\[[^\]]+\.(?:xlsx?|xlsm|xlsb|xlam|xla|csv|ods)\]|\[[^\]]+\][^+*/(),]*!|\|[^!]*!)")


def parameterized_property(owner, member, *arguments):
    """Invoca propriedades opcionais sem cair no Item padrão do objeto retornado.

    Em late binding, ``range.Resize(rows, cols)`` pode primeiro obter Resize
    sem argumentos e então indexar o Range retornado. A chamada IDispatch
    explícita preserva a semântica da propriedade e evita wrappers makepy.
    """
    import win32com.client.dynamic
    value = owner._oleobj_.Invoke(owner._oleobj_.GetIDsOfNames(member), 0, 2, True, *arguments)
    return win32com.client.dynamic.Dispatch(value) if hasattr(value, "Invoke") else value


def resize_range(target, rows, columns):
    return parameterized_property(target, "Resize", rows, columns)


def range_address(target):
    return str(parameterized_property(target, "Address", False, False))


def get_number_format(target):
    """Lê códigos invariáveis com LCID inglês, independentemente do Windows.

    O LCID padrão do late binding pode traduzir ``NumberFormat`` como formato
    local. Usar 1033 mantém o mesmo contrato dos códigos gravados no OOXML.
    """
    return target._oleobj_.Invoke(target._oleobj_.GetIDsOfNames("NumberFormat"), 1033, 2, True)


def set_number_format(target, value):
    """Aplica códigos Excel invariáveis sem reinterpretar pontos ou tokens de data."""
    target._oleobj_.Invoke(target._oleobj_.GetIDsOfNames("NumberFormat"), 1033, 4, False, value)


def invariant_method(owner, member, *arguments):
    """Executa métodos com fórmulas invariáveis sem tradução textual de tokens."""
    import win32com.client.dynamic
    value = owner._oleobj_.Invoke(owner._oleobj_.GetIDsOfNames(member), 1033, 1, True, *arguments)
    return win32com.client.dynamic.Dispatch(value) if hasattr(value, "Invoke") else value


def rule_formula(session, value, context):
    """Converte fórmulas de regras pelo parser Excel, sem editar dados do usuário.

    Validation/FormatConditions exigem separadores regionais mesmo quando o
    método recebe LCID inglês. Formula2Local no bootstrap exclusivo traduz a
    expressão inteira, preservando textos, arrays e referências sem substituição
    textual. A célula auxiliar é limpa também quando o parser recusa a expressão.
    """
    if not isinstance(value, str) or not value.startswith("="):
        return value
    check_formula(value, context)
    cell = session.bootstrap.Worksheets.Item(1).Range("A1")
    try:
        cell.Formula2 = value
        return str(cell.Formula2Local)
    finally:
        cell.ClearContents()


def validation_formula(session, operation, context):
    """Codifica listas tipadas no separador nativo e respeita o limite do Excel."""
    if "list_values" in operation:
        values = operation["list_values"]
        require(operation.get("validation_type") == "list" and "formula1" not in operation
                and isinstance(values, list) and bool(values) and all(isinstance(value, str) for value in values),
                "invalid_validation_list", "Use list_values somente na validação de lista, sem formula1.")
        separator = str(parameterized_property(session.application, "International", 5))
        require(bool(separator) and all(separator not in value for value in values), "validation_list_unrepresentable",
                "Um item contém o separador regional da lista; use formula1 com referência a um intervalo.")
        result = separator.join(values)
    else:
        result = rule_formula(session, operation.get("formula1", ""), context)
    require(not isinstance(result, str) or 0 < len(result) <= 255, "validation_formula_too_long",
            "A validação exige formula1 com até 255 caracteres; use referência a um intervalo para listas maiores.")
    return result


def check_formula(formula, context):
    require(isinstance(formula, str) and formula.startswith("="), "invalid_formula", "Fórmulas devem iniciar com =.")
    from ..component.ooxml import contains_xlm_formula
    require(not contains_xlm_formula(formula), "active_content_blocked", "Fórmulas e nomes XLM não são executados pelo backend nativo.")
    expression = re.sub(r'"(?:[^\"]|\"\")*"', '""', formula)
    external = bool(EXTERNAL_FORMULA.search(expression)) or bool(re.search(r"\bINDIRECT\s*\(", expression, re.I) and EXTERNAL_FORMULA.search(formula))
    require(not external or context.profile.get("allow_refresh") is True,
            "external_data_denied", "A fórmula depende de fonte externa não autorizada pelo perfil.")


def formula_records(sheet, context):
    """Lê fórmulas físicas inclusive em abas protegidas, sem alterar proteção.

    SpecialCells é proibido pelo Excel em algumas abas protegidas. Nessa
    condição a leitura matricial é limitada antes da inspeção individual de
    HasFormula, que distingue fórmula de texto literal iniciado por ``=``.
    """
    used = sheet.UsedRange
    if used.HasFormula is False:
        return
    maximum = context.defaults.get("max_cells", 1000000)
    protected = getattr(sheet, "ProtectContents", False) is True
    formulas = None
    if not protected:
        try:
            formulas = used.SpecialCells(-4123)
        except Exception:
            pass
    if formulas is not None:
        require(int(formulas.CountLarge) <= maximum, "cell_limit_exceeded", "A inspeção de fórmulas excede o limite configurado.")
        for index in range(1, int(formulas.Areas.Count) + 1):
            area = formulas.Areas.Item(index)
            raw = area.Formula2
            rows = raw if isinstance(raw, tuple) else ((raw,),)
            for row, entries in enumerate(rows):
                for column, value in enumerate(entries):
                    if isinstance(value, str) and value.startswith("="):
                        yield int(area.Row) + row, int(area.Column) + column, value
        return
    require(int(used.CountLarge) <= maximum, "cell_limit_exceeded", "A inspeção física de fórmulas excede o limite configurado.")
    raw = used.Formula2
    rows = raw if isinstance(raw, tuple) else ((raw,),)
    for row, entries in enumerate(rows):
        for column, value in enumerate(entries):
            if isinstance(value, str) and value.startswith("="):
                physical_row, physical_column = int(used.Row) + row, int(used.Column) + column
                if sheet.Cells(physical_row, physical_column).HasFormula:
                    yield physical_row, physical_column, value


def check_calculation_sources(workbook, context):
    """Bloqueia dependências externas observáveis antes de qualquer cálculo.

    Abrir com links desativados não impede WEBSERVICE/RTD/Python de uma fórmula
    existente. Inspecionar fórmulas/nomes e LinkSources evita contornar a política
    ao recalcular ou usar Goal Seek/tabelas de hipóteses. Suplementos e referências
    construídas em código não recebem certificação de ausência de efeitos.
    """
    if context.profile.get("allow_refresh") is True:
        return
    total = 0
    for index in range(1, int(workbook.Worksheets.Count) + 1):
        sheet = workbook.Worksheets.Item(index)
        for _, _, formula in formula_records(sheet, context):
            total += 1
            require(total <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A inspeção de fórmulas excede o limite configurado.")
            check_formula(formula, context)
    for index in range(1, int(workbook.Names.Count) + 1):
        check_formula(str(workbook.Names.Item(index).RefersTo), context)
    for kind in (1, 2):
        require(not workbook.LinkSources(kind), "external_data_denied", "A pasta possui vínculos externos; o perfil não autoriza calculá-los.")


def require_pivot_or_slicer_policy(cache, context):
    """Exige permissão quando layout/filtro pode consultar origem externa.

    OLAP local do próprio Data Model (connection type 7) permanece local.
    A permissão também cobre consultas implícitas de layout, não apenas Refresh.
    """
    if context.profile.get("allow_refresh") is True:
        return
    try:
        connection_type = int(cache.WorkbookConnection.Type)
    except Exception:
        connection_type = None
    if connection_type == 7:
        return
    try:
        source_type = int(cache.SourceType)
    except Exception:
        source_type = None
    require(not bool(cache.OLAP) and source_type in {None, 1, 3} and connection_type is None,
            "external_data_denied", "Alterar esse pivô/segmentação pode consultar fonte externa; o perfil não autoriza a operação.")


def defer_pivot_refresh(session, sheet, name):
    """Registra somente pivôs explicitamente afetados para a finalização do lote."""
    pending = getattr(session, "pending_pivot_refresh", None)
    if not isinstance(pending, set):
        pending = session.pending_pivot_refresh = set()
    pending.add((str(sheet), str(name)))


def worksheet(workbook, name):
    require(isinstance(name, str) and name, "invalid_sheet", "Informe a aba explicitamente.")
    return workbook.Worksheets.Item(name)


def assert_range_owner(target, workbook, sheet):
    """Impede que nomes definidos redirecionem uma seleção para outro contexto."""
    owner = target.Worksheet
    require(str(owner.Name).casefold() == str(sheet.Name).casefold()
            and str(owner.Parent.FullName).casefold() == str(workbook.FullName).casefold()
            and int(owner.Parent.Application.Hwnd) == int(workbook.Application.Hwnd),
            "range_context_mismatch", "O intervalo resolvido pertence a outra aba ou pasta; informe o contexto correto.")


def target_range(workbook, operation, context):
    sheet = worksheet(workbook, operation["sheet"])
    target = sheet.Range(operation["range"])
    assert_range_owner(target, workbook, sheet)
    require(int(target.Areas.Count) == 1, "invalid_range", "Use um intervalo retangular contínuo.")
    require(int(target.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "O intervalo excede o limite de células configurado.")
    return target


def matrix(values, context):
    require(isinstance(values, list) and bool(values) and all(isinstance(row, list) and row for row in values),
            "invalid_values", "Informe uma matriz retangular não vazia.")
    width = len(values[0])
    require(all(len(row) == width for row in values), "invalid_values", "A matriz deve ter linhas com o mesmo tamanho.")
    require(len(values) * width <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A matriz excede o limite de células configurado.")
    for row in values:
        for value in row:
            require(value is None or type(value) in {str, bool, int, float}, "invalid_values", "Células aceitam somente texto, número, booleano ou null.")
            if isinstance(value, str):
                require(len(value) <= 32767, "cell_text_too_long", "O texto excede os 32767 caracteres de uma célula Excel.")
            if type(value) in {int, float}:
                require((type(value) is int or math.isfinite(value)) and (value == 0 or 2.2250738585072014e-308 <= abs(value) <= 1.7976931348623157e308),
                        "numeric_out_of_range", "O número não é finito ou está fora da faixa suportada pelo Excel.")
                if type(value) is int:
                    require(int(float(value)) == value, "numeric_precision_loss", "O inteiro perde precisão no Excel; informe um texto para preservar todos os dígitos.")
    # O apóstrofo de entrada é consumido pelo Excel e impede inferir fórmula,
    # data ou número a partir de um valor cujo tipo declarado é texto.
    return tuple(tuple("'" + value if isinstance(value, str) else value for value in row) for row in values)


def write_values(target, values, context):
    """Escreve endereços físicos, inclusive quando filtros ocultam o destino.

    A atribuição matricial de Value2 pode pular linhas filtradas no Excel.
    Nessa condição, células físicas são escritas individualmente para preservar
    tanto a posição solicitada quanto os filtros e a visibilidade existentes.
    """
    data = matrix(values, context)
    rows, columns = len(data), len(data[0])
    require(int(target.CountLarge) == 1 or int(target.Rows.Count) == rows and int(target.Columns.Count) == columns,
            "range_shape_mismatch", "O intervalo de destino e a matriz têm dimensões diferentes.")
    destination = resize_range(target, rows, columns)
    if destination.EntireRow.Hidden is not False or destination.EntireColumn.Hidden is not False:
        sheet, first_row, first_column = destination.Worksheet, int(destination.Row), int(destination.Column)
        for row in range(rows):
            for column in range(columns):
                sheet.Cells(first_row + row, first_column + column).Value2 = data[row][column]
    else:
        destination.Value2 = data
    actual = destination.Value2
    actual = actual if isinstance(actual, tuple) else ((actual,),)
    for row in range(rows):
        for column in range(columns):
            expected, observed = values[row][column], actual[row][column]
            valid = math.isclose(expected, observed, rel_tol=1e-14, abs_tol=1e-300) if type(expected) in {int, float} and type(observed) in {int, float} else expected == observed and (type(expected) is bool) == (type(observed) is bool)
            require(valid, "write_validation_failed", "O Excel alterou o tipo ou conteúdo de um valor durante a escrita.")


def write_formula(target, formula, member="Formula2"):
    """Preenche fórmulas com referências relativas, incluindo linhas filtradas.

    O preenchimento matricial do Excel omite linhas filtradas. A variante
    física usa R1C1 obtido do primeiro endereço para manter o deslocamento
    relativo que o preenchimento normal aplicaria. FormulaArray conserva uma
    única matriz legada e usa sua própria operação nativa.
    """
    hidden = target.EntireRow.Hidden is not False or target.EntireColumn.Hidden is not False
    if member == "FormulaArray" or not hidden:
        setattr(target, member, formula)
        return
    sheet, first_row, first_column = target.Worksheet, int(target.Row), int(target.Column)
    first = sheet.Cells(first_row, first_column)
    setattr(first, member, formula)
    relative_member = "Formula2R1C1" if member.startswith("Formula2") else "FormulaR1C1"
    relative = getattr(first, relative_member)
    for row in range(int(target.Rows.Count)):
        for column in range(int(target.Columns.Count)):
            if row or column:
                setattr(sheet.Cells(first_row + row, first_column + column), relative_member, relative)


def copy_range(source, destination, mode, transpose, workbook):
    """Copia sem usar clipboard; transposição integral exige áreas disjuntas."""
    rows, columns = int(source.Rows.Count), int(source.Columns.Count)
    destination = resize_range(destination, columns if transpose else rows, rows if transpose else columns)
    hidden = any(target.EntireRow.Hidden is not False or target.EntireColumn.Hidden is not False for target in (source, destination))
    if mode == "all" and not transpose and not hidden:
        source.Copy(Destination=destination)
        return
    if mode == "all":
        overlap = workbook.Application.Intersect(source, destination) if source.Parent.Name == destination.Parent.Name else None
        require(overlap is None, "overlapping_transpose" if transpose else "overlapping_filtered_copy",
                "Cópia integral com transposição ou linhas/colunas ocultas exige origem e destino sem sobreposição.")
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                origin = source.Worksheet.Cells(int(source.Row) + row - 1, int(source.Column) + column - 1)
                target_row, target_column = (column, row) if transpose else (row, column)
                target = destination.Worksheet.Cells(int(destination.Row) + target_row - 1, int(destination.Column) + target_column - 1)
                origin.Copy(Destination=target)
        return
    captured = []
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            cell = source.Cells(row, column)
            if mode == "formats":
                formatting = {member: getattr(cell, member) for member in ("HorizontalAlignment", "VerticalAlignment", "WrapText", "Orientation", "IndentLevel", "ShrinkToFit")}
                formatting["NumberFormat"] = get_number_format(cell)
                font = {member: getattr(cell.Font, member) for member in ("Name", "Size", "Bold", "Italic", "Underline", "Color", "Strikethrough")}
                interior = {member: getattr(cell.Interior, member) for member in ("Pattern", "Color", "PatternColor")}
                borders = [(index, {member: getattr(cell.Borders(index), member) for member in ("LineStyle", "Weight", "Color")}) for index in (7, 8, 9, 10, 5, 6)]
                value = formatting, font, interior, borders
            else:
                value = (bool(cell.HasFormula), cell.Formula2R1C1, cell.Value2)
            captured.append((row, column, value))
    for row, column, value in captured:
        target = destination.Cells(column, row) if transpose else destination.Cells(row, column)
        if mode == "formats":
            formatting, font, interior, borders = value
            for member, item in formatting.items():
                if member == "NumberFormat":
                    set_number_format(target, item)
                else:
                    setattr(target, member, item)
            for member, item in font.items():
                setattr(target.Font, member, item)
            for member, item in interior.items():
                setattr(target.Interior, member, item)
            for index, values in borders:
                for member, item in values.items():
                    setattr(target.Borders(index), member, item)
        elif mode == "formulas" and value[0]:
            target.Formula2R1C1 = value[1]
        else:
            target.Value2 = "'" + value[2] if isinstance(value[2], str) else value[2]


def apply_operation(workbook, operation, session, context):
    """Aplica uma operação tipada; erros COM são sanitizados na fachada nativa."""
    kind = operation["type"]
    action = operation.get("action")
    basic = {"set_values", "set_formula", "clear", "format_range", "resize", "insert_range", "delete_range", "copy_range", "merge", "unmerge", "sheet", "name", "table", "filter", "sort", "validation", "conditional_format", "pivot", "chart", "slicer", "query", "model_measure", "model_relationship", "comment", "hyperlink", "page_setup", "protect", "goal_seek"}
    if kind not in basic:
        from .advanced_operations import apply_advanced_operation
        require(apply_advanced_operation(workbook, operation, session, context), "operation_unavailable", "Tipo de alteração Excel não implementado.")
        session.assert_hidden()
        return
    target = target_range(workbook, operation, context) if "range" in operation and kind not in {"table", "chart"} else None
    if kind == "set_values":
        write_values(target, operation["values"], context)
    elif kind == "set_formula":
        check_formula(operation["formula"], context)
        notation, language = operation.get("notation", "A1"), operation.get("language", "invariant")
        if operation.get("array_mode", "dynamic") == "legacy":
            require(notation == "A1" and language == "invariant", "capability_unavailable", "Fórmula matricial legada exige A1 invariável.")
            write_formula(target, operation["formula"], "FormulaArray")
        else:
            member = "Formula2" + ("R1C1" if notation == "R1C1" else "") + ("Local" if language == "local" else "")
            write_formula(target, operation["formula"], member)
    elif kind == "clear":
        getattr(target, {"contents": "ClearContents", "formats": "ClearFormats", "all": "Clear"}[operation.get("what", "contents")])()
    elif kind == "format_range":
        mappings = {"number_format": "NumberFormat", "horizontal_alignment": "HorizontalAlignment", "vertical_alignment": "VerticalAlignment", "wrap_text": "WrapText"}
        for key, member in mappings.items():
            if key in operation:
                if member == "NumberFormat":
                    set_number_format(target, operation[key])
                else:
                    setattr(target, member, operation[key])
        for key, member in {"name": "Name", "size": "Size", "bold": "Bold", "italic": "Italic", "color": "Color"}.items():
            if key in operation.get("font", {}):
                setattr(target.Font, member, operation["font"][key])
        if "fill_color" in operation:
            target.Interior.Color = operation["fill_color"]
        for key, member in {"color": "Color", "weight": "Weight", "line_style": "LineStyle"}.items():
            if key in operation.get("borders", {}):
                setattr(target.Borders, member, operation["borders"][key])
    elif kind == "resize":
        if "row_height" in operation:
            target.RowHeight = operation["row_height"]
        if "column_width" in operation:
            target.ColumnWidth = operation["column_width"]
        if operation.get("autofit") in {"rows", "both"}:
            target.Rows.AutoFit()
        if operation.get("autofit") in {"columns", "both"}:
            target.Columns.AutoFit()
    elif kind in {"insert_range", "delete_range"}:
        shift = {"down": -4121, "right": -4161, "up": -4162, "left": -4159}[operation.get("shift", "down" if kind == "insert_range" else "up")]
        getattr(target, "Insert" if kind == "insert_range" else "Delete")(Shift=shift)
    elif kind == "copy_range":
        destination = target_range(workbook, {"sheet": operation["target_sheet"], "range": operation["target_range"]}, context)
        copy_range(target, destination, operation.get("mode", "all"), operation.get("transpose", False), workbook)
    elif kind in {"merge", "unmerge"}:
        getattr(target, "Merge" if kind == "merge" else "UnMerge")()
    elif kind == "sheet":
        import pythoncom
        sheet = worksheet(workbook, operation["sheet"]) if "sheet" in operation else None
        if action == "add":
            created = workbook.Worksheets.Add(pythoncom.Missing, workbook.Sheets(workbook.Sheets.Count))
            created.Name = operation["name"]
        elif action == "rename":
            sheet.Name = operation["name"]
        elif action == "delete":
            sheet.Delete()
        elif action == "visibility":
            sheet.Visible = {"visible": -1, "hidden": 0, "very_hidden": 2}[operation["visibility"]]
        elif action in {"move", "copy"}:
            index = operation.get("index", workbook.Sheets.Count)
            require(1 <= index <= workbook.Sheets.Count, "invalid_sheet_index", "A posição de aba não existe.")
            anchor = workbook.Sheets(index)
            method = "Move" if action == "move" else "Copy"
            sheet._oleobj_.InvokeTypes(sheet._oleobj_.GetIDsOfNames(method), 0, 1, (24, 0), ((12, 17), (12, 17)), anchor._oleobj_, pythoncom.Missing)
            if action == "copy" and "name" in operation:
                workbook.Sheets(index).Name = operation["name"]
    elif kind == "name":
        names = worksheet(workbook, operation["sheet"]).Names if "sheet" in operation else workbook.Names
        if action == "delete":
            names.Item(operation["name"]).Delete()
        else:
            check_formula(operation["refers_to"], context)
            names.Add(Name=operation["name"], RefersTo=operation["refers_to"])
    elif kind == "table":
        import pythoncom
        sheet = worksheet(workbook, operation["sheet"])
        if action == "create":
            source = target_range(workbook, operation, context)
            table = sheet.ListObjects.Add(1, source, pythoncom.Missing, 1)
            table.Name = operation["name"]
        else:
            table = sheet.ListObjects.Item(operation["name"])
            if action == "resize":
                table.Resize(target_range(workbook, operation, context))
            elif action == "delete":
                table.Delete()
                return
            elif action == "convert":
                table.Unlist()
                return
            elif action == "append":
                data = matrix(operation["values"], context)
                require(len(data[0]) == table.ListColumns.Count, "range_shape_mismatch", "A matriz deve conter todas as colunas da tabela.")
                for row in operation["values"]:
                    added = table.ListRows.Add().Range
                    preserved = {column for column, value in enumerate(row, 1)
                                 if value is None and operation.get("preserve_calculated_columns", True) and added.Cells(1, column).HasFormula}
                    if not preserved:
                        write_values(added, [row], context)
                    else:
                        start = 1
                        while start <= len(row):
                            if start in preserved:
                                start += 1
                                continue
                            end = start
                            while end + 1 <= len(row) and end + 1 not in preserved:
                                end += 1
                            write_values(resize_range(added.Cells(1, start), 1, end - start + 1), [row[start - 1:end]], context)
                            start = end + 1
            elif action == "column_formula":
                check_formula(operation["formula"], context)
                body = table.ListColumns.Item(operation["column"]).DataBodyRange
                require(body is not None, "empty_table", "A tabela não possui linhas para aplicar a fórmula.")
                write_formula(body, operation["formula"])
        if "style" in operation:
            table.TableStyle = operation["style"]
        if "show_totals" in operation:
            table.ShowTotals = operation["show_totals"]
    elif kind == "filter":
        sheet = worksheet(workbook, operation["sheet"])
        if action == "clear":
            if sheet.FilterMode:
                sheet.ShowAllData()
        else:
            kwargs = {"Field": operation["field"], "Criteria1": operation["criteria1"]}
            if "operator" in operation:
                kwargs["Operator"] = operation["operator"]
            if "criteria2" in operation:
                kwargs["Criteria2"] = operation["criteria2"]
            target.AutoFilter(**kwargs)
    elif kind == "sort":
        sorting = worksheet(workbook, operation["sheet"]).Sort
        sorting.SortFields.Clear()
        for key in operation["keys"]:
            require(key["column"] <= target.Columns.Count, "invalid_sort_column", "A coluna de ordenação está fora do intervalo.")
            sorting.SortFields.Add(Key=target.Columns(key["column"]), SortOn=0, Order=1 if key.get("order", "ascending") == "ascending" else 2)
        sorting.SetRange(target)
        sorting.Header = 1 if operation.get("header", True) else 2
        sorting.Apply()
    elif kind == "validation":
        validation = target.Validation
        validation.Delete()
        if action != "delete":
            invariant_method(validation, "Add", {"whole": 1, "decimal": 2, "list": 3, "date": 4, "time": 5, "text_length": 6, "custom": 7}[operation["validation_type"]],
                             operation.get("error_style", 1), operation.get("operator", 1),
                             validation_formula(session, operation, context), rule_formula(session, operation.get("formula2", ""), context))
            for key, member in {"ignore_blank": "IgnoreBlank", "in_cell_dropdown": "InCellDropdown", "input_title": "InputTitle", "input_message": "InputMessage", "error_title": "ErrorTitle", "error_message": "ErrorMessage"}.items():
                if key in operation:
                    setattr(validation, member, operation[key])
            validation.ShowError = True
            validation.ShowInput = True
    elif kind == "conditional_format":
        conditions = target.FormatConditions
        if action == "delete":
            conditions.Delete()
        else:
            rule_type = operation["rule_type"]
            if rule_type == "data_bar":
                rule = conditions.AddDatabar()
            elif rule_type == "color_scale":
                rule = conditions.AddColorScale(ColorScaleType=3)
            elif rule_type == "icon_set":
                rule = conditions.AddIconSetCondition()
            else:
                rule = invariant_method(conditions, "Add", 2 if rule_type == "expression" else 1, operation.get("operator", 1),
                                        rule_formula(session, operation.get("formula1", ""), context), rule_formula(session, operation.get("formula2", ""), context))
            if "color" in operation:
                if rule_type == "data_bar":
                    rule.BarColor.Color = operation["color"]
                else:
                    rule.Interior.Color = operation["color"]
            if "priority" in operation:
                rule.Priority = operation["priority"]
            if "stop_if_true" in operation:
                rule.StopIfTrue = operation["stop_if_true"]
    elif kind == "pivot":
        import pythoncom
        from .native import ensure_pivot_source_ready
        sheet = worksheet(workbook, operation["sheet"])
        if action == "create":
            source = target_range(workbook, {"sheet": operation["source_sheet"], "range": operation["source_range"]}, context)
            ensure_pivot_source_ready(workbook, session, context, source=source)
            cache = workbook.PivotCaches().Create(1, source)
            pivot = cache.CreatePivotTable(sheet.Range(operation["destination"]), operation["name"])
        else:
            pivot = sheet.PivotTables(operation["name"])
            if action == "delete":
                pivot.TableRange2.Clear()
                return
            require_pivot_or_slicer_policy(pivot.PivotCache(), context)
            if action == "refresh" or operation.get("fields"):
                ensure_pivot_source_ready(workbook, session, context, cache=pivot.PivotCache())
            if action == "refresh":
                require(context.profile.get("allow_refresh") is True or int(pivot.PivotCache().SourceType) == 1,
                        "external_data_denied", "Atualização de pivô externo não autorizada.")
                require(pivot.RefreshTable() is True, "refresh_failed", "O pivô não concluiu a atualização.")
        for field in operation.get("fields", []):
            item = pivot.PivotFields(field["name"])
            if field["orientation"] == "data":
                pivot.AddDataField(item, pythoncom.Missing, AGGREGATIONS[field.get("function", "sum")])
            else:
                item.Orientation = ORIENTATIONS[field["orientation"]]
                if "position" in field:
                    item.Position = field["position"]
        if "style" in operation:
            pivot.TableStyle2 = operation["style"]
        if action in {"create", "refresh"} or operation.get("fields"):
            defer_pivot_refresh(session, sheet.Name, pivot.Name)
    elif kind == "chart":
        sheet = worksheet(workbook, operation["sheet"])
        if action == "create":
            item = sheet.ChartObjects().Add(operation.get("left", 0), operation.get("top", 0), operation.get("width", 480), operation.get("height", 280))
            item.Name = operation["name"]
        else:
            item = sheet.ChartObjects(operation["name"])
        if action == "delete":
            item.Delete()
            return
        if "range" in operation:
            item.Chart.SetSourceData(Source=target_range(workbook, operation, context))
        if "chart_type" in operation:
            item.Chart.ChartType = operation["chart_type"]
        if "title" in operation:
            item.Chart.HasTitle = bool(operation["title"])
            if operation["title"]:
                item.Chart.ChartTitle.Text = operation["title"]
        for field in ("left", "top", "width", "height"):
            if field in operation:
                setattr(item, field.title(), operation[field])
        from .advanced_operations import configure_chart
        configure_chart(item.Chart, operation, workbook, context)
    elif kind == "slicer":
        import pythoncom
        sheet = worksheet(workbook, operation["sheet"])
        if action == "create":
            pivot = sheet.PivotTables(operation["pivot"])
            require_pivot_or_slicer_policy(pivot.PivotCache(), context)
            cache = workbook.SlicerCaches.Add2(pivot, operation["field"], pythoncom.Missing, 2 if operation.get("timeline") else 1)
            cache.Slicers.Add(sheet, pythoncom.Missing, operation["name"], operation["field"], operation.get("top", 0),
                              operation.get("left", 0), operation.get("width", 150), operation.get("height", 200))
        else:
            slicer = next((s for cache in workbook.SlicerCaches for s in cache.Slicers if s.Name == operation["name"]), None)
            require(slicer is not None, "object_not_found", "A segmentação não foi encontrada.")
            if action == "delete":
                slicer.Delete()
            else:
                cache = slicer.SlicerCache
                require_pivot_or_slicer_policy(cache, context)
                selected = set(operation["items"])
                if cache.OLAP:
                    cache.VisibleSlicerItemsList = tuple(selected)
                else:
                    names = {str(item.Name) for item in cache.SlicerItems}
                    require(selected <= names and selected, "invalid_slicer_items", "A seleção contém itens inexistentes ou está vazia.")
                    cache.ClearManualFilter()
                    for item in cache.SlicerItems:
                        if str(item.Name) not in selected:
                            item.Selected = False
    elif kind == "query":
        queries = workbook.Queries
        existing = next((query for query in queries if query.Name == operation["name"]), None)
        if action == "delete":
            require(existing is not None, "object_not_found", "A consulta não foi encontrada.")
            existing.Delete()
        elif existing is None:
            queries.Add(Name=operation["name"], Formula=operation["formula"])
        else:
            existing.Formula = operation["formula"]
    elif kind == "model_measure":
        model = workbook.Model
        existing = next((measure for measure in model.ModelMeasures if measure.Name == operation["name"]), None)
        if action == "delete":
            require(existing is not None, "object_not_found", "A medida não foi encontrada.")
            existing.Delete()
        elif existing is not None:
            existing.Formula = operation["formula"]
            if "description" in operation:
                existing.Description = operation["description"]
        else:
            model.ModelMeasures.Add(MeasureName=operation["name"], AssociatedTable=model.ModelTables(operation["table"]),
                                    Formula=operation["formula"], FormatInformation=model.ModelFormatGeneral,
                                    Description=operation.get("description", ""))
    elif kind == "model_relationship":
        model = workbook.Model
        left = model.ModelTables(operation["table"]).ModelTableColumns(operation["column"])
        right = model.ModelTables(operation["related_table"]).ModelTableColumns(operation["related_column"])
        if action == "add":
            model.ModelRelationships.Add(ForeignKeyColumn=left, PrimaryKeyColumn=right)
        else:
            existing = next((item for item in model.ModelRelationships if item.ForeignKeyColumn.Name == left.Name and item.ForeignKeyTable.Name == left.Parent.Name
                             and item.PrimaryKeyColumn.Name == right.Name and item.PrimaryKeyTable.Name == right.Parent.Name), None)
            require(existing is not None, "object_not_found", "O relacionamento não foi encontrado.")
            existing.Delete()
    elif kind == "comment":
        require(target.CountLarge == 1, "invalid_range", "Comentários exigem uma única célula.")
        member = "CommentThreaded" if operation.get("threaded") else "Comment"
        existing = getattr(target, member)
        if existing is not None:
            existing.Delete()
        if action != "delete":
            getattr(target, "AddCommentThreaded" if operation.get("threaded") else "AddComment")(operation["text"])
    elif kind == "hyperlink":
        target.Hyperlinks.Delete()
        if action != "delete":
            previous = target.Value2
            link = worksheet(workbook, operation["sheet"]).Hyperlinks.Add(target, operation.get("address", ""), operation.get("sub_address", ""))
            if "text" in operation or previous is not None:
                link.TextToDisplay = operation.get("text", str(previous))
    elif kind == "page_setup":
        setup = worksheet(workbook, operation["sheet"]).PageSetup
        for key, member in {"print_area": "PrintArea", "orientation": "Orientation", "paper_size": "PaperSize", "zoom": "Zoom",
                            "fit_to_pages_wide": "FitToPagesWide", "fit_to_pages_tall": "FitToPagesTall", "print_title_rows": "PrintTitleRows", "print_title_columns": "PrintTitleColumns",
                            "left_header": "LeftHeader", "center_header": "CenterHeader", "right_header": "RightHeader", "left_footer": "LeftFooter", "center_footer": "CenterFooter", "right_footer": "RightFooter"}.items():
            if key in operation:
                if key.startswith("fit_to_"):
                    setup.Zoom = False
                setattr(setup, member, operation[key] or False if key.startswith("fit_to_") else operation[key])
    elif kind == "protect":
        owner = worksheet(workbook, operation["sheet"]) if "sheet" in operation else workbook
        password = context.resolve_password(operation.get("password_ref")) or ""
        if action == "protect" and "sheet" not in operation:
            owner.Protect(password, True, False)
        else:
            getattr(owner, "Protect" if action == "protect" else "Unprotect")(password)
    elif kind == "goal_seek":
        check_calculation_sources(workbook, context)
        check = target.GoalSeek(Goal=operation["goal"], ChangingCell=worksheet(workbook, operation["sheet"]).Range(operation["changing_range"]))
        require(check is True, "calculation_failed", "Goal Seek não encontrou solução para a meta.")
    else:
        require(False, "operation_unavailable", "Tipo de alteração Excel não implementado.")
    session.assert_hidden()
