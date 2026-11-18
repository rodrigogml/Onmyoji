"""Mantém o RPC Telegram disponível quando o ingresso HTTP encontra uma porta ocupada."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

from onmyoji_daemon.agent_notification.configuration import NotificationConfiguration
from onmyoji_daemon.rpc import RpcError, call


def test_telegram_process_keeps_control_available_when_event_http_port_is_occupied(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    workspace.mkdir()
    configuration = root / "configs"
    configuration.mkdir(parents=True)
    (configuration / "onmyoji-system.toml").write_text(
        '[codex]\nexecutable="unused-codex"\nproject_directory=' + json.dumps(workspace.as_posix()) + '\n',
        encoding="utf-8",
    )
    daemon = configuration / "daemon"
    daemon.mkdir()
    (daemon / "daemon.toml").write_text('[app_server]\nenabled=false\n', encoding="utf-8")
    data = daemon / "services" / "telegram"
    temporary = tmp_path / "process-temp"
    temporary.mkdir()
    environment = dict(os.environ)
    source = str(Path(__file__).resolve().parents[1] / "src")
    environment.update(CODEX_HOME=str(root), PYTHONDONTWRITEBYTECODE="1", PYTHONPATH=source + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else ""))
    for name in ("TEMP", "TMP", "TMPDIR"):
        environment[name] = str(temporary)

    with socket.socket() as occupied, socket.socket() as reservation:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            occupied.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        occupied.bind(("127.0.0.1", 0))
        occupied.listen(1)
        occupied_port = occupied.getsockname()[1]
        NotificationConfiguration(root).configure({"enabled": False, "telegram_receiver_enabled": False, "port": occupied_port})
        reservation.bind(("127.0.0.1", 0))
        rpc_port = reservation.getsockname()[1]
        reservation.close()
        token = "integration-fixture"
        arguments = [sys.executable, "-B", "-m", "onmyoji_daemon.telegram", "--data-dir", str(data), "--onmyoji-root", str(root), "--host", "127.0.0.1", "--port", str(rpc_port), "--token", token]
        log_path = tmp_path / "telegram-process.log"
        with log_path.open("w", encoding="utf-8") as output:
            process = subprocess.Popen(arguments, cwd=root, env=environment, stdout=output, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 10
            while True:
                assert process.poll() is None, log_path.read_text(encoding="utf-8")[-1200:]
                try:
                    ping = call("127.0.0.1", rpc_port, token, "ping", timeout=0.3)
                    break
                except (OSError, RpcError):
                    assert time.monotonic() < deadline, "O processo Telegram não disponibilizou seu controle RPC."
                    time.sleep(0.05)
            assert ping["service"] == "telegram"
            events = call("127.0.0.1", rpc_port, token, "notification.status")
            assert events["http"]["state"] == "failed"
            assert events["http"]["host"] == "127.0.0.1"
            assert events["http"]["port"] == occupied_port
            assert str(occupied_port) in events["http"]["last_error"]
            assert events["checks"]["http_listener"] is False
            assert call("127.0.0.1", rpc_port, token, "telegram.status")["listener"] == "running"
            pairing = call("127.0.0.1", rpc_port, token, "telegram.pair-request")
            valid_pin = isinstance(pairing.get("pin"), str) and re.fullmatch(r"[0-9]{6}", pairing["pin"]) is not None
            assert valid_pin, "O controle de pareamento não gerou um PIN válido."
            occupied.close()
            configured = call("127.0.0.1", rpc_port, token, "notification.configure", {"enabled": True})
            assert configured["http"]["state"] == "running"
            assert configured["http"]["port"] == occupied_port
            recovered = call("127.0.0.1", rpc_port, token, "notification.status")
            assert recovered["checks"]["http_listener"] is True
            assert recovered["enabled"] is True
            call("127.0.0.1", rpc_port, token, "shutdown")
            assert process.wait(timeout=5) == 0
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
