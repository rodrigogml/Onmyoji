"""Jobs Excel com reserva entre servidores e recuperação de processos nativos."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from ..document_processing.jobs import Jobs, _windows_job, _write_json, execution_environment
from ..document_processing.paths import contained_path, file_digest, reject_links
from .component.process import FileLease, attach_verified_process, is_process_descendant, private_memory_bytes, process_identity, same_process, terminate_owned, visible_windows
from .exception import ExcelExceptions, require


class ExcelJobs(Jobs):
    """Especializa a fila documental sem executar COM no servidor MCP.

    Reservas de perfil impedem reconciliar o job ativo de outro servidor.
    Processos Excel são recuperados por identidade, mesmo quando a ativação
    COM não os fez descendentes do worker Python.
    """

    allowed_namespaces = frozenset({"excel"})
    worker_module = "onmyoji_daemon.mcp.excel.worker"
    timeout_code = "deadline_exceeded"

    def __new__(cls, root: Path):
        return super().__new__(cls, root, "excel")

    def __init__(self, root: Path):
        super().__init__(root, "excel")

    def _initialize(self, root: Path, namespace: str):
        if not self._initialized:
            self._profile_leases: dict[str, FileLease] = {}
            self._memory_limits: dict[str, int] = {}
        super()._initialize(root, namespace)

    def register(self, profile_name: str, profile: dict, defaults: dict | None = None) -> None:
        import re
        require(isinstance(profile_name, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", profile_name) is not None,
                "invalid_profile", "Perfil Excel inválido.")
        with self._lock:
            self._memory_limits[profile_name] = (defaults or {}).get("max_memory_bytes", 2147483648)
            if profile_name not in self._profile_leases:
                # Validate the name before using it as a directory component.
                directory = self._authorized_work(profile) / "document-jobs" / "excel" / profile_name
                lease = FileLease(directory / "supervisor.lock")
                lease.acquire()
                self._profile_leases[profile_name] = lease
                try:
                    super().register(profile_name, profile, defaults)
                    for identifier, state in self._records.items():
                        if state["profile"] == profile_name and state["status"] == "interrupted":
                            self._recover_excel(identifier)
                            self._rollback_completed(identifier)
                except Exception:
                    self._profile_leases.pop(profile_name).close()
                    raise
            else:
                super().register(profile_name, profile, defaults)

    def submit(self, profile_name, profile, defaults, operation, args, request_id):
        # Policies participate in idempotency; a revoked capability must not
        # silently reuse a result produced under a more permissive profile.
        import hashlib
        policies = {key: value for key, value in profile.items() if key.startswith("allow_") or key == "trusted_content"}
        revision = hashlib.sha256(json.dumps(policies, sort_keys=True).encode()).hexdigest()
        arguments = {**args, "_policy_revision": revision}
        return super().submit(profile_name, profile, defaults, operation, arguments, request_id)

    def _input_fingerprints(self, profile: dict, args: dict, maximum: int) -> dict[str, str]:
        # VBA source is text, unlike document operations whose "source" can
        # designate a file. Only declared Excel input paths are fingerprinted.
        work = self._authorized_work(profile)
        fingerprints = {}
        def input_paths(value):
            if isinstance(value, dict):
                for field, item in value.items():
                    if field in {"input_path", "other_path", "input_module_path", "template_path"}:
                        yield field, item
                    elif isinstance(item, (dict, list)):
                        yield from input_paths(item)
            elif isinstance(value, list):
                for item in value:
                    yield from input_paths(item)
        for field, value in input_paths(args):
            path = contained_path(value, profile["readable_roots"], existing=True)
            require(not path.is_relative_to(work), "path_not_allowed", "Arquivos internos de trabalho não são documentos de entrada.")
            require(path.stat().st_size <= maximum, "input_too_large", "O arquivo excede o limite de entrada configurado.")
            fingerprints[str(path)] = file_digest(path)
            if field == "input_module_path" and path.suffix.lower() == ".frm":
                import locale
                import re
                resources = {path.with_suffix(".frx")} if path.with_suffix(".frx").exists() else set()
                module_bytes = path.read_bytes()
                for encoded in re.findall(rb'"([^"\r\n]+\.frx)"', module_bytes, flags=re.I):
                    try:
                        name = encoded.decode("utf-8" if module_bytes.startswith(b"\xef\xbb\xbf") else locale.getencoding())
                    except UnicodeError as error:
                        raise ExcelExceptions("invalid_module_resource", "O nome do recurso FRX não usa o encoding esperado pelo VBIDE.") from error
                    require(not any(character in name for character in ("/", "\\", ":")) and name not in {".", ".."},
                            "path_not_allowed", "Recursos de UserForm devem ser arquivos irmãos do módulo.")
                    resources.add(path.parent / name)
                for resource in resources:
                    resource = contained_path(str(resource), profile["readable_roots"], existing=True)
                    require(not resource.is_relative_to(work) and resource.stat().st_size <= maximum,
                            "path_not_allowed", "O recurso do UserForm não pertence às fontes autorizadas.")
                    fingerprints[str(resource)] = file_digest(resource)
        return fingerprints

    def _launch(self, identifier: str) -> subprocess.Popen:
        directory = self._safe_directory(identifier)
        request = directory / "request.json"
        reject_links(request)
        payload = json.loads(request.read_text(encoding="utf-8"))
        payload["parent_identity"] = process_identity(os.getpid())
        _write_json(request, payload)
        environment = execution_environment(directory, str(Path(__file__).resolve().parents[3]))
        # Use the same configured interpreter and its normal site packages.
        # The working directory never enters sys.path (PYTHONSAFEPATH=1).
        environment.pop("PYTHONNOUSERSITE", None)
        environment["ONMYOJI_EXCEL_PARENT_PID"] = str(os.getpid())
        options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen([sys.executable, "-B", "-m", self.worker_module, str(request)],
                                   cwd=str(directory), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, env=environment, **options)
        try:
            self._records[identifier]["worker_pid"] = process.pid
            self._records[identifier]["worker_identity"] = process_identity(process.pid)
            self._records[identifier]["launcher_identity"] = self._records[identifier]["worker_identity"]
            if os.name == "nt":
                self._job_handles[identifier] = _windows_job(process)
            _write_json(directory / "activation.json", {"launcher_identity": self._records[identifier]["launcher_identity"],
                                                        "parent_identity": payload["parent_identity"]})
        except Exception:
            self._stop_worker(identifier, process)
            raise
        return process

    def _ownership(self, identifier: str) -> dict | None:
        path = self._job_file(identifier, "ownership.json")
        try:
            if not path.exists():
                return None
            require(path.stat().st_size <= 4096, "process_ownership_unverified", "Registro de propriedade Excel inválido.")
            owner = json.loads(path.read_text(encoding="utf-8"))
            require(isinstance(owner, dict) and owner.get("worker_pid") == self._records[identifier].get("worker_pid"),
                    "process_ownership_unverified", "O processo Excel não corresponde ao worker registrado.")
            return owner
        except (OSError, ValueError, TypeError) as error:
            if isinstance(error, ExcelExceptions):
                raise
            raise ExcelExceptions("process_ownership_unverified", "Não foi possível verificar o registro de propriedade Excel.") from error

    def _public(self, state: dict) -> dict:
        result = super()._public(state)
        result.pop("worker_identity", None)
        result.pop("worker_pid", None)
        result.pop("launcher_identity", None)
        result.pop("worker_accepted", None)
        return result

    def _accept_worker(self, identifier: str) -> None:
        state = self._records[identifier]
        if state.get("worker_accepted"):
            return
        path = self._job_file(identifier, "worker-ready.json")
        if not path.exists():
            return
        require(path.stat().st_size <= 8192, "process_ownership_unverified", "A identificação do worker real é inválida.")
        ready = json.loads(path.read_text(encoding="utf-8"))
        actual = ready.get("worker_identity")
        launcher = state.get("launcher_identity")
        require(isinstance(actual, dict) and same_process(launcher, ready.get("launcher_identity"))
                and is_process_descendant(actual, launcher),
                "process_ownership_unverified", "O worker real não pertence ao processo Python iniciado pelo MCP.")
        expected_executables = {os.path.normcase(str(Path(value).resolve())) for value in (sys.executable, getattr(sys, "_base_executable", sys.executable))}
        require(os.path.normcase(str(Path(actual["executable"]).resolve())) in expected_executables,
                "process_ownership_unverified", "O worker real não usa o interpretador Python configurado.")
        if os.name == "nt":
            attach_verified_process(self._job_handles[identifier], actual)
        state.update(worker_pid=actual["pid"], worker_identity=actual, worker_accepted=True)
        self._persist(identifier, state)
        _write_json(self._job_file(identifier, "worker-accepted.json"), {"worker_identity": actual})

    def _recover_excel(self, identifier: str) -> None:
        owner = self._ownership(identifier)
        if owner is not None:
            terminate_owned(owner)

    def _worker_failure(self, identifier: str, process: subprocess.Popen) -> dict | None:
        try:
            state = self._records[identifier]
            if process.poll() is None and time.time() - state["started_at"] > state["timeout_seconds"]:
                return {"code": "deadline_exceeded", "message": "O job Excel excedeu o prazo configurado."}
            self._accept_worker(identifier)
            owner = self._ownership(identifier)
            memory = private_memory_bytes(state.get("worker_pid", process.pid)) if process.poll() is None else 0
            if owner is not None:
                actual = process_identity(owner["pid"])
                if same_process(owner, actual):
                    memory += private_memory_bytes(owner["pid"])
                    if visible_windows(owner["pid"]):
                        return {"code": "ui_required", "message": "A instância Excel solicitou interface visível; a operação foi interrompida."}
                    if process.poll() is not None:
                        terminate_owned(owner)
            profile = self._records[identifier]["profile"]
            if memory > self._memory_limits.get(profile, 2147483648):
                return {"code": "memory_limit_exceeded", "message": "O job Excel excedeu o orçamento de memória configurado."}
            return None
        except ExcelExceptions as error:
            return error.as_dict()
        except (OSError, ValueError, KeyError, TypeError):
            return {"code": "process_ownership_unverified", "message": "Não foi possível comprovar a propriedade da instância Excel."}

    def _stop_worker(self, identifier: str, process: subprocess.Popen) -> None:
        try:
            self._recover_excel(identifier)
        except (OSError, ValueError, KeyError, TypeError):
            # Do not kill a process with unverified ownership. Closing the
            # worker's job handles still contains confirmed child processes.
            pass
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

    def close(self) -> None:
        try:
            super().close()
        finally:
            for lease in self._profile_leases.values():
                lease.close()
            self._profile_leases.clear()
