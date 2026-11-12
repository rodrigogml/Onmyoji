"""Monitor BTG de leitura, isolado por perfil e sem criação/reenvio de pagamentos."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any
from .rpc import RpcServer

class Monitor:
    def __init__(self, root: Path):
        self.root = root
        self.stopped = threading.Event()
        self.health: dict[str, Any] = {"profiles": {}, "lastCycleAt": None}
        self.lock = threading.Lock()

    def cycle(self) -> None:
        with self.lock:
            self.health["profiles"] = {}
            self.health.pop("configError", None)
        self._mcp_cycle()
        with self.lock:
            self.health["lastCycleAt"] = dt.datetime.now(dt.timezone.utc).isoformat()

    def _mcp_cycle(self) -> None:
        from .mcp.configuration import McpConfiguration, McpConfigurationError
        from .mcp.runtime import mcp_runtime_python
        config = McpConfiguration(self.root)
        try:
            profiles = config.btg()["profiles"]
            available = {profile["name"] for profile in config.enabled_btg_profiles()}
        except (OSError, ValueError, McpConfigurationError):
            with self.lock:
                self.health["configError"] = "invalid_config"
            return
        for name in sorted(available):
            if self.stopped.is_set():
                break
            profile = profiles[name]
            if profile.get("monitor_enabled") is not True:
                continue
            try:
                environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent), "PYTHONUTF8": "1"}
                command = [str(mcp_runtime_python(self.root)), "-m", "onmyoji_daemon.mcp.btg.monitor", "--onmyoji-root", str(self.root), "--profile", name]
                process = subprocess.run(command, text=True, encoding="utf-8", capture_output=True, timeout=180, env=environment)
                payload = json.loads(process.stdout)
                valid = process.returncode == 0 and payload.get("ok") is True
                checks = payload.get("data", {}).get("checked", []) if valid else []
                status = {"ok": valid and all(not item.get("lastError") for item in checks), "checkedCount": len(checks), "pendingErrors": sum(bool(item.get("lastError")) for item in checks), "observedAt": dt.datetime.now(dt.timezone.utc).isoformat(), "errorCode": None if valid else payload.get("error", {}).get("code", "worker_failed")}
            except (OSError, ValueError, subprocess.TimeoutExpired, AttributeError, TypeError):
                status = {"ok": False, "errorCode": "monitor_cycle_failed"}
            with self.lock:
                self.health["profiles"]["mcp:" + name] = status

    def run(self) -> None:
        while not self.stopped.is_set():
            self.cycle()
            self.stopped.wait(30)

    def rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "shutdown":
            self.stopped.set()
            return {"state": "stopping"}
        if method == "ping":
            return {"service": "btg-payments-monitor", "state": "running"}
        if method == "btg-monitor.status":
            with self.lock:
                return json.loads(json.dumps(self.health))
        raise ValueError("unknown monitor method")

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    monitor = Monitor(args.onmyoji_root)
    worker = threading.Thread(target=monitor.run, daemon=True)
    worker.start()
    try:
        RpcServer(args.host, args.port, args.token, monitor.rpc).serve_forever(monitor.stopped)
    finally:
        monitor.stopped.set()
        worker.join(5)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
