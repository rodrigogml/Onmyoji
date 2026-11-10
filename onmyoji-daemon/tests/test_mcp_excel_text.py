"""Leitura de texto tabular com encoding explícito, valores literais e limites."""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from onmyoji_daemon.mcp.excel.component.tabular_text import TabularText, text_options
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.text_inspection import execute_text


def context_for(path, defaults=None):
    def input_path(value):
        candidate = Path(value)
        if not candidate.is_file():
            raise ExcelExceptions("file_not_found", "Arquivo sintético não encontrado.")
        return candidate
    return SimpleNamespace(input_path=input_path, defaults=defaults or {}, profile={}, job_dir=path.parent)


def execute(path, operation, **arguments):
    return execute_text(operation, {"input_path": str(path), **arguments}, context_for(path))


def csv_file(tmp_path, content='Nome,Identificador,Valor\r\n"Olá, mundo",00001234567890123456789,=1+1\r\nOutro,,001.00\r\n'):
    path = tmp_path / "dados.csv"
    path.write_bytes(content.encode("utf-8-sig"))
    return path


def test_csv_inventory_has_one_logical_sheet_and_preserves_source_bytes(tmp_path):
    path = csv_file(tmp_path)
    original = path.read_bytes()
    result = execute(path, "inspect", include_cells=True)
    assert result["format"] == "csv" and result["container"] == "text"
    assert result["encoding"] == "utf-8-sig" and result["delimiter"] == ","
    assert result["row_count"] == 3 and result["column_count"] == 3 and result["cell_count"] == 9
    assert result["sheets"][0]["name"] == "Dados" and result["sheets"][0]["data_range"] == "A1:C3"
    assert result["sheets"][0]["formula_count"] == 0 and result["has_vba"] is False
    assert result["snapshot_id"] == hashlib.sha256(original).hexdigest()
    assert result["sheets"][0]["cells"][0]["value"] == "Nome"
    assert path.read_bytes() == original


def test_text_values_are_not_numbers_dates_or_formulas(tmp_path):
    path = csv_file(tmp_path)
    result = execute(path, "read_range", sheet="dados", range="A2:C3", limit=10)
    cells = {item["address"]: item for item in result["cells"]}
    assert cells["A2"]["value"] == "Olá, mundo"
    assert cells["B2"]["value"] == "00001234567890123456789"
    assert cells["C2"]["value"] == "=1+1" and cells["C2"]["formula"] is None
    assert cells["C3"]["value"] == "001.00" and cells["C3"]["type"] == "string"
    assert cells["B3"]["value"] == "" and cells["B3"]["type"] == "string"
    assert all(item["cached_value"] is None and item["date_value"] is None for item in cells.values())
    assert result["freshness"] == "not_applicable"


def test_page_fields_and_missing_cells_remain_distinct_from_explicit_empty(tmp_path):
    path = csv_file(tmp_path, 'a,b,\r\nc\r\n')
    first = execute(path, "read_range", sheet="Dados", range="A1:C2", fields=["value", "type"], limit=2)
    assert first["total"] == 6 and first["next_offset"] == 2
    assert set(first["cells"][0]) == {"sheet", "address", "row", "column", "value", "type"}
    second = execute(path, "read_range", sheet="Dados", range="A1:C2", offset=2, limit=4, snapshot_id=first["snapshot_id"])
    assert [(item["address"], item["value"], item["type"]) for item in second["cells"]] == [
        ("C1", "", "string"), ("A2", "c", "string"), ("B2", None, "blank"), ("C2", None, "blank")]
    sparse = execute(path, "read_range", sheet="Dados", range="A1:C2", include_empty=False)
    assert sparse["total"] == 4 and sparse["cells"][-1]["address"] == "A2"
    assert execute(path, "inspect")["ragged_rows"] is True
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "read_range", sheet="Dados", range="A1", snapshot_id="0" * 64)
    assert caught.value.code == "snapshot_changed"


def test_quoted_newlines_escaped_quotes_and_tsv_default(tmp_path):
    path = tmp_path / "dados.tsv"
    path.write_bytes('Nome\tTexto\r\nA\t"Linha 1\r\nLinha 2 com ""aspas"""\r\n'.encode("utf-8"))
    result = execute(path, "read_range", sheet="Dados", range="B2")
    assert result["cells"][0]["value"] == 'Linha 1\r\nLinha 2 com "aspas"'
    assert result["format"] == "tsv" and result["delimiter"] == "\t"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"])
def test_explicit_encodings_and_nondefault_delimiter(tmp_path, encoding):
    path = tmp_path / "texto.txt"
    path.write_bytes('Código;Descrição\n001;Ação\n'.encode(encoding))
    result = execute(path, "read_range", sheet="Dados", range="A1:B2", encoding=encoding, delimiter=";")
    assert result["cells"][1]["value"] == "Descrição" and result["cells"][2]["value"] == "001"
    assert result["encoding"] == encoding and result["delimiter"] == ";"


def test_incompatible_encoding_fails_without_lossy_replacement(tmp_path):
    path = tmp_path / "cp1252.csv"; path.write_bytes('Nome\nAção\n'.encode("cp1252"))
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "inspect")
    assert caught.value.code == "text_decode_failed"
    assert execute(path, "read_range", sheet="Dados", range="A2", encoding="cp1252")["cells"][0]["value"] == "Ação"
    utf16 = tmp_path / "utf16.csv"; utf16.write_bytes(b'\xfe\xff' + 'Nome\nAção\n'.encode('utf-16-be'))
    with pytest.raises(ExcelExceptions) as caught:
        execute(utf16, "inspect", encoding="utf-16-le")
    assert caught.value.code == "text_decode_failed"


def test_plain_pk_text_is_not_mistaken_for_zip_container(tmp_path):
    path = tmp_path / "pk.csv"; path.write_text('PK,Descrição\n001,Produto\n', encoding='utf-8')
    assert execute(path, "read_range", sheet="Dados", range="A1")["cells"][0]["value"] == "PK"


def test_txt_requires_delimiter_and_invalid_options_are_rejected(tmp_path):
    path = tmp_path / "data.txt"; path.write_text("a|b", encoding="utf-8")
    for options, code in [({}, "delimiter_required"), ({"delimiter": "||"}, "invalid_delimiter"),
                          ({"delimiter": "\n"}, "invalid_delimiter"), ({"delimiter": '"'}, "invalid_delimiter"),
                          ({"delimiter": "|", "encoding": "missing-codec"}, "invalid_encoding")]:
        with pytest.raises(ExcelExceptions) as caught:
            execute(path, "inspect", **options)
        assert caught.value.code == code
    assert execute(path, "read_range", sheet="Dados", range="B1", delimiter="|")["cells"][0]["value"] == "b"


def test_search_scope_literals_regex_and_analysis(tmp_path):
    path = csv_file(tmp_path)
    assert execute(path, "search", query="olá")["matches"][0]["address"] == "A2"
    assert execute(path, "search", query="olá", case_sensitive=True)["total"] == 0
    assert execute(path, "search", query="^0+", regex=True, range="B2:C3", sheet="Dados")["total"] == 2
    assert execute(path, "search", query="=", scope="formulas")["total"] == 0
    assert execute(path, "search", query="=", scope="values")["matches"][0]["value"] == "=1+1"
    page = execute(path, "search", query="o", limit=1)
    assert page["next_offset"] == 1
    analysis = execute(path, "analyze_formulas")
    assert analysis["formulas"] == [] and analysis["summary"]["literal_equals_prefixes"] == 1
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "search", query="[", regex=True)
    assert caught.value.code == "invalid_regex"


def test_navigation_reports_nonexistent_excel_objects_as_not_applicable(tmp_path):
    path = csv_file(tmp_path)
    assert execute(path, "navigate")["items"][0]["kind"] == "tabular_text"
    assert execute(path, "navigate", kind="regions")["items"][0]["data_range"] == "A1:C3"
    tables = execute(path, "navigate", kind="tables")
    assert tables["items"] == [] and tables["status"] == "not_applicable"
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "read_range", sheet="Outra", range="A1")
    assert caught.value.code == "sheet_not_found"


def test_empty_file_blank_records_and_no_source_normalization(tmp_path):
    empty = tmp_path / "empty.csv"; empty.write_bytes(b"")
    assert execute(empty, "inspect")["sheets"][0]["data_range"] is None
    assert execute(empty, "read_range", sheet="Dados", range="A1")["cells"][0]["type"] == "blank"
    blank = tmp_path / "blank.csv"; blank.write_bytes(b'\r\n""\r\n')
    document = TabularText(blank)
    assert document.rows == [[], [""]]
    assert document.cell(1, 1)["value"] is None and document.cell(2, 1)["value"] == ""
    assert document.inventory()["ragged_rows"] is True


def test_data_rules_apply_to_text_without_numeric_coercion(tmp_path):
    path = csv_file(tmp_path, 'ID,Valor\n001,2\n001,\n')
    result = execute(path, "validate", rules=[
        {"type": "unique", "sheet": "Dados", "range": "A2:A3"},
        {"type": "required", "sheet": "Dados", "range": "B2:B3"},
        {"type": "regex", "sheet": "Dados", "range": "A2:A3", "pattern": r"\d{3}"},
        {"type": "type", "sheet": "Dados", "range": "B2", "value_type": "number"},
        {"type": "allowed_values", "sheet": "Dados", "range": "A2", "values": ["001"]},
    ])
    assert result["valid"] is False and result["status"] == "failed"
    assert [(item["rule"], item["address"]) for item in result["issues"]] == [(0, "A3"), (1, "B3"), (3, "B2")]
    assert result["calculation"]["status"] == "not_applicable"
    assert execute(path, "validate", checks=["formulas"])["valid"] is True
    assert execute(path, "validate", checks=["structure"])["valid"] is True


def test_compare_values_preservation_and_separate_parser_options(tmp_path):
    csv_path = csv_file(tmp_path, 'Nome,ID\r\nAção,001\r\n')
    tsv_path = tmp_path / "other.tsv"; tsv_path.write_bytes('Nome\tID\nAção\t001\n'.encode("utf-16"))
    result = execute(csv_path, "compare", other_path=str(tsv_path), other_encoding="utf-16")
    assert result["equal_in_scope"] is True and result["identical_bytes"] is False
    assert result["representation_changed"] is True
    preservation = execute(csv_path, "validate", checks=["preservation"], other_path=str(tsv_path), other_encoding="utf-16")
    assert preservation["valid"] is True and preservation["preservation"]["equal_values_and_structure"] is True
    changed = tmp_path / "changed.csv"; changed.write_text('Nome,ID\nAção,002,\n', encoding="utf-8")
    comparison = execute(csv_path, "compare", other_path=str(changed), scope="cells", limit=1)
    assert comparison["total"] == 2 and comparison["next_offset"] == 1
    assert comparison["differences"][0]["address"] == "B2"
    assert execute(csv_path, "compare", other_path=str(changed), scope="structure")["equal_in_scope"] is False
    assert execute(csv_path, "validate", checks=["preservation"], other_path=str(changed))["issues"][0]["code"] == "tabular_content_changed"


@pytest.mark.parametrize("limits", [{"max_input_bytes": 2}, {"max_rows": 1}, {"max_columns": 1}, {"max_cells": 1}, {"max_field_chars": 2}])
def test_each_text_limit_is_enforced_and_csv_global_limit_is_restored(tmp_path, limits):
    path = csv_file(tmp_path, "abc,def\nghi,jkl\n")
    original = csv.field_size_limit()
    with pytest.raises(ExcelExceptions) as caught:
        TabularText(path, limits=limits)
    assert caught.value.code in {"input_too_large", "text_limit"}
    assert csv.field_size_limit() == original


@pytest.mark.parametrize("content,code", [(b'"unterminated', "invalid_tabular_text"), (b'a\x00,b', "unsupported_format"),
                                        (b'PK\x03\x04not-text', "unsupported_format"), (b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1', "unsupported_format")])
def test_malformed_text_and_binary_containers_have_stable_errors(tmp_path, content, code):
    path = tmp_path / "malformed.csv"; path.write_bytes(content)
    original_limit = csv.field_size_limit()
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, "inspect")
    assert caught.value.code == code
    assert csv.field_size_limit() == original_limit


def test_operation_and_selection_contracts_reject_invalid_arguments(tmp_path):
    path = csv_file(tmp_path)
    for operation, arguments, code in [
        ("unsupported", {}, "operation_unavailable"),
        ("read_range", {"sheet": "Dados", "range": "A1", "limit": 0}, "invalid_pagination"),
        ("read_range", {"sheet": "Dados", "range": "A1", "fields": ["bad"]}, "invalid_argument"),
        ("read_range", {"sheet": "Dados", "range": "A0"}, "invalid_range"),
        ("read_range", {"sheet": "Dados", "range": "A1:XFD1048576"}, "range_too_large"),
        ("validate", {"checks": ["unknown"]}, "invalid_argument"),
        ("validate", {"checks": ["preservation"]}, "invalid_argument"),
        ("navigate", {"kind": "unknown"}, "invalid_argument"),
        ("compare", {"other_path": str(path), "scope": "unknown"}, "invalid_argument"),
    ]:
        with pytest.raises(ExcelExceptions) as caught:
            execute(path, operation, **arguments)
        assert caught.value.code == code


@pytest.mark.parametrize('encoding,bom', [('utf-16-le',b'\xff\xfe'),('utf-16-be',b'\xfe\xff')])
def test_explicit_endian_utf16_bom_is_not_part_of_the_header(tmp_path, encoding, bom):
    path = tmp_path / 'bom.tsv'
    path.write_bytes(bom + 'Código\tValor\n001\t=texto\n'.encode(encoding))
    result = execute(path, 'read_range', encoding=encoding, range='A1:B2')
    assert [cell['value'] for cell in result['cells']] == ['Código','Valor','001','=texto']
    assert execute(path, 'search', encoding=encoding, query='=', scope='formulas', range='A1:B2')['total'] == 0


def test_utf16_endian_bom_mismatch_fails_clearly(tmp_path):
    path = tmp_path / 'wrong.tsv'
    path.write_bytes(b'\xff\xfe' + 'Código\tValor\n'.encode('utf-16-le'))
    with pytest.raises(ExcelExceptions) as caught:
        execute(path, 'inspect', encoding='utf-16-be')
    assert caught.value.code == 'text_decode_failed'
