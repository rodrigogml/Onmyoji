"""Snapshot de texto tabular com parsing explícito e valores preservados.

CSV/TSV são dados tabulares, sem abas, estilos ou fórmulas Excel. O leitor não
infere números, datas nem conteúdo executável e nunca regrava o arquivo fonte.
"""
from __future__ import annotations

from contextlib import contextmanager
import csv
import hashlib
from pathlib import Path
import threading

from .ooxml import CFB_SIGNATURE, column_name, range_coordinates
from ..exception import ExcelExceptions, require


TEXT_EXTENSIONS = frozenset({".csv", ".tsv", ".txt"})
ENCODINGS = frozenset({"utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1252", "latin-1"})
DEFAULT_TEXT_LIMITS = {"max_input_bytes": 262144000, "max_cells": 1000000,
                       "max_rows": 1048576, "max_columns": 16384, "max_field_chars": 1048576}
_reader_lock = threading.RLock()


@contextmanager
def _field_limit(limit: int):
    """Aplica o limite somente durante parsing no worker e restaura o global.

    Jobs usam processos privados. O lock também serializa chamadas concorrentes
    deste componente em testes/uso embutido, sem alterar o limite após o retorno.
    """
    with _reader_lock:
        original = csv.field_size_limit()
        try:
            csv.field_size_limit(limit)
            yield
        finally:
            csv.field_size_limit(original)


def text_options(path: Path, arguments: dict | None = None) -> tuple[str, str]:
    """Resolve encoding e delimitador sem heurística dependente da amostra.

    CSV usa vírgula, TSV usa tab e TXT exige delimitador. ``invalid_encoding``
    e ``invalid_delimiter`` indicam opções incompatíveis antes de ler conteúdo.
    """
    arguments = arguments or {}
    encoding = arguments.get("encoding", "utf-8-sig")
    require(isinstance(encoding, str) and encoding.casefold() in ENCODINGS,
            "invalid_encoding", "Escolha um encoding de texto suportado e explícito.")
    encoding = encoding.casefold()
    delimiter = arguments.get("delimiter")
    if delimiter is None:
        suffix = path.suffix.casefold()
        require(suffix in {".csv", ".tsv"}, "delimiter_required", "Arquivos TXT e extensões genéricas exigem delimiter explícito.")
        delimiter = "\t" if suffix == ".tsv" else ","
    require(isinstance(delimiter, str) and len(delimiter) == 1 and delimiter not in {'"', "\r", "\n", "\x00"},
            "invalid_delimiter", "O delimitador deve ser um caractere, diferente de aspas, quebra de linha ou NUL.")
    return encoding, delimiter


class TabularText:
    """Mantém as linhas lidas com limites de tamanho compatíveis com o Excel.

    Campos vazios explícitos são strings vazias; células ausentes em linhas
    irregulares são vazias sem valor. Prefixos ``=`` e números longos continuam
    strings. ``snapshot_id`` identifica os bytes originais, sem normalizá-los.
    """

    sheet_name = "Dados"

    def __init__(self, path: Path, arguments: dict | None = None, limits: dict | None = None):
        self.path = Path(path)
        self.encoding, self.delimiter = text_options(self.path, arguments)
        self.limits = {**DEFAULT_TEXT_LIMITS, **{key: value for key, value in (limits or {}).items() if key in DEFAULT_TEXT_LIMITS}}
        require(all(type(value) is int and value > 0 for value in self.limits.values()), "invalid_argument", "Os limites de texto devem ser inteiros positivos.")
        self.limits["max_rows"] = min(self.limits["max_rows"], 1048576)
        self.limits["max_columns"] = min(self.limits["max_columns"], 16384)
        require(self.path.is_file(), "file_not_found", "O arquivo de texto tabular não foi encontrado.")
        self.bytes = self.path.stat().st_size
        require(self.bytes <= self.limits["max_input_bytes"], "input_too_large", "O texto excede o limite de entrada configurado.")
        with self.path.open("rb") as stream:
            signature = stream.read(8)
            require(signature[:4] not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"} and signature != CFB_SIGNATURE,
                    "unsupported_format", "O arquivo possui container binário Excel; use o backend de planilhas correspondente.")
            stream.seek(0)
            digest = hashlib.sha256()
            for data in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(data)
        self.snapshot_id = digest.hexdigest()
        self.format = "tsv" if self.delimiter == "\t" else "csv" if self.path.suffix.casefold() == ".csv" else "text"
        self.rows: list[list[str]] = []
        self.column_count = 0
        self.cell_count = 0
        self._parse()

    def _parse(self) -> None:
        try:
            with self.path.open("r", encoding=self.encoding, errors="strict", newline="") as stream, _field_limit(self.limits["max_field_chars"]):
                # Explicit UTF-16 endian encodings may leave the matching BOM
                # as a character. It is a file marker, never the first header.
                first = stream.read(1)
                require(first != "\ufffe", "text_decode_failed", "O marcador UTF-16 não corresponde ao encoding informado.")
                if first != "\ufeff":
                    stream.seek(0)
                reader = csv.reader(stream, delimiter=self.delimiter, quotechar='"', doublequote=True, strict=True)
                for row in reader:
                    require(len(self.rows) < self.limits["max_rows"], "text_limit", "O texto excede o limite de linhas.")
                    require(len(row) <= self.limits["max_columns"], "text_limit", "O texto excede o limite de colunas.")
                    require(all("\x00" not in value for value in row), "unsupported_format", "O texto contém NUL; confira o encoding ou o formato real do arquivo.")
                    require(all(len(value) <= self.limits["max_field_chars"] for value in row), "text_limit", "Um campo excede o limite configurado.")
                    self.cell_count += len(row)
                    require(self.cell_count <= self.limits["max_cells"], "text_limit", "O texto excede o limite de células.")
                    self.rows.append(row)
                    self.column_count = max(self.column_count, len(row))
        except UnicodeError as error:
            raise ExcelExceptions("text_decode_failed", "O conteúdo não pode ser decodificado com o encoding informado.") from error
        except csv.Error as error:
            code = "text_limit" if "field larger than field limit" in str(error) else "invalid_tabular_text"
            message = "Um campo excede o limite configurado." if code == "text_limit" else "O texto tabular possui estrutura de aspas ou registros inválida."
            raise ExcelExceptions(code, message) from error

    def require_sheet(self, name: str | None = None) -> str:
        require(name is None or isinstance(name, str) and name.casefold() == self.sheet_name.casefold(),
                "sheet_not_found", "Texto tabular possui somente a aba lógica Dados.")
        return self.sheet_name

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def data_range(self) -> str | None:
        return f"A1:{column_name(self.column_count)}{self.row_count}" if self.row_count and self.column_count else None

    def cell(self, row: int, column: int) -> dict:
        require(type(row) is int and 1 <= row <= 1048576 and type(column) is int and 1 <= column <= 16384,
                "invalid_range", "Coordenadas de célula fora dos limites do Excel.")
        value = self.rows[row - 1][column - 1] if row <= self.row_count and column <= len(self.rows[row - 1]) else None
        return {"sheet": self.sheet_name, "address": f"{column_name(column)}{row}", "row": row, "column": column,
                "type": "blank" if value is None else "string", "value": value, "formula": None,
                "cached_value": None, "cached_type": None, "error": None, "date_value": None,
                "display_text": value, "number_format": "@", "style_index": None,
                "freshness": "not_applicable", "hidden_row": False, "hidden_column": False}

    def selected_cells(self, arguments: dict, *, include_empty: bool = False):
        self.require_sheet(arguments.get("sheet"))
        reference = arguments.get("range")
        area = range_coordinates(reference) if reference else None
        if area is not None and include_empty:
            count = (area[2] - area[0] + 1) * (area[3] - area[1] + 1)
            require(count <= self.limits["max_cells"], "range_too_large", "O intervalo excede o limite de células; selecione blocos menores.")
            for row in range(area[0], area[2] + 1):
                for column in range(area[1], area[3] + 1):
                    yield self.cell(row, column)
            return
        for row_index, row in enumerate(self.rows, 1):
            if area is not None and not area[0] <= row_index <= area[2]:
                continue
            for column_index in range(1, len(row) + 1):
                if area is None or area[1] <= column_index <= area[3]:
                    yield self.cell(row_index, column_index)

    def inventory(self, include_cells: bool = False) -> dict:
        widths = {len(row) for row in self.rows}
        result = {
            "format": self.format, "container": "text", "backend": "direct", "snapshot_id": self.snapshot_id,
            "bytes": self.bytes, "freshness": "not_applicable", "encoding": self.encoding, "delimiter": self.delimiter,
            "row_count": self.row_count, "column_count": self.column_count, "cell_count": self.cell_count,
            "rectangular_cell_count": self.row_count * self.column_count, "ragged_rows": len(widths) > 1,
            "sheets": [{"name": self.sheet_name, "kind": "tabular_text", "index": 1, "state": "visible", "data_range": self.data_range,
                        "cell_count": self.cell_count, "formula_count": 0}],
            "defined_names": [], "tables": [], "features": {}, "has_vba": False, "has_xlm": False,
            "has_signatures": False, "signatures": [], "active_content_verified": True,
            "structure_errors": [], "formula_errors": [], "path_sensitive_formulas": [],
            "limitations": ["Todos os campos são texto; não há inferência de números, datas ou fórmulas.",
                            "O formato não armazena estilos, tabelas Excel, objetos, macros ou resultados de cálculo."]}
        if include_cells:
            result["sheets"][0]["cells"] = list(self.selected_cells({}))
        return result
