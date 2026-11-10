"""Adaptadores de ação com fronteiras externas sem retry automático."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from ..rpc import RpcError, call
from ..supervision.component.process_group import ServiceProcessGroup
from ..supervisor import endpoint

MAX_OUTPUT_BYTES = 10 * 1024 * 1024


def notification_action(root, occurrence):
    event = {**occurrence["snapshot"]["action"]["event"], "event_id": occurrence["event_id"]}
    try:
        address = endpoint(root)
    except (OSError, ValueError, KeyError):
        return "failed", None, "daemon_unavailable"
    try:
        result = call(*address, "notification.submit", event, timeout=None)
    except RpcError as error:
        return ("uncertain" if error.code == "acceptance_unknown" else "failed"), None, error.code or "submission_failed"
    except (OSError, ValueError):
        return "uncertain", None, "acceptance_unknown"
    execution, delivery = result.get("execution"), result.get("delivery")
    state = "completed" if execution == "completed" and delivery == "confirmed" else "uncertain" if "uncertain" in {execution, delivery} else "failed"
    return state, result, result.get("reason")


def process_action(root: Path, workspace: Path, occurrence: dict, stop: threading.Event):
    action = occurrence["snapshot"]["action"]
    cwd = Path(action["cwd"]).resolve(strict=True)
    if not cwd.is_relative_to(workspace.resolve()) or not cwd.is_dir() or not Path(action["executable"]).is_file():
        return "failed", None, "invalid_process_path"
    output = workspace / ".onmyoji" / "task-scheduler" / occurrence["id"]
    if not output.resolve().is_relative_to(workspace.resolve()):
        return "failed", None, "invalid_output_path"
    output.mkdir(parents=True, exist_ok=False)
    temporary_root = root / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scheduled-process-", dir=temporary_root) as temporary:
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
        try:
            group = ServiceProcessGroup(process)
            def drain(source, name):
                retained, total = 0, 0
                path = output / (name + ".txt")
                with source, path.open("xb") as target:
                    while True:
                        data = source.read(65536)
                        if not data:
                            break
                        total += len(data)
                        chunk = data[:max(0, MAX_OUTPUT_BYTES - retained)]
                        target.write(chunk)
                        retained += len(chunk)
                metadata[name] = {"path": str(path), "bytes": total, "truncated": total > MAX_OUTPUT_BYTES}
            for source, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                thread = threading.Thread(target=drain, args=(source, name), daemon=True)
                readers.append(thread); thread.start()
            process.stdin.write((json.dumps([action["executable"], *action["arguments"]]) + "\n").encode("utf-8"))
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
            return ("completed" if not reason and process.returncode == 0 else "failed"), result, reason or ("nonzero_exit" if process.returncode else None)
        finally:
            if group is not None and not group_closed:
                group.close()
            elif process.poll() is None:
                process.terminate(); process.wait(timeout=5)
            for thread in readers:
                thread.join(timeout=5)
