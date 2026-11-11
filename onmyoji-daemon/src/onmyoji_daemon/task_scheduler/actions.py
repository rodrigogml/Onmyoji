"""Adaptadores de ação com fronteiras externas sem retry automático."""
from __future__ import annotations

from contextlib import ExitStack
import json
import http.client
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from ..agent_notification.configuration import NotificationConfiguration
from ..supervision.component.process_group import ServiceProcessGroup

MAX_OUTPUT_BYTES = 10 * 1024 * 1024


def event_action(root, occurrence, *, before_dispatch=None):
    event = {**occurrence["snapshot"]["action"]["event"], "event_id": occurrence["event_id"]}
    if before_dispatch and not before_dispatch():
        return "failed", None, "definition_withdrawn"
    connection = None
    try:
        port = NotificationConfiguration(Path(root)).load()["port"]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=None)
        body = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        connection.request("POST", "/v1/events", body=body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        if response.status != 200:
            return "failed", result, result.get("error", {}).get("code", "submission_rejected")
    except ConnectionRefusedError:
        return "failed", None, "daemon_unavailable"
    except (OSError, ValueError, json.JSONDecodeError):
        return "uncertain", None, "acceptance_unknown"
    finally:
        if connection is not None:
            connection.close()
    state = result.get("status") if result.get("status") in {"completed", "failed", "uncertain"} else "uncertain"
    return state, result, result.get("reason")


def script_action(root: Path, workspace: Path, occurrence: dict, stop: threading.Event, *, before_dispatch=None):
    boundary = {"dispatched": False}
    try:
        return _script_action(root, workspace, occurrence, stop, boundary, before_dispatch)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ("uncertain" if boundary["dispatched"] else "failed"), None, "process_interrupted" if boundary["dispatched"] else "process_preparation_failed"


def _script_action(root: Path, workspace: Path, occurrence: dict, stop: threading.Event, boundary: dict, before_dispatch):
    action = occurrence["snapshot"]["action"]
    cwd = workspace.resolve(strict=True)
    script = (cwd / action["path"]).resolve(strict=True)
    if not script.is_relative_to(cwd) or not script.is_file() or script.suffix.casefold() != ".py":
        return "failed", None, "invalid_script_path"
    if stop.is_set():
        return "failed", None, "service_stopping_before_action"
    if before_dispatch and not before_dispatch():
        return "failed", None, "definition_withdrawn"
    output = workspace / ".onmyoji" / "task-scheduler" / occurrence["id"]
    if not output.resolve().is_relative_to(workspace.resolve()):
        return "failed", None, "invalid_output_path"
    output.mkdir(parents=True, exist_ok=False)
    temporary_root = root / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scheduled-process-", dir=temporary_root) as temporary, ExitStack() as opened_files:
        environment = dict(os.environ)
        for key in list(environment):
            if key.upper().startswith("ONMYOJI_") and any(part in key.upper() for part in ("TOKEN", "SECRET", "PASSWORD")):
                del environment[key]
        environment.update(TEMP=temporary, TMP=temporary, TMPDIR=temporary, PYTHONDONTWRITEBYTECODE="1")
        source_root = str(Path(__file__).resolve().parents[2])
        environment["PYTHONPATH"] = source_root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen([sys.executable, "-B", "-m", "onmyoji_daemon.task_scheduler.process_worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd, env=environment, start_new_session=os.name != "nt", creationflags=flags)
        group = None
        group_closed = False
        readers = []
        metadata = {}
        capture_errors = []
        try:
            group = ServiceProcessGroup(process)
            outputs = {name: opened_files.enter_context((output / (name + ".txt")).open("xb")) for name in ("stdout", "stderr")}
            def drain(source, name, target):
                retained, total = 0, 0
                path = output / (name + ".txt")
                try:
                    with source:
                        while True:
                            data = source.read(65536)
                            if not data:
                                break
                            total += len(data)
                            chunk = data[:max(0, MAX_OUTPUT_BYTES - retained)]
                            target.write(chunk)
                            retained += len(chunk)
                    target.flush()
                except OSError:
                    capture_errors.append(name)
                metadata[name] = {"path": str(path), "bytes": total, "truncated": total > MAX_OUTPUT_BYTES}
            for source, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                thread = threading.Thread(target=drain, args=(source, name, outputs[name]), daemon=True)
                readers.append(thread); thread.start()
            if stop.is_set():
                return "failed", None, "service_stopping"
            boundary["dispatched"] = True
            process.stdin.write((json.dumps([sys.executable, "-B", str(script), *action["args"]]) + "\n").encode("utf-8"))
            process.stdin.close()
            deadline = time.monotonic() + action["timeout_seconds"]
            reason = None
            while process.poll() is None:
                if stop.wait(0.05):
                    reason = "service_stopping"
                    break
                if time.monotonic() >= deadline:
                    reason = "process_timeout"
                    break
            group.close()
            group_closed = True
            for thread in readers:
                thread.join(timeout=5)
            if any(thread.is_alive() for thread in readers):
                return "uncertain", metadata, "output_not_drained"
            result = {"returncode": process.returncode, **metadata}
            if capture_errors:
                return "failed", result, "output_capture_failed"
            return ("completed" if not reason and process.returncode == 0 else "failed"), result, reason or ("nonzero_exit" if process.returncode else None)
        finally:
            if group is not None and not group_closed:
                group.close()
            elif process.poll() is None:
                process.terminate(); process.wait(timeout=5)
            for thread in readers:
                thread.join(timeout=5)
