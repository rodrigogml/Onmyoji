"""Diário local de inclusões BIS10, sem retomada automática de envios incertos.

O diário guarda somente a entrada financeira efetiva e o resultado sanitizado
fornecidos pelo serviço. Configuração e credenciais não pertencem ao contrato.
Uma reserva SENDING permanece bloqueada após reinício, sem prazo de expiração.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator, Mapping

from ..artifacts import ArtifactPathError, reject_links


class RequestStoreError(ValueError):
    """Falha funcional do diário, identificada por código estável e dados mínimos."""

    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.data = data or {}


def _json(value: Any) -> str:
    """Serializa JSON estrito e estável; rejeita tipos sem representação segura."""
    def check(item: Any) -> None:
        if isinstance(item, dict):
            if not all(isinstance(key, str) for key in item):
                raise ValueError
            for child in item.values():
                check(child)
        elif isinstance(item, list):
            for child in item:
                check(child)
        elif item is not None and type(item) not in (str, int, float, bool):
            raise ValueError

    try:
        check(value)
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise RequestStoreError("invalid_request", "O diário exige conteúdo JSON válido.") from error


def destination_id(host: str, port: int) -> str:
    """Identifica host/porta sem credenciais; aliases DNS distintos não são resolvidos."""
    if (not isinstance(host, str) or not host.strip() or isinstance(port, bool)
            or not isinstance(port, int) or not 1 <= port <= 65535):
        raise RequestStoreError("invalid_request", "Destino BIS10 inválido.")
    normalized = host.strip().lower().rstrip(".")
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    try:
        normalized = ipaddress.ip_address(normalized).compressed
    except ValueError:
        pass
    if not normalized or any(character.isspace() for character in normalized) or any(character in normalized for character in "/\\@"):
        raise RequestStoreError("invalid_request", "Destino BIS10 inválido.")
    return hashlib.sha256(_json([normalized, port]).encode("utf-8")).hexdigest()


class RequestStore:
    """Coordena inclusões entre processos que usam o mesmo arquivo SQLite.

    ``begin`` devolve ``(registro, is_new)``. Somente ``is_new=True`` autoriza
    iniciar o subprocesso. Replays preservam os dados efetivos da primeira
    tentativa; SENDING e UNKNOWN nunca podem ser reenviados automaticamente.
    A proteção é local e não coordena outros clientes ou a transação do BIS10.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.is_absolute():
            raise RequestStoreError("invalid_storage", "O diário BIS10 exige um caminho absoluto no workspace.")
        try:
            reject_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connection(write=True) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1):
                    raise RequestStoreError("request_schema_version", "Versão do diário BIS10 não suportada.")
                connection.execute("""CREATE TABLE IF NOT EXISTS bill_requests (
                    destination TEXT NOT NULL, company_id INTEGER NOT NULL,
                    request_id TEXT NOT NULL, canonical_code TEXT NOT NULL,
                    payload_hash TEXT NOT NULL, payload TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('SENDING','SUCCEEDED','REJECTED','UNKNOWN')),
                    result TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(destination, company_id, request_id)
                )""")
                connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS bill_request_pending_code
                    ON bill_requests(destination, company_id, canonical_code)
                    WHERE state IN ('SENDING','UNKNOWN')""")
                connection.execute("PRAGMA user_version=1")
        except (OSError, ArtifactPathError) as error:
            raise RequestStoreError("request_storage_error", "Não foi possível abrir o diário BIS10 no workspace.") from error

    @contextmanager
    def _connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            reject_links(self.path)
            connection = sqlite3.connect(self.path, timeout=30)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError, ArtifactPathError) as error:
            if connection is not None:
                connection.rollback()
            raise RequestStoreError("request_storage_error", "Não foi possível acessar o diário BIS10.") from error
        except BaseException:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _key(destination: str, company_id: int, request_id: str) -> tuple[str, int, str]:
        if (not isinstance(destination, str) or len(destination) != 64
                or any(character not in "0123456789abcdef" for character in destination)
                or not isinstance(company_id, int) or isinstance(company_id, bool) or company_id <= 0
                or not isinstance(request_id, str) or not 1 <= len(request_id) <= 128
                or request_id.strip() != request_id or any(ord(character) < 32 for character in request_id)):
            raise RequestStoreError("invalid_request", "Destino, empresa ou request_id inválido para o diário BIS10.")
        return destination, company_id, request_id

    @staticmethod
    def _record(row: sqlite3.Row) -> dict[str, Any]:
        record = dict(row)
        try:
            record["payload"] = json.loads(record["payload"])
            record["result"] = json.loads(record["result"]) if record["result"] is not None else None
            if not isinstance(record["payload"], dict) or record["result"] is not None and not isinstance(record["result"], dict):
                raise ValueError
        except (TypeError, ValueError) as error:
            raise RequestStoreError("request_storage_error", "O diário BIS10 contém um registro inválido.") from error
        return record

    @staticmethod
    def _find(connection: sqlite3.Connection, key: tuple[str, int, str]) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM bill_requests WHERE destination=? AND company_id=? AND request_id=?", key).fetchone()

    def get(self, destination: str, company_id: int, request_id: str) -> dict[str, Any] | None:
        """Consulta a tentativa local; não consulta o BIS10 nem libera reserva."""
        key = self._key(destination, company_id, request_id)
        with self._connection() as connection:
            row = self._find(connection, key)
            return self._record(row) if row is not None else None

    def begin(self, destination: str, company_id: int, request_id: str,
              payload: Mapping[str, Any], canonical_code: str, *,
              effective_payload: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        """Reserva antes do envio ou recupera uma tentativa sem autorizar reenvio.

        O hash usa ``payload`` já normalizado pelo serviço. ``effective_payload``
        pode acrescentar padrões variáveis, como data de lançamento: esses dados
        ficam congelados, mas não causam conflito quando o padrão muda no replay.
        Chave com conteúdo diferente gera ``request_conflict``; outro envio
        pendente do código gera ``bill_code_pending`` e deve ser reconciliado.
        """
        key = self._key(destination, company_id, request_id)
        if (not isinstance(canonical_code, str) or len(canonical_code) != 44
                or not canonical_code.isascii() or not canonical_code.isdigit()
                or not isinstance(payload, Mapping)
                or effective_payload is not None and not isinstance(effective_payload, Mapping)):
            raise RequestStoreError("invalid_request", "Código canônico e payload inválidos para o diário BIS10.")
        payload_json = _json(dict(payload))
        fingerprint = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        effective_json = _json(dict(effective_payload) if effective_payload is not None else dict(payload))
        with self._connection(write=True) as connection:
            existing = self._find(connection, key)
            if existing is not None:
                if existing["payload_hash"] != fingerprint or existing["canonical_code"] != canonical_code:
                    raise RequestStoreError("request_conflict", "request_id já utilizado com conteúdo diferente.", {"request_id": request_id})
                return self._record(existing), False
            pending = connection.execute("""SELECT request_id, state FROM bill_requests
                WHERE destination=? AND company_id=? AND canonical_code=?
                AND state IN ('SENDING','UNKNOWN')""", (destination, company_id, canonical_code)).fetchone()
            if pending is not None:
                raise RequestStoreError("bill_code_pending", "Há uma tentativa pendente para este código; consulte o resultado antes de outra inclusão.", dict(pending))
            stamp = datetime.now(timezone.utc).isoformat()
            connection.execute("""INSERT INTO bill_requests
                (destination,company_id,request_id,canonical_code,payload_hash,payload,state,created_at,updated_at)
                VALUES(?,?,?,?,?,?,'SENDING',?,?)""", (*key, canonical_code, fingerprint, effective_json, stamp, stamp))
            return self._record(self._find(connection, key)), True

    def _finish(self, destination: str, company_id: int, request_id: str,
                state: str, result: Mapping[str, Any] | None) -> dict[str, Any]:
        key = self._key(destination, company_id, request_id)
        if result is not None and not isinstance(result, Mapping):
            raise RequestStoreError("invalid_request", "Resultado do diário BIS10 deve ser um objeto JSON.")
        encoded = _json(dict(result)) if result is not None else None
        if state == "SUCCEEDED" and encoded is None:
            raise RequestStoreError("invalid_request", "Confirmação do diário BIS10 exige o resultado validado.")
        with self._connection(write=True) as connection:
            row = self._find(connection, key)
            if row is None:
                raise RequestStoreError("request_not_found", "Tentativa BIS10 não encontrada.")
            if row["state"] == state and (encoded is None or row["result"] == encoded):
                return self._record(row)
            if row["state"] not in {"SENDING", "UNKNOWN"} or row["state"] == "UNKNOWN" and state == "REJECTED":
                raise RequestStoreError("request_state_conflict", "O resultado conhecido da tentativa não permite esta alteração.", {"state": row["state"]})
            connection.execute("""UPDATE bill_requests SET state=?,result=?,updated_at=?
                WHERE destination=? AND company_id=? AND request_id=?""",
                               (state, encoded, datetime.now(timezone.utc).isoformat(), *key))
            return self._record(self._find(connection, key))

    def success(self, destination: str, company_id: int, request_id: str,
                result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Registra resultado com IDs já validados pelo serviço, inclusive após reconciliação inequívoca."""
        return self._finish(destination, company_id, request_id, "SUCCEEDED", result)

    def rejected(self, destination: str, company_id: int, request_id: str,
                 result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Registra rejeição comprovada antes da gravação; não resolve UNKNOWN nem autoriza repetir a chave."""
        return self._finish(destination, company_id, request_id, "REJECTED", result)

    def unknown(self, destination: str, company_id: int, request_id: str,
                result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Mantém a exclusão do código após timeout, interrupção ou resposta sem confirmação suficiente."""
        return self._finish(destination, company_id, request_id, "UNKNOWN", result)
