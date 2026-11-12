from __future__ import annotations

from dataclasses import dataclass, field
import json
import hashlib
from pathlib import Path
import secrets
import socket
import subprocess
import os
import re
import threading
import time
import uuid
from typing import Any

from .registry import SERVICES, ServiceSpec
from .rpc import RpcError, RpcServer, call, disconnected
from .supervision.component.process_group import ServiceProcessGroup
from .supervision.publication import PublicationCoordinator
from .state_layout import daemon_runtime_path


def free_port() -> int:
    with socket.socket() as value:
        value.bind(("127.0.0.1", 0)); return int(value.getsockname()[1])


@dataclass
class ManagedService:
    spec: ServiceSpec
    enabled: bool = False
    process: subprocess.Popen[str] | None = None
    port: int = 0
    token: str = ""
    state: str = "stopped"
    last_error: str | None = None
    output_tail: str = ""
    log_path: Path | None = None
    generation: str = ""
    lifecycle_lock: Any = field(default_factory=threading.RLock, repr=False)
    outbound_lock: Any = field(default_factory=threading.Lock, repr=False)
    process_group: ServiceProcessGroup | None = None
    intentional_stop: bool = False


class Supervisor:
    def __init__(self, onmyoji_root: Path):
        self.onmyoji_root = onmyoji_root.resolve()
        self.root = self.onmyoji_root / "configs" / "daemon"; self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = daemon_runtime_path(self.onmyoji_root); self.runtime.mkdir(parents=True, exist_ok=True)
        self.endpoint_file = self.runtime / "endpoint.json"
        self.process_file = self.runtime / "process.json"
        self.lock_file = self.runtime / "supervisor.lock"
        self.state_file = self.root / "services.json"
        self.log_root = self.runtime / "logs"; self.log_root.mkdir(parents=True, exist_ok=True)
        self.host, self.token, self.stop_event = "127.0.0.1", secrets.token_urlsafe(32), threading.Event()
        from .agent_notification.configuration import NotificationConfiguration
        limits = NotificationConfiguration(self.onmyoji_root).load()
        self.rpc = RpcServer(self.host, free_port(), self.token, self.handle, long_methods=("notification.submit",), long_limit=limits["long_limit"], event_max_bytes=limits["max_request_bytes"], on_rejection=self._notification_transport_rejected)
        self.services = {name: ManagedService(spec) for name, spec in SERVICES.items()}
        self.publications = PublicationCoordinator(self.onmyoji_root, self.services, self._call_service, {self.rpc.port})
        self.recovery_delay_seconds = 15.0
        self.next_recovery_at: dict[str, float] = {}
        self._load_state()

    def _acquire_lock(self) -> None:
        """Garante um único supervisor por CODEX_HOME, removendo somente lock órfão."""
        try: revision = hashlib.sha256((self.root / "daemon.toml").read_bytes()).hexdigest()
        except OSError: revision = ""
        payload = {"pid": os.getpid(), "root": str(self.onmyoji_root), "instance": uuid.uuid4().hex, "started_at": time.time(), "configuration_revision": revision}
        try:
            descriptor = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try: previous = json.loads(self.lock_file.read_text(encoding="utf-8")); pid = int(previous.get("pid", 0))
            except (OSError, ValueError, TypeError): pid = 0
            alive = False
            if pid > 0:
                if os.name == "nt":
                    probe = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True, capture_output=True, check=False)
                    alive = str(pid) in probe.stdout and "No tasks" not in probe.stdout
                else:
                    try: os.kill(pid, 0); alive = True
                    except OSError: pass
            if alive: raise RuntimeError("another daemon supervisor is already running for this instance")
            self.lock_file.unlink(missing_ok=True)
            descriptor = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output: json.dump(payload, output)
        self.process_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _load_state(self) -> None:
        try: values = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError): return
        for name, service in self.services.items(): service.enabled = bool(values.get(name, {}).get("enabled", False))

    def _save_state(self) -> None:
        self.state_file.write_text(json.dumps({name: {"enabled": item.enabled} for name, item in self.services.items()}, indent=2), encoding="utf-8")

    def _endpoint(self) -> None:
        self.endpoint_file.write_text(json.dumps({"host": self.host, "port": self.rpc.port, "token": self.token}), encoding="utf-8")

    def status(self, name: str) -> dict[str, Any]:
        service = self.services[name]
        if service.process and service.process.poll() is not None:
            if service.intentional_stop:
                service.state = "stopped"
            elif service.process.returncode:
                service.state = "failed"
                if not service.last_error: service.last_error = service.output_tail or f"process exited with {service.process.returncode}"
            else: service.state = "stopped"
        return {"name": name, "enabled": service.enabled, "state": service.state, "pid": service.process.pid if service.process and service.process.poll() is None else None, "last_error": service.last_error, "log_path": str(service.log_path) if service.log_path else None}

    @staticmethod
    def _safe_output(line: str) -> str:
        return re.sub(r"(?i)(token|password|secret)\s*[=:]\s*\S+", r"\1=[redacted]", line).strip()[-800:]

    def _capture_output(self, service: ManagedService) -> None:
        if not service.process or not service.process.stdout or not service.log_path: return
        try:
            with service.log_path.open("a", encoding="utf-8") as output:
                for raw in service.process.stdout:
                    line = self._safe_output(raw)
                    if not line: continue
                    output.write(line + "\n"); output.flush()
                    service.output_tail = line
                    if service.process.poll() is not None and service.process.returncode:
                        service.last_error = line
        except OSError:
            pass

    def start(self, name: str) -> dict[str, Any]:
        with self.services[name].lifecycle_lock:
            return self._start(name)

    def _start(self, name: str) -> dict[str, Any]:
        service = self.services[name]
        if service.process and service.process.poll() is None: return self.status(name)
        service.intentional_stop = False
        if service.process_group:
            try:
                service.process_group.close()
                service.process_group = None
            except (OSError, subprocess.SubprocessError) as error:
                service.state, service.last_error = "failed", "A geração anterior ainda não foi encerrada."
                return self.status(name)
        with self.publications.lock:
            self.publications.invalidate(name, service.generation)
            service.generation, service.state = uuid.uuid4().hex, "starting"
        service.port, service.token = free_port(), secrets.token_urlsafe(32)
        data_dir = self.root / "services" / name; data_dir.mkdir(parents=True, exist_ok=True)
        command = service.spec.command(data_dir) + ["--host", self.host, "--port", str(service.port), "--token=" + service.token, "--onmyoji-root", str(self.onmyoji_root)]
        try:
            environment = dict(os.environ)
            source_root = str(Path(__file__).resolve().parents[1])
            environment["PYTHONPATH"] = source_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["ONMYOJI_SUPERVISOR_PID"] = str(os.getpid())
            temporary_root = self.onmyoji_root / "tmp" / "daemon" / name
            temporary_root.mkdir(parents=True, exist_ok=True)
            for variable in ("TMP", "TEMP", "TMPDIR"):
                environment[variable] = str(temporary_root)
            service.log_path = self.log_root / f"{name}.log"
            service.process = subprocess.Popen(command, cwd=str(self.onmyoji_root), text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment,
                                               start_new_session=service.spec.contain_process_tree and os.name != "nt",
                                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if service.spec.contain_process_tree:
                service.process_group = ServiceProcessGroup(service.process)
            threading.Thread(target=self._capture_output, args=(service,), daemon=True).start()
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if service.process.poll() is not None:
                    detail = service.output_tail or f"process exited with {service.process.returncode}"
                    raise RuntimeError(detail)
                try:
                    call(self.host, service.port, service.token, "ping", timeout=0.4)
                    if name == "task-scheduler":
                        call(self.host, service.port, service.token, "task.activate", timeout=5)
                    with self.publications.lock:
                        service.state, service.last_error = "running", None
                    return self.status(name)
                except OSError: time.sleep(0.05)
            raise TimeoutError("service did not become ready")
        except Exception as error:
            if service.process_group:
                try:
                    service.process_group.close()
                    service.process_group = None
                except (OSError, subprocess.SubprocessError):
                    pass
            if service.process and service.process.poll() is None:
                try:
                    service.process.terminate(); service.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try: service.process.kill(); service.process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired): pass
            service.state, service.last_error = "failed", str(error); return self.status(name)

    def _recover_enabled_services_once(self) -> None:
        """Agenda nova inicialização após falha transitória de serviço habilitado."""
        now = time.monotonic()
        for name, service in self.services.items():
            state = self.status(name)
            if not service.enabled or state["state"] != "failed" or now < self.next_recovery_at.get(name, 0):
                continue
            self.next_recovery_at[name] = now + self.recovery_delay_seconds
            self.start(name)

    def _recover_enabled_services(self) -> None:
        while not self.stop_event.wait(1):
            self._recover_enabled_services_once()

    def _reconcile_publications(self) -> None:
        while not self.stop_event.wait(1):
            try:
                self.publications.reconcile()
            except (OSError, RuntimeError, ValueError):
                # Uma integração indisponível não encerra a supervisão local.
                continue

    def _call_service(self, name: str, method: str, params: dict[str, Any], timeout: float = 5, skip_busy: bool = False) -> Any:
        """Serializa chamadas por serviço e permite pular RPCs de negócio ocupados."""
        service = self.services[name]
        acquired = service.outbound_lock.acquire(blocking=not skip_busy)
        if not acquired:
            return None
        try:
            if not service.process or service.process.poll() is not None:
                raise RpcError(f"{name} is not running", "service_unavailable")
            return call(self.host, service.port, service.token, method, params, timeout=timeout)
        finally:
            service.outbound_lock.release()

    def stop(self, name: str) -> dict[str, Any]:
        with self.services[name].lifecycle_lock:
            return self._stop(name)

    def _stop(self, name: str) -> dict[str, Any]:
        service = self.services[name]
        with self.publications.lock:
            service.intentional_stop = True
            service.state = "stopping"
            self.publications.invalidate(name, service.generation)
        if service.process and service.process.poll() is None:
            try:
                grace = 5
                if name == "telegram":
                    import tomllib
                    try:
                        configuration = tomllib.loads((self.root / "services" / "telegram" / "config.toml").read_text(encoding="utf-8"))
                        grace = float(configuration.get("agent", {}).get("interrupt_grace_seconds", 15)) + 5
                    except (OSError, ValueError):
                        grace = 20
                self._call_service(name, "shutdown", {}, timeout=grace, skip_busy=True)
                service.process.wait(timeout=grace)
            except Exception:
                service.process.terminate()
                try: service.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    service.process.kill(); service.process.wait(timeout=5)
        if service.process_group:
            service.process_group.close()
            service.process_group = None
        service.state = "stopped"; return self.status(name)

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping": return {"service": "onmyoji-daemon", "state": "running"}
        if method == "list-services": return [self.status(name) for name in self.services]
        if method.startswith("gateway.publication.") or method in {"notification.transport-rejected", "task.activate", "cloudflare-tunnel.activate", "mini-apps.publication.update"}:
            raise RpcError("Operação interna reservada ao Supervisor.", "internal_operation")
        if method in {"start", "stop", "restart", "enable", "disable", "status"}:
            name = str(params.get("service") or "telegram")
            if name not in self.services: raise ValueError("unknown registered service")
            if method == "start": return self.start(name)
            if method == "stop": return self.stop(name)
            if method == "restart": self.stop(name); return self.start(name)
            if method == "enable": self.services[name].enabled = True; self._save_state(); return self.status(name)
            if method == "disable": self.services[name].enabled = False; self.stop(name); self._save_state(); return self.status(name)
            return self.status(name)
        for name, service in self.services.items():
            if method.startswith(service.spec.namespaces):
                if not service.process or service.process.poll() is not None: raise RuntimeError(f"{name} is not running")
                if method == "notification.submit":
                    with service.lifecycle_lock:
                        address = (self.host, service.port, service.token)
                    return call(*address, method, params, timeout=None, abandon=disconnected)
                if method.startswith("mini-apps.oauth."):
                    timeout = 200
                elif method == "mini-apps.file-review.create":
                    timeout = 30
                elif method in {"telegram.outbound.send", "telegram.outbound.send_location", "telegram.reply.send_location"}:
                    timeout = 120
                elif method == "telegram.sync-commands":
                    timeout = 60
                else:
                    timeout = 5
                return self._call_service(name, method, params, timeout=timeout)
        if method == "shutdown": self.stop_event.set(); return {"state": "stopping"}
        raise ValueError("unknown method")

    def _notification_transport_rejected(self, code):
        service = self.services.get("telegram")
        if service and service.process and service.process.poll() is None:
            try:
                self._call_service("telegram", "notification.transport-rejected", {"code": code}, timeout=1, skip_busy=True)
            except (OSError, RpcError):
                pass

    def run_forever(self) -> None:
        self._acquire_lock(); self._endpoint(); self._save_state()
        for name, service in self.services.items():
            if service.enabled: self.start(name)
        threading.Thread(target=self._recover_enabled_services, daemon=True).start()
        publication_thread = threading.Thread(target=self._reconcile_publications, name="onmyoji-publications", daemon=True)
        publication_thread.start()
        try: self.rpc.serve_forever(self.stop_event)
        finally:
            self.stop_event.set()
            publication_thread.join(timeout=5)
            for name in self.services: self.stop(name)
            self.endpoint_file.unlink(missing_ok=True); self.process_file.unlink(missing_ok=True); self.lock_file.unlink(missing_ok=True); self.rpc.close()


def endpoint(onmyoji_root: Path) -> tuple[str, int, str]:
    values = json.loads((daemon_runtime_path(onmyoji_root) / "endpoint.json").read_text(encoding="utf-8"))
    return str(values["host"]), int(values["port"]), str(values["token"])
