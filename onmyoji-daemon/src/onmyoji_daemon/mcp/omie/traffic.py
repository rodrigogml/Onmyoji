"""Controle local de consumo, concorrência e cooldown da API Omie.

Os limites são preventivos por processo. O bloqueio HTTP 425 é registrado no
estado privado para que reiniciar o daemon não ignore os 30 minutos da Omie.
Nenhuma credencial, payload ou registro financeiro é persistido.
"""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
import errno
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from math import isfinite
import json
import os
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
        self._method_calls: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._root_calls: dict[str, deque[float]] = defaultdict(deque)
        self._last_call: dict[tuple[str, str], float] = {}
        self._pauses: dict[tuple[str, str], float] = {}

    @staticmethod
    def _key(root: Path, app_key: str, method: str) -> tuple[str, str]:
        digest = sha256((app_key + "\0" + method).encode("utf-8")).hexdigest()
        return str(root.resolve()), digest

    @staticmethod
    def _pause_path(root: str) -> Path:
        return Path(root) / "state" / "mcp" / "omie" / "cooldowns.json"

    def _disk_pauses(self, root: str) -> dict[str, float]:
        try:
            saved = json.loads(self._pause_path(root).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(saved, dict):
            return {}
        return {key: float(value) for key, value in saved.items()
                if isinstance(key, str) and type(value) in (int, float) and isfinite(value) and value > time()}

    @contextmanager
    def serialize(self, root: Path, app_key: str, method: str):
        """Serializa chamadas de uma mesma App Key + método entre processos da instalação.

        O arquivo de trava não contém App Key, payload nem qualquer dado do ERP.
        A trava por thread evita reentrância no processo; a trava do sistema
        operacional coordena processos independentes que usam o mesmo root.
        """
        key = self._key(root, app_key, method)
        with self._guard:
            thread_lock = self._method_locks.setdefault(key, Lock())
        with thread_lock:
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
                # msvcrt.locking requer ao menos um byte; flock também aceita.
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    if not handle.read(1):
                        handle.seek(0)
                        handle.write(b"0")
                        handle.flush()
                else:
                    import fcntl
                deadline = monotonic() + 60.0
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
                try:
                    yield
                finally:
                    handle.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def pause(self, root: Path, app_key: str, method: str, seconds: float) -> None:
        """Registra cooldown no processo e em state/, sem gravar a App Key."""
        key = self._key(root, app_key, method)
        expires = time() + max(0.0, seconds)
        with self._guard:
            self._pauses[key] = max(expires, self._pauses.get(key, 0.0))
            entries = self._disk_pauses(key[0])
            entries[key[1]] = max(self._pauses[key], entries.get(key[1], 0.0))
            path = self._pause_path(key[0])
            temp = path.with_name(path.name + f".{os.getpid()}.tmp")
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temp.write_text(json.dumps(entries), encoding="utf-8")
                os.replace(temp, path)
            except OSError:
                # Bloqueio em memória continua ativo caso o estado esteja indisponível.
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass

    def await_slot(
        self, root: Path, app_key: str, method: str, *,
        interval: float = 0.35,
        method_limit: int = 180,
        root_limit: int = 900,
    ) -> None:
        """Reserva uma requisição respeitando janela móvel e cooldown ativo."""
        key = self._key(root, app_key, method)
        while True:
            with self._guard:
                now_wall = time()
                blocked = max(self._pauses.get(key, 0.0), self._disk_pauses(key[0]).get(key[1], 0.0))
                if blocked > now_wall:
                    raise OmieError(
                        "omie_cooldown", "A chamada Omie permanece em bloqueio temporário; não foi reenviada.",
                        {"retryable": False, "retry_after_seconds": max(1, int(blocked - now_wall + 0.999))},
                    )
                now = monotonic()
                root_calls = self._root_calls["process"]
                method_calls = self._method_calls[key]
                while root_calls and now - root_calls[0] >= 60:
                    root_calls.popleft()
                while method_calls and now - method_calls[0] >= 60:
                    method_calls.popleft()
                delay = max(0.0, self._last_call.get(key, now - interval) + interval - now)
                if len(root_calls) >= root_limit:
                    delay = max(delay, 60 - (now - root_calls[0]))
                if len(method_calls) >= method_limit:
                    delay = max(delay, 60 - (now - method_calls[0]))
                if delay <= 0:
                    self._last_call[key] = now
                    method_calls.append(now)
                    root_calls.append(now)
                    return
            sleep(delay)


TRAFFIC_GATE = OmieTrafficGate()
