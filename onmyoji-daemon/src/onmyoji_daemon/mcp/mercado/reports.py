"""Cache transitório e paginação segura de relatórios CSV Mercado Pago."""
from __future__ import annotations

import csv
import hashlib
import io
from pathlib import Path
import time
from typing import Any
import zipfile

from .client import decode_cursor, encode_cursor, envelope
from .exception import MercadoError


class ReportReader:
    def __init__(self, root: Path, defaults: dict[str, Any]):
        self.root = root.resolve()
        self.defaults = defaults
        self.directory = self.root / "tmp" / "mcp" / "mercado" / "reports"
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.directory.resolve().is_relative_to(self.root / "tmp"):
            raise MercadoError("invalid_report_file", "Cache de relatório fora de tmp.")

    def cleanup(self) -> None:
        threshold = time.time() - self.defaults["report_cache_ttl_seconds"]
        for path in self.directory.iterdir():
            if path.is_file() and path.stat().st_mtime < threshold:
                path.unlink(missing_ok=True)

    def cache(self, profile: str, report_id: str, content: bytes) -> Path:
        self.cleanup()
        if len(content) > self.defaults["max_report_bytes"]:
            raise MercadoError("report_too_large", "Relatório excede o limite permitido.")
        key = hashlib.sha256(f"{profile}:{report_id}".encode()).hexdigest()
        target = self.directory / (key + ".csv")
        data = self._extract(content)
        if len(data) > self.defaults["max_report_bytes"]:
            raise MercadoError("report_too_large", "Expansão do relatório excede o limite permitido.")
        target.write_bytes(data)
        return target

    def cached(self, profile: str, report_id: str) -> Path | None:
        """Retorna somente cache ainda vigente; conteúdo expirado é eliminado."""
        self.cleanup()
        key = hashlib.sha256(f"{profile}:{report_id}".encode()).hexdigest()
        target = self.directory / (key + ".csv")
        return target if target.is_file() else None

    def _extract(self, content: bytes) -> bytes:
        if not content.startswith(b"PK\x03\x04"):
            return content
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                files = [item for item in archive.infolist() if not item.is_dir()]
                if len(files) != 1:
                    raise MercadoError("invalid_report_file", "ZIP deve conter exatamente um CSV.")
                item = files[0]
                name = Path(item.filename)
                if name.name != item.filename.replace("\\", "/") or name.suffix.casefold() != ".csv" or item.file_size > self.defaults["max_report_bytes"] or item.file_size > max(1, len(content)) * 100:
                    raise MercadoError("invalid_report_file", "ZIP de relatório possui caminho ou expansão inválida.")
                return archive.read(item)
        except MercadoError:
            raise
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise MercadoError("invalid_report_file", "ZIP de relatório inválido.") from error

    def rows(self, profile: str, family: str, report_id: str, path: Path, limit: int, cursor: str | None, separator: str) -> dict[str, Any]:
        query = {"family": family, "report_id": report_id, "separator": separator}
        offset = decode_cursor(cursor, "mercadopago.report_rows_list", profile, query) if cursor else 0
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                text = stream.read()
            reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=separator)
            if not reader.fieldnames or any(not field for field in reader.fieldnames):
                raise MercadoError("invalid_report_file", "Cabeçalhos CSV ausentes ou inválidos.")
            rows = list(reader)
        except (OSError, UnicodeDecodeError, csv.Error) as error:
            raise MercadoError("invalid_report_file", "CSV de relatório inválido.") from error
        selected = rows[offset:offset + limit]
        next_offset = offset + len(selected)
        next_cursor = encode_cursor("mercadopago.report_rows_list", profile, query, next_offset) if next_offset < len(rows) else None
        return envelope(profile, "mercadopago", "mercadopago.reports", selected, limit, next_cursor)
