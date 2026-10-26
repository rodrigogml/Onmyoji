"""Atualização atômica de notes OAuth sem staging em texto claro ou argumentos secretos."""
from __future__ import annotations

import ctypes
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Any


def replace_database(source: Path, destination: Path) -> None:
    """Substitui o KDBX preservando a ACL do destino no Windows."""
    if os.name != "nt":
        os.replace(source, destination)
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    replace.restype = wintypes.BOOL
    if not replace(str(destination), str(source), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def database_lock(database: Path):
    """Serializa escritas OAuth em entradas diferentes do mesmo KDBX entre processos."""
    from .keepass import CredentialError
    with database.with_name(database.name + ".oauth.lock").open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise CredentialError("vault_busy", "Outra integração está atualizando o cofre; tente novamente.")
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def save_notes(database: Path, password: str, entry_path: str, state: dict[str, Any]) -> None:
    from .keepass import CredentialError
    try:
        database = database.resolve()
        with database_lock(database):
            _save_notes(database, password, entry_path, state)
    except CredentialError:
        raise
    except OSError as error:
        raise CredentialError("vault_write_failed", "Não foi possível bloquear o cofre para gravação OAuth.") from error


def _save_notes(database: Path, password: str, entry_path: str, state: dict[str, Any]) -> None:
    from .keepass import CredentialError
    try:
        from pykeepass import PyKeePass
    except ImportError as error:
        raise CredentialError("dependency_missing", "Prepare o runtime MCP para persistir o consentimento OAuth.") from error
    temporary: Path | None = None
    try:
        serialized = json.dumps(state, ensure_ascii=False, allow_nan=False)
        original = database.read_bytes()
        original_hash = hashlib.sha256(original).digest()
        keepass = PyKeePass(io.BytesIO(original), password=password)
        normalized = entry_path.replace("\\", "/").strip("/")
        entries = [entry for entry in keepass.entries if "/".join(entry.path).strip("/") == normalized]
        if len(entries) != 1:
            raise CredentialError("entry_not_unique", "Entrada OAuth ausente ou duplicada; cofre não alterado.")
        entries[0].notes = serialized
        encrypted = io.BytesIO()
        keepass.save(encrypted)
        verified = PyKeePass(io.BytesIO(encrypted.getvalue()), password=password)
        checked = [entry for entry in verified.entries if "/".join(entry.path).strip("/") == normalized]
        if len(checked) != 1 or checked[0].notes != serialized:
            raise CredentialError("vault_write_failed", "Falha ao validar o estado OAuth criptografado.")
        with tempfile.NamedTemporaryFile(dir=database.parent, prefix=database.name + ".", suffix=".kdbx", delete=False) as target:
            temporary = Path(target.name)
            target.write(encrypted.getvalue())
            target.flush()
            os.fsync(target.fileno())
        shutil.copystat(database, temporary)
        for attempt in range(6):
            if hashlib.sha256(database.read_bytes()).digest() != original_hash:
                raise CredentialError("vault_changed", "O cofre mudou durante a gravação OAuth; repita após sincronizá-lo.")
            try:
                replace_database(temporary, database)
                temporary = None
                return
            except PermissionError as error:
                if getattr(error, "winerror", None) != 32 or attempt == 5:
                    raise
                time.sleep(0.2)
    except CredentialError:
        raise
    except Exception as error:
        raise CredentialError("vault_write_failed", "Não foi possível persistir o estado OAuth no cofre MCP.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
