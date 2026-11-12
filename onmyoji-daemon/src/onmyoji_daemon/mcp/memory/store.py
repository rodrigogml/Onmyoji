"""Persistência SQLite privada das instruções atuais do Shikigami."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import unicodedata
from uuid import uuid4


class MemoryStoreError(ValueError):
    """Erro funcional da memória, seguro para apresentação ao agente."""


def normalize_text(value: str) -> str:
    """Normaliza texto somente para comparação, sem alterar o conteúdo salvo."""
    return " ".join(value.casefold().split())


def normalize_keywords(values: object) -> list[str]:
    """Valida e normaliza termos de recuperação informados pelo agente."""
    if not isinstance(values, list):
        raise MemoryStoreError("keywords deve ser uma lista de 3 a 8 termos específicos.")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise MemoryStoreError("Cada keyword deve ser texto.")
        keyword = " ".join(value.strip().casefold().split())
        if not 2 <= len(keyword) <= 40:
            raise MemoryStoreError("Cada keyword deve ter entre 2 e 40 caracteres.")
        if keyword not in normalized:
            normalized.append(keyword)
    if not 3 <= len(normalized) <= 8:
        raise MemoryStoreError("Informe de 3 a 8 keywords distintas.")
    return normalized


class MemoryStore:
    """Mantém apenas instruções vigentes; revisão substitui e exclusão remove."""

    def __init__(self, root: Path):
        self.directory = root.resolve() / "configs" / "mcp" / "memory"
        self.path = self.directory / "instructions.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS instructions (
                id TEXT PRIMARY KEY,
                applies_when TEXT NOT NULL,
                instruction TEXT NOT NULL,
                keywords_json TEXT NOT NULL,
                applies_when_normalized TEXT NOT NULL UNIQUE
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS instructions_fts USING fts5(
                id UNINDEXED, applies_when, instruction, keywords,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        return connection

    @staticmethod
    def _text(value: object, field: str, minimum: int, maximum: int) -> str:
        if not isinstance(value, str):
            raise MemoryStoreError(f"{field} deve ser texto.")
        text = " ".join(value.strip().split())
        if not minimum <= len(text) <= maximum:
            raise MemoryStoreError(f"{field} deve ter entre {minimum} e {maximum} caracteres.")
        return text

    @staticmethod
    def _item(row: sqlite3.Row) -> dict[str, object]:
        return {"id": row["id"], "applies_when": row["applies_when"], "instruction": row["instruction"], "keywords": json.loads(row["keywords_json"])}

    @staticmethod
    def _fts_query(query: str) -> str:
        folded = "".join(char for char in unicodedata.normalize("NFKD", query) if not unicodedata.combining(char))
        tokens = re.findall(r"[^\W_]{2,}", folded, flags=re.UNICODE)
        if not tokens:
            raise MemoryStoreError("A busca precisa conter ao menos um termo útil.")
        return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens[:20])

    def learn(self, applies_when: object, instruction: object, keywords: object) -> dict[str, object]:
        condition = self._text(applies_when, "applies_when", 10, 500)
        directive = self._text(instruction, "instruction", 10, 2000)
        terms = normalize_keywords(keywords)
        normalized = normalize_text(condition)
        with closing(self._connect()) as connection, connection:
            existing = connection.execute("SELECT * FROM instructions WHERE applies_when_normalized=?", (normalized,)).fetchone()
            if existing:
                if normalize_text(existing["instruction"]) == normalize_text(directive) and json.loads(existing["keywords_json"]) == terms:
                    return {"created": False, "instruction": self._item(existing)}
                raise MemoryStoreError("Já existe uma instrução para essa condição. Consulte-a e use memory.revise_instruction em vez de criar uma duplicata.")
            identifier = str(uuid4())
            encoded = json.dumps(terms, ensure_ascii=False, separators=(",", ":"))
            connection.execute("INSERT INTO instructions VALUES (?, ?, ?, ?, ?)", (identifier, condition, directive, encoded, normalized))
            connection.execute("INSERT INTO instructions_fts(id, applies_when, instruction, keywords) VALUES (?, ?, ?, ?)", (identifier, condition, directive, " ".join(terms)))
            row = connection.execute("SELECT * FROM instructions WHERE id=?", (identifier,)).fetchone()
        return {"created": True, "instruction": self._item(row)}

    def get(self, identifier: object) -> dict[str, object]:
        value = self._text(identifier, "id", 1, 100)
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM instructions WHERE id=?", (value,)).fetchone()
        if not row:
            raise MemoryStoreError("Instrução de memória não encontrada.")
        return self._item(row)

    def revise(self, identifier: object, applies_when: object, instruction: object, keywords: object) -> dict[str, object]:
        value = self._text(identifier, "id", 1, 100)
        condition = self._text(applies_when, "applies_when", 10, 500)
        directive = self._text(instruction, "instruction", 10, 2000)
        terms = normalize_keywords(keywords)
        encoded = json.dumps(terms, ensure_ascii=False, separators=(",", ":"))
        normalized = normalize_text(condition)
        with closing(self._connect()) as connection, connection:
            current = connection.execute("SELECT id FROM instructions WHERE id=?", (value,)).fetchone()
            if not current:
                raise MemoryStoreError("Instrução de memória não encontrada.")
            duplicate = connection.execute("SELECT id FROM instructions WHERE applies_when_normalized=? AND id<>?", (normalized, value)).fetchone()
            if duplicate:
                raise MemoryStoreError("Outra instrução já usa essa condição; revise ou exclua a duplicata conscientemente.")
            connection.execute("UPDATE instructions SET applies_when=?, instruction=?, keywords_json=?, applies_when_normalized=? WHERE id=?", (condition, directive, encoded, normalized, value))
            connection.execute("DELETE FROM instructions_fts WHERE id=?", (value,))
            connection.execute("INSERT INTO instructions_fts(id, applies_when, instruction, keywords) VALUES (?, ?, ?, ?)", (value, condition, directive, " ".join(terms)))
            row = connection.execute("SELECT * FROM instructions WHERE id=?", (value,)).fetchone()
        return {"revised": True, "instruction": self._item(row)}

    def delete(self, identifier: object) -> dict[str, object]:
        value = self._text(identifier, "id", 1, 100)
        with closing(self._connect()) as connection, connection:
            row = connection.execute("SELECT * FROM instructions WHERE id=?", (value,)).fetchone()
            if not row:
                raise MemoryStoreError("Instrução de memória não encontrada.")
            connection.execute("DELETE FROM instructions_fts WHERE id=?", (value,))
            connection.execute("DELETE FROM instructions WHERE id=?", (value,))
        return {"deleted": True, "id": value}

    def search(self, query: object, limit: int) -> dict[str, object]:
        text = self._text(query, "query", 2, 500)
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT i.* FROM instructions_fts f JOIN instructions i ON i.id=f.id WHERE instructions_fts MATCH ? ORDER BY bm25(instructions_fts) LIMIT ?",
                (self._fts_query(text), limit),
            ).fetchall()
        return {"instructions": [self._item(row) for row in rows], "count": len(rows)}

    def list(self, limit: int, offset: int) -> dict[str, object]:
        with closing(self._connect()) as connection, connection:
            total = int(connection.execute("SELECT COUNT(*) FROM instructions").fetchone()[0])
            rows = connection.execute("SELECT * FROM instructions ORDER BY applies_when_normalized LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return {"instructions": [self._item(row) for row in rows], "count": len(rows), "total": total, "offset": offset}
