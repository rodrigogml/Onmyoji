"""Cache privado de respostas Omie compartilhado entre processos da instalação.

O chamador serializa consultas iguais pela trava de método antes de consultar
este cache. Gerações e leases impedem reutilizar respostas anteriores a uma
escrita, inclusive quando uma leitura termina depois da invalidação.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
from time import time
from typing import Any, Iterable, Mapping
from uuid import uuid4

from .exception import OmieError


DEFAULT_TTL = 61.0
MAX_TTL = 3600.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 512
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_ALIASES = 2048
MAX_WRITE_LEASE = 3600.0
_ERRORS = (OSError, sqlite3.Error, TypeError, ValueError, UnicodeError, OverflowError, RecursionError)


def _error() -> OmieError:
    return OmieError(
        "omie_local_cache_error", "Não foi possível consultar ou atualizar o cache compartilhado da Omie; nenhuma nova chamada foi autorizada por esse cache.",
        {"retryable": False},
    )


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("invalid_cache_key")
    return value


def _document(value: Any) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_cache_document")
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _duration(value: Any, maximum: float) -> float:
    if type(value) not in (int, float) or not isfinite(value) or not 0 < value <= maximum:
        raise ValueError("invalid_cache_duration")
    return float(value)


def _endpoints(values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError("invalid_cache_resources")
    result = tuple(sorted({_text(value) for value in values}))
    if not result:
        raise ValueError("missing_cache_resources")
    return result


def _success(data: Mapping[str, Any]) -> bool:
    return not data.get("faultstring") and all(
        field not in data or (type(data[field]) in (str, int) and data[field] in (0, "0"))
        for field in ("cCodStatus", "codigo_status")
    )


def _fingerprint(value: Any) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ValueError("invalid_request_fingerprint")
    return value


def _non_json_constant(_value: str) -> None:
    raise ValueError("invalid_json_constant")


@dataclass(frozen=True)
class CacheHit:
    """Resposta lida do cache, com os instantes UTC e a geração do recurso."""

    data: dict[str, Any]
    stored_at: float
    expires_at: float
    generation: int


class ResponseCache:
    """Isola respostas pela App Key sem persistir a credencial nem os filtros.

    ``put`` aceita somente objetos JSON bem-sucedidos e respeita limites de
    tamanho, retenção e número de entradas. Leases de escrita expiram para
    recuperar processos interrompidos; ``end_write`` invalida novamente mesmo
    após essa expiração. Falhas de estado, I/O ou serialização lançam
    ``omie_local_cache_error``; não autorizam uma consulta HTTP alternativa.
    """

    def __init__(self, root: Path, app_key: str):
        try:
            if not isinstance(app_key, str) or not app_key:
                raise ValueError("missing_app_key")
            self.path = root.resolve() / "state" / "mcp" / "omie" / "responses.sqlite3"
            self.scope = sha256(app_key.encode("utf-8")).hexdigest()
        except _ERRORS as error:
            raise _error() from error

    @staticmethod
    def request_fingerprint(endpoint: str, method: str, params: Mapping[str, Any]) -> str:
        """Identifica a requisição independentemente de TTL ou invalidações."""
        try:
            key = _document({"endpoint": _text(endpoint), "method": _text(method), "params": json.loads(_document(params))})
            return sha256(key.encode("utf-8")).hexdigest()
        except _ERRORS as error:
            raise _error() from error

    @contextmanager
    def _transaction(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(self.path, timeout=5.0)) as connection:
                with connection:
                    connection.execute("BEGIN IMMEDIATE")
                    version = connection.execute("PRAGMA user_version").fetchone()[0]
                    if version not in (0, 1):
                        raise ValueError("unsupported_cache_schema")
                    connection.execute("CREATE TABLE IF NOT EXISTS resources (scope TEXT NOT NULL, endpoint TEXT NOT NULL, generation INTEGER NOT NULL, PRIMARY KEY(scope, endpoint))")
                    connection.execute("CREATE TABLE IF NOT EXISTS responses (scope TEXT NOT NULL, endpoint TEXT NOT NULL, fingerprint TEXT NOT NULL, generation INTEGER NOT NULL, stored_at REAL NOT NULL, expires_at REAL NOT NULL, data TEXT NOT NULL, size INTEGER NOT NULL, PRIMARY KEY(scope, endpoint, fingerprint))")
                    connection.execute("CREATE TABLE IF NOT EXISTS writes (scope TEXT NOT NULL, endpoint TEXT NOT NULL, token TEXT NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(scope, endpoint, token))")
                    connection.execute("CREATE TABLE IF NOT EXISTS aliases (scope TEXT NOT NULL, endpoint TEXT NOT NULL, source TEXT NOT NULL, fingerprint TEXT NOT NULL, expires_at REAL NOT NULL, PRIMARY KEY(scope, endpoint, source))")
                    connection.execute("PRAGMA user_version=1")
                    now = time()
                    connection.execute("DELETE FROM responses WHERE expires_at <= ?", (now,))
                    connection.execute("DELETE FROM writes WHERE expires_at <= ?", (now,))
                    connection.execute("DELETE FROM aliases WHERE expires_at <= ?", (now,))
                    yield connection
        except _ERRORS as error:
            raise _error() from error

    def _generation(self, connection: sqlite3.Connection, endpoint: str) -> int:
        row = connection.execute("SELECT generation FROM resources WHERE scope=? AND endpoint=?", (self.scope, endpoint)).fetchone()
        generation = row[0] if row is not None else 0
        if type(generation) is not int or generation < 0:
            raise ValueError("invalid_cache_generation")
        return generation

    def generation(self, endpoint: str) -> int:
        """Retorna a geração que a leitura deve manter até ``put``."""
        try:
            endpoint = _text(endpoint)
            with self._transaction() as connection:
                return self._generation(connection, endpoint)
        except _ERRORS as error:
            raise _error() from error

    def _active(self, connection: sqlite3.Connection, endpoint: str) -> bool:
        return connection.execute("SELECT 1 FROM writes WHERE scope=? AND endpoint=? LIMIT 1", (self.scope, endpoint)).fetchone() is not None

    def is_current(self, endpoint: str, generation: int) -> bool:
        """Confere atomicamente se o snapshot ainda precede nenhuma escrita.

        Uma resposta recusada por tamanho pode continuar utilizável fora do
        cache. Uma resposta cuja geração mudou, ou produzida durante escrita
        ativa, não serve como confirmação do estado atual.
        """
        try:
            endpoint = _text(endpoint)
            if type(generation) is not int or generation < 0:
                raise ValueError("invalid_cache_generation")
            with self._transaction() as connection:
                return not self._active(connection, endpoint) and generation == self._generation(connection, endpoint)
        except _ERRORS as error:
            raise _error() from error

    def resolve_fingerprint(self, endpoint: str, method: str, params: Mapping[str, Any]) -> str:
        """Reúne ID e integração já comprovados no histórico de consumo.

        O alias não é usado por ``get`` para escolher dados: ele só identifica
        a mesma consulta para o intervalo entre requisições. Sobrevive à
        invalidação das respostas até sua própria expiração.
        """
        source = self.request_fingerprint(endpoint, method, params)
        with self._transaction() as connection:
            row = connection.execute("SELECT fingerprint FROM aliases WHERE scope=? AND endpoint=? AND source=?", (self.scope, endpoint, source)).fetchone()
            return source if row is None else _fingerprint(row[0])

    def register_aliases(self, endpoint: str, method: str, queries: Iterable[Mapping[str, Any]], *, fingerprint: str, generation: int, ttl: float = 300.0) -> bool:
        """Registra equivalências comprovadas pela resposta da consulta.

        ``fingerprint`` é a chave efetivamente usada para fazer HTTP. O registro
        só ocorre se a geração da leitura permanecer válida, sem escrita ativa.
        Parâmetros e identificadores claros não são gravados na tabela de aliases.
        """
        try:
            endpoint = _text(endpoint)
            _text(method)
            canonical = _fingerprint(fingerprint)
            duration = _duration(ttl, MAX_TTL)
            if duration < DEFAULT_TTL or type(generation) is not int or generation < 0:
                raise ValueError("invalid_alias_lifetime")
            sources = {self.request_fingerprint(endpoint, method, params) for params in queries}
            if not sources:
                raise ValueError("missing_alias_queries")
            with self._transaction() as connection:
                if self._active(connection, endpoint) or generation != self._generation(connection, endpoint):
                    return False
                expires_at = time() + duration
                connection.executemany("INSERT OR REPLACE INTO aliases VALUES (?, ?, ?, ?, ?)", [(self.scope, endpoint, source, canonical, expires_at) for source in sources])
                connection.execute("DELETE FROM aliases WHERE rowid IN (SELECT rowid FROM aliases ORDER BY expires_at, rowid LIMIT MAX(0, (SELECT COUNT(*) FROM aliases)-?))", (MAX_ALIASES,))
                return True
        except _ERRORS as error:
            raise _error() from error

    def get(self, endpoint: str, method: str, params: Mapping[str, Any]) -> CacheHit | None:
        """Retorna somente resposta vigente, sem uma escrita ativa no recurso."""
        fingerprint = self.request_fingerprint(endpoint, method, params)
        with self._transaction() as connection:
            if self._active(connection, endpoint):
                return None
            row = connection.execute("SELECT generation, stored_at, expires_at, data FROM responses WHERE scope=? AND endpoint=? AND fingerprint=?", (self.scope, endpoint, fingerprint)).fetchone()
            if row is None:
                return None
            generation, stored_at, expires_at, document = row
            if type(generation) is not int or generation < 0 or type(stored_at) not in (int, float) or type(expires_at) not in (int, float) or not isfinite(stored_at) or not isfinite(expires_at) or expires_at <= stored_at:
                raise ValueError("invalid_cache_metadata")
            now = time()
            if generation != self._generation(connection, endpoint) or now < stored_at or now >= expires_at:
                return None
            data = json.loads(document, parse_constant=_non_json_constant)
            if not isinstance(data, dict) or not _success(data):
                raise ValueError("invalid_cache_response")
            return CacheHit(data, float(stored_at), float(expires_at), generation)

    def put(self, endpoint: str, method: str, params: Mapping[str, Any], data: Mapping[str, Any], *, generation: int, ttl: float = DEFAULT_TTL) -> bool:
        """Publica a resposta se a geração não mudou e não há escrita ativa.

        Retorna ``False`` para resposta de falha, resposta maior que o limite,
        geração superada ou recurso em escrita. O chamador não deve reaproveitar
        essa resposta como entrada de cache caso o armazenamento seja recusado.
        """
        fingerprint = self.request_fingerprint(endpoint, method, params)
        try:
            duration = _duration(ttl, MAX_TTL)
            if type(generation) is not int or generation < 0:
                raise ValueError("invalid_cache_generation")
            document = _document(data)
            size = len(document.encode("utf-8"))
            if not _success(data) or size > MAX_RESPONSE_BYTES:
                return False
            with self._transaction() as connection:
                if self._active(connection, endpoint) or generation != self._generation(connection, endpoint):
                    return False
                now = time()
                connection.execute("INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (self.scope, endpoint, fingerprint, generation, now, now + duration, document, size))
                while True:
                    count, total = connection.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM responses").fetchone()
                    if count <= MAX_ENTRIES and total <= MAX_TOTAL_BYTES:
                        break
                    connection.execute("DELETE FROM responses WHERE rowid IN (SELECT rowid FROM responses ORDER BY stored_at, rowid LIMIT 1)")
                return connection.execute("SELECT 1 FROM responses WHERE scope=? AND endpoint=? AND fingerprint=?", (self.scope, endpoint, fingerprint)).fetchone() is not None
        except _ERRORS as error:
            raise _error() from error

    def _invalidate(self, connection: sqlite3.Connection, endpoints: tuple[str, ...]) -> None:
        for endpoint in endpoints:
            generation = self._generation(connection, endpoint) + 1
            connection.execute("INSERT OR REPLACE INTO resources VALUES (?, ?, ?)", (self.scope, endpoint, generation))
            connection.execute("DELETE FROM responses WHERE scope=? AND endpoint=?", (self.scope, endpoint))

    def invalidate(self, endpoints: Iterable[str]) -> None:
        """Invalida recursos relacionados atomicamente para todos os processos."""
        try:
            resources = _endpoints(endpoints)
            with self._transaction() as connection:
                self._invalidate(connection, resources)
        except _ERRORS as error:
            raise _error() from error

    def begin_write(self, endpoints: Iterable[str], *, lease_seconds: float = 300.0) -> str:
        """Invalida recursos e impede cache enquanto a escrita estiver em voo."""
        try:
            resources = _endpoints(endpoints)
            duration = _duration(lease_seconds, MAX_WRITE_LEASE)
            token = uuid4().hex
            with self._transaction() as connection:
                self._invalidate(connection, resources)
                expires_at = time() + duration
                connection.executemany("INSERT INTO writes VALUES (?, ?, ?, ?)", [(self.scope, endpoint, token, expires_at) for endpoint in resources])
            return token
        except _ERRORS as error:
            raise _error() from error

    def end_write(self, endpoints: Iterable[str], token: str) -> None:
        """Invalida novamente e libera somente o lease da escrita indicada.

        A invalidação também ocorre após expiração do lease ou chamada repetida,
        evitando preservar uma resposta produzida enquanto a escrita terminava.
        Leases de outros processos continuam bloqueando o recurso.
        """
        try:
            resources = _endpoints(endpoints)
            if not isinstance(token, str) or len(token) != 32 or any(ch not in "0123456789abcdef" for ch in token):
                raise ValueError("invalid_write_token")
            with self._transaction() as connection:
                self._invalidate(connection, resources)
                connection.executemany("DELETE FROM writes WHERE scope=? AND endpoint=? AND token=?", [(self.scope, endpoint, token) for endpoint in resources])
        except _ERRORS as error:
            raise _error() from error
