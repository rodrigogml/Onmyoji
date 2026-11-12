"""Identidade privada do App Server e encerramento seguro de órfão conhecido.

Stdio não é reconectável após perder o Gateway. Antes de abrir outro processo,
o checkpoint permite encerrar somente a identidade nativa que ele registrou.
PID sozinho nunca autoriza término; identidade inacessível bloqueia a abertura.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import select
import signal
import sqlite3

from .codex_capabilities import executable_digest
from .codex_execution import APP_SERVER_ARGUMENTS


CHECKPOINT_KEY = "app_server_process"
MAX_WAIT_SECONDS = 5.0


class ProcessRecoveryError(RuntimeError):
    """Impede nova abertura quando a posse ou o término não estão comprovados."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Reconciliação segura do App Server pendente: " + reason)


@dataclass(frozen=True)
class ProcessRecoveryResult:
    action: str
    process_epoch: str | None = None
    pid: int | None = None


def _path_hash(path: Path | str) -> str:
    return sha256(os.path.normcase(str(Path(path).resolve(strict=True))).encode("utf-8")).hexdigest()


def _identity(pid: int, created: str, executable: str) -> dict:
    digest = executable_digest(executable)
    if not digest:
        raise ProcessRecoveryError("process_identity_unavailable")
    return {"pid": pid, "created": created, "native_executable_path_sha256": _path_hash(executable), "native_executable_sha256": digest}


class _WindowsProcess:
    def __init__(self, pid: int, terminate: bool):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes, self.pid = ctypes, wintypes, pid
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
            "GetExitCodeProcess": ([wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
            "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        }
        for name, (args, result) in signatures.items():
            getattr(self.api, name).argtypes, getattr(self.api, name).restype = args, result
        self.handle = self.api.OpenProcess(0x1000 | 0x100000 | (1 if terminate else 0), False, pid)
        if not self.handle:
            if ctypes.get_last_error() in {87, 1168}:
                raise ProcessLookupError(pid)
            raise ProcessRecoveryError("process_identity_unavailable")

    def identity(self) -> dict | None:
        if self.api.WaitForSingleObject(self.handle, 0) == 0:
            return None
        created, exited, kernel, user = (self.wintypes.FILETIME() for _ in range(4))
        if not self.api.GetProcessTimes(self.handle, self.ctypes.byref(created), self.ctypes.byref(exited), self.ctypes.byref(kernel), self.ctypes.byref(user)):
            raise ProcessRecoveryError("process_identity_unavailable")
        size = self.wintypes.DWORD(32768)
        path = self.ctypes.create_unicode_buffer(size.value)
        if not self.api.QueryFullProcessImageNameW(self.handle, 0, path, self.ctypes.byref(size)):
            raise ProcessRecoveryError("process_identity_unavailable")
        return _identity(self.pid, str((created.dwHighDateTime << 32) | created.dwLowDateTime), path.value)

    def terminate(self) -> None:
        if not self.api.TerminateProcess(self.handle, 1) and self.api.WaitForSingleObject(self.handle, 0) != 0:
            raise ProcessRecoveryError("process_termination_failed")
        if self.api.WaitForSingleObject(self.handle, int(MAX_WAIT_SECONDS * 1000)) != 0:
            raise ProcessRecoveryError("process_termination_unconfirmed")

    def close(self) -> None:
        self.api.CloseHandle(self.handle)


class _LinuxProcess:
    def __init__(self, pid: int, terminate: bool):
        self.pid = pid
        self.fd = os.pidfd_open(pid) if hasattr(os, "pidfd_open") else None
        if terminate and (self.fd is None or not hasattr(signal, "pidfd_send_signal")):
            self.close()
            raise ProcessRecoveryError("process_pin_unavailable")

    def _exited(self) -> bool:
        return self.fd is not None and bool(select.select([self.fd], [], [], 0)[0])

    def identity(self) -> dict | None:
        if self._exited():
            return None
        try:
            proc = Path("/proc") / str(self.pid)
            fields = (proc / "stat").read_text().rpartition(")")[2].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
            if not boot:
                raise ValueError("Missing boot identity")
            identity = _identity(self.pid, boot + ":" + fields[19], os.readlink(proc / "exe"))
        except (OSError, ValueError, IndexError, ProcessRecoveryError) as error:
            # /proc/exe pode sumir com o líder morto e outros threads vivos;
            # boot_id ou o arquivo do binário também podem ficar inacessíveis.
            # Somente o pidfd pronto confirma o fim de todo o grupo pinado.
            if self._exited():
                return None
            raise ProcessRecoveryError("process_identity_unavailable") from error
        return None if self._exited() else identity

    def terminate(self) -> None:
        signal.pidfd_send_signal(self.fd, signal.SIGKILL)
        if not select.select([self.fd], [], [], MAX_WAIT_SECONDS)[0]:
            raise ProcessRecoveryError("process_termination_unconfirmed")

    def matches_node_launcher(self, executable: str) -> bool:
        """Confere o Node e a forma atual ou legada do launcher sem registrar seu conteúdo."""
        try:
            proc = Path("/proc") / str(self.pid)
            native = Path(os.readlink(proc / "exe"))
            arguments = (proc / "cmdline").read_bytes().split(b"\0")
            if arguments and arguments[-1] == b"":
                arguments.pop()
            if self._exited() or native.name not in {"node", "nodejs"} or len(arguments) < 4:
                return False
            launcher = Path(os.fsdecode(arguments[1])).resolve(strict=True)
            expected = Path(executable).expanduser().resolve(strict=True)
            current = tuple(argument.encode("utf-8") for argument in APP_SERVER_ARGUMENTS)
            legacy = (b"app-server", b"--stdio")
            return launcher == expected and tuple(arguments[2:]) in {current, legacy}
        except (OSError, ValueError):
            raise ProcessRecoveryError("process_identity_unavailable") from None

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def _open_native(pid: int, terminate: bool = False):
    try:
        if os.name == "nt":
            return _WindowsProcess(pid, terminate)
        if os.name == "posix" and Path("/proc").is_dir():
            return _LinuxProcess(pid, terminate)
        raise ProcessRecoveryError("process_platform_unverified")
    except ProcessLookupError:
        return None
    except (OSError, ValueError, IndexError) as error:
        raise ProcessRecoveryError("process_identity_unavailable") from error


def _read(store) -> dict | None:
    try:
        with store.lock:
            row = store.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", (CHECKPOINT_KEY,)).fetchone()
    except sqlite3.Error as error:
        raise ProcessRecoveryError("process_checkpoint_unavailable") from error
    if not row:
        return None
    try:
        value = json.loads(row[0])
        if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1 or value.get("state") not in {"active", "stopped", "replaced"} or type(value.get("pid")) is not int or not 1 <= value["pid"] <= 0xFFFFFFFF or not isinstance(value.get("process_epoch"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value["process_epoch"]) or not isinstance(value.get("created"), str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,128}", value["created"]):
            raise ValueError()
        for key in ("instance_sha256", "native_executable_path_sha256", "native_executable_sha256", "expected_executable_sha256"):
            if not isinstance(value.get(key), str) or len(value[key]) != 64 or any(character not in "0123456789abcdef" for character in value[key]):
                raise ValueError()
        if value.get("launcher", "native") not in {"native", "node_script"}:
            raise ValueError()
        return value
    except (ValueError, TypeError) as error:
        raise ProcessRecoveryError("process_checkpoint_invalid") from error


def _finish(store, record: dict, state: str) -> None:
    try:
        with store.lock, store.db:
            if _read(store) != record:
                raise ProcessRecoveryError("process_checkpoint_changed")
            store.db.execute("UPDATE gateway_checkpoints SET value=? WHERE key=?", (json.dumps({**record, "state": state}, sort_keys=True), CHECKPOINT_KEY))
    except sqlite3.Error as error:
        raise ProcessRecoveryError("process_checkpoint_unavailable") from error


def _matches(record: dict, identity: dict) -> bool:
    return all(record.get(key) == identity.get(key) for key in ("pid", "created", "native_executable_path_sha256", "native_executable_sha256"))


def _same_process(record: dict, identity: dict) -> bool:
    return all(record.get(key) == identity.get(key) for key in ("pid", "created"))


def record_process(store, root: Path, executable: str, client) -> bool:
    """Persiste após initialize e antes de geração; fake sem PID não tem órfão.

    A falha impede turn/start. O chamador continua responsável por encerrar um
    cliente que abriu mas não conseguiu registrar; não deve perder seu Popen.
    """
    process = getattr(client, "process", None)
    pid = getattr(process, "pid", None)
    if pid is None:
        return False
    epoch = getattr(client, "process_epoch", None)
    if type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF or not isinstance(epoch, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", epoch) or not callable(getattr(process, "poll", None)):
        raise ProcessRecoveryError("process_identity_unavailable")
    native = _open_native(pid)
    try:
        identity = native.identity() if native else None
        expected = executable_digest(executable)
        if not identity or process.poll() is not None or not expected:
            raise ProcessRecoveryError("process_identity_unavailable")
        launcher = "native"
        if identity["native_executable_sha256"] != expected:
            verify = getattr(native, "matches_node_launcher", None)
            if not callable(verify) or not verify(executable) or native.identity() != identity or executable_digest(executable) != expected or process.poll() is not None:
                raise ProcessRecoveryError("process_identity_unavailable")
            launcher = "node_script"
        record = {"schema_version": 1, "process_epoch": epoch, "state": "active", "instance_sha256": _path_hash(root), "expected_executable_sha256": expected, "launcher": launcher, **identity}
        with store.lock, store.db:
            previous = _read(store)
            if previous and previous["state"] == "active" and previous != record:
                raise ProcessRecoveryError("process_checkpoint_active")
            store.db.execute("INSERT OR REPLACE INTO gateway_checkpoints VALUES (?,?)", (CHECKPOINT_KEY, json.dumps(record, sort_keys=True)))
        return True
    except sqlite3.Error as error:
        raise ProcessRecoveryError("process_checkpoint_unavailable") from error
    except (OSError, ValueError) as error:
        raise ProcessRecoveryError("process_identity_unavailable") from error
    finally:
        if native:
            native.close()


def recover_process(store, root: Path, executable: str) -> ProcessRecoveryResult:
    """Encerra apenas órfão com posse comprovada, sem reconectar seu stdio."""
    record = _read(store)
    if not record:
        return ProcessRecoveryResult("absent")
    epoch, pid = record["process_epoch"], record["pid"]
    if record["state"] != "active":
        return ProcessRecoveryResult("already_stopped", epoch, pid)
    native = _open_native(pid, terminate=True)
    try:
        identity = native.identity() if native else None
        if identity is None:
            _finish(store, record, "stopped")
            return ProcessRecoveryResult("gone", epoch, pid)
        if record["instance_sha256"] != _path_hash(root):
            raise ProcessRecoveryError("process_instance_mismatch")
        if not _same_process(record, identity):
            _finish(store, record, "replaced")
            return ProcessRecoveryResult("identity_mismatch", epoch, pid)
        # Rename altera /proc/exe e o hash vem do arquivo no disco. Nenhum
        # desses sinais prova morte com a mesma criação; não liberar abertura.
        if not _matches(record, identity):
            raise ProcessRecoveryError("process_executable_unverified")
        if executable_digest(executable) != record["expected_executable_sha256"]:
            raise ProcessRecoveryError("process_executable_changed")
        if record.get("launcher") == "node_script":
            verify = getattr(native, "matches_node_launcher", None)
            if not callable(verify) or not verify(executable) or native.identity() != identity or executable_digest(executable) != record["expected_executable_sha256"]:
                raise ProcessRecoveryError("process_executable_unverified")
            # O wrapper Node pode manter um filho nativo. A morte do wrapper
            # não comprova que esse filho terminou nem autoriza outro servidor.
            raise ProcessRecoveryError("process_launcher_recovery_unverified")
        native.terminate()
        _finish(store, record, "stopped")
        return ProcessRecoveryResult("terminated", epoch, pid)
    except (OSError, ValueError) as error:
        raise ProcessRecoveryError("process_identity_unavailable") from error
    finally:
        if native:
            native.close()


def mark_stopped(store, epoch: str) -> bool:
    """Marca stopped somente depois de confirmar morte ou substituição do PID."""
    record = _read(store)
    if not record or record["process_epoch"] != epoch:
        return False
    if record["state"] != "active":
        return True
    native = _open_native(record["pid"])
    try:
        identity = native.identity() if native else None
        if identity is not None and _same_process(record, identity):
            raise ProcessRecoveryError("process_still_running" if _matches(record, identity) else "process_executable_unverified")
        _finish(store, record, "stopped")
        return True
    except (OSError, ValueError) as error:
        raise ProcessRecoveryError("process_identity_unavailable") from error
    finally:
        if native:
            native.close()
