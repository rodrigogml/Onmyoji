"""Schemas fechados das operações funcionais Excel, sem código executável livre."""
from __future__ import annotations


def text(maximum=4096, minimum=1, description=""):
    return {"type": "string", "minLength": minimum, "maxLength": maximum, "description": description}


def enum(*values):
    return {"type": "string", "enum": list(values)}


def obj(properties, required=()):
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def array(items, minimum=0, maximum=1000):
    return {"type": "array", "items": items, "minItems": minimum, "maxItems": maximum}


BOOL = {"type": "boolean"}
NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
POSITIVE = {"type": "integer", "minimum": 1}
NONNEGATIVE = {"type": "integer", "minimum": 0}
VALUE = {"type": ["string", "number", "boolean", "null"]}
VALUES = array(array(VALUE, 1, 16384), 1, 1048576)
COM_VALUE = {"oneOf": [VALUE, array(VALUE)]}
PATH = text(description="Caminho absoluto dentro das raízes autorizadas do workspace.")
SHEET = text(31)
RANGE = text(description="Referência A1 explícita; nomes e referências estruturadas dependem do backend.")
COLOR = {"type": "integer", "minimum": 0, "maximum": 16777215}
PASSWORD_REF = obj({"credential_profile": text(), "credential_entry_path": text(), "field": enum("password")}, ["credential_profile", "credential_entry_path"])
PAGE = {"offset": NONNEGATIVE, "limit": {"type": "integer", "minimum": 1, "maximum": 1000}}
SELECTION = {"sheet": SHEET, "range": RANGE}
CALCULATION = obj({"mode": enum("none", "incremental", "full", "full_rebuild"), "refresh_connections": BOOL, "refresh_require_verified": BOOL, "refresh_pivots": BOOL, "enable_vba_udfs": BOOL,
                   "output_mode": enum("preserve", "manual", "automatic"), "calculate_on_open": BOOL})
VALIDATION = obj({"reject_new_formula_errors": BOOL, "require_preservation": BOOL})
FONT = obj({"name": text(200), "size": {"type": "number", "exclusiveMinimum": 0, "maximum": 409}, "bold": BOOL, "italic": BOOL, "color": COLOR})
BORDER = obj({"color": COLOR, "weight": INTEGER, "line_style": INTEGER})
SELECTOR = array(obj({"collection": text(80), "name": text(), "index": POSITIVE}, ["collection"]), 0, 20)


def operation(kind, properties, required=(), conditions=None):
    result = obj({"type": {"const": kind}, **properties}, ["type", *required])
    if conditions:
        result["allOf"] = [{"if": {"properties": {"action": {"const": action}}, "required": ["action"]},
                            "then": {"required": fields}} for action, fields in conditions.items()]
    return result


OPERATIONS = [
    operation("set_values", {**SELECTION, "values": VALUES}, ["sheet", "range", "values"]),
    operation("set_formula", {**SELECTION, "formula": text(32767), "notation": enum("A1", "R1C1"), "language": enum("invariant", "local"), "array_mode": enum("dynamic", "legacy")}, ["sheet", "range", "formula"]),
    operation("clear", {**SELECTION, "what": enum("contents", "formats", "all")}, ["sheet", "range"]),
    operation("format_range", {**SELECTION, "number_format": text(1000), "font": FONT, "fill_color": COLOR, "horizontal_alignment": INTEGER,
                                "vertical_alignment": INTEGER, "wrap_text": BOOL, "borders": BORDER}, ["sheet", "range"]),
    operation("resize", {**SELECTION, "row_height": {"type": "number", "minimum": 0, "maximum": 409},
                          "column_width": {"type": "number", "minimum": 0, "maximum": 255}, "autofit": enum("rows", "columns", "both")}, ["sheet", "range"]),
    operation("insert_range", {**SELECTION, "shift": enum("down", "right")}, ["sheet", "range"]),
    operation("delete_range", {**SELECTION, "shift": enum("up", "left")}, ["sheet", "range"]),
    operation("copy_range", {**SELECTION, "target_sheet": SHEET, "target_range": RANGE, "mode": enum("all", "values", "formulas", "formats"), "transpose": BOOL}, ["sheet", "range", "target_sheet", "target_range"]),
    operation("merge", SELECTION, ["sheet", "range"]),
    operation("unmerge", SELECTION, ["sheet", "range"]),
    operation("sheet", {"action": enum("add", "rename", "delete", "copy", "move", "visibility"), "sheet": SHEET, "name": SHEET,
                         "index": POSITIVE, "visibility": enum("visible", "hidden", "very_hidden")}, ["action"],
              {"add": ["name"], "rename": ["sheet", "name"], "delete": ["sheet"], "copy": ["sheet"], "move": ["sheet", "index"], "visibility": ["sheet", "visibility"]}),
    operation("name", {"action": enum("set", "delete"), "name": text(255), "refers_to": text(32767), "sheet": SHEET}, ["action", "name"], {"set": ["refers_to"]}),
    operation("table", {"action": enum("create", "resize", "delete", "convert", "append", "column_formula"), "sheet": SHEET, "name": text(255),
                         "range": RANGE, "style": text(100), "values": VALUES, "column": text(255), "formula": text(32767), "show_totals": BOOL,
                         "preserve_calculated_columns": BOOL}, ["action", "sheet", "name"],
              {"create": ["range"], "resize": ["range"], "append": ["values"], "column_formula": ["column", "formula"]}),
    operation("filter", {**SELECTION, "action": enum("apply", "clear"), "field": POSITIVE, "criteria1": COM_VALUE, "operator": INTEGER, "criteria2": COM_VALUE}, ["sheet", "range", "action"], {"apply": ["field", "criteria1"]}),
    operation("sort", {**SELECTION, "keys": array(obj({"column": POSITIVE, "order": enum("ascending", "descending")}, ["column"]), 1, 64), "header": BOOL}, ["sheet", "range", "keys"]),
    operation("validation", {**SELECTION, "action": enum("set", "delete"), "validation_type": enum("list", "whole", "decimal", "date", "time", "text_length", "custom"),
                              "formula1": {"type": ["string", "number"], "maxLength": 255}, "formula2": VALUE, "list_values": array(text(255, 0), 1, 255),
                              "operator": INTEGER, "error_style": INTEGER, "ignore_blank": BOOL, "in_cell_dropdown": BOOL,
                              "input_title": text(32, 0), "input_message": text(255, 0), "error_title": text(32, 0), "error_message": text(255, 0)}, ["sheet", "range", "action"], {"set": ["validation_type"]}),
    operation("conditional_format", {**SELECTION, "action": enum("add", "delete"), "rule_type": enum("expression", "cell_value", "data_bar", "color_scale", "icon_set"),
                                      "formula1": VALUE, "formula2": VALUE, "operator": INTEGER, "color": COLOR, "priority": POSITIVE, "stop_if_true": BOOL},
              ["sheet", "range", "action"], {"add": ["rule_type"]}),
    operation("pivot", {"action": enum("create", "layout", "refresh", "delete"), "sheet": SHEET, "name": text(255), "source_sheet": SHEET,
                         "source_range": RANGE, "destination": RANGE, "style": text(100), "fields": array(obj({"name": text(255), "orientation": enum("row", "column", "page", "data"),
                          "position": POSITIVE, "function": enum("sum", "count", "average", "min", "max", "product", "count_numbers", "stddev", "variance")}, ["name", "orientation"]), 0, 256)},
              ["action", "sheet", "name"], {"create": ["source_sheet", "source_range", "destination"]}),
    operation("chart", {"action": enum("create", "update", "delete"), "sheet": SHEET, "name": text(255), "range": RANGE, "chart_type": INTEGER,
                         "title": text(1000), "left": NUMBER, "top": NUMBER, "width": {"type": "number", "exclusiveMinimum": 0}, "height": {"type": "number", "exclusiveMinimum": 0}}, ["action", "sheet", "name"], {"create": ["range"]}),
    operation("slicer", {"action": enum("create", "delete", "select"), "sheet": SHEET, "name": text(255), "pivot": text(255), "field": text(255),
                          "items": array(text()), "left": NUMBER, "top": NUMBER, "width": NUMBER, "height": NUMBER, "timeline": BOOL}, ["action", "sheet", "name"], {"create": ["pivot", "field"], "select": ["items"]}),
    operation("query", {"action": enum("set", "delete"), "name": text(255), "formula": text(2000000)}, ["action", "name"], {"set": ["formula"]}),
    operation("model_measure", {"action": enum("set", "delete"), "table": text(255), "name": text(255), "formula": text(2000000), "description": text(10000, 0)}, ["action", "table", "name"], {"set": ["formula"]}),
    operation("model_relationship", {"action": enum("add", "delete"), "table": text(255), "column": text(255), "related_table": text(255), "related_column": text(255)}, ["action", "table", "column", "related_table", "related_column"]),
    operation("comment", {**SELECTION, "action": enum("set", "delete"), "text": text(32767, 0), "threaded": BOOL}, ["sheet", "range", "action"], {"set": ["text"]}),
    operation("hyperlink", {**SELECTION, "action": enum("set", "delete"), "address": text(4096, 0), "sub_address": text(4096, 0), "text": text(32767, 0)}, ["sheet", "range", "action"]),
    operation("page_setup", {"sheet": SHEET, "print_area": text(4096, 0), "orientation": INTEGER, "paper_size": INTEGER, "zoom": {"type": "integer", "minimum": 10, "maximum": 400},
                              "fit_to_pages_wide": NONNEGATIVE, "fit_to_pages_tall": NONNEGATIVE, "print_title_rows": text(4096, 0), "print_title_columns": text(4096, 0),
                              "left_header": text(255, 0), "center_header": text(255, 0), "right_header": text(255, 0), "left_footer": text(255, 0), "center_footer": text(255, 0), "right_footer": text(255, 0)}, ["sheet"]),
    operation("protect", {"sheet": SHEET, "action": enum("protect", "unprotect"), "password_ref": PASSWORD_REF}, ["action"]),
    operation("goal_seek", {**SELECTION, "goal": NUMBER, "changing_range": RANGE}, ["sheet", "range", "goal", "changing_range"]),
    operation("autofill", {**SELECTION, "destination": RANGE, "fill_type": INTEGER}, ["sheet", "range", "destination"]),
    operation("outline", {**SELECTION, "action": enum("group", "ungroup", "show_detail", "clear"), "orientation": enum("rows", "columns"), "show": BOOL},
              ["sheet", "range", "action", "orientation"], {"show_detail": ["show"]}),
    operation("subtotal", {**SELECTION, "group_by": POSITIVE, "function": enum("sum", "count", "average", "min", "max", "product", "count_numbers", "stddev", "variance"),
                           "total_columns": array(POSITIVE, 1, 16384), "replace": BOOL, "page_breaks": BOOL, "summary_below": BOOL}, ["sheet", "range", "group_by", "function", "total_columns"]),
    operation("scenario", {"action": enum("add", "show", "delete"), "sheet": SHEET, "name": text(255), "changing_range": RANGE, "values": array(VALUE, 1, 32)},
              ["action", "sheet", "name"], {"add": ["changing_range", "values"]}),
    operation("data_table", {**SELECTION, "row_input": RANGE, "column_input": RANGE}, ["sheet", "range"]),
    operation("connection", {"action": enum("add", "delete", "update"), "name": text(255), "description": text(10000, 0), "connection_string": text(32767),
                              "connection_ref": obj({"credential_profile": text(), "credential_entry_path": text(), "field": enum("password", "notes")}, ["credential_profile", "credential_entry_path"]),
                              "command_text": text(2000000), "command_type": INTEGER, "create_model_connection": BOOL, "refresh_with_refresh_all": BOOL}, ["action", "name"]),
    operation("query_load", {"sheet": SHEET, "name": text(255), "destination": RANGE, "table_name": text(255), "refresh": BOOL}, ["sheet", "name", "destination", "table_name"]),
    operation("copy_sheet_from", {"input_path": PATH, "sheet": SHEET, "name": SHEET, "password_ref": PASSWORD_REF}, ["input_path", "sheet"]),
    operation("sparkline", {**SELECTION, "action": enum("create", "delete"), "source_sheet": SHEET, "source_range": RANGE,
                            "sparkline_type": enum("line", "column", "win_loss"), "color": COLOR}, ["sheet", "range", "action"], {"create": ["source_sheet", "source_range"]}),
    operation("shape", {"action": enum("add", "update", "delete"), "sheet": SHEET, "name": text(255), "shape_type": INTEGER,
                         "left": NUMBER, "top": NUMBER, "width": NUMBER, "height": NUMBER, "text": text(32767, 0), "fill_color": COLOR, "line_color": COLOR, "rotation": NUMBER},
              ["action", "sheet", "name"], {"add": ["shape_type", "left", "top", "width", "height"]}),
    operation("image", {"action": enum("add", "delete"), "sheet": SHEET, "name": text(255), "input_path": PATH,
                         "left": NUMBER, "top": NUMBER, "width": NUMBER, "height": NUMBER}, ["action", "sheet", "name"], {"add": ["input_path", "left", "top", "width", "height"]}),
    operation("control", {"action": enum("add", "update", "delete"), "sheet": SHEET, "name": text(255),
                           "control_type": enum("button", "checkbox", "dropdown", "groupbox", "label", "listbox", "optionbutton", "scrollbar", "spinner"),
                           "left": NUMBER, "top": NUMBER, "width": NUMBER, "height": NUMBER, "caption": text(32767, 0), "linked_cell": RANGE,
                           "list_fill_range": RANGE, "value": INTEGER, "minimum": INTEGER, "maximum": INTEGER, "small_change": INTEGER, "large_change": INTEGER},
              ["action", "sheet", "name"], {"add": ["control_type", "left", "top", "width", "height"]}),
    operation("view", {"sheet": SHEET, "freeze_rows": {"type": "integer", "minimum": 0, "maximum": 1048575},
                        "freeze_columns": {"type": "integer", "minimum": 0, "maximum": 16383}, "zoom": {"type": "integer", "minimum": 10, "maximum": 400},
                        "show_gridlines": BOOL, "show_headers": BOOL, "right_to_left": BOOL, "show_zeros": BOOL}, ["sheet"]),
    operation("timeline_filter", {"cache": text(255), "start_date": text(40), "end_date": text(40), "clear": BOOL}, ["cache"]),
    operation("pivot_source", {"sheet": SHEET, "name": text(255), "source_sheet": SHEET, "source_range": RANGE}, ["sheet", "name", "source_sheet", "source_range"]),
    operation("pivot_field", {"sheet": SHEET, "name": text(255), "field": text(255), "action": enum("clear", "items", "label", "value", "date"),
                               "items": array(text(), 1, 100000), "operator": enum("equals", "not_equals", "contains", "not_contains", "begins_with", "not_begins_with", "ends_with", "not_ends_with", "greater_than", "greater_or_equal", "less_than", "less_or_equal", "between", "not_between", "top_count", "bottom_count", "top_percent", "bottom_percent", "top_sum", "bottom_sum"),
                               "criteria1": VALUE, "criteria2": VALUE, "data_field": text(255)}, ["sheet", "name", "field", "action"],
              {"items": ["items"], "label": ["operator", "criteria1"], "value": ["operator", "criteria1", "data_field"], "date": ["operator", "criteria1"]}),
    operation("pivot_calculated_field", {"sheet": SHEET, "name": text(255), "field": text(255), "action": enum("set", "delete"), "formula": text(32767)}, ["sheet", "name", "field", "action"], {"set": ["formula"]}),
    operation("pivot_group", {"sheet": SHEET, "name": text(255), "field": text(255), "action": enum("group", "ungroup"),
                               "start": {"oneOf": [NUMBER, text(40), {"const": True}]}, "end": {"oneOf": [NUMBER, text(40), {"const": True}]},
                               "interval": {"type": "number", "exclusiveMinimum": 0}, "periods": array(BOOL, 7, 7)}, ["sheet", "name", "field", "action"]),
    operation("pivot_drilldown", {"sheet": SHEET, "name": text(255), "range": RANGE, "destination_sheet": SHEET}, ["sheet", "name", "range", "destination_sheet"]),
    operation("advanced_filter", {**SELECTION, "criteria_sheet": SHEET, "criteria_range": RANGE, "action": enum("in_place", "copy"),
                                   "copy_sheet": SHEET, "copy_range": RANGE, "unique": BOOL}, ["sheet", "range", "action"], {"copy": ["copy_sheet", "copy_range"]}),
]

CHART_SERIES = obj({"action": enum("add", "update", "delete"), "index": POSITIVE, "name": text(255), "source_sheet": SHEET, "values_range": RANGE, "x_values_range": RANGE,
                    "chart_type": INTEGER, "axis_group": enum("primary", "secondary")})
CHART_SERIES["allOf"] = [
    {"if": {"properties": {"action": {"enum": ["update", "delete"]}}, "required": ["action"]}, "then": {"required": ["index"]}},
    {"if": {"anyOf": [{"not": {"required": ["action"]}}, {"properties": {"action": {"const": "add"}}, "required": ["action"]}]}, "then": {"required": ["values_range"]}},
]
CHART_AXIS = obj({"axis": enum("category", "value", "series"), "group": enum("primary", "secondary"), "title": text(1000),
                  "minimum": NUMBER, "maximum": NUMBER, "number_format": text(1000)}, ["axis"])
for _operation in OPERATIONS:
    if _operation["properties"]["type"]["const"] == "validation":
        _operation.setdefault("allOf", []).extend([
            {"if": {"properties": {"action": {"const": "set"}, "validation_type": {"const": "list"}}, "required": ["action", "validation_type"]},
             "then": {"oneOf": [{"required": ["formula1"]}, {"required": ["list_values"]}]}},
            {"if": {"properties": {"action": {"const": "set"}, "validation_type": {"not": {"const": "list"}}}, "required": ["action", "validation_type"]},
             "then": {"required": ["formula1"]}},
            {"if": {"required": ["list_values"]}, "then": {"properties": {"action": {"const": "set"}, "validation_type": {"const": "list"}}, "required": ["validation_type"], "not": {"required": ["formula1"]}}},
        ])
    if _operation["properties"]["type"]["const"] == "chart":
        _operation["properties"].update(series=array(CHART_SERIES, 1, 255), axes=array(CHART_AXIS, 0, 6), has_legend=BOOL, legend_position=INTEGER,
                                         data_labels=obj({"show_value": BOOL, "show_series_name": BOOL, "show_category_name": BOOL, "number_format": text(1000)}))
    if _operation["properties"]["type"]["const"] == "data_table":
        _operation["anyOf"] = [{"required": ["row_input"]}, {"required": ["column_input"]}]
    if _operation["properties"]["type"]["const"] == "connection":
        _operation["allOf"] = [{"if": {"properties": {"action": {"const": "add"}}, "required": ["action"]},
                                "then": {"required": ["command_text"], "oneOf": [{"required": ["connection_string"]}, {"required": ["connection_ref"]}]}}]
    if _operation["properties"]["type"]["const"] == "timeline_filter":
        _operation["anyOf"] = [{"properties": {"clear": {"const": True}}, "required": ["clear"]}, {"required": ["start_date", "end_date"]}]
    if _operation["properties"]["type"]["const"] == "pivot_field":
        _operation.setdefault("allOf", []).append({"if": {"properties": {"operator": {"enum": ["between", "not_between"]}}, "required": ["operator"]}, "then": {"required": ["criteria2"]}})

DATA_RULE = obj({"type": enum("required", "unique", "type", "range", "regex", "allowed_values"), **SELECTION,
                 "value_type": enum("string", "number", "boolean", "date", "integer"), "minimum": NUMBER, "maximum": NUMBER,
                 "pattern": text(1000), "values": array(VALUE), "allow_blank": BOOL}, ["type", "sheet", "range"])
DATA_RULE["allOf"] = [
    {"if": {"properties": {"type": {"const": kind}}, "required": ["type"]}, "then": {"required": fields}}
    for kind, fields in {"type": ["value_type"], "regex": ["pattern"], "allowed_values": ["values"]}.items()
] + [{"if": {"properties": {"type": {"const": "range"}}, "required": ["type"]},
      "then": {"anyOf": [{"required": ["minimum"]}, {"required": ["maximum"]}]}}]

JOB_SPECS = {
    "get_job": ("Consulta estado e progresso da execução Excel.", {"job_id": text(32)}, ["job_id"]),
    "get_job_result": ("Lê resultado paginado. JSON extenso usa result_fragment e next_offset; concatene os fragmentos antes de decodificar.", {"job_id": text(32), **PAGE}, ["job_id"]),
    "cancel_job": ("Cancela somente a execução e os processos comprovadamente próprios.", {"job_id": text(32)}, ["job_id"]),
    "list_jobs": ("Lista execuções do perfil selecionado.", {}, []),
}
SPECS = {
    "get_capabilities": ("Diagnóstico estático de backends e capacidades; não inicia Excel.", {}, []),
    "describe_feature": ("Descreve suporte, pré-requisitos e limites de uma capacidade.", {"feature": text(100)}, ["feature"]),
    "native_probe": ("Inicia Excel exclusivo e oculto para conferir o motor nativo sem abrir documentos do usuário.", {}, []),
    "inspect": ("Inspeciona formato, abas, objetos e recursos sem executar conteúdo ativo.", {"include_cells": BOOL}, []),
    "navigate": ("Lista abas, tabelas, nomes, objetos ou regiões sem mover o cursor.", {"sheet": SHEET, "kind": enum("sheets", "tables", "names", "objects", "regions", "pivots", "charts", "queries", "connections", "vba", "model")}, []),
    "read_range": ("Lê células paginadas e distingue fórmula de cache salvo; cache não comprova atualização.", {**SELECTION, **PAGE,
                    "fields": array(enum("value", "formula", "cached_value", "display_text", "type", "number_format", "error", "date_value"), 1, 8), "snapshot_id": text(64), "include_empty": BOOL}, ["sheet", "range"]),
    "search": ("Busca valores ou fórmulas em snapshot identificado, com paginação.", {**SELECTION, **PAGE, "query": text(10000), "scope": enum("values", "formulas", "all"), "regex": BOOL, "case_sensitive": BOOL, "snapshot_id": text(64)}, ["query"]),
    "analyze_formulas": ("Analisa fórmulas e erros observáveis, indicando dependências não resolvidas.", {**SELECTION, **PAGE}, []),
    "validate": ("Valida estrutura, fórmulas e regras de dados; preservação exige outra pasta de referência.", {"checks": array(enum("structure", "formulas", "data", "preservation"), 1, 4), "rules": array(DATA_RULE), "other_path": PATH}, []),
    "compare": ("Compara células e estrutura de duas pastas sem regravar as fontes.", {"other_path": PATH, "scope": enum("all", "cells", "structure"), **PAGE}, ["other_path"]),
    "create": ("Cria pasta Excel com operações tipadas e publica após validação.", {"output_path": PATH, "template_path": PATH, "password_ref": PASSWORD_REF, "operations": array({"oneOf": OPERATIONS}, 0, 10000), "calculation": CALCULATION, "validation": VALIDATION}, []),
    "apply_changes": ("Aplica alterações tipadas em uma cópia e publica em destino novo.", {"output_path": PATH, "operations": array({"oneOf": OPERATIONS}, 1, 10000), "calculation": CALCULATION, "validation": VALIDATION}, ["operations"]),
    "recalculate": ("Calcula fórmulas no Excel nativo, salva e confere a persistência dos resultados.", {"output_path": PATH, "calculation": CALCULATION, "validation": VALIDATION}, []),
    "refresh": ("Atualiza fontes selecionadas e pivôs com resultado individual; não presume sucesso por RefreshAll.", {"output_path": PATH, "connections": array(text()), "pivots": BOOL, "require_verified": BOOL, "calculation": CALCULATION, "validation": VALIDATION}, []),
    "export": ("Exporta seleção ou converte pasta, com destino novo e perdas explícitas.", {"output_path": PATH, "format": enum("pdf", "csv", "tsv", "xlsx", "xlsm", "xlsb", "xls", "xltx", "xltm", "xlam", "xla", "xlt", "png", "jpeg", "json"), "sheet": SHEET, "range": RANGE, "chart_name": text(255), "encoding": enum("utf-8", "utf-8-sig", "utf-16", "cp1252"), "delimiter": text(1), "allow_feature_loss": BOOL}, ["format"]),
    "get_vba_source": ("Lê módulos VBA quando o acesso VBIDE estiver autorizado e disponível.", {"module": text(255)}, []),
    "edit_vba": ("Edita ou importa/exporta código VBA com hash opcional da fonte e preservação do original.", {"output_path": PATH, "action": enum("set", "delete", "import", "export"), "module": text(255), "source": text(2000000, 0), "source_sha256": text(64), "input_module_path": PATH, "output_module_path": PATH, "allow_signature_invalidation": BOOL}, ["action", "module"]),
    "run_macro": ("Executa procedimento explicitamente selecionado em conteúdo confiável; não repete após falha incerta.", {"output_path": PATH, "procedure": text(255), "args": array(COM_VALUE, 0, 30), "calculation": CALCULATION, "validation": VALIDATION}, ["procedure"]),
    "describe_object": ("Descreve somente membros do adaptador COM permitido; não executa código livre.", {"selector": SELECTOR}, []),
    "get_object": ("Lê propriedade pública permitida do modelo de objetos da pasta.", {"selector": SELECTOR, "member": text(100)}, ["member"]),
    "set_object": ("Altera propriedade permitida em cópia; acesso COM permanece restrito ao adaptador.", {"output_path": PATH, "selector": SELECTOR, "member": text(100), "value": COM_VALUE}, ["member", "value"]),
    "call_object": ("Chama método permitido do adaptador COM; UI e execução arbitrária são recusadas.", {"output_path": PATH, "selector": SELECTOR, "member": text(100), "args": array(COM_VALUE, 0, 30)}, ["member"]),
}
WRITING = {"create", "apply_changes", "recalculate", "refresh", "export", "edit_vba", "run_macro", "set_object", "call_object"}
IMMEDIATE = {"get_capabilities", "describe_feature", *JOB_SPECS}
NO_INPUT = {"create", "native_probe", *IMMEDIATE}
