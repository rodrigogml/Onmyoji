"""Assinatura de notificações preserva o protocolo sob rajadas de deltas."""
from __future__ import annotations

import json
import queue
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import codex_app_server as transport
from onmyoji_daemon.codex_app_server import CodexAppServer, CodexProtocolError


class _Stream:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self, _size=-1):
        return self.lines.get()

    def emit(self, message):
        self.lines.put(json.dumps(message) + "\n")

    def close(self):
        self.lines.put("")


class _Process:
    def __init__(self):
        self.stdout, self.stderr = _Stream(), _Stream()
        self.stdin = SimpleNamespace(write=self.write, flush=lambda: None)
        self.messages = []
        self.on_write = None
        self.exit_code = None
        self.responses = queue.Queue()

    def write(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        if self.on_write and self.on_write(message):
            return len(raw)
        if "method" in message and "id" in message:
            self.stdout.emit({"id": message["id"], "result": {"ok": True}})
        elif "method" not in message:
            self.responses.put(message)
        return len(raw)

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.exit_code = -15
        self.stdout.close()
        self.stderr.close()

    def wait(self, timeout=None):
        return self.exit_code

    def kill(self):
        self.terminate()


def _configure(monkeypatch, tmp_path, **options):
    process = _Process()
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *_args, **_kwargs: process)
    launch = SimpleNamespace(app_server_launch=lambda: (["codex", "app-server"], tmp_path, {}))
    monkeypatch.setattr(transport.CodexLaunch, "from_system", lambda _root: launch)
    return CodexAppServer(SimpleNamespace(root=tmp_path), **options), process


def _wait(event):
    assert event.wait(5), "A fronteira de protocolo esperada não foi observada"


def test_ignored_delta_burst_preserves_terminal_order_and_rpc_progress(monkeypatch, tmp_path):
    """Dez mil deltas não ocupam a fila mesmo com o consumidor da thread parado."""
    client, process = _configure(
        monkeypatch, tmp_path, event_limit=2, event_workers=1,
        notification_methods=frozenset({"item/completed", "turn/completed"}),
    )
    entered, release, completed = [threading.Event() for _ in range(3)]
    observed = []

    def event(method, params, context):
        observed.append((method, params.get("n"), context["sequence"]))
        if params.get("n") == 1:
            entered.set()
            _wait(release)
        if method == "turn/completed":
            completed.set()

    client.event_handler = event
    client.start()
    try:
        process.stdout.emit({"method": "item/completed", "params": {"threadId": "same", "n": 1}})
        _wait(entered)
        ignored = ("item/agentMessage/delta", "item/reasoning/textDelta", "item/commandExecution/outputDelta", "future/unknown")
        for index in range(10000):
            process.stdout.emit({"method": ignored[index % len(ignored)], "params": {"threadId": "same", "delta": "x"}})
        process.stdout.emit({"method": "item/completed", "params": {"threadId": "same", "n": 2}})
        process.stdout.emit({"method": "turn/completed", "params": {"threadId": "same", "n": 3}})
        assert client.request("thread/read", timeout=5) == {"ok": True}
        assert [(method, number) for method, number, _sequence in observed] == [("item/completed", 1)]
        assert client.running()
        release.set()
        _wait(completed)
        assert [(method, number) for method, number, _sequence in observed] == [
            ("item/completed", 1), ("item/completed", 2), ("turn/completed", 3),
        ]
        sequences = [sequence for _method, _number, sequence in observed]
        assert sequences[0] < sequences[1] < sequences[2]
    finally:
        release.set()
        client.stop()


def test_empty_subscription_preserves_rpc_responses_and_server_requests(monkeypatch, tmp_path):
    client, process = _configure(monkeypatch, tmp_path, notification_methods=frozenset())
    requests, notifications = [], []

    def request(method, params, context):
        requests.append((method, params, context["rpc_id"]))
        return {"choice": "approved"}

    client.request_event_handler = request
    client.event_handler = lambda method, _params, _context: notifications.append(method)
    client.start()
    try:
        process.stdout.emit({"method": "tool/ask", "params": {"threadId": "same"}})
        process.stdout.emit({"id": 700, "method": "tool/ask", "params": {"threadId": "same"}})
        assert process.responses.get(timeout=5) == {"id": 700, "result": {"choice": "approved"}}
        assert client.request("thread/read") == {"ok": True}
        assert requests == [("tool/ask", {"threadId": "same"}, 700)]
        assert notifications == []
        assert client.running()
    finally:
        client.stop()


def test_selected_account_notification_without_thread_id_is_preserved(monkeypatch, tmp_path):
    client, process = _configure(
        monkeypatch, tmp_path, notification_methods=frozenset({"account/login/completed"}),
    )
    received = threading.Event()
    observed = []

    def event(method, params, _context):
        observed.append((method, params))
        received.set()

    client.event_handler = event
    client.start()
    try:
        process.stdout.emit({"method": "account/login/completed", "params": {"loginId": "login", "success": True}})
        _wait(received)
        assert observed == [("account/login/completed", {"loginId": "login", "success": True})]
        assert client.running()
    finally:
        client.stop()


@pytest.mark.parametrize("options", [{}, {"notification_methods": None}])
def test_default_subscription_preserves_unknown_notifications(monkeypatch, tmp_path, options):
    client, process = _configure(monkeypatch, tmp_path, event_workers=1, **options)
    received = threading.Event()
    observed = []

    def notification(method, params):
        observed.append((method, params))
        if len(observed) == 2:
            received.set()

    client.notification_handler = notification
    client.start()
    try:
        process.stdout.emit({"method": "future/unknown", "params": {"threadId": "same", "n": 1}})
        process.stdout.emit({"method": "future/account", "params": {"n": 2}})
        _wait(received)
        assert observed == [("future/unknown", {"threadId": "same", "n": 1}), ("future/account", {"n": 2})]
    finally:
        client.stop()


def test_empty_subscription_preserves_local_late_response_and_transport_loss(monkeypatch, tmp_path):
    client, process = _configure(monkeypatch, tmp_path, notification_methods=frozenset())
    late, lost = threading.Event(), threading.Event()
    observed = []

    def event(method, params, context):
        observed.append((method, params, context))
        if method == "transport/late_response":
            late.set()
        if method == "transport/lost":
            lost.set()

    client.event_handler = event
    client.start()
    try:
        process.on_write = lambda message: message.get("method") == "turn/steer"
        with pytest.raises(CodexProtocolError) as failure:
            client.request("turn/steer", timeout=0.02)
        assert failure.value.kind == "uncertain"
        process.stdout.emit({"method": "transport/lost", "params": {"reason": "spoofed"}})
        process.stdout.emit({"method": "transport/late_response", "params": {"id": "spoofed"}})
        process.stdout.emit({"id": failure.value.request_id, "result": {"turnId": "turn"}})
        _wait(late)
        assert [method for method, _params, _context in observed] == ["transport/late_response"]
        assert observed[0][1]["result"] == {"turnId": "turn"}
        assert observed[0][2]["transport_generated"] is True
        assert client.running()
        process.stdout.close()
        _wait(lost)
        assert [method for method, _params, _context in observed] == ["transport/late_response", "transport/lost"]
        assert observed[-1][2]["transport_generated"] is True
    finally:
        client.stop()


def test_relevant_terminal_overflow_remains_an_explicit_transport_failure(monkeypatch, tmp_path):
    client, process = _configure(
        monkeypatch, tmp_path, event_limit=1, event_workers=1, event_drain_timeout=0.02,
        notification_methods=frozenset({"turn/completed"}),
    )
    entered, release, lost = [threading.Event() for _ in range(3)]
    losses = []

    def event(method, params, context):
        if method == "transport/lost":
            losses.append((params, context))
            lost.set()
        else:
            entered.set()
            _wait(release)

    client.event_handler = event
    client.start()
    try:
        process.stdout.emit({"method": "turn/completed", "params": {"threadId": "same", "n": 1}})
        _wait(entered)
        process.stdout.emit({"method": "turn/completed", "params": {"threadId": "same", "n": 2}})
        process.stdout.emit({"method": "turn/completed", "params": {"threadId": "same", "n": 3}})
        _wait(lost)
        assert losses[0][0]["cause"] == "event_overflow"
        assert losses[0][1]["transport_generated"] is True
        assert not client.running()
    finally:
        release.set()
        client.stop()


@pytest.mark.parametrize("identifier", [True, [], {}])
def test_filtered_method_with_invalid_rpc_id_is_still_rejected(monkeypatch, tmp_path, identifier):
    client, process = _configure(monkeypatch, tmp_path, notification_methods=frozenset())
    lost = threading.Event()
    causes = []

    def event(method, params, _context):
        if method == "transport/lost":
            causes.append(params["cause"])
            lost.set()

    client.event_handler = event
    client.start()
    try:
        process.stdout.emit({"id": identifier, "method": "future/unknown", "params": {}})
        _wait(lost)
        assert causes == ["invalid_request_id"]
        assert not client.running()
    finally:
        client.stop()


@pytest.mark.parametrize("methods", [[], set(), "item/completed", True, frozenset({""}), frozenset({1}), frozenset({"item/completed", None})])
def test_invalid_notification_subscription_is_rejected(tmp_path, methods):
    with pytest.raises(ValueError):
        CodexAppServer(SimpleNamespace(root=tmp_path), notification_methods=methods)
