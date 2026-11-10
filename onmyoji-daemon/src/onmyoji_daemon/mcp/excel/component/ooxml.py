"""Inspeção limitada de pacotes Excel sem executar fórmulas ou conteúdo ativo.

O componente lê XML e caches salvos, preservando a fonte byte a byte. Formatos
binários ou criptografados são identificados, mas não recebem uma certificação
de ausência de conteúdo ativo. Erros de container usam códigos públicos estáveis.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
import hashlib
import io
import math
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
import zlib
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, LargeZipFile, ZipFile

from ..exception import ExcelExceptions, require


DEFAULT_LIMITS = {
    "max_package_entries": 20000,
    "max_uncompressed_bytes": 536870912,
    "max_xml_bytes": 67108864,
    "max_compression_ratio": 1000,
    "max_cells": 1000000,
}
CFB_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
XML_MAIN_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml": "xlsm",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": "xltx",
    "application/vnd.ms-excel.template.macroEnabled.main+xml": "xltm",
    "application/vnd.ms-excel.addin.macroEnabled.main+xml": "xlam",
    "application/vnd.ms-excel.sheet.binary.macroEnabled.main": "xlsb",
}
BUILTIN_FORMATS = {
    0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00",
    9: "0%", 10: "0.00%", 11: "0.00E+00", 12: "# ?/?", 13: "# ??/??",
    14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy",
    18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss",
    22: "m/d/yy h:mm", 37: "#,##0 ;(#,##0)", 38: "#,##0 ;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)", 40: "#,##0.00;[Red](#,##0.00)",
    45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@",
}
CELL_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})$")
FORMULA_ERROR_CODES = {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA", "#SPILL!", "#CALC!", "#FIELD!", "#BLOCKED!", "#CONNECT!", "#BUSY!", "#PYTHON!"}
XLM_FORMULA = re.compile(r"(?i)(?<![A-Za-z0-9_])(?:_xlfn\.)?(?:EVALUATE|EXEC|CALL|REGISTER(?:\.ID)?|RUN|GET\.CELL|GET\.WORKBOOK|SET\.NAME)\s*\(")


def contains_xlm_formula(formula: str | None) -> bool:
    """Detecta funções de macro XLM em expressões, sem confundir literais.

    Nomes definidos podem hospedar EVALUATE/CALL sem uma macrosheet. A
    detecção é conservadora: não executa nomes para decidir se são macros.
    """
    if not isinstance(formula, str):
        return False
    expression = re.sub(r'"(?:[^\"]|\"\")*"|\'(?:[^\']|\'\')*\'(?=!)', "", formula)
    return XLM_FORMULA.search(expression) is not None


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child(node: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in node if local_name(item.tag) == name), None)


def children(node: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in node if local_name(item.tag) == name]


def attribute(node: ET.Element, name: str, default: str = "") -> str:
    return next((value for key, value in node.attrib.items() if local_name(key) == name), default)


def column_number(value: str) -> int:
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z]{1,3}", value) is not None,
            "invalid_range", "Informe um identificador de coluna válido.")
    result = 0
    for character in value.upper():
        result = result * 26 + ord(character) - 64
    require(1 <= result <= 16384, "invalid_range", "A coluna deve estar entre A e XFD.")
    return result


def column_name(value: int) -> str:
    require(type(value) is int and 1 <= value <= 16384, "invalid_range", "Índice de coluna inválido.")
    result = ""
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(remainder + 65) + result
    return result


def cell_coordinates(value: str) -> tuple[int, int]:
    match = CELL_RE.fullmatch(value) if isinstance(value, str) else None
    require(match is not None, "invalid_range", "Informe uma referência A1 de célula válida.")
    row = int(match.group(2))
    require(row <= 1048576, "invalid_range", "A linha deve estar entre 1 e 1048576.")
    return row, column_number(match.group(1))


def range_coordinates(value: str) -> tuple[int, int, int, int]:
    require(isinstance(value, str) and value and "," not in value and "!" not in value,
            "invalid_range", "Informe um intervalo A1 retangular, sem nome de aba.")
    parts = value.split(":")
    require(len(parts) <= 2, "invalid_range", "Intervalo A1 inválido.")
    first_row, first_column = cell_coordinates(parts[0])
    last_row, last_column = cell_coordinates(parts[-1])
    require(last_row >= first_row and last_column >= first_column, "invalid_range", "O fim do intervalo deve suceder seu início.")
    return first_row, first_column, last_row, last_column


def in_range(address: str, area: tuple[int, int, int, int]) -> bool:
    row, column = cell_coordinates(address)
    return area[0] <= row <= area[2] and area[1] <= column <= area[3]


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flag(value: str) -> bool:
    return value.casefold() in {"1", "true", "on"}


def _safe_xml(data: bytes) -> ET.Element:
    # Expat rejects undeclared entities; additionally reject declarations before
    # parsing, including UTF-16 input where the ASCII tokens contain NUL bytes.
    probe = data.replace(b"\x00", b"")
    require(re.search(br"<!\s*(DOCTYPE|ENTITY)\b", probe, re.I) is None,
            "unsafe_xml", "O pacote contém uma declaração XML não permitida.")
    try:
        return ET.fromstring(data)
    except (ET.ParseError, ValueError) as error:
        raise ExcelExceptions("invalid_package", "Uma parte XML da planilha está inválida.") from error


def _relationship_source(name: str) -> str:
    if name == "_rels/.rels":
        return ""
    path = PurePosixPath(name)
    require(path.parent.name == "_rels" and path.name.endswith(".rels"),
            "invalid_package", "Parte de relacionamentos inválida.")
    return str(path.parent.parent / path.name[:-5])


def _relationship_target(source: str, target: str) -> str:
    target = unquote(target)
    parsed = urlsplit(target)
    require(not parsed.scheme and not parsed.netloc and "\\" not in target and "\x00" not in target,
            "invalid_relationship", "Um relacionamento interno contém um destino não permitido.")
    value = parsed.path
    if not value:
        return source
    joined = value.lstrip("/") if value.startswith("/") else posixpath.join(posixpath.dirname(source), value)
    normalized = posixpath.normpath(joined)
    require(normalized not in {"", ".", ".."} and not normalized.startswith("../"),
            "invalid_relationship", "Um relacionamento tenta sair do pacote.")
    return normalized


def _cfb_stream_names(path: Path, maximum: int) -> list[str]:
    """Lê apenas o diretório CFB para distinguir criptografia e formato legado.

    Não interpreta o conteúdo BIFF nem certifica macros. Cadeias inválidas,
    circulares ou extensas retornam ``invalid_package`` em vez de abrir Excel.
    """
    size = path.stat().st_size
    require(size <= maximum, "input_too_large", "O arquivo excede o limite de inspeção configurado.")
    with path.open("rb") as stream:
        header = stream.read(512)
        require(len(header) == 512 and header[:8] == CFB_SIGNATURE, "invalid_package", "Cabeçalho CFB inválido.")
        sector_shift = struct.unpack_from("<H", header, 30)[0]
        require(sector_shift in {9, 12}, "invalid_package", "Tamanho de setor CFB inválido.")
        sector_size = 1 << sector_shift
        sector_count = size // sector_size - 1
        require(sector_count >= 0, "invalid_package", "Container CFB incompleto.")

        def sector(identifier: int) -> bytes:
            require(0 <= identifier < sector_count, "invalid_package", "Setor CFB fora do arquivo.")
            stream.seek((identifier + 1) * sector_size)
            data = stream.read(sector_size)
            require(len(data) == sector_size, "invalid_package", "Setor CFB incompleto.")
            return data

        fat_count = struct.unpack_from("<I", header, 44)[0]
        require(fat_count <= sector_count, "invalid_package", "Diretório FAT CFB inválido.")
        fat_sectors = [item for item in struct.unpack_from("<109I", header, 76) if item < 0xFFFFFFFA]
        difat = struct.unpack_from("<I", header, 68)[0]
        difat_count = struct.unpack_from("<I", header, 72)[0]
        require(difat_count <= sector_count, "invalid_package", "Cadeia DIFAT CFB inválida.")
        seen = set()
        for _ in range(difat_count):
            require(difat not in seen, "invalid_package", "Cadeia CFB circular.")
            seen.add(difat)
            values = struct.unpack("<" + "I" * (sector_size // 4), sector(difat))
            fat_sectors.extend(item for item in values[:-1] if item < 0xFFFFFFFA)
            difat = values[-1]
        require(len(fat_sectors) >= fat_count, "invalid_package", "FAT CFB incompleta.")
        fat = []
        for identifier in fat_sectors[:fat_count]:
            fat.extend(struct.unpack("<" + "I" * (sector_size // 4), sector(identifier)))
        directory_sector = struct.unpack_from("<I", header, 48)[0]
        names, seen = [], set()
        while directory_sector != 0xFFFFFFFE:
            require(directory_sector not in seen and directory_sector < len(fat), "invalid_package", "Cadeia de diretório CFB inválida.")
            seen.add(directory_sector)
            require(len(seen) <= 8192, "package_limit", "O diretório CFB excede o limite de inspeção.")
            data = sector(directory_sector)
            for index in range(0, sector_size, 128):
                entry = data[index:index + 128]
                length = struct.unpack_from("<H", entry, 64)[0]
                if entry[66] == 2:
                    require(2 <= length <= 64 and length % 2 == 0, "invalid_package", "Nome de stream CFB inválido.")
                    try:
                        names.append(entry[:length - 2].decode("utf-16-le"))
                    except UnicodeDecodeError as error:
                        raise ExcelExceptions("invalid_package", "Nome de stream CFB inválido.") from error
            directory_sector = fat[directory_sector]
        return names


class SpreadsheetPackage:
    """Representa um snapshot OOXML com limites de descompressão e leitura.

    Nenhuma parte é extraída para disco. Referências externas são inventariadas,
    nunca acessadas. ``cells`` fornece caches salvos, sem calcular ou renderizar.
    """

    def __init__(self, path: Path, limits: dict | None = None):
        self.path = Path(path)
        selected_limits = dict(limits or {})
        for administrative, internal in {"max_package_bytes": "max_uncompressed_bytes", "max_zip_entries": "max_package_entries"}.items():
            if administrative in selected_limits and internal not in selected_limits:
                selected_limits[internal] = selected_limits[administrative]
        self.limits = {**DEFAULT_LIMITS, **{key: value for key, value in selected_limits.items() if key in DEFAULT_LIMITS}}
        require(all(type(value) is int and value > 0 for value in self.limits.values()), "invalid_argument", "Os limites de inspeção devem ser inteiros positivos.")
        self.archive: ZipFile | None = None
        self.names: set[str] = set()
        self.content_types: dict[str, str] = {}
        self.relationships: dict[str, list[dict]] = {}
        self.structure_errors: list[dict] = []
        self._xml: dict[str, ET.Element] = {}
        self._cells: dict[str, dict[str, dict]] = {}
        self._sheet_metadata: dict[str, dict] = {}
        self._shared_strings: list[str] | None = None
        self._styles: list[dict] | None = None
        self._cell_count = 0
        self.workbook_part = ""
        self.workbook: ET.Element | None = None
        self.sheets: list[dict] = []
        self.date_system = "1900"
        self.format = "unknown"
        try:
            self.archive = ZipFile(self.path)
            self._validate_entries()
            self._load_content_types()
            self._load_relationships()
            self._load_workbook()
        except ExcelExceptions:
            self.close()
            raise
        except (BadZipFile, LargeZipFile, OSError, RuntimeError, UnicodeError, ValueError, KeyError, NotImplementedError, zlib.error) as error:
            self.close()
            raise ExcelExceptions("invalid_package", "O arquivo não é um pacote Excel válido e legível.") from error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        if self.archive is not None:
            self.archive.close()
            self.archive = None

    def _validate_entries(self) -> None:
        entries = self.archive.infolist()
        require(len(entries) <= self.limits["max_package_entries"], "package_limit", "O pacote contém partes demais.")
        total, folded = 0, set()
        for info in entries:
            name = info.filename
            require(info.orig_filename == name, "invalid_package", "O pacote contém um nome de parte normalizado ou truncado.")
            require(name and not name.startswith(("/", "\\")) and "\\" not in name and "\x00" not in name and ":" not in name
                    and all(item not in {".", ".."} for item in name.rstrip("/").split("/")),
                    "invalid_package", "O pacote contém um nome de parte não permitido.")
            require(name.casefold() not in folded, "invalid_package", "O pacote contém nomes de partes duplicados.")
            folded.add(name.casefold())
            require(not info.flag_bits & 1, "encrypted_archive", "Um pacote ZIP criptografado não é um formato Excel compatível.")
            require(not stat.S_ISLNK(info.external_attr >> 16), "invalid_package", "O pacote contém um link simbólico.")
            total += info.file_size
            require(total <= self.limits["max_uncompressed_bytes"], "package_limit", "O pacote excede o limite de descompressão.")
            require(info.file_size <= 100000 or info.file_size <= max(info.compress_size, 1) * self.limits["max_compression_ratio"],
                    "package_limit", "Uma parte do pacote excede a taxa de compressão permitida.")
            if not info.is_dir():
                self.names.add(name)
        require("[Content_Types].xml" in self.names, "invalid_package", "O pacote não possui tipos de conteúdo OOXML.")

    def xml(self, name: str) -> ET.Element:
        if name not in self._xml:
            require(name in self.names, "invalid_package", "Uma parte necessária não foi encontrada no pacote.")
            require(self.archive.getinfo(name).file_size <= self.limits["max_xml_bytes"], "package_limit", "Uma parte XML excede o limite de leitura.")
            try:
                data = self.archive.read(name)
            except (BadZipFile, RuntimeError, OSError, NotImplementedError, zlib.error) as error:
                raise ExcelExceptions("invalid_package", "Falha de integridade em uma parte do pacote.") from error
            self._xml[name] = _safe_xml(data)
        return self._xml[name]

    def _load_content_types(self) -> None:
        root = self.xml("[Content_Types].xml")
        require(local_name(root.tag) == "Types", "invalid_package", "Tipos de conteúdo OOXML inválidos.")
        defaults = {}
        for node in root:
            if local_name(node.tag) == "Default":
                extension = node.get("Extension", "").casefold()
                require(extension and extension not in defaults and node.get("ContentType") and not any(character in extension for character in "/\\:"),
                        "invalid_package", "Um tipo padrão de conteúdo está inválido ou duplicado.")
                defaults[extension] = node.get("ContentType", "")
            elif local_name(node.tag) == "Override":
                part = _relationship_target("", node.get("PartName", ""))
                require(part not in self.content_types, "invalid_package", "Tipo de conteúdo duplicado.")
                self.content_types[part] = node.get("ContentType", "")
                if part not in self.names:
                    self.structure_errors.append({"code": "missing_content_part", "part": part})
        for name in self.names:
            self.content_types.setdefault(name, defaults.get(name.rsplit(".", 1)[-1].casefold(), ""))

    def _load_relationships(self) -> None:
        for name in sorted(self.names):
            if not name.endswith(".rels"):
                continue
            source = _relationship_source(name)
            root = self.xml(name)
            require(local_name(root.tag) == "Relationships", "invalid_package", "Relacionamentos OOXML inválidos.")
            records, identifiers = [], set()
            if source and source not in self.names:
                self.structure_errors.append({"code": "orphan_relationship_part", "part": name})
            for node in root:
                if local_name(node.tag) != "Relationship":
                    continue
                identifier, relation_type = node.get("Id", ""), node.get("Type", "")
                require(identifier and identifier not in identifiers and relation_type,
                        "invalid_relationship", "Um relacionamento está inválido ou duplicado.")
                identifiers.add(identifier)
                external = node.get("TargetMode", "").casefold() == "external"
                target = node.get("Target", "")
                require(bool(target), "invalid_relationship", "Um relacionamento não possui destino.")
                resolved = target if external else _relationship_target(source, target)
                records.append({"id": identifier, "type": relation_type, "target": resolved, "external": external})
                if not external and resolved not in self.names:
                    self.structure_errors.append({"code": "missing_relationship_target", "part": source, "target": resolved})
            self.relationships[source] = records

    def _load_workbook(self) -> None:
        targets = [item["target"] for item in self.relationships.get("", []) if item["type"].endswith("/officeDocument") and not item["external"]]
        require(len(targets) == 1, "invalid_package", "O pacote deve identificar exatamente uma pasta de trabalho.")
        self.workbook_part = targets[0]
        main_type = self.content_types.get(self.workbook_part, "")
        require(main_type in XML_MAIN_TYPES, "unsupported_format", "O pacote não identifica um formato de pasta Excel suportado.")
        self.format = XML_MAIN_TYPES[main_type]
        if self.format == "xlsb":
            return
        self.workbook = self.xml(self.workbook_part)
        require(local_name(self.workbook.tag) == "workbook", "invalid_package", "A parte principal não é uma pasta de trabalho.")
        properties = child(self.workbook, "workbookPr")
        if properties is not None and _flag(properties.get("date1904", "0")):
            self.date_system = "1904"
        sheet_container = child(self.workbook, "sheets")
        require(sheet_container is not None, "invalid_package", "A pasta não possui uma lista de abas.")
        relationships = {item["id"]: item for item in self.relationships.get(self.workbook_part, [])}
        names, sheet_ids = set(), set()
        for index, node in enumerate(sheet_container):
            if local_name(node.tag) != "sheet":
                continue
            name, sheet_id = node.get("name", ""), node.get("sheetId", "")
            require(name and name.casefold() not in names and sheet_id and sheet_id not in sheet_ids,
                    "invalid_package", "A pasta possui nome ou identificador de aba inválido.")
            names.add(name.casefold()); sheet_ids.add(sheet_id)
            relation = relationships.get(attribute(node, "id"))
            require(relation is not None and not relation["external"], "invalid_relationship", "Uma aba não possui relacionamento interno válido.")
            self.sheets.append({"name": name, "sheet_id": sheet_id, "index": index + 1,
                                "state": node.get("state", "visible"), "part": relation["target"],
                                "kind": relation["type"].rsplit("/", 1)[-1]})

    def sheet(self, name: str | None) -> dict:
        require(self.format != "xlsb", "direct_read_unavailable", "O formato binário exige o backend Excel para navegar por células.")
        if name is None:
            require(bool(self.sheets), "sheet_not_found", "A pasta não possui abas.")
            return self.sheets[0]
        require(isinstance(name, str), "invalid_argument", "Informe o nome da aba.")
        result = next((item for item in self.sheets if item["name"].casefold() == name.casefold()), None)
        require(result is not None, "sheet_not_found", "A aba solicitada não foi encontrada.")
        return result

    def shared_strings(self) -> list[str]:
        if self._shared_strings is None:
            part = next((item["target"] for item in self.relationships.get(self.workbook_part, []) if item["type"].endswith("/sharedStrings") and not item["external"]), None)
            self._shared_strings = []
            if part:
                for node in self.xml(part):
                    if local_name(node.tag) == "si":
                        # Phonetic annotations are not part of the cell text.
                        text = "".join((item.text or "") for run in node if local_name(run.tag) in {"t", "r"} for item in run.iter() if local_name(item.tag) == "t")
                        self._shared_strings.append(text)
                        require(len(self._shared_strings) <= self.limits["max_cells"], "package_limit", "A tabela de textos excede o limite configurado.")
        return self._shared_strings

    def styles(self) -> list[dict]:
        if self._styles is None:
            part = next((item["target"] for item in self.relationships.get(self.workbook_part, []) if item["type"].endswith("/styles") and not item["external"]), None)
            self._styles = [{"num_fmt_id": 0, "number_format": "General"}]
            if part:
                root = self.xml(part)
                formats = dict(BUILTIN_FORMATS)
                formats_node = child(root, "numFmts")
                if formats_node is not None:
                    for node in formats_node:
                        try:
                            formats[int(node.get("numFmtId", "-1"))] = node.get("formatCode", "")
                        except ValueError as error:
                            raise ExcelExceptions("invalid_package", "Um formato numérico está inválido.") from error
                xfs = child(root, "cellXfs")
                if xfs is not None:
                    self._styles = []
                    for node in xfs:
                        try:
                            format_id = int(node.get("numFmtId", "0"))
                        except ValueError as error:
                            raise ExcelExceptions("invalid_package", "Um estilo de célula está inválido.") from error
                        self._styles.append({"num_fmt_id": format_id, "number_format": formats.get(format_id), "attributes": dict(node.attrib)})
        return self._styles

    def _value(self, value_type: str, raw: str | None, inline: ET.Element | None) -> tuple[object, str]:
        if value_type == "inlineStr":
            value = "" if inline is None else "".join((item.text or "") for item in inline.iter() if local_name(item.tag) == "t")
            return value, "string"
        if raw is None:
            return None, "blank"
        if value_type == "s":
            try:
                index = int(raw)
                require(0 <= index < len(self.shared_strings()), "invalid_package", "Uma célula aponta para texto compartilhado inexistente.")
                return self.shared_strings()[index], "string"
            except ValueError as error:
                raise ExcelExceptions("invalid_package", "Índice de texto compartilhado inválido.") from error
        if value_type in {"str", "d"}:
            return raw, "date" if value_type == "d" else "string"
        if value_type == "b":
            require(raw in {"0", "1", "true", "false"}, "invalid_package", "Valor booleano de célula inválido.")
            return raw in {"1", "true"}, "boolean"
        if value_type == "e":
            return raw, "error"
        require(value_type in {"n", ""}, "invalid_package", "Tipo de célula OOXML desconhecido.")
        if raw == "":
            return None, "blank"
        try:
            number = int(raw) if re.fullmatch(r"[+-]?\d+", raw) else float(raw)
            require(not isinstance(number, float) or math.isfinite(number), "invalid_package", "Valor numérico de célula não finito.")
            return number, "number"
        except ValueError as error:
            raise ExcelExceptions("invalid_package", "Valor numérico de célula inválido.") from error

    def cells(self, name: str) -> dict[str, dict]:
        sheet = self.sheet(name)
        require(sheet["kind"].casefold() == "worksheet", "direct_read_unavailable", "A aba solicitada não é uma worksheet XML.")
        if sheet["name"] in self._cells:
            return self._cells[sheet["name"]]
        root = self.xml(sheet["part"])
        result, hidden_rows, hidden_columns = {}, set(), []
        columns = child(root, "cols")
        if columns is not None:
            for node in columns:
                if _flag(node.get("hidden", "0")):
                    try:
                        first, last = int(node.get("min", "0")), int(node.get("max", "0"))
                    except ValueError as error:
                        raise ExcelExceptions("invalid_package", "Metadados de coluna inválidos.") from error
                    require(1 <= first <= last <= 16384, "invalid_package", "Metadados de coluna inválidos.")
                    hidden_columns.append((first, last))
        merges_node = child(root, "mergeCells")
        merges = [node.get("ref", "") for node in merges_node] if merges_node is not None else []
        for merge in merges:
            range_coordinates(merge)
        data = child(root, "sheetData")
        row_index = 0
        shared_masters = {}
        if data is not None:
            for row in data:
                if local_name(row.tag) != "row":
                    continue
                try:
                    row_index = int(row.get("r", str(row_index + 1)))
                except ValueError as error:
                    raise ExcelExceptions("invalid_package", "Índice de linha inválido.") from error
                require(1 <= row_index <= 1048576, "invalid_package", "Índice de linha inválido.")
                if _flag(row.get("hidden", "0")):
                    hidden_rows.add(row_index)
                column_index = 0
                for node in row:
                    if local_name(node.tag) != "c":
                        continue
                    address = node.get("r", f"{column_name(column_index + 1)}{row_index}").replace("$", "").upper()
                    cell_row, column_index = cell_coordinates(address)
                    require(cell_row == row_index and address not in result, "invalid_package", "Referência de célula inválida ou duplicada.")
                    self._cell_count += 1
                    require(self._cell_count <= self.limits["max_cells"], "package_limit", "A leitura excede o limite de células configurado.")
                    value_node, formula_node = child(node, "v"), child(node, "f")
                    raw = value_node.text if value_node is not None else None
                    if value_node is not None and raw is None and node.get("t") == "str":
                        raw = ""
                    value, value_type = self._value(node.get("t", "n"), raw, child(node, "is"))
                    try:
                        style_index = int(node.get("s", "0"))
                    except ValueError as error:
                        raise ExcelExceptions("invalid_package", "Índice de estilo inválido.") from error
                    require(0 <= style_index < len(self.styles()), "invalid_package", "Uma célula aponta para um estilo inexistente.")
                    style = self.styles()[style_index]
                    formula = "=" + (formula_node.text or "") if formula_node is not None and formula_node.text is not None else None
                    entry = {"address": address, "row": row_index, "column": column_index,
                             "type": "formula" if formula_node is not None else value_type,
                             "value": None if formula_node is not None else value,
                             "formula": formula, "cached_value": value if formula_node is not None else None,
                             "raw_value": raw, "cached_type": value_type if formula_node is not None else None,
                             "error": value if value_type == "error" else None,
                             "style_index": style_index, "number_format": style["number_format"],
                             "display_text": None, "date_value": excel_date(value, self.date_system, style["number_format"]) if value_type == "number" else value if value_type == "date" else None,
                             "freshness": "saved_cache" if formula_node is not None and value_node is not None and raw is not None else "unknown",
                             "hidden_row": row_index in hidden_rows,
                             "hidden_column": any(first <= column_index <= last for first, last in hidden_columns)}
                    if formula_node is not None:
                        entry["formula_kind"] = formula_node.get("t", "normal")
                        if formula_node.get("ref"):
                            entry["formula_range"] = formula_node.get("ref")
                        if entry["formula_kind"] == "shared":
                            index = formula_node.get("si", "")
                            require(index, "invalid_package", "Uma fórmula compartilhada não possui identificador.")
                            entry["shared_formula_index"] = index
                            if formula:
                                require(index not in shared_masters, "invalid_package", "Fórmula compartilhada duplicada.")
                                shared_masters[index] = (address, formula)
                    result[address] = entry
        for entry in result.values():
            if entry.get("formula_kind") == "shared" and entry["formula"] is None:
                master = shared_masters.get(entry["shared_formula_index"])
                require(master is not None, "invalid_package", "Uma fórmula compartilhada não possui célula de origem.")
                entry["shared_formula_master"] = master[0]
                entry["formula"] = translate_shared_formula(master[1], master[0], entry["address"])
                entry["formula_status"] = "shared_expanded"
        validation_node = child(root, "dataValidations")
        validations = []
        if validation_node is not None:
            for node in validation_node:
                validations.append({**dict(node.attrib), "formula1": _element_text(child(node, "formula1")), "formula2": _element_text(child(node, "formula2"))})
        regular_validations = list(validation_node) if validation_node is not None else []
        extended_validations = [item for item in root.iter() if local_name(item.tag) == "dataValidation" and item not in regular_validations]
        auto_filter = child(root, "autoFilter")
        self._cells[sheet["name"]] = result
        self._sheet_metadata[sheet["name"]] = {
            "merged_ranges": merges, "hidden_rows": sorted(hidden_rows), "hidden_columns": hidden_columns,
            "data_validations": validations, "extended_validation_count": len(extended_validations),
            "auto_filter": xml_summary(auto_filter) if auto_filter is not None else None,
            "protection": dict(child(root, "sheetProtection").attrib) if child(root, "sheetProtection") is not None else None,
        }
        return result

    def sheet_metadata(self, name: str) -> dict:
        self.cells(name)
        return self._sheet_metadata[self.sheet(name)["name"]]

    def names_defined(self) -> list[dict]:
        if self.workbook is None:
            return []
        container = child(self.workbook, "definedNames")
        result = []
        if container is not None:
            for node in container:
                entry = {"name": node.get("name", ""), "formula": node.text or "", "hidden": _flag(node.get("hidden", "0")), "scope": "workbook"}
                if node.get("localSheetId") is not None:
                    try:
                        index = int(node.get("localSheetId"))
                    except ValueError as error:
                        raise ExcelExceptions("invalid_package", "Escopo de nome definido inválido.") from error
                    require(0 <= index < len(self.sheets), "invalid_package", "Escopo de nome definido inexistente.")
                    entry["scope"] = self.sheets[index]["name"]
                result.append(entry)
        return result

    def tables(self) -> list[dict]:
        result = []
        for sheet in self.sheets:
            for relation in self.relationships.get(sheet["part"], []):
                if relation["external"] or not relation["type"].endswith("/table"):
                    continue
                root = self.xml(relation["target"])
                table_columns = child(root, "tableColumns")
                result.append({"sheet": sheet["name"], "part": relation["target"], **dict(root.attrib),
                               "columns": [{**dict(node.attrib), "calculated_column_formula": _element_text(child(node, "calculatedColumnFormula")), "totals_row_formula": _element_text(child(node, "totalsRowFormula"))} for node in table_columns] if table_columns is not None else []})
        return result

    def manifest(self) -> list[dict]:
        result = []
        for name in sorted(self.names):
            digest = hashlib.sha256()
            is_xml = name.casefold().endswith((".xml", ".rels", ".vml")) or self.content_types.get(name, "").casefold().endswith("+xml")
            if is_xml:
                require(self.archive.getinfo(name).file_size <= self.limits["max_xml_bytes"], "package_limit", "Uma parte XML excede o limite de leitura.")
            xml_data = io.BytesIO() if is_xml else None
            try:
                with self.archive.open(name) as stream:
                    for data in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(data)
                        if xml_data is not None:
                            xml_data.write(data)
            except (BadZipFile, RuntimeError, OSError, NotImplementedError, zlib.error) as error:
                raise ExcelExceptions("invalid_package", "Falha de integridade no pacote Excel.") from error
            if xml_data is not None:
                _safe_xml(xml_data.getvalue())
            result.append({"name": name, "bytes": self.archive.getinfo(name).file_size, "sha256": digest.hexdigest(), "content_type": self.content_types.get(name, "")})
        return result

    def inventory(self, include_cells: bool = False) -> dict:
        manifest = self.manifest()
        content_types = [value.casefold() for value in self.content_types.values()]
        types = [item["type"].casefold() for group in self.relationships.values() for item in group]
        has_vba = any("vbaproject" in item for item in content_types + types) or any(name.casefold().endswith("vbaproject.bin") for name in self.names)
        has_xlm = any("macrosheet" in item for item in content_types + types) or any("/macrosheets/" in "/" + name.casefold() for name in self.names)
        signatures = sorted(name for name in self.names if name.casefold().startswith("_xmlsignatures/") or "vbaprojectsignature" in name.casefold() or "digital-signature" in self.content_types.get(name, "").casefold())
        calculation = child(self.workbook, "calcPr") if self.workbook is not None else None
        calc_properties = dict(calculation.attrib) if calculation is not None else {}
        sheets, errors, sensitive = [], [], []
        if self.format != "xlsb":
            for sheet in self.sheets:
                entry = dict(sheet)
                if sheet["kind"].casefold() == "worksheet":
                    cells = self.cells(sheet["name"])
                    coordinates = [(item["row"], item["column"]) for item in cells.values() if item["type"] != "blank"]
                    extent = None
                    if coordinates:
                        extent = f"{column_name(min(item[1] for item in coordinates))}{min(item[0] for item in coordinates)}:{column_name(max(item[1] for item in coordinates))}{max(item[0] for item in coordinates)}"
                    entry.update(cell_count=len(cells), formula_count=sum(item["type"] == "formula" for item in cells.values()), data_range=extent, **self.sheet_metadata(sheet["name"]))
                    for address, value in cells.items():
                        if value["error"] is not None:
                            errors.append({"sheet": sheet["name"], "address": address, "error": value["error"], "formula": value["formula"]})
                        formula = value["formula"] or ""
                        if re.search(r'\bCELL\s*\(\s*"filename"|\bINFO\s*\(\s*"directory"', formula, re.I):
                            sensitive.append({"sheet": sheet["name"], "address": address, "reason": "workbook_location"})
                    if include_cells:
                        entry["cells"] = list(cells.values())
                sheets.append(entry)
        external = [{"part": source, **item} for source, group in self.relationships.items() for item in group if item["external"]]
        feature_counts = Counter()
        for name, value in self.content_types.items():
            lowered = value.casefold()
            for feature, token in {"tables": ".table+xml", "pivot_tables": ".pivottable+xml", "pivot_caches": ".pivotcachedefinition+xml", "charts": ".chart+xml", "drawings": ".drawing+xml", "comments": ".comments+xml", "threaded_comments": "threadedcomments", "slicers": "slicer", "connections": ".connections+xml", "query_tables": ".querytable+xml", "data_model": "model+data", "controls": "activex", "rich_data": "richdata"}.items():
                if token in lowered:
                    feature_counts[feature] += 1
        tables, defined_names = self.tables(), self.names_defined()
        xlm_expressions = [{"scope": item["scope"], "name": item["name"]} for item in defined_names if contains_xlm_formula(item["formula"])]
        for sheet in self.sheets:
            if sheet["kind"].casefold() == "worksheet":
                xlm_expressions.extend({"sheet": sheet["name"], "address": item["address"]} for item in self.cells(sheet["name"]).values() if contains_xlm_formula(item["formula"]))
        has_xlm = has_xlm or bool(xlm_expressions)
        unknown = sorted(name for name in self.names if name not in self._xml and not name.endswith(".rels") and name != "[Content_Types].xml")
        return {"format": self.format, "container": "opc_zip", "snapshot_id": _digest(self.path), "backend": "direct", "freshness": "saved_cache",
                "bytes": self.path.stat().st_size, "has_vba": has_vba, "has_xlm": has_xlm,
                "has_signatures": bool(signatures), "signatures": signatures,
                "active_content_verified": self.format != "xlsb", "date_system": self.date_system,
                "calc_properties": calc_properties, "calculation_mode": calc_properties.get("calcMode", "auto"),
                "sheets": sheets, "defined_names": defined_names, "tables": tables,
                "external_relationships": external, "features": dict(feature_counts),
                "formula_errors": errors, "path_sensitive_formulas": sensitive,
                "xlm_expressions": xlm_expressions,
                "package_manifest": manifest, "opaque_parts": unknown,
                "structure_errors": list(self.structure_errors),
                "limitations": ["Valores de fórmulas são caches salvos, sem prova de atualização.", "Texto exibido e equivalência visual exigem o backend Excel.", "Partes opacas são inventariadas; sua semântica não é validada."]}


def _element_text(node: ET.Element | None) -> str | None:
    return node.text if node is not None else None


def translate_shared_formula(formula: str, origin: str, target: str) -> str:
    """Desloca referências A1 compartilhadas sem tocar em strings ou nomes.

    Células absolutas, abas entre apóstrofos, conteúdo de referências estruturadas
    e identificadores de pasta entre colchetes são preservados. A função apenas
    traduz referências; ela não avalia ou valida a linguagem de fórmulas.
    """
    origin_row, origin_column = cell_coordinates(origin)
    target_row, target_column = cell_coordinates(target)
    row_delta, column_delta = target_row - origin_row, target_column - origin_column

    def cell(match):
        column_absolute, column, row_absolute, row = match.groups()
        try:
            column = column_number(column)
        except ExcelExceptions:
            return match.group(0)
        row = int(row)
        if row > 1048576:
            return match.group(0)
        column += 0 if column_absolute else column_delta
        row += 0 if row_absolute else row_delta
        if not 1 <= column <= 16384 or not 1 <= row <= 1048576:
            return "#REF!"
        return column_absolute + column_name(column) + row_absolute + str(row)

    def columns(match):
        first_absolute, first, last_absolute, last = match.groups()
        try:
            first = column_number(first) + (0 if first_absolute else column_delta)
            last = column_number(last) + (0 if last_absolute else column_delta)
        except ExcelExceptions:
            return match.group(0)
        if not 1 <= first <= 16384 or not 1 <= last <= 16384:
            return "#REF!"
        return first_absolute + column_name(first) + ":" + last_absolute + column_name(last)

    def rows(match):
        first_absolute, first, last_absolute, last = match.groups()
        first = int(first) + (0 if first_absolute else row_delta)
        last = int(last) + (0 if last_absolute else row_delta)
        if not 1 <= first <= 1048576 or not 1 <= last <= 1048576:
            return "#REF!"
        return first_absolute + str(first) + ":" + last_absolute + str(last)

    def translate(text):
        text = re.sub(r"(?<![A-Za-z0-9_.])([\$]?)([A-Za-z]{1,3})([\$]?)([1-9]\d{0,6})(?![A-Za-z0-9_.!])(?!\s*\()", cell, text)
        text = re.sub(r"(?<![A-Za-z0-9_.])([\$]?)([A-Za-z]{1,3}):([\$]?)([A-Za-z]{1,3})(?![A-Za-z0-9_.!])", columns, text)
        return re.sub(r"(?<![A-Za-z0-9_.])([\$]?)([1-9]\d{0,6}):([\$]?)([1-9]\d{0,6})(?![A-Za-z0-9_.])", rows, text)

    result, start, index = [], 0, 0
    while index < len(formula):
        character = formula[index]
        if character not in {'"', "'", "["}:
            index += 1
            continue
        result.append(translate(formula[start:index]))
        token_start = index
        index += 1
        if character == "[":
            depth = 1
            while index < len(formula) and depth:
                depth += (formula[index] == "[") - (formula[index] == "]")
                index += 1
        else:
            while index < len(formula):
                if formula[index] == character:
                    index += 1
                    if index < len(formula) and formula[index] == character:
                        index += 1
                        continue
                    break
                index += 1
        result.append(formula[token_start:index])
        start = index
    result.append(translate(formula[start:]))
    return "".join(result)


def xml_summary(node: ET.Element, depth: int = 0) -> dict:
    require(depth <= 64, "package_limit", "Uma estrutura XML excede a profundidade permitida.")
    return {"name": local_name(node.tag), "attributes": dict(node.attrib), "text": node.text if node.text and node.text.strip() else None,
            "children": [xml_summary(item, depth + 1) for item in node]}


def excel_date(value: object, date_system: str, number_format: str | None) -> str | None:
    """Deriva ISO de serial com formato temporal, mantendo o serial como valor.

    O serial 60 no sistema 1900 mantém a data fictícia do Excel. Formatos de
    duração são retornados como duração ISO; nenhum timezone é adicionado.
    """
    if type(value) not in {int, float} or not number_format:
        return None
    stripped = re.sub(r'"[^\"]*"|\\.|\[[^\]]*\]', "", number_format)
    if not re.search(r"[ymdhs]", stripped, re.I):
        return None
    try:
        if re.search(r"\[[hms]+\]", number_format, re.I):
            seconds = value * 86400
            return f"PT{seconds:g}S"
        if date_system == "1900" and 60 <= value < 61:
            seconds = round((value - 60) * 86400)
            return "1900-02-29" + (f"T{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}" if seconds else "")
        origin = datetime(1904, 1, 1) if date_system == "1904" else datetime(1899, 12, 31)
        adjusted = value - 1 if date_system == "1900" and value > 60 else value
        result = origin + timedelta(days=adjusted)
        if not re.search(r"[yd]", stripped, re.I) and re.search(r"[hs]", stripped, re.I):
            return result.time().isoformat()
        return result.date().isoformat() if result.time().isoformat() == "00:00:00" else result.isoformat()
    except (OverflowError, ValueError):
        return None


def inspect_package(path: Path, limits: dict | None = None) -> dict:
    """Inventaria o formato real sem abrir Excel.

    Lança ``invalid_package`` para container inválido, ``package_limit`` para
    limites excedidos e ``unsupported_format`` quando o conteúdo não é Excel.
    """
    path = Path(path)
    require(path.is_file(), "file_not_found", "O arquivo de entrada não foi encontrado.")
    with path.open("rb") as stream:
        signature = stream.read(8)
    if signature.startswith(b"PK"):
        with SpreadsheetPackage(path, limits) as package:
            return package.inventory()
    if signature == CFB_SIGNATURE:
        effective = {**DEFAULT_LIMITS, **(limits or {})}
        names = _cfb_stream_names(path, effective.get("max_input_bytes", 262144000))
        encrypted = "EncryptedPackage" in names or "EncryptionInfo" in names
        known_legacy = any(name in {"Workbook", "Book"} for name in names)
        require(encrypted or known_legacy, "unsupported_format", "O container composto não identifica uma pasta Excel.")
        return {"format": "encrypted_ooxml" if encrypted else "xls", "container": "cfb", "snapshot_id": _digest(path),
                "backend": "direct", "freshness": "unknown", "bytes": path.stat().st_size,
                "encrypted": encrypted, "has_vba": any(name.casefold() in {"_vba_project", "vba"} for name in names),
                "has_xlm": None, "has_signatures": None, "signatures": [], "active_content_verified": False,
                "calc_properties": {}, "calculation_mode": None, "path_sensitive_formulas": [],
                "package_manifest": [], "structure_errors": [], "sheets": [], "stream_names": sorted(names),
                "limitations": ["O conteúdo binário/criptografado não recebeu inspeção de macros XLM nem de fórmulas."]}
    raise ExcelExceptions("unsupported_format", "O conteúdo não é uma pasta Excel OOXML ou binária reconhecida.")


def preflight(path: Path, profile: dict | None = None, limits: dict | None = None) -> dict:
    """Informa se a abertura nativa respeita a política de conteúdo ativo.

    Detectar XLM impede a abertura neste backend. Formatos cuja ausência de
    conteúdo ativo não foi comprovada exigem política administrativa específica.
    A permissão retornada não implica confiança em VBA nem autoriza sua execução.
    """
    report = inspect_package(path, limits)
    profile = profile or {}
    blocked = []
    if report.get("has_xlm"):
        blocked.append("xlm_macros")
    if report.get("structure_errors"):
        blocked.append("invalid_structure")
    if not report.get("active_content_verified") and not profile.get("allow_unverified_active_content", False):
        blocked.append("active_content_unverified")
    if report.get("format") in {"xlam"} and not profile.get("allow_addins", False):
        blocked.append("addin_loading_disabled")
    report.update(native_open_allowed=not blocked, blocked_reasons=blocked)
    return report


def formula_errors(path: Path, limits: dict | None = None) -> list[dict]:
    return inspect_package(path, limits).get("formula_errors", [])


def path_sensitive_formulas(path: Path, limits: dict | None = None) -> list[dict]:
    return inspect_package(path, limits).get("path_sensitive_formulas", [])
