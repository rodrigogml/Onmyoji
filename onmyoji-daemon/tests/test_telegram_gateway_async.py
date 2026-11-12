"""Fronteiras assíncronas do Gateway completo com portas externas fictícias."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from onmyoji_daemon import telegram_execution
from onmyoji_daemon.codex_app_server import CodexProtocolError
from onmyoji_daemon.telegram import Gateway, Settings
from onmyoji_daemon.telegram_delivery import DeliveryOwner, DeliverySuppressed
from onmyoji_daemon.telegram_persistence import GatewayPersistenceError


class FakeTelegram:
    def __init__(self):
        self.changed = threading.Condition()
        self.sent, self.deleted, self.acks, self.files = [], [], [], []
        self.before_send = None

    def send(self, chat, text, **values):
        if self.before_send:
            self.before_send(chat, text, values)
        with self.changed:
            receipt = {"message_id": len(self.sent) + len(self.files) + 1}
            self.sent.append({"chat": chat, "text": text, "values": values, **receipt})
            self.changed.notify_all()
            return receipt

    def send_file(self, method, chat, path, field, **values):
        with self.changed:
            receipt = {"message_id": len(self.sent) + len(self.files) + 1}
            self.files.append({"method": method, "chat": chat, "path": path, "field": field, "values": values, **receipt})
            self.changed.notify_all()
            return receipt

    def delete(self, chat, message):
        with self.changed:
            self.deleted.append((chat, message))
            self.changed.notify_all()

    def typing(self, *_args, **_kwargs):
        pass

    def answer_callback(self, identifier):
        with self.changed:
            self.acks.append(identifier)
            self.changed.notify_all()

    def wait_text(self, fragment, *, chat=None):
        with self.changed:
            assert self.changed.wait_for(lambda: any(fragment in item["text"] and (chat is None or item["chat"] == chat) for item in self.sent), timeout=3)
            return next(item for item in self.sent if fragment in item["text"] and (chat is None or item["chat"] == chat))


class FakeCodex:
    """Entrega eventos de propósito antes do ACK, como o transporte real permite."""
    def __init__(self):
        self.process_epoch = "integration-epoch"
        self.changed = threading.Condition()
        self.calls, self.threads, self.turns = [], [], {}
        self.timeouts = []
        self.event_handler = self.request_event_handler = None
        self.notification_handler = self.request_handler = None
        self.on_start = self.on_interrupt = self.on_steer = None
        self.alive, self.busy, self.starts, self.stops = True, False, 0, 0
        self.sequence = 0

    def start(self):
        self.starts += 1
        self.alive = True

    def stop(self):
        self.stops += 1
        self.alive = False

    def running(self):
        return self.alive

    def request(self, method, params=None, timeout=45):
        params = json.loads(json.dumps(params or {}))
        with self.changed:
            self.calls.append((method, params))
            self.timeouts.append((method, timeout))
            if method == "thread/start":
                thread = "thread-" + str(len(self.threads) + 1)
                self.threads.append(thread)
                self.changed.notify_all()
                return {"thread": {"id": thread}}
            if method == "thread/resume":
                self.changed.notify_all()
                return {"thread": {"id": params["threadId"]}}
            if method == "turn/start":
                thread = params["threadId"]
                turn = "turn-" + str(sum(name == "turn/start" for name, _ in self.calls))
                self.turns[thread] = turn
            else:
                turn = self.turns.get(params.get("threadId"))
            self.changed.notify_all()
        if method == "turn/start":
            self.emit("turn/started", params["threadId"], turn, turn={"id": turn, "status": "inProgress"})
            if self.on_start:
                self.on_start(params, turn)
            return {"turn": {"id": turn, "status": "inProgress"}}
        if method == "turn/steer":
            assert params["expectedTurnId"] == turn
            if self.on_steer:
                self.on_steer(params, turn)
            return {"turnId": turn}
        if method == "turn/interrupt":
            if self.on_interrupt:
                self.on_interrupt(params)
            return {}
        if method == "thread/read":
            return {"thread": {"id": params["threadId"], "turns": []}}
        return {}

    def emit(self, method, thread, turn_id=None, **params):
        self.sequence += 1
        payload = {"threadId": thread, **params}
        if turn_id:
            payload["turnId"] = turn_id
        assert self.event_handler is not None
        self.event_handler(method, payload, {"process_epoch": self.process_epoch, "sequence": self.sequence})

    def item(self, thread, text, *, phase="final_answer", identifier="final"):
        self.emit("item/completed", thread, self.turns[thread], item={"id": identifier, "type": "agentMessage", "phase": phase, "text": text})

    def complete(self, thread, status="completed"):
        self.emit("turn/completed", thread, self.turns[thread], turn={"id": self.turns[thread], "status": status})

    def wait_calls(self, method, count=1):
        with self.changed:
            assert self.changed.wait_for(lambda: sum(name == method for name, _ in self.calls) >= count, timeout=3), (method, self.calls)
            return [params for name, params in self.calls if name == method]


@pytest.fixture
def lab(tmp_path, monkeypatch):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    config = root / "configs"
    config.mkdir(parents=True)
    workspace.mkdir()
    (config / "onmyoji-system.toml").write_text('[codex]\nexecutable="codex"\nmodel="probe"\nproject_directory=' + json.dumps(workspace.as_posix()) + '\n', encoding="utf-8")
    daemon = config / "daemon"
    daemon.mkdir()
    (daemon / "daemon.toml").write_text('[app_server]\nenabled=true\n', encoding="utf-8")
    data = daemon / "services" / "telegram"
    data.mkdir(parents=True)
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="fake"\ntoken_entry="fake"\n[agent]\nmax_parallel_conversations=2\nturn_timeout_seconds=30\n', encoding="utf-8")
    instance = Gateway(Settings.load(root, data))
    instance.api, instance.app_server = FakeTelegram(), FakeCodex()
    instance.contacts = SimpleNamespace(owners=lambda: {9, 10}, observe_owner_language=lambda _user: False)
    capabilities = SimpleNamespace(supports=lambda feature: feature in {"steering", "subagents", "post_turn"}, input_supported=lambda _kind: True)
    instance.capabilities = capabilities
    launch = SimpleNamespace(synchronize_shikigami_skills=lambda: False, synchronize_shikigami_agents=lambda: False, developer_instructions=lambda: "Instruções da instância")
    monkeypatch.setattr(telegram_execution.CodexLaunch, "from_system", lambda _root: launch)
    monkeypatch.setattr(telegram_execution.CapabilitySet, "from_instance", lambda *_args, **_kwargs: capabilities)
    monkeypatch.setattr(telegram_execution, "mcp_reconciliation_pending", lambda _root: False)
    value = SimpleNamespace(gateway=instance, api=instance.api, client=instance.app_server, tmp=tmp_path)
    yield value
    instance.stop_event.set()
    watcher = getattr(value, "notification_watcher", None)
    if watcher:
        watcher.join(timeout=3)
    with instance.app_server_lock:
        active = list(instance.app_turns.values())
    for turn in active:
        if not turn.completed.is_set():
            value.client.emit("turn/completed", turn.thread_id, turn.turn_id, turn={"id": turn.turn_id, "status": "interrupted"})
    with instance.workers_changed:
        instance.workers_changed.notify_all()
    for slot in list(instance._slots.values()):
        slot.done.wait(3)
    teardown_deadline = time.monotonic() + 3
    teardown_tick = threading.Event()
    while True:
        with instance.workers_changed:
            workers_pending = bool(instance.workers)
        if not workers_pending:
            break
        assert time.monotonic() < teardown_deadline, "Workers devem encerrar antes de liberar o lease do laboratório"
        teardown_tick.wait(0.001)
    instance.deliveries._executor.shutdown(wait=True)
    instance.store.release_lease()


def submit(lab, text, *, conversation="9", route="auto", wake=True):
    gateway = lab.gateway
    identifier = gateway.store.enqueue(conversation, gateway._generation(conversation), text, [], {"source_trust": "OWNER", "conversation_kind": "private"}, route=route)
    if wake:
        gateway._wake_worker(conversation)
    return identifier


def active_slot(lab, conversation="9"):
    with lab.gateway.app_server_lock:
        return lab.gateway._slots[conversation]


def finish(lab, thread, text="Resposta final"):
    with lab.gateway.app_server_lock:
        slot = next(slot for slot in lab.gateway._slots.values() if slot.active and slot.active.thread_id == thread)
    lab.client.item(thread, text)
    lab.client.complete(thread)
    assert slot.done.wait(3)
    return slot


def test_gateway_accepts_events_and_completion_before_start_ack(lab):
    def before_ack(params, _turn):
        lab.client.item(params["threadId"], "Concluído antes do ACK")
        lab.client.complete(params["threadId"])

    lab.client.on_start = before_ack
    inbox = submit(lab, "primeira")
    lab.api.wait_text("Concluído antes do ACK")
    with lab.gateway.workers_changed:
        assert lab.gateway.workers_changed.wait_for(lambda: not lab.gateway.workers, timeout=3)
    assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("done",)
    assert lab.gateway.database.execute("SELECT state FROM input_operations WHERE inbox_id=?", (inbox,)).fetchall() == [("accepted",)]
    assert lab.gateway.database.execute("SELECT state,result FROM execution_turns").fetchone() == ("terminal", "completed")
    assert len(lab.client.wait_calls("turn/start")) == 1
    assert lab.client.stops == 0 and lab.client.running()


def test_two_steering_inputs_share_turn_and_keep_thoughts_until_final(lab):
    lab.gateway.settings = replace(lab.gateway.settings, busy_input_policy="steer")
    lab.gateway.work = threading.BoundedSemaphore(1)
    first = submit(lab, "trabalho longo")
    start = lab.client.wait_calls("turn/start")[0]
    thread = start["threadId"]
    lab.client.item(thread, "Estou verificando", phase="commentary", identifier="thought")
    thought = lab.api.wait_text("Estou verificando")
    second = submit(lab, "corrigir escopo")
    third = submit(lab, "incluir verificação")
    steering = lab.client.wait_calls("turn/steer", 2)
    assert [json.loads(item["input"][0]["text"])["telegram_turn"]["content"] for item in steering] == ["corrigir escopo", "incluir verificação"]
    assert all(item["expectedTurnId"] == lab.client.turns[thread] for item in steering)
    assert not lab.api.deleted
    assert len(lab.client.wait_calls("turn/start")) == 1
    finish(lab, thread, "Consolidação final")
    assert (9, thought["message_id"]) in lab.api.deleted
    assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE id IN (?,?,?)", (first, second, third)).fetchall() == [("done",), ("done",), ("done",)]


def test_default_queue_starts_next_turn_on_same_thread_and_process(lab):
    submit(lab, "primeira")
    first = lab.client.wait_calls("turn/start")[0]
    second_id = submit(lab, "segunda")
    assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (second_id,)).fetchone() == ("pending",)
    assert not any(method == "turn/steer" for method, _ in lab.client.calls)
    finish(lab, first["threadId"], "Primeira final")
    second = lab.client.wait_calls("turn/start", 2)[1]
    assert second["threadId"] == first["threadId"]
    finish(lab, second["threadId"], "Segunda final")
    assert len(lab.client.threads) == 1
    assert lab.client.stops == 0 and lab.client.running()


def test_final_item_does_not_finish_turn_or_clean_thoughts(lab):
    submit(lab, "aguardar conclusão protocolar")
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    slot = active_slot(lab)
    lab.client.item(thread, "Comentário anterior", phase="commentary", identifier="thought")
    lab.client.item(thread, "Final ainda sem completed")
    assert not slot.active.completed.is_set() and not slot.done.is_set()
    assert not any("Final ainda sem completed" in item["text"] for item in lab.api.sent)
    assert lab.api.deleted == []
    lab.client.complete(thread)
    assert slot.done.wait(3)
    lab.api.wait_text("Final ainda sem completed")
    assert lab.api.deleted


def test_failed_turn_delivers_text_as_partial_and_keeps_failed_status(lab):
    def failed(params, _turn):
        lab.client.item(params["threadId"], "Resultado incompleto")
        lab.client.complete(params["threadId"], "failed")

    lab.client.on_start = failed
    submit(lab, "falhar depois de produzir texto")
    sent = lab.api.wait_text("Resposta parcial")
    assert "Resultado incompleto" in sent["text"]
    assert lab.gateway.database.execute("SELECT state,result FROM execution_turns").fetchone() == ("terminal", "failed")


def test_timeout_without_terminal_blocks_replay_and_keeps_thoughts(lab):
    lab.gateway.settings = replace(lab.gateway.settings, turn_timeout=0.02, interrupt_grace_seconds=0.02)
    started = threading.Event()

    def progress(params, _turn):
        lab.client.item(params["threadId"], "Pensamento que deve permanecer", phase="commentary", identifier="thought")
        started.set()

    lab.client.on_start = progress
    inbox = submit(lab, "timeout controlado")
    assert started.wait(3)
    slot = active_slot(lab)
    lab.client.wait_calls("turn/interrupt")
    assert slot.done.wait(3)
    assert lab.gateway.store.blocked("9")
    assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone() == ("accepted",)
    assert lab.gateway.database.execute("SELECT state FROM execution_turns").fetchone() == ("uncertain",)
    assert not lab.api.deleted
    assert len(lab.client.wait_calls("turn/start")) == 1


def test_new_generation_waits_for_old_terminal_and_suppresses_old_output(lab):
    submit(lab, "antiga")
    old_thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    old = active_slot(lab)
    resource = lab.gateway.staging_root / "old" / "attachment" / "file.txt"
    resource.parent.mkdir(parents=True)
    resource.write_text("conteúdo", encoding="utf-8")
    lab.gateway.store.retain(str(resource), old.identifier)
    violations = []

    def watch_new(_params, _turn):
        if not old.active.completed.is_set() or not old.done.is_set():
            violations.append("new model execution before old terminal")

    lab.gateway._new_conversation("9")
    lab.client.wait_calls("turn/interrupt")
    assert resource.exists() and old.active.suppressed and not old.active.completed.is_set()
    lab.client.on_start = watch_new
    submit(lab, "nova geração")
    lab.client.item(old_thread, "Resposta antiga tardia")
    lab.client.complete(old_thread, "interrupted")
    new_thread = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
    assert old.done.wait(3)
    assert not resource.exists()
    assert not violations and new_thread != old_thread
    finish(lab, new_thread, "Resposta nova")
    assert not any("Resposta antiga tardia" in item["text"] for item in lab.api.sent)


class SessionRegistry(dict):
    def __init__(self):
        super().__init__()
        self.ready = threading.Event()

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.ready.set()


def test_menu_wait_does_not_block_another_conversation_event_or_turn(lab):
    submit(lab, "abrir menu", conversation="9")
    first = lab.client.wait_calls("turn/start")[0]["threadId"]
    registry = SessionRegistry()
    lab.gateway.menu_sessions = registry
    outcomes = []

    def menu():
        outcomes.append(lab.gateway._app_request_event("item/tool/call", {"threadId": first, "turnId": lab.client.turns[first], "namespace": "telegram_gateway", "tool": "ask_menu", "arguments": {"question": "Escolha", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}}, {"process_epoch": lab.client.process_epoch, "rpc_id": 500}))

    worker = threading.Thread(target=menu)
    worker.start()
    assert registry.ready.wait(3)
    submit(lab, "outra conversa", conversation="10")
    other = lab.client.wait_calls("turn/start", 2)[1]["threadId"]
    lab.client.item(other, "Progresso independente", phase="commentary", identifier="other-thought")
    lab.api.wait_text("Progresso independente", chat=10)
    finish(lab, other, "Outra concluída")
    token, session = next(iter(registry.items()))
    lab.gateway._callback({"id": "click", "from": {"id": 9}, "message": {"chat": {"id": 9, "type": "private"}, "message_id": session.message_id}, "data": f"ag:{token}:0"})
    worker.join(3)
    assert not worker.is_alive() and outcomes[0]["success"]
    finish(lab, first, "Menu concluído")


def seed_completed_parent(lab):
    generation = lab.gateway._generation("9")
    with lab.gateway.database_lock, lab.gateway.database:
        lab.gateway.database.execute("INSERT INTO execution_turns(execution_id,conversation_id,generation,process_epoch,thread_id,turn_id,state,result,created_at,completed_at) VALUES ('parent-exec','9',?,'integration-epoch','parent-thread','parent-turn','terminal','completed',0,1)", (generation,))
        lab.gateway.database.execute("UPDATE conversations SET codex_thread_id='parent-thread' WHERE chat_id='9'")
    return generation


def test_e9_continuation_is_tool_data_and_pending_user_input_has_priority(lab, monkeypatch):
    lab.gateway.settings = replace(lab.gateway.settings, post_turn_results=True)
    generation = seed_completed_parent(lab)
    wake = lab.gateway._wake_worker
    monkeypatch.setattr(lab.gateway, "_wake_worker", lambda _conversation: None)
    data = {"status": "completed", "text": "Resultado do filho", "provenance": {"kind": "subagent_result"}}
    lab.gateway._enqueue_related_result("9", generation, "parent-exec", "work", 1, data, "stable-result")
    lab.gateway._enqueue_related_result("9", generation, "parent-exec", "work", 1, data, "stable-result")
    submit(lab, "Mensagem real prioritária", wake=False)
    monkeypatch.setattr(lab.gateway, "_wake_worker", wake)
    wake("9")
    first = lab.client.wait_calls("turn/start")[0]
    assert "toolOutput" not in first
    assert json.loads(first["input"][0]["text"])["telegram_turn"]["content"] == "Mensagem real prioritária"
    finish(lab, first["threadId"], "Usuário atendido")
    second = lab.client.wait_calls("turn/start", 2)[1]
    assert second["input"] == [] and second["toolOutput"]["name"] == "onmyoji_related_work"
    output = json.loads(second["toolOutput"]["output"])
    assert output["authority"] == "data_only" and output["result"]["text"] == "Resultado do filho"
    row = lab.gateway.database.execute("SELECT turn_context,generation FROM inbox_items WHERE source_key='stable-result'").fetchone()
    assert row[1] == generation and json.loads(row[0])["source_trust"] == "SYSTEM_EVENT"
    assert lab.gateway.database.execute("SELECT COUNT(*) FROM inbox_items WHERE source_key='stable-result'").fetchone()[0] == 1
    finish(lab, second["threadId"], "Resultado consolidado pelo principal")


def test_e9_old_generation_result_cannot_enqueue(lab, monkeypatch):
    generation = seed_completed_parent(lab)
    monkeypatch.setattr(lab.gateway, "_wake_worker", lambda _conversation: None)
    lab.gateway._new_conversation("9")
    with pytest.raises(DeliverySuppressed):
        lab.gateway._enqueue_related_result("9", generation, "parent-exec", "work", 1, {"text": "antigo"}, "old-result")
    assert not lab.gateway.database.execute("SELECT 1 FROM inbox_items WHERE source_key='old-result'").fetchone()


def test_terminal_output_recovers_without_model_execution_and_cleans_own_thought(lab):
    generation = lab.gateway._generation("9")
    inbox = submit(lab, "pedido persistido", wake=False)
    execution = lab.gateway.store.reserve("9", generation, inbox, 30)
    lab.gateway.store.bind(execution, lab.client.process_epoch, "saved-thread", "saved-turn")
    operation = lab.gateway.store.operation(inbox, execution, "turn/start", None)
    lab.gateway.store.operation_result(operation, "accepted")
    owner = DeliveryOwner("9", generation, execution, "saved-thread", "saved-turn")
    thought = lab.gateway.deliveries.text(owner, "saved-thought", "thought", "thought", lab.api, 9, "💭 Progresso persistido")[0]
    lab.gateway.store.save_terminal_output(execution, "completed", "Final reconstruído", "final", "text", True)
    lab.gateway._restore_terminal_outputs()
    lab.api.wait_text("Final reconstruído")
    assert (9, thought["message_id"]) in lab.api.deleted
    assert not any(method == "turn/start" for method, _ in lab.client.calls)
    lab.gateway._restore_terminal_outputs()
    assert sum("Final reconstruído" in item["text"] for item in lab.api.sent) == 1


def test_terminal_delivery_waits_for_late_thought_send_then_cleans_it(lab):
    submit(lab, "pensamento lento")
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    slot = active_slot(lab)
    entered, release = threading.Event(), threading.Event()

    def slow(_chat, text, _values):
        if "Pensamento lento" in text:
            entered.set()
            assert release.wait(3)

    lab.api.before_send = slow
    thought_worker = threading.Thread(target=lambda: lab.client.item(thread, "Pensamento lento", phase="commentary", identifier="slow-thought"))
    thought_worker.start()
    assert entered.wait(3)
    lab.client.item(thread, "Final posterior")
    lab.client.complete(thread)
    assert not slot.done.is_set() and not lab.api.deleted
    release.set()
    thought_worker.join(3)
    assert slot.done.wait(3)
    thought = lab.api.wait_text("Pensamento lento")
    lab.api.wait_text("Final posterior")
    assert (9, thought["message_id"]) in lab.api.deleted


def test_all_integration_activity_stays_inside_root_tmp(lab):
    project_root = Path(__file__).resolve().parents[2]
    assert lab.tmp.resolve().is_relative_to(project_root / "tmp")
    assert lab.gateway.settings.project.resolve().is_relative_to(project_root / "tmp")
    assert lab.gateway.settings.data_dir.resolve().is_relative_to(project_root / "tmp")


def test_steer_ack_after_turn_finished_closes_input_without_starting_again(lab):
    lab.gateway.settings = replace(lab.gateway.settings, busy_input_policy="steer")
    submit(lab, "principal")
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    slot = active_slot(lab)

    def finish_before_ack(params, _turn):
        lab.client.item(params["threadId"], "Resultado com a correção")
        lab.client.complete(params["threadId"])
        assert slot.done.wait(3), "A conclusão deve poder anteceder o ACK de steering"

    lab.client.on_steer = finish_before_ack
    correction = submit(lab, "correção aceita no turno")
    lab.client.wait_calls("turn/steer")
    with lab.gateway.workers_changed:
        assert lab.gateway.workers_changed.wait_for(lambda: not lab.gateway.workers, timeout=3)
    assert lab.gateway.database.execute("SELECT state,execution_id FROM inbox_items WHERE id=?", (correction,)).fetchone() == ("done", slot.identifier)
    assert len(lab.client.wait_calls("turn/start")) == 1


def test_confirmed_steering_rejection_after_finish_routes_input_once_to_next_turn(lab):
    lab.gateway.settings = replace(lab.gateway.settings, busy_input_policy="steer")
    submit(lab, "principal")
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    slot = active_slot(lab)

    def reject_after_finish(params, _turn):
        lab.client.item(params["threadId"], "Primeiro concluído")
        lab.client.complete(params["threadId"])
        assert slot.done.wait(3)
        raise CodexProtocolError("No active turn", kind="rejected", rpc_code=-32600)

    lab.client.on_steer = reject_after_finish
    correction = submit(lab, "correção não aceita pelo primeiro")
    second = lab.client.wait_calls("turn/start", 2)[1]
    assert second["threadId"] == thread
    assert json.loads(second["input"][0]["text"])["telegram_turn"]["content"] == "correção não aceita pelo primeiro"
    finish(lab, thread, "Correção processada no próximo")
    assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (correction,)).fetchone() == ("done",)
    assert len(lab.client.wait_calls("turn/steer")) == 1
    assert len(lab.client.wait_calls("turn/start", 2)) == 2


def test_cancel_during_tts_sends_cancel_notice_instead_of_new_audio(lab, monkeypatch):
    lab.gateway.settings = replace(lab.gateway.settings, voice_enabled=True)
    lab.gateway._set_reply_mode("9", "audio")
    entered, release = threading.Event(), threading.Event()

    def synthesize(_text, path):
        entered.set()
        assert release.wait(3)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic audio")

    monkeypatch.setattr(lab.gateway, "_tts", synthesize)
    submit(lab, "resposta em voz")
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    slot = active_slot(lab)
    lab.client.item(thread, "Conteúdo normal que não deve ser enviado depois de cancelar")
    lab.client.complete(thread)
    assert entered.wait(3)
    try:
        assert lab.gateway._cancel_conversation("9")
    finally:
        release.set()
    assert slot.done.wait(3)
    lab.api.wait_text("O turno foi cancelado")
    assert not lab.api.files
    assert not any("Conteúdo normal" in message["text"] for message in lab.api.sent)
    assert lab.gateway.database.execute("SELECT kind,item_id,state FROM gateway_deliveries WHERE execution_id=? AND item_id='cancelled_notice'", (slot.identifier,)).fetchall() == [("terminal", "cancelled_notice", "sent")]


def persisted_terminal(lab, *, text="RESPOSTA_ANTIGA", mode="audio"):
    generation = lab.gateway._generation("9")
    inbox = submit(lab, "recuperar saída", wake=False)
    execution = lab.gateway.store.reserve("9", generation, inbox, 30)
    lab.gateway.store.bind(execution, lab.client.process_epoch, "saved-thread", "saved-turn")
    operation = lab.gateway.store.operation(inbox, execution, "turn/start", None)
    lab.gateway.store.operation_result(operation, "accepted")
    owner = DeliveryOwner("9", generation, execution, "saved-thread", "saved-turn")
    lab.gateway.store.save_terminal_output(execution, "completed", text, "original-final", mode, True)
    return execution, owner


def test_cancelled_terminal_recovery_hydrates_flag_and_uses_distinct_notice_key(lab, monkeypatch):
    execution, owner = persisted_terminal(lab)
    old_final = lab.gateway.deliveries.plan_text(owner, f"{execution}:final:original-final", "final", "original-final", 9, "RESPOSTA_ANTIGA")[0]
    old_thought = lab.gateway.deliveries.plan_text(owner, f"{execution}:thought:before-crash", "thought", "before-crash", 9, "PROGRESSO_ANTIGO")[0]
    with lab.gateway.database_lock, lab.gateway.database:
        lab.gateway.database.execute("UPDATE execution_turns SET user_cancelled=1 WHERE execution_id=?", (execution,))
    monkeypatch.setattr(lab.gateway, "_tts", lambda *_args: pytest.fail("Cancelamento persistido não deve sintetizar áudio antigo"))
    lab.gateway._restore_terminal_outputs()
    lab.api.wait_text("O turno foi cancelado")
    assert not lab.api.files and not lab.client.calls
    assert not any("RESPOSTA_ANTIGA" in message["text"] or "PROGRESSO_ANTIGO" in message["text"] for message in lab.api.sent)
    with lab.gateway.database_lock:
        assert lab.gateway.database.execute("SELECT state FROM gateway_deliveries WHERE delivery_key IN (?,?) ORDER BY delivery_key", (old_final, old_thought)).fetchall() == [("suppressed",), ("suppressed",)]
    lab.gateway._restore_terminal_outputs()
    assert len(lab.api.sent) == 1


def test_cancel_suppresses_completed_child_result_that_is_still_retrying_enqueue(lab):
    generation = seed_completed_parent(lab)
    with lab.gateway.database_lock, lab.gateway.database:
        lab.gateway.database.execute("INSERT INTO related_work(work_id,execution_id,parent_thread_id,child_thread_id,state,result_ref,result_hash,result_version,delivered_version,updated_at) VALUES ('deferred','parent-exec','parent-thread','child','completed','{}','hash',1,0,100)")
        lab.gateway.database.execute("INSERT INTO related_work_continuations(continuation_key,work_id,attempt,result_version,state,enqueue_attempts,updated_at) VALUES ('continuation','deferred',1,1,'uncertain',1,100)")
    assert lab.gateway._cancel_conversation("9")
    with lab.gateway.database_lock:
        assert lab.gateway.database.execute("SELECT user_cancelled FROM execution_turns WHERE execution_id='parent-exec'").fetchone() == (1,)
        assert lab.gateway.database.execute("SELECT state FROM related_work WHERE work_id='deferred'").fetchone() == ("cancelled",)
        assert lab.gateway.database.execute("SELECT state FROM related_work_continuations WHERE continuation_key='continuation'").fetchone() == ("suppressed",)
    assert lab.gateway.related_work.tick(enabled=True) == 0
    with pytest.raises(DeliverySuppressed):
        lab.gateway._enqueue_related_result("9", generation, "parent-exec", "deferred", 1, {"text": "tardio"}, "late-key")
    assert not lab.client.calls


def test_cancel_suppresses_queued_result_even_after_monitor_marked_it_enqueued(lab, monkeypatch):
    generation = seed_completed_parent(lab)
    monkeypatch.setattr(lab.gateway, "_wake_worker", lambda _conversation: None)
    lab.gateway._enqueue_related_result("9", generation, "parent-exec", "completed-work", 1, {"text": "resultado pronto"}, "queued-result")
    assert lab.gateway._cancel_conversation("9")
    with lab.gateway.database_lock:
        assert lab.gateway.database.execute("SELECT user_cancelled FROM execution_turns WHERE execution_id='parent-exec'").fetchone() == (1,)
        assert lab.gateway.database.execute("SELECT state FROM inbox_items WHERE source_key='queued-result'").fetchone() == ("cancelled",)
    assert not lab.client.calls


def test_cancelled_recovery_does_not_replace_or_repeat_a_final_already_sent(lab):
    execution, owner = persisted_terminal(lab, text="Enviado antes do cancelamento", mode="text")
    lab.gateway.deliveries.text(owner, f"{execution}:final:original-final", "final", "original-final", lab.api, 9, "Enviado antes do cancelamento")
    with lab.gateway.database_lock, lab.gateway.database:
        lab.gateway.database.execute("UPDATE execution_turns SET user_cancelled=1,delivery_state='sent' WHERE execution_id=?", (execution,))
    lab.gateway._restore_terminal_outputs()
    assert [message["text"] for message in lab.api.sent] == ["Enviado antes do cancelamento"]
    assert not lab.client.calls


def test_cancel_during_final_admission_suppresses_normal_answer_before_http(lab):
    entered, release = threading.Event(), threading.Event()
    original = lab.gateway.deliveries.can_send

    def admission(owner, kind):
        if kind == "final":
            entered.set()
            assert release.wait(3)
        return original(owner, kind)

    lab.gateway.deliveries.can_send = admission
    submit(lab, "cancelar antes do envio final")
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    slot = active_slot(lab)
    lab.client.item(thread, "FINAL_NORMAL_NAO_ENVIAR")
    lab.client.complete(thread)
    assert entered.wait(3)
    try:
        assert lab.gateway._cancel_conversation("9")
    finally:
        release.set()
    assert slot.done.wait(3)
    lab.api.wait_text("O turno foi cancelado")
    assert not any("FINAL_NORMAL_NAO_ENVIAR" in message["text"] for message in lab.api.sent)
    with lab.gateway.database_lock:
        assert lab.gateway.database.execute("SELECT kind,state FROM gateway_deliveries WHERE execution_id=? ORDER BY kind", (slot.identifier,)).fetchall() == [("final", "suppressed"), ("terminal", "sent")]


def test_interrupt_ack_consumes_same_grace_without_clearing_thought_or_other_turn(lab, monkeypatch):
    now = [500.0]
    monkeypatch.setattr(telegram_execution, "time", SimpleNamespace(time=time.time, monotonic=lambda: now[0]))
    other_waiting = threading.Event()
    other_thread = []
    remaining = []

    def start(params, _turn):
        active = lab.gateway.app_turns[params["threadId"]]
        if not other_thread:
            other_thread.append(params["threadId"])
            original_wait = active.completed.wait

            def entered_wait(timeout=None):
                other_waiting.set()
                return original_wait(timeout)

            active.completed.wait = entered_wait
        else:
            lab.client.item(params["threadId"], "Pensamento antes da interrupção", phase="commentary", identifier="thought")
            lab.api.wait_text("Pensamento antes da interrupção", chat=9)

    lab.client.on_start = start
    submit(lab, "outra conversa em andamento", conversation="10")
    assert other_waiting.wait(3)
    lab.gateway.settings = replace(lab.gateway.settings, turn_timeout=0.01, interrupt_grace_seconds=0.25)

    def delayed_interrupt(params):
        assert params["threadId"] != other_thread[0]
        active = lab.gateway.app_turns[params["threadId"]]
        original_wait = active.completed.wait

        def observe_wait(timeout=None):
            remaining.append(timeout)
            return original_wait(timeout)

        active.completed.wait = observe_wait
        # O relógio controlado representa um ACK que gastou todo o prazo.
        now[0] += lab.gateway.settings.interrupt_grace_seconds

    lab.client.on_interrupt = delayed_interrupt
    submit(lab, "prazo curto", conversation="9")
    lab.client.wait_calls("turn/start", 2)
    slot = active_slot(lab, "9")
    assert slot.done.wait(3)
    assert remaining and remaining[0] == 0
    assert [timeout for method, timeout in lab.client.timeouts if method == "turn/interrupt"] == [pytest.approx(0.25)]
    assert lab.gateway.store.blocked("9")
    assert not lab.api.deleted
    assert not active_slot(lab, "10").active.completed.is_set()
    finish(lab, other_thread[0], "Outra conversa preservada")


def test_shutdown_keeps_writer_lease_while_an_http_job_can_still_write(lab):
    entered, release = threading.Event(), threading.Event()
    # O laboratório substitui start() para não abrir credenciais ou Telegram;
    # conserva aqui a reserva operacional que start() normalmente adquire.
    lab.gateway.store.acquire_lease(lab.gateway.settings.state_dir / "gateway-writer.lock")

    def http_job():
        entered.set()
        assert release.wait(3)
        lab.gateway.store.checkpoint("still-owned")
        return "confirmed"

    future = lab.gateway.deliveries.submit("9", http_job)
    assert entered.wait(3)
    lab.gateway._shutdown_execution()
    try:
        with pytest.raises(GatewayPersistenceError, match="Outro Gateway"):
            Gateway(lab.gateway.settings)
    finally:
        release.set()
    assert future.result(timeout=3) == "confirmed"
    # Somente o laboratório já drenado libera explicitamente o lease.
    lab.gateway.store.release_lease()
    reopened = Gateway(lab.gateway.settings)
    reopened.store.release_lease()


def test_shutdown_never_starts_reserved_second_slot_and_preserves_unsent_queue(lab, monkeypatch):
    lab.gateway.work = threading.BoundedSemaphore(1)
    lab.gateway.settings = replace(lab.gateway.settings, interrupt_grace_seconds=0.25)
    submit(lab, "primeira ativa", conversation="9")
    first_thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    first = active_slot(lab, "9")
    second_registered = threading.Event()
    original_execute = lab.gateway._execute_inbox

    def execute(slot):
        if slot.conversation_id == "10":
            second_registered.set()
        original_execute(slot)

    monkeypatch.setattr(lab.gateway, "_execute_inbox", execute)
    pending = submit(lab, "segunda ainda não enviada", conversation="10")
    assert not second_registered.is_set()
    assert "10" not in lab.gateway._slots
    lab.client.on_interrupt = lambda params: lab.client.complete(params["threadId"], "interrupted")
    lab.gateway._shutdown_execution()
    assert first.done.wait(3)
    assert len(lab.client.wait_calls("turn/start")) == 1
    with lab.gateway.database_lock:
        assert lab.gateway.database.execute("SELECT state,execution_id FROM inbox_items WHERE id=?", (pending,)).fetchone() == ("pending", None)
        assert lab.gateway.database.execute("SELECT COUNT(*) FROM execution_turns WHERE conversation_id='10'").fetchone() == (0,)
        assert lab.gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == (first_thread,)
        assert lab.gateway.database.execute("SELECT COUNT(*) FROM input_operations WHERE inbox_id=?", (pending,)).fetchone() == (0,)
    lab.gateway.store.recover()
    assert lab.gateway.store.next_input("10", lab.gateway._generation("10"))["id"] == pending
    assert len(lab.client.wait_calls("turn/start")) == 1
    lab.gateway.store.release_lease()
    reopened = Gateway(lab.gateway.settings)
    try:
        reopened.store.recover()
        assert reopened.store.next_input("10", reopened._generation("10"))["id"] == pending
        assert reopened.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == (first_thread,)
        assert len(lab.client.wait_calls("turn/start")) == 1
    finally:
        reopened.store.release_lease()
        reopened.database.close()
