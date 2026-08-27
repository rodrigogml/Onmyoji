from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import socket
import subprocess
import os
import threading
import time
from typing import Any

from .registry import SERVICES, ServiceSpec
from .rpc import RpcServer, call


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


class Supervisor:
    def __init__(self, onmyoji_root: Path):
        self.onmyoji_root = onmyoji_root.resolve()
        self.root = self.onmyoji_root / "configs" / "daemon"
        self.runtime = self.root / "runtime"; self.runtime.mkdir(parents=True, exist_ok=True)
        self.endpoint_file = self.runtime / "endpoint.json"
        self.state_file = self.root / "services.json"
        self.host, self.token, self.stop_event = "127.0.0.1", secrets.token_urlsafe(32), threading.Event()
        self.rpc = RpcServer(self.host, free_port(), self.token, self.handle)
        self.services = {name: ManagedService(spec) for name, spec in SERVICES.items()}
        self._load_state()

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
        if service.process and service.process.poll() is not None: service.state = "failed" if service.last_error else "stopped"
        return {"name": name, "enabled": service.enabled, "state": service.state, "pid": service.process.pid if service.process and service.process.poll() is None else None, "last_error": service.last_error}

    def start(self, name: str) -> dict[str, Any]:
        service = self.services[name]
        if service.process and service.process.poll() is None: return self.status(name)
        service.port, service.token = free_port(), secrets.token_urlsafe(32)
        data_dir = self.root / "services" / name; data_dir.mkdir(parents=True, exist_ok=True)
        command = service.spec.command(data_dir) + ["--host", self.host, "--port", str(service.port), "--token", service.token, "--onmyoji-root", str(self.onmyoji_root)]
        try:
            environment = dict(os.environ)
            source_root = str(Path(__file__).resolve().parents[1])
            environment["PYTHONPATH"] = source_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
            service.process = subprocess.Popen(command, cwd=str(self.onmyoji_root), text=True, env=environment)
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if service.process.poll() is not None: raise RuntimeError(f"process exited with {service.process.returncode}")
                try:
                    call(self.host, service.port, service.token, "ping", timeout=0.4)
                    service.state, service.last_error = "running", None; return self.status(name)
                except OSError: time.sleep(0.05)
            raise TimeoutError("service did not become ready")
        except Exception as error:
            service.state, service.last_error = "failed", str(error); return self.status(name)

    def stop(self, name: str) -> dict[str, Any]:
        service = self.services[name]
        if service.process and service.process.poll() is None:
            try: call(self.host, service.port, service.token, "shutdown"); service.process.wait(timeout=5)
            except Exception:
                service.process.terminate(); service.process.wait(timeout=5)
        service.state = "stopped"; return self.status(name)

    def handle(self, method: str, params: dict[str, Any]) -> Any:
        if method == "ping": return {"service": "onmyoji-daemon", "state": "running"}
        if method == "list-services": return [self.status(name) for name in self.services]
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
            if method.startswith(service.spec.namespace):
                if not service.process or service.process.poll() is not None: raise RuntimeError(f"{name} is not running")
                return call(self.host, service.port, service.token, method, params)
        if method == "shutdown": self.stop_event.set(); return {"state": "stopping"}
        raise ValueError("unknown method")

    def run_forever(self) -> None:
        self._endpoint(); self._save_state()
        for name, service in self.services.items():
            if service.enabled: self.start(name)
        try: self.rpc.serve_forever(self.stop_event)
        finally:
            for name in self.services: self.stop(name)
            self.endpoint_file.unlink(missing_ok=True); self.rpc.close()


def endpoint(onmyoji_root: Path) -> tuple[str, int, str]:
    values = json.loads((onmyoji_root / "configs" / "daemon" / "runtime" / "endpoint.json").read_text(encoding="utf-8"))
    return str(values["host"]), int(values["port"]), str(values["token"])
