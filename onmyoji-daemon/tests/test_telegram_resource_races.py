"""Reciclagem de recursos observa reservas e turnos sem perder a thread durável."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import telegram_execution
from onmyoji_daemon.telegram import Gateway, Settings


class ResourceApi:
    def __init__(self):
        self.changed = threading.Condition()
        self.sent = []

    def send(self, chat, text, **values):
        with self.changed:
            self.sent.append((chat, text, values))
            self.changed.notify_all()
            return {"message_id": len(self.sent)}

    def typing(self, *_args, **_kwargs): return True
    def delete(self, *_args, **_kwargs): return True

    def wait_answer(self):
        with self.changed:
            assert self.changed.wait_for(lambda: any("Resposta controlada" in text for _, text, _ in self.sent), timeout=3)


class ResourceClient:
    """Porta de protocolo com contagem de processos, sem simular a política de reciclagem."""
    def __init__(self):
        self.changed = threading.Condition()
        self.calls = []
        self.turns = {}
        self.alive, self.busy = True, False
        self.starts, self.stops = 0, 0
        self.process_epoch = "resource-epoch-0"
        self.event_handler = None
        self.turn_ready = threading.Event()

    def running(self): return self.alive

    def start(self):
        if not self.alive:
            self.starts += 1
            self.process_epoch = f"resource-epoch-{self.starts}"
            self.alive = True

    def stop(self):
        self.stops += 1
        self.alive = False

    def emit(self, method, params):
        assert self.event_handler is not None
        self.event_handler(method, params, {"process_epoch": self.process_epoch})

    def request(self, method, params=None, **_kwargs):
        params = json.loads(json.dumps(params or {}))
        with self.changed:
            self.calls.append((method, params))
            if method == "thread/resume":
                result = {"thread": {"id": params["threadId"]}}
            elif method == "thread/start":
                result = {"thread": {"id": "new-thread"}}
            elif method == "turn/start":
                turn = f"turn-{len(self.turns) + 1}"
                self.turns[params["threadId"]] = turn
                result = {"turn": {"id": turn, "status": "inProgress"}}
            elif method == "turn/steer":
                assert self.turns[params["threadId"]] == params["expectedTurnId"]
                result = {"turnId": params["expectedTurnId"]}
            elif method == "turn/interrupt":
                result = {}
            else:
                raise AssertionError(f"RPC inesperado no teste de recursos: {method}")
            self.changed.notify_all()
        if method == "turn/start":
            self.emit("turn/started", {"threadId": params["threadId"], "turn": result["turn"]})
            self.turn_ready.set()
        elif method == "turn/interrupt":
            self.complete(params["threadId"], status="interrupted")
        return result

    def wait_calls(self, method, count=1):
        with self.changed:
            assert self.changed.wait_for(lambda: sum(name == method for name, _ in self.calls) >= count, timeout=3)
            return [params for name, params in self.calls if name == method]

    def complete(self, thread, *, status="completed"):
        turn = self.turns[thread]
        if status == "completed":
            self.emit("item/completed", {"threadId": thread, "turnId": turn, "item": {"id": "final", "type": "agentMessage", "phase": "final_answer", "text": "Resposta controlada"}})
        self.emit("turn/completed", {"threadId": thread, "turn": {"id": turn, "status": status}})


@pytest.fixture
def resource_gateway(tmp_path: Path, monkeypatch):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    data = root / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True); workspace.mkdir()
    (root / "configs" / "onmyoji-system.toml").write_text('[codex]\nexecutable="fixture-codex"\nmodel="fixture-model"\nproject_directory=' + json.dumps(workspace.as_posix()) + '\n', encoding="utf-8")
    (root / "configs" / "daemon" / "daemon.toml").write_text('[app_server]\nenabled=true\n', encoding="utf-8")
    (data / "config.toml").write_text('schema_version=2\n[telegram]\nvault_profile="fixture"\ntoken_entry="fixture"\n', encoding="utf-8")
    gateway = Gateway(replace(Settings.load(root, data), busy_input_policy="steer", turn_timeout=10))
    client, api = ResourceClient(), ResourceApi()
    gateway.app_server, gateway.api = client, api
    gateway.contacts.add_owner({"id": 9})
    capabilities = SimpleNamespace(supports=lambda feature: feature in {"base", "steering"}, input_supported=lambda _kind: True)
    gateway.capabilities = capabilities
    monkeypatch.setattr(telegram_execution.CapabilitySet, "from_instance", lambda *_args, **_kwargs: capabilities)
    monkeypatch.setattr(telegram_execution, "record_process", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(telegram_execution, "recover_process", lambda *_args, **_kwargs: SimpleNamespace(process_epoch=None, action="not_recorded"))
    monkeypatch.setattr(gateway, "_schedule_delete", lambda *_args: None)
    gateway._conversation_settings("9")
    with gateway.database_lock, gateway.database:
        gateway.database.execute("UPDATE conversations SET codex_thread_id='persisted-thread' WHERE chat_id='9'")
    yield SimpleNamespace(gateway=gateway, client=client, api=api)
    gateway.stop_event.set()
    with gateway.app_server_lock:
        active = list(gateway.app_turns.values())
        slots = list(gateway._slots.values())
    for turn in active:
        if not turn.completed.is_set(): client.complete(turn.thread_id, status="interrupted")
    with gateway.workers_changed: gateway.workers_changed.notify_all()
    for slot in slots: assert slot.done.wait(3)
    gateway.store.release_lease()


def incoming(identifier, text):
    return {"update_id": identifier, "message": {"message_id": identifier, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": text}}


def test_idle_resource_recycle_serializes_model_opening_but_not_ingress_reservation(resource_gateway, monkeypatch):
    lab = resource_gateway
    entered, release, model_waiting = threading.Event(), threading.Event(), threading.Event()
    pending = {"changed": True}
    sync = []
    def skills(_launch):
        sync.append("skills")
        if pending["changed"]:
            entered.set(); assert release.wait(3)
            pending["changed"] = False
            return True
        return False
    monkeypatch.setattr(telegram_execution.CodexLaunch, "synchronize_shikigami_skills", skills)
    monkeypatch.setattr(telegram_execution.CodexLaunch, "synchronize_shikigami_agents", lambda _launch: sync.append("profiles") or False)
    monkeypatch.setattr(telegram_execution, "mcp_reconciliation_pending", lambda _root: False)
    actual_app_client = lab.gateway._app_client
    def observing_app_client():
        model_waiting.set()
        return actual_app_client()
    monkeypatch.setattr(lab.gateway, "_app_client", observing_app_client)
    with ThreadPoolExecutor(max_workers=1) as executor:
        recycling = executor.submit(actual_app_client)
        assert entered.wait(1)
        try:
            lab.gateway._receive_update(incoming(1, "Execute depois da revisão completa"))
            assert model_waiting.wait(2)
            with lab.gateway.app_server_lock:
                slot = lab.gateway._slots["9"]
            with lab.gateway.database_lock:
                assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (slot.inbox["id"],)).fetchone() == ("running",)
                assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("persisted-thread",)
            assert not slot.done.is_set() and lab.client.calls == []
            assert lab.client.stops == lab.client.starts == 0
        finally:
            release.set()
        assert recycling.result(timeout=3) is lab.client
    started = lab.client.wait_calls("turn/start")[0]
    assert lab.client.turn_ready.wait(2)
    assert started["threadId"] == "persisted-thread"
    assert lab.client.wait_calls("thread/resume")[0]["threadId"] == "persisted-thread"
    assert not any(method == "thread/start" for method, _ in lab.client.calls)
    assert lab.client.stops == lab.client.starts == 1
    assert sync[0:2] == ["skills", "profiles"]
    lab.client.complete("persisted-thread")
    assert slot.done.wait(3)
    lab.api.wait_answer()


def test_active_turn_and_steering_defer_all_resources_until_terminal(resource_gateway, monkeypatch):
    lab = resource_gateway
    pending = {"changed": False}
    sync = []
    def skills(_launch):
        sync.append("skills")
        changed, pending["changed"] = pending["changed"], False
        return changed
    monkeypatch.setattr(telegram_execution.CodexLaunch, "synchronize_shikigami_skills", skills)
    monkeypatch.setattr(telegram_execution.CodexLaunch, "synchronize_shikigami_agents", lambda _launch: sync.append("profiles") or False)
    monkeypatch.setattr(telegram_execution, "mcp_reconciliation_pending", lambda _root: pending["changed"])
    lab.gateway._receive_update(incoming(1, "Comece o trabalho"))
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    assert lab.client.turn_ready.wait(2)
    with lab.gateway.app_server_lock:
        slot = lab.gateway._slots["9"]
    initial_sync = list(sync)
    pending["changed"] = True
    assert lab.gateway._app_client() is lab.client
    lab.gateway._receive_update(incoming(2, "Acrescente esta orientação"))
    steering = lab.client.wait_calls("turn/steer")[0]
    assert steering["threadId"] == thread and steering["expectedTurnId"] == lab.client.turns[thread]
    assert lab.gateway._app_client() is lab.client
    assert sync == initial_sync and pending["changed"]
    assert lab.client.stops == lab.client.starts == 0
    lab.client.complete(thread)
    assert slot.done.wait(3)
    lab.api.wait_answer()
    assert lab.gateway._app_client() is lab.client
    assert sync == initial_sync + ["skills", "profiles"]
    assert lab.client.stops == lab.client.starts == 1
    assert sum(method == "turn/start" for method, _ in lab.client.calls) == 1
    with lab.gateway.database_lock:
        assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == (thread,)
