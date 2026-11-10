"""Análise de hipóteses, organização, fontes de dados e cópia entre pastas Excel."""
from __future__ import annotations

from pathlib import Path
import shutil

from ..component.ooxml import preflight
from ..exception import require
from .operations import AGGREGATIONS, target_range, worksheet


KINDS = frozenset({"autofill", "outline", "subtotal", "scenario", "data_table", "connection", "query_load", "copy_sheet_from"})


def _cell(workbook, sheet, address, context):
    target = target_range(workbook, {"sheet": sheet, "range": address}, context)
    require(int(target.CountLarge) == 1, "single_cell_required", "A entrada desta operação deve ser uma única célula.")
    return target


def _external_policy(context):
    require(context.profile.get("allow_refresh") is True and context.profile.get("trusted_content") is True,
            "external_data_denied", "Conexões exigem fontes externas habilitadas e conteúdo explicitamente confiável.")


def _connection(workbook, operation, context):
    _external_policy(context)
    action, name = operation["action"], operation["name"]
    if action == "delete":
        workbook.Connections.Item(name).Delete()
        return
    reference = operation.get("connection_ref")
    require(not (reference and "connection_string" in operation), "invalid_argument", "Informe texto ou referência da conexão, sem combinar as duas opções.")
    connection_string = context.resolve_password(reference) if reference else operation.get("connection_string")
    if action == "add":
        require(isinstance(connection_string, str) and connection_string and "command_text" in operation,
                "invalid_connection", "A criação da conexão exige string de conexão e comando.")
        connection = workbook.Connections.Add2(name, operation.get("description", ""), connection_string, operation["command_text"],
                                                operation.get("command_type", 2), operation.get("create_model_connection", False), False)
    else:
        connection = workbook.Connections.Item(name)
        require(not operation.get("create_model_connection"), "invalid_connection", "Criar conexão de modelo exige uma nova conexão.")
        if "description" in operation:
            connection.Description = operation["description"]
    if "refresh_with_refresh_all" in operation:
        connection.RefreshWithRefreshAll = operation["refresh_with_refresh_all"]
    connection_type = int(connection.Type)
    if connection_type == 8:
        require(action == "add" and operation.get("create_model_connection") is True,
                "connection_type_unavailable", "Uma conexão de tabela local do modelo deve ser criada explicitamente; edição de provedor não se aplica.")
        return
    require(connection_type in {1, 2}, "connection_type_unavailable", "A edição declarativa da conexão aceita fontes OLEDB/ODBC.")
    provider = connection.OLEDBConnection if connection_type == 1 else connection.ODBCConnection
    provider.SavePassword = False
    provider.RefreshOnFileOpen = False
    provider.BackgroundQuery = False
    if action == "update":
        if connection_string is not None:
            provider.Connection = connection_string
        if "command_text" in operation:
            provider.CommandText = operation["command_text"]
        if "command_type" in operation:
            require(connection_type == 1, "connection_type_unavailable", "CommandType exige conexão OLEDB.")
            provider.CommandType = operation["command_type"]


def _query_load(workbook, operation, context):
    require(context.profile.get("allow_refresh") is True, "external_data_denied", "Carregar consulta exige a permissão de fontes externas.")
    query = workbook.Queries.Item(operation["name"])
    query_name = str(query.Name)
    require(all(character not in query_name for character in '\x00\r\n;"'), "invalid_query_name", "O nome da consulta não permite construir uma conexão Mashup inequívoca.")
    destination = _cell(workbook, operation["sheet"], operation["destination"], context)
    sheet = worksheet(workbook, operation["sheet"])
    connection = "OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=" + query_name + ";Extended Properties=\"\""
    table = sheet.ListObjects.Add(SourceType=0, Source=connection, LinkSource=True, XlListObjectHasHeaders=1, Destination=destination)
    table.Name = operation["table_name"]
    target = table.QueryTable
    target.CommandType = 2
    target.CommandText = "SELECT * FROM [" + query_name.replace("]", "]]") + "]"
    target.BackgroundQuery = False
    target.RefreshOnFileOpen = False
    target.SavePassword = False
    if operation.get("refresh", False):
        from .operations import check_calculation_sources
        check_calculation_sources(workbook, context)
        completed = target.Refresh(BackgroundQuery=False)
        require(completed is True and not target.Refreshing, "refresh_failed", "A consulta carregada não concluiu a atualização.")
        require(int(table.Range.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "Os dados da consulta excedem o limite de células configurado.")


def _copy_sheet(workbook, operation, session, context):
    import pythoncom
    source = context.input_path(operation["input_path"])
    information = preflight(source, context.profile, context.defaults)
    require(information.get("native_open_allowed") is True, "active_content_blocked", "A pasta de origem não passou pelo preflight nativo.")
    copied = context.temporary_path(source.suffix)
    shutil.copyfile(source, copied)
    external = session.open(copied, context.resolve_password(operation.get("password_ref")))
    try:
        before = int(workbook.Sheets.Count)
        original = worksheet(external, operation["sheet"])
        # Use an explicit Before argument: this build's late-bound Copy loses
        # an omitted first parameter and silently creates a new workbook.
        # The copied sheet is inserted before the last existing destination.
        anchor = workbook.Sheets.Item(before)
        original._oleobj_.InvokeTypes(original._oleobj_.GetIDsOfNames("Copy"), 0, 1, (24, 0), ((12, 17), (12, 17)), anchor._oleobj_, pythoncom.Missing)
        require(int(workbook.Sheets.Count) == before + 1, "sheet_copy_failed", "A cópia não criou a aba esperada na pasta de destino.")
        created = workbook.Sheets.Item(before)
        if "name" in operation:
            created.Name = operation["name"]
        require(int(created.UsedRange.CountLarge) <= context.defaults.get("max_cells", 1000000), "cell_limit_exceeded", "A aba copiada excede o limite de células configurado.")
    finally:
        external.Close(SaveChanges=False)


def apply_analysis_operation(workbook, operation, session, context) -> bool:
    """Aplica uma família de análise e retorna falso somente para tipo alheio.

    Conexões nunca são tratadas como atualizadas apenas porque foram criadas.
    ``Scenario.Show`` aplica entradas de hipóteses, sem abrir uma janela. A
    cópia entre pastas usa fonte autorizada, preflight e uma cópia de execução.
    """
    kind = operation.get("type")
    if kind not in KINDS:
        return False
    if kind == "connection":
        _connection(workbook, operation, context)
    elif kind == "query_load":
        _query_load(workbook, operation, context)
    elif kind == "copy_sheet_from":
        _copy_sheet(workbook, operation, session, context)
    elif kind == "outline":
        sheet = worksheet(workbook, operation["sheet"])
        selected = sheet.Range(operation["range"])
        require(int(selected.Areas.Count) == 1, "invalid_range", "Use um intervalo contínuo para a estrutura de tópicos.")
        target = selected.EntireRow if operation["orientation"] == "rows" else selected.EntireColumn
        action = operation["action"]
        if action in {"group", "ungroup", "clear"}:
            getattr(target, {"group": "Group", "ungroup": "Ungroup", "clear": "ClearOutline"}[action])()
        else:
            require(int(selected.Rows.Count if operation["orientation"] == "rows" else selected.Columns.Count) == 1,
                    "summary_range_required", "Expandir/recolher exige uma única linha ou coluna de resumo do agrupamento.")
            target.ShowDetail = operation["show"]
    elif kind == "scenario":
        sheet = worksheet(workbook, operation["sheet"])
        action = operation["action"]
        scenarios = sheet.Scenarios()
        if action == "add":
            changing = target_range(workbook, {"sheet": operation["sheet"], "range": operation["changing_range"]}, context)
            require(int(changing.CountLarge) <= 32 and int(changing.CountLarge) == len(operation["values"]),
                    "scenario_values_mismatch", "Um cenário aceita até 32 células e exige um valor por célula.")
            require(all(isinstance(value, (str, int, float, bool)) and value is not None for value in operation["values"]),
                    "invalid_scenario_values", "Valores de cenário devem ser texto, número ou booleano.")
            values = tuple("'" + value if isinstance(value, str) else value for value in operation["values"])
            scenarios.Add(Name=operation["name"], ChangingCells=changing, Values=values)
        else:
            if action == "show":
                from .operations import check_calculation_sources
                check_calculation_sources(workbook, context)
            getattr(scenarios.Item(operation["name"]), "Show" if action == "show" else "Delete")()
    else:
        target = target_range(workbook, operation, context)
        if kind == "autofill":
            destination = target_range(workbook, {"sheet": operation["sheet"], "range": operation["destination"]}, context)
            source_row, source_column = int(target.Row), int(target.Column)
            require(int(destination.Row) <= source_row and int(destination.Column) <= source_column
                    and int(destination.Row) + int(destination.Rows.Count) >= source_row + int(target.Rows.Count)
                    and int(destination.Column) + int(destination.Columns.Count) >= source_column + int(target.Columns.Count),
                    "invalid_fill_destination", "O destino do preenchimento deve conter o intervalo de origem.")
            target.AutoFill(Destination=destination, Type=operation.get("fill_type", 0))
        elif kind == "subtotal":
            columns = int(target.Columns.Count)
            require(int(target.Rows.Count) >= 2 and 1 <= operation["group_by"] <= columns
                    and all(1 <= column <= columns for column in operation["total_columns"]),
                    "invalid_subtotal", "Agrupamento e colunas dos subtotais devem pertencer a um intervalo com cabeçalho e dados.")
            target.Subtotal(GroupBy=operation["group_by"], Function=AGGREGATIONS[operation["function"]], TotalList=tuple(operation["total_columns"]),
                            Replace=operation.get("replace", True), PageBreaks=operation.get("page_breaks", False), SummaryBelowData=1 if operation.get("summary_below", True) else 0)
        elif kind == "data_table":
            from .operations import check_calculation_sources
            check_calculation_sources(workbook, context)
            require("row_input" in operation or "column_input" in operation, "input_required", "Tabela de hipóteses exige ao menos uma célula de entrada.")
            inputs = {}
            for key, parameter in (("row_input", "RowInput"), ("column_input", "ColumnInput")):
                if key in operation:
                    inputs[parameter] = _cell(workbook, operation["sheet"], operation[key], context)
            target.Table(**inputs)
    session.assert_hidden()
    return True
