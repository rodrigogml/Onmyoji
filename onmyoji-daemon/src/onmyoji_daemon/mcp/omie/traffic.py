"""Controle local de consumo, concorrência e cooldown da API Omie.

Intervalos, janelas de consumo, consultas repetidas e cooldowns são coordenados
em SQLite entre os processos da mesma instalação. O estado contém somente
hashes e contadores; nenhuma credencial, payload ou registro financeiro é persistido.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from math import isfinite
import json
import os
import sqlite3
from pathlib import Path
from threading import Lock, RLock
from time import monotonic, sleep, time
from typing import Any, Mapping

from .exception import OmieError


def retry_after_seconds(headers: Mapping[str, Any] | None) -> float | None:
    """Interpreta Retry-After em segundos ou data HTTP, sem confiar no texto bruto."""
    if headers is None:
        return None
    value = headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(value))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if isfinite(seconds) else None


class OmieTrafficGate:
    """Limita leituras/escritas por método e compartilha o estado entre serviços."""

    def __init__(self) -> None:
        self._guard = RLock()
        self._method_locks: dict[tuple[str, str], Lock] = {}

    @staticmethod
    def _key(root: Path, app_key: str, method: str) -> tuple[str, str]:
        digest = sha256((app_key + "\0" + method).encode("utf-8")).hexdigest()
        return str(root.resolve()), digest

    @contextmanager
    def _state(self, root: Path):
        """Abre uma transação curta de coordenação; falhas impedem o envio à API."""
        connection = None
        try:
            path = root.resolve() / "state" / "mcp" / "omie" / "traffic.sqlite3"
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=3, isolation_level=None)
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("unsupported_state_version")
            for statement in (
                "CREATE TABLE IF NOT EXISTS events (app TEXT NOT NULL, method TEXT NOT NULL, sent REAL NOT NULL)",
                "CREATE INDEX IF NOT EXISTS events_method ON events (method, sent)",
                "CREATE INDEX IF NOT EXISTS events_app ON events (app, sent)",
                "CREATE INDEX IF NOT EXISTS events_time ON events (sent)",
                "CREATE TABLE IF NOT EXISTS last_calls (method TEXT PRIMARY KEY, sent REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS cooldowns (method TEXT PRIMARY KEY, until REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS fingerprints (fingerprint TEXT PRIMARY KEY, until REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS inflight (method TEXT PRIMARY KEY, until REAL NOT NULL)",
                "CREATE TABLE IF NOT EXISTS daily (app TEXT NOT NULL, bucket INTEGER NOT NULL, count INTEGER NOT NULL, PRIMARY KEY(app, bucket))",
            ):
                connection.execute(statement)
            connection.execute("PRAGMA user_version=1")
            self._import_cooldowns(connection, path.parent / "cooldowns.json")
            yield connection
            connection.commit()
        except (OSError, sqlite3.Error, ValueError, TypeError) as error:
            if connection is not None:
                connection.rollback()
            raise OmieError(
                "omie_local_state_error", "Não foi possível coordenar o consumo Omie no estado compartilhado.",
                {"retryable": False},
            ) from error
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _import_cooldowns(connection: sqlite3.Connection, path: Path) -> None:
        """Preserva bloqueios antigos sem apagar o arquivo usado por versões anteriores."""
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        if not isinstance(saved, dict):
            raise ValueError("invalid_legacy_cooldowns")
        now = time()
        for method, until in saved.items():
            if not isinstance(method, str) or type(until) not in (int, float) or not isfinite(until):
                raise ValueError("invalid_legacy_cooldown")
            if until > now:
                connection.execute(
                    "INSERT INTO cooldowns VALUES (?, ?) ON CONFLICT(method) DO UPDATE SET until=MAX(until, excluded.until)",
                    (method, until),
                )

    @staticmethod
    def _fingerprint(app_key: str, method: str, fingerprint: str) -> str:
        return sha256((app_key + "\0" + method + "\0" + fingerprint).encode("utf-8")).hexdigest()

    @staticmethod
    def _timestamp(value: Any) -> float:
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError("invalid_shared_timestamp")
        return float(value)

    @contextmanager
    def serialize(self, root: Path, app_key: str, method: str, *, wait_seconds: float = 60):
        """Serializa chamadas de uma mesma App Key + método entre processos da instalação.

        O arquivo de trava não contém App Key, payload nem qualquer dado do ERP.
        A trava por thread evita reentrância no processo; a trava do sistema
        operacional coordena processos independentes que usam o mesmo root.
        No Windows, reserva o primeiro byte mesmo em arquivo vazio, sem ler ou
        escrever uma região que outra chamada pode manter bloqueada.

        ``wait_seconds`` limita a espera total por thread e arquivo (60 por
        padrão). Lança ``omie_local_busy`` quando esse prazo esgota e
        ``omie_local_lock_error`` quando a trava não pode ser
        preparada, adquirida ou liberada por uma falha local. A falha de
        liberação após sucesso da operação inclui ``phase="release"`` nos
        detalhes; quando a operação já falhou, preserva sua exceção original.
        """
        if type(wait_seconds) not in (int, float) or not isfinite(wait_seconds) or not 0 <= wait_seconds <= 3600:
            raise OmieError("invalid_config", "Prazo de espera da trava Omie inválido.", {"retryable": False})
        key = self._key(root, app_key, method)
        with self._guard:
            thread_lock = self._method_locks.setdefault(key, Lock())
        deadline = monotonic() + wait_seconds
        if not thread_lock.acquire(timeout=wait_seconds):
            raise OmieError(
                "omie_local_busy", "Outra chamada Omie desse método continua em execução nesta instalação.",
                {"retryable": False, "retry_after_seconds": 3},
            )
        try:
            path = Path(key[0]) / "state" / "mcp" / "omie" / "method-locks" / (key[1] + ".lock")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("a+b")
            except OSError as error:
                raise OmieError(
                    "omie_local_lock_error", "Não foi possível preparar a trava de concorrência Omie.",
                    {"retryable": False},
                ) from error
            try:
                if os.name == "nt":
                    import msvcrt
                else:
                    import fcntl
                while True:
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError as error:
                        if error.errno not in {errno.EACCES, errno.EAGAIN}:
                            raise OmieError(
                                "omie_local_lock_error", "Não foi possível obter a trava de concorrência Omie.",
                                {"retryable": False},
                            ) from error
                        if monotonic() >= deadline:
                            raise OmieError(
                                "omie_local_busy", "Outra chamada Omie desse método continua em execução nesta instalação.",
                                {"retryable": False, "retry_after_seconds": 3},
                            ) from error
                        sleep(0.1)
                operation_failed = False
                try:
                    yield
                except BaseException:
                    operation_failed = True
                    raise
                finally:
                    try:
                        handle.seek(0)
                        if os.name == "nt":
                            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError as error:
                        if not operation_failed:
                            raise OmieError(
                                "omie_local_lock_error", "Não foi possível liberar a trava de concorrência Omie.",
                                {"retryable": False, "phase": "release"},
                            ) from error
            finally:
                handle.close()
        finally:
            thread_lock.release()

    def pause(self, root: Path, app_key: str, method: str, seconds: float) -> None:
        """Persiste o maior cooldown do método sem sobrescrever pausas concorrentes."""
        if type(seconds) not in (int, float) or not isfinite(seconds):
            raise OmieError("invalid_config", "Duração de bloqueio Omie inválida.", {"retryable": False})
        key = self._key(root, app_key, method)[1]
        with self._state(root) as connection:
            connection.execute(
                "INSERT INTO cooldowns VALUES (?, ?) ON CONFLICT(method) DO UPDATE SET until=MAX(until, excluded.until)",
                (key, time() + max(0.0, seconds)),
            )

    def complete(self, root: Path, app_key: str, method: str, fingerprint: str | None) -> None:
        """Reserva 61 segundos desde o término remoto, inclusive quando a leitura falhou.

        Encurta a reserva preventiva de timeout mais 61 segundos e libera o
        lease do método. A janela sobrevive à invalidação de respostas do cache.
        ``None`` libera apenas o lease, sem registrar fingerprint de escrita.
        Deve executar sob a mesma ``serialize`` que protegeu ``await_slot`` e a
        requisição: essa exclusão garante que a conclusão pertence ao único
        proprietário vivo do método.
        """
        if fingerprint is None and not (root.resolve() / "state/mcp/omie/traffic.sqlite3").exists():
            return
        if fingerprint is not None and (not isinstance(fingerprint, str) or not fingerprint):
            raise OmieError("invalid_config", "Fingerprint de leitura Omie inválido.", {"retryable": False})
        with self._state(root) as connection:
            connection.execute("DELETE FROM inflight WHERE method=?", (self._key(root, app_key, method)[1],))
            if fingerprint is not None:
                key = self._fingerprint(app_key, method, fingerprint)
                connection.execute(
                    "INSERT INTO fingerprints VALUES (?, ?) ON CONFLICT(fingerprint) DO UPDATE SET until=excluded.until",
                    (key, time() + 61.0),
                )

    def _reserve(
        self, root: Path, app_key: str, method: str, interval: float, method_limit: int,
        root_limit: int, fingerprint: str | None, app_limit: int | None, day_limit: int | None, request_timeout: float,
    ) -> tuple[float, str, bool]:
        now = time()
        app = sha256(app_key.encode("utf-8")).hexdigest()
        key = self._key(root, app_key, method)[1]
        with self._state(root) as connection:
            connection.execute("DELETE FROM events WHERE sent <= ?", (now - 60.0,))
            connection.execute("DELETE FROM cooldowns WHERE until <= ?", (now,))
            connection.execute("DELETE FROM fingerprints WHERE until <= ?", (now,))
            connection.execute("DELETE FROM inflight WHERE until <= ?", (now,))
            minimum_bucket = int((now - 86400.0) // 3600)
            connection.execute("DELETE FROM daily WHERE bucket < ?", (minimum_bucket,))
            row = connection.execute("SELECT until FROM cooldowns WHERE method=?", (key,)).fetchone()
            if row is not None:
                return max(0.0, self._timestamp(row[0]) - now), "remote_cooldown", True
            delay, reason = 0.0, "method_interval"
            row = connection.execute("SELECT sent FROM last_calls WHERE method=?", (key,)).fetchone()
            if row is not None:
                delay = max(0.0, self._timestamp(row[0]) + interval - now)
            row = connection.execute("SELECT until FROM inflight WHERE method=?", (key,)).fetchone()
            if row is not None:
                wait = self._timestamp(row[0]) - now
                if wait > delay:
                    delay, reason = wait, "method_in_progress"
            for where, parameters, limit, label in (
                ("method=?", (key,), method_limit, "method_minute_limit"),
                ("1=1", (), root_limit, "installation_minute_limit"),
                ("app=?", (app,), app_limit, "application_minute_limit"),
            ):
                if limit is None:
                    continue
                count, oldest = connection.execute("SELECT COUNT(*), MIN(sent) FROM events WHERE " + where, parameters).fetchone()
                if count >= limit:
                    wait = self._timestamp(oldest) + 60.0 - now
                    if wait > delay:
                        delay, reason = wait, label
            if day_limit is not None:
                count, oldest = connection.execute("SELECT SUM(count), MIN(bucket) FROM daily WHERE app=?", (app,)).fetchone()
                if count is not None and (type(count) is not int or count < 0 or type(oldest) is not int):
                    raise ValueError("invalid_daily_counter")
                if count is not None and count >= day_limit:
                    wait = (oldest + 1) * 3600.0 + 86400.0 - now
                    if wait > delay:
                        delay, reason = wait, "application_daily_limit"
            fingerprint_key = self._fingerprint(app_key, method, fingerprint) if fingerprint is not None else None
            if fingerprint_key is not None:
                row = connection.execute("SELECT until FROM fingerprints WHERE fingerprint=?", (fingerprint_key,)).fetchone()
                if row is not None:
                    wait = self._timestamp(row[0]) - now
                    if wait > delay:
                        delay, reason = wait, "identical_request_interval"
            if delay <= 0:
                connection.execute("INSERT INTO events VALUES (?, ?, ?)", (app, key, now))
                connection.execute("INSERT INTO last_calls VALUES (?, ?) ON CONFLICT(method) DO UPDATE SET sent=excluded.sent", (key, now))
                connection.execute(
                    "INSERT INTO daily VALUES (?, ?, 1) ON CONFLICT(app,bucket) DO UPDATE SET count=count+1",
                    (app, int(now // 3600)),
                )
                if request_timeout > 0:
                    connection.execute("INSERT OR REPLACE INTO inflight VALUES (?, ?)", (key, now + request_timeout))
                if fingerprint_key is not None:
                    connection.execute(
                        "INSERT INTO fingerprints VALUES (?, ?) ON CONFLICT(fingerprint) DO UPDATE SET until=MAX(until, excluded.until)",
                        (fingerprint_key, now + request_timeout + 61.0),
                    )
            return max(0.0, delay), reason, False

    def await_slot(
        self, root: Path, app_key: str, method: str, *,
        interval: float = 0.35, method_limit: int = 180, root_limit: int = 900,
        request_fingerprint: str | None = None, app_limit: int | None = None, day_limit: int | None = None,
        request_timeout: float = 0,
    ) -> None:
        """Reserva o consumo entre processos antes de enviar uma requisição.

        Coordena intervalo e cotas por método, instalação e aplicação. Leituras
        idênticas aguardam 61 segundos desde o término anterior. A cota diária
        usa buckets de uma hora numa janela conservadora de 24 horas: pode
        preservar até uma hora adicional antes de liberar nova cota.

        ``request_timeout`` reserva o método durante a chamada e protege a
        repetição até timeout mais 61 segundos caso o processo seja interrompido
        antes de ``complete``. O ciclo reserva, HTTP e conclusão deve permanecer
        dentro de ``serialize``; escritas também chamam ``complete`` com ``None``.

        A espera local tem orçamento de 65 segundos e é feita fora da transação,
        em passos de até um segundo. ``omie_rate_limited`` informa o prazo quando
        esse orçamento não basta; pausas curtas também aguardam nesse orçamento.
        ``omie_cooldown`` preserva bloqueios remotos longos sem aguardar 30 minutos.
        Falhas do estado geram ``omie_local_state_error`` e
        impedem o envio, em vez de ignorar o controle compartilhado.
        """
        if type(interval) not in (int, float) or not isfinite(interval) or interval < 0:
            raise OmieError("invalid_config", "Intervalo Omie inválido.", {"retryable": False})
        if type(request_timeout) not in (int, float) or not isfinite(request_timeout) or not 0 <= request_timeout <= 3500:
            raise OmieError("invalid_config", "Timeout de coordenação Omie inválido.", {"retryable": False})
        if any(type(limit) is not int or limit < 1 for limit in (method_limit, root_limit)) or any(
            limit is not None and (type(limit) is not int or limit < 1) for limit in (app_limit, day_limit)
        ):
            raise OmieError("invalid_config", "Limites Omie inválidos.", {"retryable": False})
        if request_fingerprint is not None and (not isinstance(request_fingerprint, str) or not request_fingerprint):
            raise OmieError("invalid_config", "Fingerprint de leitura Omie inválido.", {"retryable": False})
        deadline = monotonic() + 65.0
        while True:
            delay, reason, remote = self._reserve(
                root, app_key, method, interval, method_limit, root_limit, request_fingerprint, app_limit, day_limit, request_timeout,
            )
            if delay <= 0:
                return
            if delay > max(0.0, deadline - monotonic()):
                if remote:
                    raise OmieError(
                        "omie_cooldown", "A chamada Omie permanece em bloqueio temporário; não foi reenviada.",
                        {"retryable": False, "retry_after_seconds": max(1, int(delay + 0.999)), "reason": reason},
                    )
                raise OmieError(
                    "omie_rate_limited", "A consulta Omie aguarda o intervalo compartilhado de consumo; a chamada não foi enviada.",
                    {"retryable": False, "retry_after_seconds": max(1, int(delay + 0.999)), "reason": reason},
                )
            sleep(min(1.0, delay))


TRAFFIC_GATE = OmieTrafficGate()
