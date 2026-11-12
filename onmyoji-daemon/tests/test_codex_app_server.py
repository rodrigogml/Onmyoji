"""Contratos do transporte App Server sem rede ou contas reais."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import codex_app_server as transport
from onmyoji_daemon.codex_app_server import CodexAppServer, CodexProtocolError


class Stream:
    def __init__(self):
        self.lines = queue.Queue()

    def readline(self, _size=-1):
        return self.lines.get()

    def emit(self, value):
        self.lines.put(json.dumps(value) + "\n")

    def close(self):
        self.lines.put("")

    def __iter__(self):
        return self

    def __next__(self):
        value = self.readline()
        if not value:
            raise StopIteration
        return value


class Process:
    def __init__(self):
        self.stdout, self.stderr = Stream(), Stream()
        self.stdin = SimpleNamespace(write=self.write, flush=lambda: None)
        self.messages = []
        self.on_write = None
        self.exit_code = None
        self.response_events = {}

    def write(self, raw):
        message = json.loads(raw)
        self.messages.append(message)
        if self.on_write and self.on_write(message):
            return len(raw)
        if "method" in message and "id" in message:
            self.stdout.emit({"id": message["id"], "result": {"ok": True}})
        if "method" not in message:
            self.response_events.setdefault(message["id"], threading.Event()).set()
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


def wait(event):
    assert event.wait(2), "A fronteira de protocolo esperada não foi observada"


def configure(monkeypatch, tmp_path, processes, **options):
    pending = iter(processes)
    monkeypatch.setattr(transport.subprocess, "Popen", lambda *_args, **_kwargs: next(pending))
    launch = SimpleNamespace(app_server_launch=lambda: (["codex", "app-server"], tmp_path, {}))
    monkeypatch.setattr(transport.CodexLaunch, "from_system", lambda _root: launch)
    return CodexAppServer(SimpleNamespace(root=tmp_path), **options)


@pytest.fixture
def connected(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process])
    client.start()
    try:
        yield client, process
    finally:
        client.stop()


def test_handshake_and_rpc_correlate_without_restarting_process(connected):
    client, process = connected
    epoch = client.process_epoch
    assert process.messages[0]["method"] == "initialize"
    assert process.messages[1]["method"] == "initialized"
    assert client.request("thread/resume", {"threadId": "saved"}) == {"ok": True}
    client.start()
    assert client.process_epoch == epoch == client.epoch
    assert client.pending_count == 0
    assert [m["method"] for m in process.messages].count("initialize") == 1


def test_menu_request_cannot_block_notifications_or_rpc_acknowledgements(connected):
    client, process = connected
    entered, release, same_thread, other_thread = [threading.Event() for _ in range(4)]
    contexts = []

    def menu(_method, _params, context):
        contexts.append(context)
        entered.set()
        wait(release)
        return {"choice": "approved"}

    def event(method, params, context):
        if method == "item/completed":
            contexts.append(context)
            (same_thread if params["threadId"] == "a" else other_thread).set()

    client.request_event_handler, client.event_handler = menu, event
    process.stdout.emit({"id": 500, "method": "tool/ask", "params": {"threadId": "a"}})
    wait(entered)
    process.stdout.emit({"method": "item/completed", "params": {"threadId": "a"}})
    process.stdout.emit({"method": "item/completed", "params": {"threadId": "b"}})
    wait(same_thread)
    wait(other_thread)
    assert client.busy
    assert client.request("thread/read") == {"ok": True}
    release.set()
    wait(process.response_events.setdefault(500, threading.Event()))
    assert contexts[0]["rpc_id"] == contexts[0]["request_id"] == 500
    assert all(context["process_epoch"] == client.process_epoch for context in contexts)


def test_slow_notification_preserves_thread_order_and_other_conversation(connected):
    client, process = connected
    entered, release, same_done, other_done = [threading.Event() for _ in range(4)]
    observed = []

    def event(_method, params, _context):
        if "n" not in params:
            return
        if params["n"] == 1:
            entered.set()
            wait(release)
        observed.append(params["n"])
        if params["n"] == 2:
            same_done.set()
        if params["n"] == 3:
            other_done.set()

    client.event_handler = event
    process.stdout.emit({"method": "item/completed", "params": {"threadId": "a", "n": 1}})
    wait(entered)
    process.stdout.emit({"method": "turn/completed", "params": {"threadId": "a", "n": 2}})
    process.stdout.emit({"method": "item/completed", "params": {"threadId": "b", "n": 3}})
    wait(other_done)
    assert 2 not in observed
    release.set()
    wait(same_done)
    assert observed.index(1) < observed.index(2)


def test_eof_resolves_written_request_as_unknown_without_waiting_timeout(connected):
    client, process = connected
    lost = threading.Event()
    client.event_handler = lambda method, _params, _context: lost.set() if method == "transport/lost" else None

    def write(message):
        if message.get("method") == "turn/start":
            process.stdout.close()
            return True
        return False

    process.on_write = write
    with pytest.raises(CodexProtocolError) as failure:
        client.request("turn/start", timeout=20)
    assert failure.value.kind == "transport_lost"
    assert failure.value.written and failure.value.acceptance_unknown
    assert failure.value.method == "turn/start"
    assert failure.value.process_epoch == client.process_epoch
    assert client.pending_count == 0
    wait(lost)


def test_rpc_timeout_never_retries_and_late_ack_is_observable(connected):
    client, process = connected
    late = threading.Event()
    replies = []
    client.event_handler = lambda method, params, _context: (replies.append(params), late.set()) if method == "transport/late_response" else None
    process.on_write = lambda message: message.get("method") == "turn/steer"
    with pytest.raises(CodexProtocolError) as failure:
        client.request("turn/steer", {"expectedTurnId": "t"}, timeout=0.02)
    assert failure.value.kind == "uncertain" and failure.value.written
    assert len([m for m in process.messages if m.get("method") == "turn/steer"]) == 1
    process.stdout.emit({"id": failure.value.request_id, "result": {"turnId": "t"}})
    wait(late)
    assert replies[0]["result"]["turnId"] == "t"
    assert client.running()


def test_timeout_before_writer_starts_cancels_queued_operation(connected):
    client, process = connected
    blocked, release, finished = [threading.Event() for _ in range(3)]

    def write(message):
        if message.get("method") == "block":
            blocked.set()
            wait(release)
        return False

    process.on_write = write
    worker = threading.Thread(target=lambda: (client.notify("block"), finished.set()))
    worker.start()
    wait(blocked)
    with pytest.raises(CodexProtocolError) as failure:
        client.request("turn/start", timeout=0.02)
    assert failure.value.kind == "not_sent" and not failure.value.written
    release.set()
    wait(finished)
    assert client.request("barrier") == {"ok": True}
    assert not any(m.get("method") == "turn/start" for m in process.messages)
    worker.join(2)


def test_structured_rejection_and_safe_error_detail(connected):
    client, process = connected

    def write(message):
        if message.get("method") == "turn/steer":
            process.stdout.emit({"id": message["id"], "error": {"code": -32600, "message": "thread not found token=private https://secret.example/a", "data": {"reason": "turnNotActive"}}})
            return True
        return False

    process.on_write = write
    with pytest.raises(CodexProtocolError) as failure:
        client.request("turn/steer")
    assert failure.value.definitive_rejection and not failure.value.acceptance_unknown
    assert failure.value.rpc_code == -32600
    assert failure.value.rpc_data == {"reason": "turnNotActive"}
    assert "thread not found" in str(failure.value)
    assert "private" not in str(failure.value) and "secret.example" not in str(failure.value)


def test_duplicate_server_request_is_executed_and_answered_once(connected):
    client, process = connected
    calls, observed = [], threading.Event()
    client.request_handler = lambda method, params: calls.append((method, params)) or {"ok": True}
    client.notification_handler = lambda _method, _params: observed.set()
    message = {"id": 700, "method": "tool/send", "params": {"threadId": "a"}}
    process.stdout.emit(message)
    process.stdout.emit(message)
    wait(process.response_events.setdefault(700, threading.Event()))
    process.stdout.emit(message)
    process.stdout.emit({"method": "barrier", "params": {"threadId": "a"}})
    wait(observed)
    assert len(calls) == 1
    assert len([m for m in process.messages if m.get("id") == 700]) == 1


def test_server_request_limit_rejects_without_blocking_reader(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], request_limit=1)
    entered, release = threading.Event(), threading.Event()
    client.request_handler = lambda _method, _params: (entered.set(), wait(release), {"ok": True})[-1]
    client.start()
    try:
        process.stdout.emit({"id": 800, "method": "menu", "params": {}})
        wait(entered)
        process.stdout.emit({"id": 801, "method": "send", "params": {}})
        wait(process.response_events.setdefault(801, threading.Event()))
        rejected = next(m for m in process.messages if m.get("id") == 801)
        assert rejected["error"]["code"] == -32001
        assert client.request("ping") == {"ok": True}
        release.set()
        wait(process.response_events.setdefault(800, threading.Event()))
    finally:
        release.set()
        client.stop()


def test_notification_overflow_is_explicit_process_loss(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], event_limit=1, event_workers=1, event_drain_timeout=0.02)
    entered, release, lost = [threading.Event() for _ in range(3)]
    reasons = []

    def event(method, params, _context):
        if method == "transport/lost":
            reasons.append(params)
            lost.set()
        else:
            entered.set()
            wait(release)

    client.event_handler = event
    client.start()
    try:
        for n in range(3):
            process.stdout.emit({"method": "item/completed", "params": {"threadId": "a", "n": n}})
            if n == 0:
                wait(entered)
        wait(lost)
        assert reasons == [{"reason": "drain_incomplete", "cause": "event_overflow", "events_drained": False}]
        assert client.draining and not client.running()
    finally:
        release.set()
        client.stop()


@pytest.mark.parametrize("raw,reason", [("not json\n", "invalid_json"), ("[]\n", "invalid_envelope"), ("x" * 600, "frame_too_large")])
def test_invalid_inbound_frames_fail_closed(monkeypatch, tmp_path, raw, reason):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], max_frame_bytes=512)
    observed, reasons = threading.Event(), []
    client.event_handler = lambda method, params, _context: (reasons.append(params["reason"]), observed.set()) if method == "transport/lost" else None
    client.start()
    try:
        process.stdout.lines.put(raw)
        wait(observed)
        assert reasons == [reason]
    finally:
        client.stop()


def test_oversized_outbound_is_not_sent_and_keeps_process_available(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], max_frame_bytes=512)
    client.start()
    try:
        with pytest.raises(CodexProtocolError) as failure:
            client.request("turn/start", {"input": "x" * 600})
        assert failure.value.kind == "not_sent"
        assert client.running()
        assert client.request("ping") == {"ok": True}
    finally:
        client.stop()


def test_server_requests_without_handler_fail_and_handler_exception_is_sanitized(connected):
    client, process = connected
    process.stdout.emit({"id": 901, "method": "tool", "params": {}})
    wait(process.response_events.setdefault(901, threading.Event()))
    assert next(m for m in process.messages if m.get("id") == 901)["error"]["code"] == -32000

    def bad_handler(_method, _params):
        raise ValueError("secret=never-expose-me")

    client.request_handler = bad_handler
    process.stdout.emit({"id": 902, "method": "tool", "params": {}})
    wait(process.response_events.setdefault(902, threading.Event()))
    assert "never-expose-me" not in json.dumps(process.messages)


def test_restart_changes_epoch_and_old_runtime_cannot_complete_new_request(monkeypatch, tmp_path):
    first, second = Process(), Process()
    client = configure(monkeypatch, tmp_path, [first, second])
    client.start()
    first_epoch, old_runtime = client.epoch, client._runtime
    client.stop()
    client.start()
    try:
        assert client.epoch != first_epoch
        assert old_runtime.closed.is_set()
        assert client.wait_events(first_epoch, timeout=0.1)
        assert client.request("thread/resume") == {"ok": True}
        assert second.messages[0]["method"] == "initialize"
    finally:
        client.stop()


def test_stderr_does_not_store_raw_diagnostic_payload(connected):
    client, process = connected
    process.stderr.lines.put("private user content token=abc\n")
    process.stderr.close()
    client.stderr_reader.join(2)
    assert client.last_error == "Codex App Server emitiu diagnóstico em stderr"


@pytest.mark.parametrize("options", [{"event_limit": 0}, {"request_limit": 0}, {"event_workers": 0}, {"request_workers": 0}, {"max_frame_bytes": 0}, {"event_limit": None}, {"request_limit": True}, {"request_workers": 2.5}, {"max_frame_bytes": float("inf")}])
def test_invalid_transport_limits_are_rejected(tmp_path, options):
    with pytest.raises(ValueError, match="limits"):
        CodexAppServer(SimpleNamespace(root=tmp_path), **options)


def test_launch_configuration_failure_is_not_sent(monkeypatch, tmp_path):
    def broken(_root):
        raise transport.CodexExecutionError("invalid launch")

    monkeypatch.setattr(transport.CodexLaunch, "from_system", broken)
    client = CodexAppServer(SimpleNamespace(root=tmp_path))
    with pytest.raises(CodexProtocolError) as failure:
        client.request("thread/start")
    assert failure.value.kind == "not_sent"
    assert client.pending_count == 0
    client.stop()


@pytest.mark.parametrize("message,reason", [
    ({"id": True, "result": {}}, "invalid_response"),
    ({"result": {}}, "invalid_response"),
    ({"id": 1, "result": {}, "error": {}}, "invalid_response"),
    ({"id": False, "method": "tool"}, "invalid_request_id"),
    ({"id": None, "method": "tool"}, "invalid_request_id"),
    ({"unrecognized": "content"}, "invalid_envelope"),
])
def test_malformed_rpc_identity_never_aliases_another_request(connected, message, reason):
    client, process = connected
    seen, reasons = threading.Event(), []
    client.event_handler = lambda method, params, _context: (reasons.append(params["reason"]), seen.set()) if method == "transport/lost" else None
    process.stdout.emit(message)
    wait(seen)
    assert reasons == [reason]


def test_concurrent_start_uses_one_process_and_one_handshake(monkeypatch, tmp_path):
    process = Process()
    handshake, first_done, second_done = [threading.Event() for _ in range(3)]

    def write(message):
        if message.get("method") == "initialize":
            handshake.set()
            return True
        return False

    process.on_write = write
    client = configure(monkeypatch, tmp_path, [process])
    errors = []

    def run(done):
        try:
            assert client.request("thread/read") == {"ok": True}
        except Exception as error:
            errors.append(error)
        finally:
            done.set()

    first = threading.Thread(target=run, args=(first_done,))
    second = threading.Thread(target=run, args=(second_done,))
    first.start()
    wait(handshake)
    second.start()
    process.stdout.emit({"id": process.messages[0]["id"], "result": {}})
    try:
        wait(first_done)
        wait(second_done)
        assert errors == []
        assert len([m for m in process.messages if m.get("method") == "initialize"]) == 1
    finally:
        client.stop()
        first.join(2)
        second.join(2)


def test_client_pending_limit_rejects_before_writing(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], request_limit=1)
    client.start()
    written, completed = threading.Event(), threading.Event()
    values = []

    def write(message):
        if message.get("method") == "slow":
            written.set()
            return True
        return False

    process.on_write = write
    worker = threading.Thread(target=lambda: (values.append(client.request("slow")), completed.set()))
    worker.start()
    wait(written)
    try:
        with pytest.raises(CodexProtocolError) as failure:
            client.request("turn/start")
        assert failure.value.kind == "not_sent"
        assert not any(m.get("method") == "turn/start" for m in process.messages)
        slow = next(m for m in process.messages if m.get("method") == "slow")
        process.stdout.emit({"id": slow["id"], "result": {"finished": True}})
        wait(completed)
        assert values == [{"finished": True}]
    finally:
        client.stop()
        worker.join(2)


def test_write_failure_is_unknown_and_does_not_retry(connected):
    client, process = connected

    def write(message):
        if message.get("method") == "turn/start":
            raise BrokenPipeError("password=private")
        return False

    process.on_write = write
    with pytest.raises(CodexProtocolError) as failure:
        client.request("turn/start")
    assert failure.value.acceptance_unknown and failure.value.written
    assert "private" not in str(failure.value)
    assert len([m for m in process.messages if m.get("method") == "turn/start"]) == 1


def test_initialization_rejection_is_not_hidden(monkeypatch, tmp_path):
    process = Process()

    def write(message):
        if message.get("method") == "initialize":
            process.stdout.emit({"id": message["id"], "error": {"code": -32600, "message": "unsupported capability"}})
            return True
        return False

    process.on_write = write
    client = configure(monkeypatch, tmp_path, [process])
    with pytest.raises(CodexProtocolError) as failure:
        client.start()
    assert failure.value.kind == "rejected"
    assert client.state == "lost"
    assert not any(m.get("method") == "initialized" for m in process.messages)
    client.stop()


def test_event_handler_failure_triggers_explicit_recovery(connected):
    client, process = connected
    lost = threading.Event()
    reasons = []

    def event(method, params, _context):
        if method == "transport/lost":
            reasons.append(params["reason"])
            lost.set()
        else:
            raise ValueError("user input should not be logged")

    client.event_handler = event
    process.stdout.emit({"method": "item/completed", "params": {"threadId": "a"}})
    wait(lost)
    assert reasons == ["event_handler_failed"]
    assert "user input" not in client.last_error


def test_conflicting_reuse_of_server_request_id_fails_closed(connected):
    client, process = connected
    seen, reasons, calls = threading.Event(), [], []
    client.request_handler = lambda method, params: calls.append((method, params)) or {}
    client.event_handler = lambda method, params, _context: (reasons.append(params["reason"]), seen.set()) if method == "transport/lost" else None
    process.stdout.emit({"id": 903, "method": "tool/send", "params": {"threadId": "a", "text": "first"}})
    wait(process.response_events.setdefault(903, threading.Event()))
    process.stdout.emit({"id": 903, "method": "tool/send", "params": {"threadId": "a", "text": "changed"}})
    wait(seen)
    assert reasons == ["request_identity_conflict"]
    assert len(calls) == 1


def test_stop_escalates_a_process_that_does_not_terminate(monkeypatch, tmp_path):
    class Unresponsive(Process):
        killed = False

        def terminate(self):
            pass

        def wait(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired("fake", timeout)
            return self.exit_code

        def kill(self):
            self.killed = True
            super().terminate()

    process = Unresponsive()
    client = configure(monkeypatch, tmp_path, [process])
    client.start()
    client.stop()
    assert process.killed
    assert client.state == "stopped" and client.process is None


def test_native_stdio_process_handshake_unicode_and_eof(monkeypatch, tmp_path):
    """Exercita pipes reais do SO com servidor fictício, sem rede nem Codex autenticado."""
    program = tmp_path / "fake_app_server.py"
    program.write_text('''import json
import sys
for line in sys.stdin:
    message = json.loads(line)
    if message.get("method") == "exit":
        break
    if "id" in message:
        print(json.dumps({"id": message["id"], "result": message.get("params") or {}}, ensure_ascii=False), flush=True)
''', encoding="utf-8")
    environment = {**os.environ, "PYTHONUTF8": "1"}
    launch = SimpleNamespace(app_server_launch=lambda: ([sys.executable, "-B", str(program)], tmp_path, environment))
    monkeypatch.setattr(transport.CodexLaunch, "from_system", lambda _root: launch)
    client = CodexAppServer(SimpleNamespace(root=tmp_path))
    client.start()
    try:
        assert client.request("echo", {"text": "ação e informações"}) == {"text": "ação e informações"}
        with pytest.raises(CodexProtocolError) as failure:
            client.request("exit", timeout=20)
        assert failure.value.kind == "transport_lost" and failure.value.written
    finally:
        client.stop()


def test_lost_waits_for_admitted_events_but_pending_rpc_fails_immediately(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], event_drain_timeout=2)
    entered, release, written, rpc_failed, lost = [threading.Event() for _ in range(5)]
    order, failures = [], []

    def event(method, _params, _context):
        if method == "item/completed":
            entered.set()
            wait(release)
        order.append(method)
        if method == "transport/lost":
            lost.set()

    def write(message):
        if message.get("method") == "slow":
            written.set()
            return True
        return False

    def request():
        try:
            client.request("slow", timeout=20)
        except CodexProtocolError as error:
            failures.append(error)
            rpc_failed.set()

    client.event_handler, process.on_write = event, write
    client.start()
    epoch = client.epoch
    worker = threading.Thread(target=request)
    worker.start()
    wait(written)
    process.stdout.emit({"method": "item/completed", "params": {"threadId": "a"}})
    wait(entered)
    process.stdout.emit({"method": "turn/completed", "params": {"threadId": "a"}})
    process.stdout.close()
    try:
        wait(rpc_failed)
        assert failures[0].kind == "transport_lost"
        assert not client.wait_events(epoch, timeout=0.01)
        assert not lost.is_set() and order == []
        release.set()
        assert client.wait_events(epoch, timeout=2)
        wait(lost)
        assert order == ["item/completed", "turn/completed", "transport/lost"]
    finally:
        release.set()
        client.stop()
        worker.join(2)


def test_blocked_event_handler_has_finite_drain_and_explicit_incomplete_reason(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], event_drain_timeout=0.02)
    entered, release, lost = threading.Event(), threading.Event(), threading.Event()
    reported = []

    def event(method, params, _context):
        if method == "transport/lost":
            reported.append(params)
            lost.set()
        else:
            entered.set()
            wait(release)

    client.event_handler = event
    client.start()
    epoch = client.epoch
    process.stdout.emit({"method": "turn/completed", "params": {"threadId": "a"}})
    wait(entered)
    process.stdout.close()
    try:
        wait(lost)
        assert reported == [{"reason": "drain_incomplete", "cause": "eof", "events_drained": False}]
        assert not client.wait_events(epoch, timeout=0)
        assert not client.running()
        release.set()
        assert client.wait_events(epoch, timeout=2)
    finally:
        release.set()
        client.stop()


def test_frame_in_dispatch_before_process_loss_is_not_orphaned(monkeypatch, tmp_path):
    process = Process()
    client = configure(monkeypatch, tmp_path, [process], event_drain_timeout=2)
    entered, release, lost = threading.Event(), threading.Event(), threading.Event()
    order = []
    original = client._dispatch_event

    def hold_dispatch(runtime, method, params, context):
        if method == "turn/completed":
            entered.set()
            wait(release)
        original(runtime, method, params, context)

    client._dispatch_event = hold_dispatch
    client.event_handler = lambda method, _params, _context: (order.append(method), lost.set() if method == "transport/lost" else None)
    client.start()
    epoch = client.epoch
    process.stdout.emit({"method": "turn/completed", "params": {"threadId": "a"}})
    wait(entered)
    client.stop()
    try:
        assert not client.wait_events(epoch, timeout=0)
        assert not lost.is_set()
        release.set()
        assert client.wait_events(epoch, timeout=2)
        wait(lost)
        assert order == ["turn/completed", "transport/lost"]
    finally:
        release.set()
        client.stop()


def test_wait_events_unknown_epoch_does_not_claim_proof_of_drain(connected):
    client, _process = connected
    assert not client.wait_events("unknown", timeout=0)
    assert client.wait_events(client.epoch, timeout=0.1)


@pytest.mark.parametrize("timeout", [-1, None, True, float("inf"), float("nan")])
def test_event_drain_rejects_unbounded_or_invalid_timeout(connected, timeout):
    client, _process = connected
    with pytest.raises(ValueError, match="timeout"):
        client.wait_events(client.epoch, timeout=timeout)


def test_logout_sends_unit_rpc_without_params(connected):
    """O contrato do App Server distingue params ausente de params={}."""
    client, process = connected
    assert client.request("account/logout", {}) == {"ok": True}
    messages = [item for item in process.messages if item.get("method") == "account/logout"]
    assert len(messages) == 1
    assert "params" not in messages[0]
    assert isinstance(messages[0]["id"], int)
