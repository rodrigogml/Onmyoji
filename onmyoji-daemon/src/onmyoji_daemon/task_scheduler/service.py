"""Lifecycle supervisionado e dispatcher independente do gateway."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
import tomllib

from .actions import email_check_action, event_action, script_action
from .repository import TaskRepository
from ..rpc import RpcError, RpcServer


class TaskScheduler:
    def __init__(self, root, data_dir, *, workspace=None, clock=None):
        self.root, self.data_dir = Path(root), Path(data_dir)
        if workspace is None:
            system = tomllib.loads((self.root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
            workspace = Path(system["codex"]["project_directory"])
        self.workspace = Path(workspace).resolve()
        if not self.workspace.is_dir() or self.workspace.is_relative_to(self.root.resolve()):
            raise RpcError("Configure workspace externo à instância.", "invalid_workspace")
        configuration = self.data_dir / "config.toml"
        options = tomllib.loads(configuration.read_text(encoding="utf-8")) if configuration.exists() else {}
        self.parallel = options.get("max_parallel_actions", 4)
        if type(self.parallel) is not int or not 1 <= self.parallel <= 64:
            raise RpcError("Limite de ações deve ser de 1 a 64.", "invalid_config")
        self.script_timeout_seconds = options.get("script_timeout_seconds", 900)
        if type(self.script_timeout_seconds) is not int or not 1 <= self.script_timeout_seconds <= 604800:
            raise RpcError("Timeout padrão de scripts inválido.", "invalid_config")
        self.repository = TaskRepository(self.data_dir / "state" / "scheduler.sqlite3", self.workspace, clock, self.data_dir / "schedules", self.script_timeout_seconds)
        self.stop_event, self.changed = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.active = {}
        self.last_error = None
        self.thread = None
        self._last_reconcile = float("-inf")

    def handle(self, method, params):
        if method == "ping":
            return {"service": "task-scheduler", "state": "running"}
        if method == "shutdown":
            self.stop_event.set(); self.changed.set()
            return {"state": "stopping"}
        if method == "task.activate":
            if self.thread is None:
                self.start()
            return {"state": "running"}
        action = method.removeprefix("task.")
        repository = self.repository
        if action == "status":
            with self.lock:
                active = len(self.active)
            return {"state": "running", "max_parallel_actions": self.parallel, "script_timeout_seconds": self.script_timeout_seconds, "active": active, "counts": repository.summary(), "last_error": self.last_error}
        if action == "get":
            return repository.get(params.get("id"))
        elif action == "list":
            return repository.list(**params)
        elif action == "history":
            return repository.history(params.get("id"), params.get("offset", 0), params.get("limit", 20))
        elif action == "resolve":
            result = repository.resolve(params.get("id"), params.get("reason"), params.get("confirm"))
        elif action == "preview":
            count = params.get("count", 5)
            if type(count) is not int or not 1 <= count <= 100:
                raise RpcError("Quantidade de preview deve ser de 1 a 100.", "invalid_argument")
            rule = params.get("time") or repository.get(params.get("id"))["time"]
            return repository.preview(rule, count)
        elif action in {"create", "update", "enable", "disable", "delete"}:
            raise RpcError("Edite o arquivo TOML do agendamento; autoria remota não integra a versão 1.", "authoring_unsupported")
        else:
            raise RpcError("Operação do agendador desconhecida.", "unknown_method")
        self.changed.set()
        return result

    def tick(self):
        if self.stop_event.is_set():
            return
        self.repository.materialize()
        with self.lock:
            available = self.parallel - len(self.active)
        for _ in range(available):
            if self.stop_event.is_set():
                break
            occurrence = self.repository.claim()
            if occurrence is None:
                break
            thread = threading.Thread(target=self._execute, args=(occurrence,), daemon=True)
            with self.lock:
                self.active[occurrence["id"]] = thread
            thread.start()

    def _execute(self, occurrence):
        try:
            if self.stop_event.is_set():
                return
            before_dispatch = lambda: self.repository.begin(occurrence["id"])
            action_type = occurrence["snapshot"]["action"]["type"]
            if action_type == "event":
                state, result, reason = event_action(self.root, occurrence, before_dispatch=before_dispatch)
            elif action_type == "email_check":
                state, result, reason = email_check_action(self.root, occurrence, self.stop_event, before_dispatch=before_dispatch)
            else:
                state, result, reason = script_action(self.root, self.workspace, occurrence, self.stop_event, before_dispatch=before_dispatch)
            if reason == "service_stopping_before_action":
                return  # Reserva comprovadamente não enviada retoma no próximo startup.
            self.repository.finish(occurrence["id"], state, result, reason)
        except Exception:
            self.repository.finish(occurrence["id"], "uncertain", reason="action_interrupted")
        finally:
            with self.lock:
                self.active.pop(occurrence["id"], None)
            self.changed.set()

    def _watch(self):
        last_purge = float("-inf")
        while not self.stop_event.is_set():
            self.changed.clear()
            try:
                self._reconcile_if_due()
                self.tick()
                if time.monotonic() - last_purge >= 86400:
                    self.repository.purge(); last_purge = time.monotonic()
            except Exception:
                self.last_error = "Falha de manutenção do agendador; consulte estado e histórico."
            self.changed.wait(1)

    def _reconcile_if_due(self):
        now = time.monotonic()
        if now - self._last_reconcile < 5:
            return False
        self.repository.reconcile()
        self._last_reconcile = now
        return True

    def start(self):
        self.repository.reconcile()
        self._last_reconcile = time.monotonic()
        self.thread = threading.Thread(target=self._watch, daemon=True, name="task-scheduler")
        self.thread.start()

    def close(self):
        self.stop_event.set(); self.changed.set()
        if self.thread:
            self.thread.join(2)
        with self.lock:
            active = list(self.active.values())
        for thread in active:
            thread.join(12)
        # Não fechar SQLite enquanto uma submissão longa ainda referencia o
        # repositório. O processo terminará e a recuperação marcará incerteza.
        if not any(thread.is_alive() for thread in active):
            self.repository.close()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    args = parser.parse_args(argv)
    scheduler = TaskScheduler(args.onmyoji_root, args.data_dir)
    server = RpcServer(args.host, args.port, args.token, scheduler.handle)
    try:
        server.serve_forever(scheduler.stop_event)
    finally:
        scheduler.close(); server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
