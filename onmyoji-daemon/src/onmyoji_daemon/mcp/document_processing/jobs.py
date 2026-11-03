"""Fila persistente com processos isolados para PDF e OCR locais."""
from __future__ import annotations

import atexit
import copy
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4

from .contracts import DocumentError, require
from .paths import contained_path, file_digest, reject_links


TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
_instances: dict[tuple[str, str], "Jobs"] = {}
_instances_lock = threading.Lock()


def _write_json(path: Path, value: dict) -> None:
    reject_links(path)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
        reject_links(path)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def execution_environment(directory: Path, source: str) -> dict[str, str]:
    """Direciona temporários e caches de bibliotecas para o trabalho do job."""
    reject_links(directory)
    temporary, cache = directory / "tmp", directory / "cache"
    temporary.mkdir(exist_ok=True); cache.mkdir(exist_ok=True)
    reject_links(temporary); reject_links(cache)
    environment = {**os.environ, "PYTHONPATH": source, "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1",
                   "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "ONMYOJI_DOCUMENT_PARENT_PID": str(os.getpid())}
    for name in ("PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP"):
        environment.pop(name, None)
    for name in ("TMP", "TEMP", "TMPDIR"):
        environment[name] = str(temporary)
    environment.update(XDG_CACHE_HOME=str(cache), HF_HOME=str(cache / "huggingface"), TORCH_HOME=str(cache / "torch"),
                       PADDLE_HOME=str(cache / "paddle"), PADDLE_PDX_CACHE_HOME=str(cache / "paddle-pdx"),
                       MPLCONFIGDIR=str(cache / "matplotlib"), NUMBA_CACHE_DIR=str(cache / "numba"), PYTHONPYCACHEPREFIX=str(cache / "pycache"))
    return environment


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False, timeout=10)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.SubprocessError):
        if process.poll() is None:
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
            else:
                process.kill()


def _windows_job(process: subprocess.Popen):
    """Agrupa descendentes com encerramento automático ao morrer o MCP."""
    import ctypes
    from ctypes import wintypes
    class BasicLimits(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Limits(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise OSError("Não foi possível isolar o processo documental.")
    limits = Limits(); limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        kernel.CloseHandle(handle)
        raise OSError("Não foi possível isolar o processo documental.")
    return handle


def _close_windows_job(handle) -> None:
    if handle is not None:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)


class Jobs:
    """Mantém uma fila por perfil sem executar motores no servidor MCP.

    Estados e pedidos ficam no diretório de trabalho autorizado. Reinícios
    marcam pedidos incompletos como interrompidos; nenhuma escrita é repetida
    implicitamente. IDs e consultas são sempre limitados ao perfil selecionado.
    """

    def __new__(cls, root: Path, namespace: str):
        key = (str(Path(root).resolve()), namespace)
        with _instances_lock:
            if key not in _instances:
                instance = super().__new__(cls)
                instance._initialized = False
                instance._initialization_lock = threading.Lock()
                _instances[key] = instance
            return _instances[key]

    def __init__(self, root: Path, namespace: str):
        with self._initialization_lock:
            self._initialize(root, namespace)

    def _initialize(self, root: Path, namespace: str):
        if self._initialized:
            return
        require(namespace in {"pdf", "ocr"}, "invalid_namespace", "Módulo de documentos inválido.")
        self._initialized = True
        self.root = Path(root).resolve()
        self.namespace = namespace
        self._records: dict[str, dict] = {}
        self._directories: dict[str, Path] = {}
        self._profiles: dict[str, Path] = {}
        self._settings: dict[str, dict] = {}
        self._retention: dict[str, float] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._job_handles: dict[str, object] = {}
        self._passwords: dict[str, str] = {}
        self._password_key = os.urandom(32)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._last_cleanup = time.time()
        self._thread = threading.Thread(target=self._supervise, name="onmyoji-" + namespace + "-jobs", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    def register(self, profile_name: str, profile: dict, defaults: dict | None = None) -> None:
        require(isinstance(profile_name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", profile_name) is not None, "invalid_profile", "Perfil de documentos inválido.")
        with self._lock:
            work = self._authorized_work(profile)
            directory = work.resolve() / "document-jobs" / self.namespace / profile_name
            reject_links(directory)
            self._retention[profile_name] = (defaults or {}).get("retention_hours", 24) * 3600
            if profile_name in self._profiles:
                require(self._profiles[profile_name] == directory, "profile_busy", "Reinicie o MCP após alterar o diretório de trabalho do perfil.")
                self._settings[profile_name] = copy.deepcopy(profile)
                return
            directory.mkdir(parents=True, exist_ok=True)
            reject_links(directory)
            self._profiles[profile_name] = directory
            self._settings[profile_name] = copy.deepcopy(profile)
            retention = self._retention[profile_name]
            for candidate in directory.iterdir():
                if not candidate.is_dir() or candidate.is_symlink() or not re.fullmatch(r"[a-f0-9]{32}", candidate.name):
                    continue
                try:
                    reject_links(candidate / "state.json")
                    state = json.loads((candidate / "state.json").read_text(encoding="utf-8"))
                    if not self._valid_state(state, profile_name, candidate.name):
                        continue
                    if state.get("status") in {"queued", "running"}:
                        state.update(status="interrupted", finished_at=time.time(), error={"code": "interrupted", "message": "A execução foi interrompida; envie um novo request_id para tentar novamente."})
                        _write_json(candidate / "state.json", state)
                    if state.get("status") in TERMINAL and time.time() - state.get("finished_at", state.get("created_at", 0)) > retention:
                        reject_links(candidate)
                        shutil.rmtree(candidate)
                        continue
                    self._records[candidate.name] = state
                    self._directories[candidate.name] = candidate
                except (OSError, ValueError, TypeError):
                    continue

    def _authorized_work(self, profile: dict) -> Path:
        from .configuration import validate_profile_directories
        try:
            return validate_profile_directories(self.root, profile)
        except (ValueError, OSError) as error:
            raise DocumentError("path_not_allowed", "As permissões documentais devem ficar no workspace configurado.") from error

    def _safe_directory(self, identifier: str) -> Path:
        state = self._records[identifier]
        profile_name = state["profile"]
        work = self._authorized_work(self._settings[profile_name])
        expected = work / "document-jobs" / self.namespace / profile_name / identifier
        directory = self._directories[identifier]
        reject_links(directory)
        require(directory == expected and directory.is_dir(), "job_unavailable", "O diretório da execução não está disponível no workspace atual.")
        return directory

    def _job_file(self, identifier: str, name: str) -> Path:
        path = self._safe_directory(identifier) / name
        reject_links(path)
        return path

    def _persist(self, identifier: str, value: dict) -> None:
        try:
            _write_json(self._job_file(identifier, "state.json"), value)
        except (OSError, ValueError):
            # Um diretório removido ou movido não autoriza gravar em seu novo destino.
            pass

    def _valid_state(self, state: object, profile_name: str, identifier: str) -> bool:
        return (isinstance(state, dict) and state.get("profile") == profile_name and state.get("job_id") == identifier
                and state.get("namespace") == self.namespace and state.get("status") in TERMINAL | {"queued", "running"}
                and isinstance(state.get("operation"), str) and isinstance(state.get("request_hash"), str)
                and type(state.get("created_at")) in {int, float} and math.isfinite(state["created_at"])
                and type(state.get("timeout_seconds")) in {int, float} and math.isfinite(state["timeout_seconds"])
                and ("finished_at" not in state or type(state["finished_at"]) in {int, float} and math.isfinite(state["finished_at"])))

    def _input_fingerprints(self, profile: dict, args: dict, maximum: int) -> dict[str, str]:
        fingerprints = {}
        work = self._authorized_work(profile)
        def inspect(value, key=""):
            if isinstance(value, dict):
                for field, item in value.items():
                    inspect(item, field)
            elif isinstance(value, list):
                for item in value:
                    inspect(item, key)
            elif isinstance(value, str) and key in {"input_path", "input_paths", "path", "source", "source_path", "image_path", "insert_path", "overlay_path", "attachment_path", "left_path", "right_path", "other_path", "trust_roots"}:
                path = contained_path(value, profile.get("readable_roots", []), existing=True)
                require(not path.is_relative_to(work), "path_not_allowed", "Arquivos internos de trabalho não são documentos de entrada.")
                require(path.stat().st_size <= maximum, "input_too_large", "O arquivo excede o limite de entrada configurado.")
                fingerprints[str(path)] = file_digest(path)
        inspect(args)
        return fingerprints

    def submit(self, profile_name: str, profile: dict, defaults: dict, operation: str, args: dict, request_id: str) -> dict:
        require(isinstance(request_id, str) and 1 <= len(request_id) <= 200 and all(ord(char) >= 32 for char in request_id), "invalid_request_id", "Informe request_id com até 200 caracteres.")
        self.register(profile_name, profile, defaults)
        password = args.get("password")
        require(password is None or isinstance(password, str) and password, "invalid_password", "A senha direta do PDF deve ser um texto não vazio.")
        safe_args = {key: value for key, value in args.items() if key != "password"}
        fingerprints = self._input_fingerprints(profile, safe_args, defaults.get("max_input_bytes", 262144000))
        password_digest = hmac.new(self._password_key, password.encode("utf-8"), hashlib.sha256).hexdigest() if password else None
        payload = {"operation": operation, "arguments": safe_args, "password_digest": password_digest, "sources": fingerprints}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        with self._lock:
            require(not self._closed.is_set(), "service_closed", "O serviço documental está encerrando.")
            for state in self._records.values():
                if state["profile"] == profile_name and state.get("request_id") == request_id:
                    require(state["request_hash"] == digest, "request_conflict", "request_id já foi utilizado com argumentos ou arquivos diferentes.")
                    return self._public(state)
            pending = sum(state["profile"] == profile_name and state["status"] not in TERMINAL for state in self._records.values())
            require(pending < defaults.get("max_queue", 16), "queue_full", "A fila do perfil está cheia.")
            identifier = uuid4().hex
            directory = self._profiles[profile_name] / identifier
            reject_links(directory)
            directory.mkdir()
            reject_links(directory)
            state = {"job_id": identifier, "namespace": self.namespace, "profile": profile_name, "operation": operation, "status": "queued", "created_at": time.time(), "request_id": request_id, "request_hash": digest, "timeout_seconds": defaults.get("timeout_seconds", 1800)}
            request = {"root": str(self.root), "namespace": self.namespace, "profile_name": profile_name, "profile": profile, "defaults": defaults, "operation": operation, "arguments": safe_args, "direct_password": password is not None, "sources": fingerprints}
            _write_json(directory / "request.json", request)
            _write_json(directory / "state.json", state)
            self._records[identifier] = state
            self._directories[identifier] = directory
            if password is not None:
                self._passwords[identifier] = password
            return self._public(state)

    def _state(self, profile_name: str, identifier: str) -> dict:
        require(isinstance(identifier, str) and re.fullmatch(r"[a-f0-9]{32}", identifier) is not None, "job_not_found", "Execução não encontrada neste perfil.")
        state = self._records.get(identifier)
        require(state is not None and state["profile"] == profile_name, "job_not_found", "Execução não encontrada neste perfil.")
        self._safe_directory(identifier)
        return state

    def _public(self, state: dict) -> dict:
        result = {key: copy.deepcopy(value) for key, value in state.items() if key not in {"request_hash", "timeout_seconds"}}
        directory = self._directories.get(state["job_id"])
        if directory and state["status"] == "running":
            try:
                result["progress"] = json.loads(self._job_file(state["job_id"], "progress.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        return result

    def get(self, profile_name: str, job_id: str) -> dict:
        with self._lock:
            return self._public(self._state(profile_name, job_id))

    def result(self, profile_name: str, job_id: str, offset: int = 0, limit: int = 100) -> dict:
        require(type(offset) is int and offset >= 0 and type(limit) is int and 1 <= limit <= 1000, "invalid_pagination", "Use offset não negativo e limit entre 1 e 1000.")
        with self._lock:
            state = self._state(profile_name, job_id)
            require(state["status"] == "succeeded", "result_unavailable", "O resultado ainda não está disponível; consulte o estado da execução.")
            try:
                result = json.loads(self._job_file(job_id, "result.json").read_text(encoding="utf-8"))
                require(isinstance(result, dict), "result_unavailable", "O resultado da execução possui formato inválido.")
            except (OSError, ValueError) as error:
                raise DocumentError("result_unavailable", "O resultado da execução não está disponível.") from error
        # JSON extenso, inclusive objetos/tabelas aninhados, usa fragmentos
        # limitados. O resultado integral permanece disponível no workspace.
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized) > 32000:
            start = offset * 32000
            return {"job_id": job_id, "status": "succeeded", "offset": offset, "limit": 1,
                    "next_offset": offset + 1 if start + 32000 < len(serialized) else None,
                    "result_file": str(self._directories[job_id] / "result.json"),
                    "encoding": "json-fragments", "total_characters": len(serialized),
                    "result_fragment": serialized[start:start + 32000]}
        paginated = {}
        remaining = False
        for key, value in result.items():
            if isinstance(value, list):
                paginated[key] = value[offset:offset + limit]
                remaining |= len(value) > offset + limit
            else:
                paginated[key] = value
        return {"job_id": job_id, "status": "succeeded", "offset": offset, "limit": limit, "next_offset": offset + limit if remaining else None, "result": paginated}

    def cancel(self, profile_name: str, job_id: str) -> dict:
        with self._lock:
            state = self._state(profile_name, job_id)
            if state["status"] not in TERMINAL:
                state.update(status="cancelled", finished_at=time.time())
                process = self._processes.pop(job_id, None)
                if process:
                    _stop_process(process)
                _close_windows_job(self._job_handles.pop(job_id, None))
                self._rollback_completed(job_id)
                self._passwords.pop(job_id, None)
                self._persist(job_id, state)
            return self._public(state)

    def list(self, profile_name: str) -> dict:
        with self._lock:
            records = sorted((state for state in self._records.values() if state["profile"] == profile_name), key=lambda state: state["created_at"], reverse=True)
            for state in records:
                self._safe_directory(state["job_id"])
            return {"jobs": [self._public(state) for state in records[:100]], "total": len(records)}

    def _launch(self, identifier: str) -> subprocess.Popen:
        source = str(Path(__file__).resolve().parents[3])
        directory = self._safe_directory(identifier)
        reject_links(directory / "request.json")
        environment = execution_environment(directory, source)
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        password = self._passwords.get(identifier)
        process = subprocess.Popen([sys.executable, "-B", "-m", "onmyoji_daemon.mcp.document_processing.worker", str(directory / "request.json")], cwd=str(directory), stdin=subprocess.PIPE if password is not None else subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=environment, **options)
        if password is not None:
            try:
                require(process.stdin is not None, "worker_unavailable", "Não foi possível transmitir a senha direta ao processo documental.")
                process.stdin.write(password.encode("utf-8"))
                process.stdin.close()
                self._passwords.pop(identifier, None)
            except (OSError, ValueError, DocumentError):
                _stop_process(process)
                raise
        if os.name == "nt":
            try:
                self._job_handles[identifier] = _windows_job(process)
            except OSError:
                _stop_process(process)
                raise
        return process

    def _rollback_completed(self, identifier: str) -> None:
        try:
            completion = json.loads(self._job_file(identifier, "publication.json").read_text(encoding="utf-8"))
            profile = self._settings[self._records[identifier]["profile"]]
            for artifact in completion.get("artifacts", []):
                path = contained_path(artifact["path"], profile.get("writable_roots", []))
                from .configuration import workspace_for
                require(path.is_relative_to(workspace_for(self.root)), "path_not_allowed", "A publicação não pertence ao workspace atual.")
                identity = path.stat() if path.is_file() else None
                if identity is not None and identity.st_ino == artifact.get("inode") and identity.st_dev == artifact.get("device") and file_digest(path) == artifact.get("sha256"):
                    path.unlink()
                temporary = Path(artifact["temporary"]) if artifact.get("temporary") else None
                if temporary is not None:
                    require(temporary.parent == path.parent and re.fullmatch(r"\.onmyoji-[a-f0-9]{32}\.tmp", temporary.name) is not None, "path_not_allowed", "Arquivo transitório de publicação inválido.")
                    contained_path(str(temporary), profile.get("writable_roots", []))
                if temporary is not None and temporary.is_file():
                    temporary_identity = temporary.stat()
                    if temporary_identity.st_ino == artifact.get("inode") and temporary_identity.st_dev == artifact.get("device"):
                        temporary.unlink()
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def _supervise(self) -> None:
        while not self._closed.wait(0.05):
            with self._lock:
                if time.time() - self._last_cleanup > 60:
                    for identifier, state in list(self._records.items()):
                        elapsed = time.time() - state.get("finished_at", state["created_at"])
                        if state["status"] in TERMINAL and elapsed > self._retention.get(state["profile"], 86400):
                            try:
                                directory = self._safe_directory(identifier)
                                shutil.rmtree(directory)
                                del self._directories[identifier]
                                del self._records[identifier]
                            except (OSError, ValueError):
                                pass
                    self._last_cleanup = time.time()
                for identifier, process in list(self._processes.items()):
                    state = self._records[identifier]
                    try:
                        self._safe_directory(identifier)
                    except (OSError, ValueError):
                        _stop_process(process)
                        self._rollback_completed(identifier)
                        state.update(status="failed", finished_at=time.time(), error={"code": "workspace_changed", "message": "O workspace ou diretório de trabalho mudou durante a execução."})
                        del self._processes[identifier]
                        _close_windows_job(self._job_handles.pop(identifier, None))
                        continue
                    exit_code = process.poll()
                    if exit_code is None and time.time() - state["started_at"] <= state["timeout_seconds"]:
                        continue
                    timed_out = exit_code is None
                    if timed_out:
                        _stop_process(process)
                    try:
                        completion = json.loads(self._job_file(identifier, "completion.json").read_text(encoding="utf-8"))
                        result_exists = self._job_file(identifier, "result.json").is_file()
                    except (OSError, ValueError):
                        completion = {}
                        result_exists = False
                    if not timed_out and exit_code == 0 and completion.get("ok") is True and result_exists:
                        state.update(status="succeeded", artifacts=completion.get("artifacts", []))
                    else:
                        self._rollback_completed(identifier)
                        state.update(status="failed", error={"code": "timeout" if timed_out else completion.get("error", {}).get("code", "worker_failed"), "message": "A execução excedeu o prazo configurado." if timed_out else completion.get("error", {}).get("message", "O processo documental não concluiu a operação.")})
                    state["finished_at"] = time.time()
                    self._persist(identifier, state)
                    self._passwords.pop(identifier, None)
                    del self._processes[identifier]
                    _close_windows_job(self._job_handles.pop(identifier, None))
                busy = {self._records[identifier]["profile"] for identifier in self._processes}
                for state in sorted(self._records.values(), key=lambda item: item["created_at"]):
                    if state["status"] != "queued" or state["profile"] in busy:
                        continue
                    identifier = state["job_id"]
                    try:
                        self._processes[identifier] = self._launch(identifier)
                        state.update(status="running", started_at=time.time())
                        busy.add(state["profile"])
                    except (OSError, ValueError):
                        state.update(status="failed", finished_at=time.time(), error={"code": "worker_unavailable", "message": "Não foi possível iniciar o processo documental."})
                        self._passwords.pop(identifier, None)
                    self._persist(identifier, state)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        with self._lock:
            for identifier, process in self._processes.items():
                _stop_process(process)
                self._rollback_completed(identifier)
            for identifier, state in self._records.items():
                if state["status"] not in TERMINAL:
                    state.update(status="interrupted", finished_at=time.time(), error={"code": "interrupted", "message": "O servidor MCP foi encerrado durante a execução."})
                    self._persist(identifier, state)
            self._processes.clear()
            self._passwords.clear()
            for handle in self._job_handles.values():
                _close_windows_job(handle)
            self._job_handles.clear()
        with _instances_lock:
            _instances.pop((str(self.root), self.namespace), None)
