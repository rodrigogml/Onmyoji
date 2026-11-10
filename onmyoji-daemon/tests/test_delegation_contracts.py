"""Fronteiras de autorização, publicação, persistência e recuperação do plano."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import platform
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from onmyoji_daemon import codex_capabilities
from onmyoji_daemon.codex_capabilities import CapabilitySet, REPORT_RELATIVE
from onmyoji_daemon.telegram import AppServerTurn, ConversationExecution, Gateway, Settings
from onmyoji_daemon.telegram_delivery import DeliveryOwner, DeliveryUncertain
from onmyoji_daemon.telegram_persistence import GatewayPersistenceError, GatewayStore, protect_legacy_gateway


@pytest.fixture
def contract_gateway(tmp_path):
    instance_root, workspace = tmp_path / "instance", tmp_path / "workspace"
    configs = instance_root / "configs"
    configs.mkdir(parents=True)
    workspace.mkdir()
    (configs / "onmyoji-system.toml").write_text('[codex]\nexecutable="codex"\nproject_directory=' + json.dumps(workspace.as_posix()) + '\nmodel="probe"\n', encoding="utf-8")
    daemon = configs / "daemon"
    daemon.mkdir()
    (daemon / "daemon.toml").write_text("[app_server]\nenabled=true\n", encoding="utf-8")
    data = daemon / "services" / "telegram"
    data.mkdir(parents=True)
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="fixture"\ntoken_entry="fixture"\n', encoding="utf-8")
    gateway = Gateway(Settings.load(instance_root, data))
    sent, deleted = [], []
    class Api:
        def send(self, chat, text, **values):
            sent.append((chat, text, values))
            return {"message_id": len(sent)}
        def delete(self, chat, message):
            deleted.append((chat, message))
        def typing(self, *args, **kwargs):
            pass
    gateway.api = Api()
    gateway._contract_sent, gateway._contract_deleted = sent, deleted
    yield gateway
    gateway.stop_event.set()
    with gateway.workers_changed:
        gateway.workers_changed.notify_all()
    gateway.store.release_lease()


def reserve_active(gateway):
    generation = gateway._generation("9")
    inbox = gateway.store.enqueue("9", generation, "solicitação fictícia", [], {"source_trust": "OWNER"})
    item = gateway.store.next_input("9", generation)
    execution = gateway.store.reserve("9", generation, inbox, 30)
    gateway.store.bind(execution, "epoch", "thread", "turn")
    operation = gateway.store.operation(inbox, execution, "turn/start", None)
    gateway.store.operation_result(operation, "accepted")
    active = AppServerTurn(9, "thread", threading.Event(), turn_id="turn", conversation_id="9", generation=generation, execution_id=execution, inbox_id=inbox, process_epoch="epoch")
    gateway.app_turns["thread"] = active
    return item, active


def test_unreadable_private_report_does_not_fall_through_to_packaged_support(tmp_path, monkeypatch):
    """Permissão negada no relatório privado conserva revogação fail-closed."""
    private = tmp_path / REPORT_RELATIVE
    private.parent.mkdir(parents=True)
    private.write_text("{}", encoding="utf-8")
    packaged_hash = "9d563ee4e0441ff9d59d612013e0460ddf8e5709735c2d9f6b98b7ef8ca4eb42"
    monkeypatch.setattr(codex_capabilities, "executable_digest", lambda _: packaged_hash)
    original = Path.exists
    def inaccessible(path):
        if path == private:
            raise PermissionError("fixture inaccessible private report")
        return original(path)
    monkeypatch.setattr(Path, "exists", inaccessible)
    capabilities = CapabilitySet.from_instance(tmp_path, "fixture", tmp_path)
    assert not capabilities.supports("steering")
    assert capabilities.reason("steering") == "report_invalid"


def test_inaccessible_instance_root_cannot_enable_or_raise_during_discovery(tmp_path, monkeypatch):
    """Certificação opcional não impede inicialização se a raiz ficou inacessível."""
    original = Path.resolve
    def inaccessible(path, *args, **kwargs):
        if path == tmp_path:
            raise PermissionError("fixture root unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", inaccessible)
    capabilities = CapabilitySet.from_instance(tmp_path, "fixture", tmp_path)
    assert not capabilities.supports("base")
    assert capabilities.reason("base") == "instance_unavailable"


def test_inaccessible_workspace_cannot_fall_through_to_packaged_support(tmp_path, monkeypatch):
    """Sem conhecer o workspace, um certificado distribuído não é autorização."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(codex_capabilities, "executable_digest", lambda _: "9d563ee4e0441ff9d59d612013e0460ddf8e5709735c2d9f6b98b7ef8ca4eb42")
    original = Path.exists
    def inaccessible(path):
        if path == workspace / ".git":
            raise PermissionError("fixture workspace unavailable")
        return original(path)
    monkeypatch.setattr(Path, "exists", inaccessible)
    capabilities = CapabilitySet.from_instance(tmp_path, "fixture", workspace)
    assert not capabilities.supports("steering")
    assert capabilities.reason("steering") == "workspace_unavailable"


def test_inaccessible_packaged_report_is_a_safe_disabled_capability(tmp_path, monkeypatch):
    """Instalação sem permissão de leitura mantém fila em vez de lançar erro."""
    binary_hash = "9d563ee4e0441ff9d59d612013e0460ddf8e5709735c2d9f6b98b7ef8ca4eb42"
    monkeypatch.setattr(codex_capabilities, "executable_digest", lambda _: binary_hash)
    original = Path.is_file
    def inaccessible(path):
        if path.name == binary_hash + ".json":
            raise PermissionError("fixture resource unavailable")
        return original(path)
    monkeypatch.setattr(Path, "is_file", inaccessible)
    capabilities = CapabilitySet.from_instance(tmp_path, "fixture", tmp_path)
    assert not capabilities.supports("steering")
    assert capabilities.reason("steering") == "report_invalid"


def test_valid_private_certificate_cannot_enable_an_unreadable_binary(tmp_path, monkeypatch):
    """A versão declarada no relatório não substitui leitura do executável real."""
    binary = tmp_path / "fixture.exe"
    binary.write_bytes(b"fixture runtime")
    private = tmp_path / REPORT_RELATIVE
    private.parent.mkdir(parents=True)
    private.write_text(json.dumps({"schema_version": 1, "runtime": {"executable_sha256": sha256(binary.read_bytes()).hexdigest(), "schema_sha256": "c" * 64, "transport": "stdio", "platform": platform.system(), "adapter_revision": codex_capabilities.ADAPTER_REVISION}, "gates": {"G0": {"status": "supported", "evidence_kind": "runtime", "checks": ["runtime_probe"]}}}), encoding="utf-8")
    original = Path.open
    def inaccessible(path, *args, **kwargs):
        if path == binary:
            raise PermissionError("fixture binary unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", inaccessible)
    capabilities = CapabilitySet.from_instance(tmp_path, str(binary), tmp_path)
    assert not capabilities.supports("base")
    assert capabilities.reason("base") == "executable_changed"


def test_configured_binary_respects_explicit_workspace_and_certified_optional_enums(tmp_path):
    """O caller pode escolher workspace sem herdar o diretório do config."""
    binary = tmp_path / "fixture.exe"
    binary.write_bytes(b"fixture runtime")
    explicit = tmp_path / "plain-workspace"
    explicit.mkdir()
    configured = tmp_path / "configured-git"
    configured.mkdir()
    (configured / ".git").write_text("fixture", encoding="utf-8")
    report = {
        "schema_version": 1,
        "runtime": {"executable_sha256": codex_capabilities.executable_digest(str(binary)), "schema_sha256": "c" * 64, "transport": "stdio", "platform": platform.system(), "adapter_revision": codex_capabilities.ADAPTER_REVISION},
        "gates": {
            "G0": {"status": "supported", "evidence_kind": "runtime", "checks": ["runtime_probe"]},
            "G4": {"status": "supported", "evidence_kind": "runtime", "checks": ["runtime_probe"], "discovery": "workspace_codex_agents", "snapshot": "startup", "clients": ["native", "console", "app_server"], "workspace_kinds": ["plain"], "profile_reasoning_summaries": ["concise", None], "profile_verbosities": ["low", 42]},
        },
    }
    private = tmp_path / REPORT_RELATIVE
    private.parent.mkdir(parents=True)
    private.write_text(json.dumps(report), encoding="utf-8")
    (tmp_path / "configs" / "onmyoji-system.toml").write_text('[codex]\nexecutable=' + json.dumps(str(binary)) + '\nproject_directory=' + json.dumps(str(configured)) + '\n', encoding="utf-8")
    capabilities = CapabilitySet.from_instance(tmp_path, workspace=explicit)
    assert capabilities.workspace_kind == "plain" and capabilities.supports("agent_profiles")
    profile = codex_capabilities.load_profile_capability(tmp_path, str(binary), explicit)
    assert profile["profile_reasoning_summaries"] == ["concise"]
    assert profile["profile_verbosities"] == ["low"]


def test_ingress_claim_is_atomic_across_concurrent_preparation(contract_gateway):
    """Dois preparadores concorrentes não podem consumir o mesmo update."""
    source, inserted = contract_gateway.store.ingest("bot", {"update_id": 80}, "9", 0)
    assert inserted
    barrier = threading.Barrier(3)
    results = []
    def claim():
        barrier.wait(timeout=5)
        results.append(contract_gateway.store.ingress_claim(source))
    workers = [threading.Thread(target=claim) for _ in range(2)]
    for worker in workers:
        worker.start()
    barrier.wait(timeout=5)
    for worker in workers:
        worker.join(5)
        assert not worker.is_alive()
    assert sorted(results) == [False, True]
    assert contract_gateway.database.execute("SELECT state FROM ingress_updates WHERE source_key=?", (source,)).fetchone() == ("preparing",)


def test_checkpoint_allows_numeric_gaps_without_regression_or_payload_replacement(contract_gateway):
    first = {"update_id": 10, "message": {"text": "primeiro payload"}}
    source, _ = contract_gateway.store.ingest("bot", first, "9", 0)
    contract_gateway.store.ingest("bot", {"update_id": 900}, "9", 0)
    assert contract_gateway.store.checkpoint("bot") == 901
    assert contract_gateway.store.ingest("bot", {"update_id": 10, "message": {"text": "payload alterado"}}, "outra", 8) == (source, False)
    assert contract_gateway.store.checkpoint("bot") == 901
    payload, conversation, generation = contract_gateway.database.execute("SELECT payload,conversation_id,generation FROM ingress_updates WHERE source_key=?", (source,)).fetchone()
    assert json.loads(payload) == first
    assert (conversation, generation) == ("9", 0)


def test_consistent_backup_restores_schema_and_uncertainty_without_overwriting(contract_gateway, tmp_path):
    """Snapshot consistente conserva incertezas; backup não sobrescreve destino."""
    _, active = reserve_active(contract_gateway)
    contract_gateway.store.recover()
    backup = tmp_path / "snapshot" / "gateway.sqlite3"
    contract_gateway.store.backup(backup)
    digest = sha256(backup.read_bytes()).hexdigest()
    with pytest.raises(GatewayPersistenceError):
        contract_gateway.store.backup(backup)
    assert sha256(backup.read_bytes()).hexdigest() == digest
    contract_gateway.store.resolve("9", discard=True)
    restored = sqlite3.connect(backup)
    try:
        store = GatewayStore(restored, threading.RLock())
        assert store.blocked("9")
        assert restored.execute("SELECT state FROM execution_turns WHERE execution_id=?", (active.execution_id,)).fetchone() == ("uncertain",)
        assert restored.execute("SELECT MAX(version) FROM gateway_schema_migrations").fetchone()[0] >= 2
        store.recover()
        assert store.blocked("9")
    finally:
        restored.close()


def test_unknown_newer_schema_refuses_downgrade_without_modifying_marker(tmp_path):
    database = sqlite3.connect(tmp_path / "future.sqlite3")
    database.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY,applied_at REAL NOT NULL)")
    database.execute("INSERT INTO gateway_schema_migrations VALUES (999,0)")
    database.commit()
    try:
        with pytest.raises(GatewayPersistenceError, match="mais novo"):
            GatewayStore(database, threading.RLock())
        assert database.execute("SELECT version FROM gateway_schema_migrations").fetchall() == [(999,)]
    finally:
        database.close()


def test_config_upgrade_preserves_bytes_backup_and_is_idempotent(tmp_path):
    configuration = tmp_path / "configs" / "gateway.toml"
    configuration.parent.mkdir()
    original = 'schema_version = 1 # configuração atual\r\n\r\n[telegram]\r\nvault_profile = "fixture"\r\n'.encode("utf-8")
    configuration.write_bytes(original)
    protect_legacy_gateway(tmp_path, configuration)
    backup = configuration.with_name("config-before-delegation-v1.toml")
    assert backup.read_bytes() == original
    assert configuration.read_bytes() == original.replace(b"schema_version = 1", b"schema_version = 2", 1)
    assert not configuration.read_bytes().startswith(b"\xef\xbb\xbf")
    first = configuration.read_bytes()
    protect_legacy_gateway(tmp_path, configuration)
    assert configuration.read_bytes() == first and backup.read_bytes() == original
    assert list((tmp_path / "tmp").glob("gateway-config-*")) == []


def test_failed_config_publication_keeps_original_backup_and_cleans_staging(tmp_path, monkeypatch):
    configuration = tmp_path / "configs" / "gateway.toml"
    configuration.parent.mkdir()
    original = b'schema_version=1\n[telegram]\nvault_profile="fixture"\n'
    configuration.write_bytes(original)
    previous_replace = Path.replace
    def fail_publication(path, destination):
        if Path(destination) == configuration:
            raise PermissionError("fixture cannot publish")
        return previous_replace(path, destination)
    monkeypatch.setattr(Path, "replace", fail_publication)
    with pytest.raises(PermissionError):
        protect_legacy_gateway(tmp_path, configuration)
    assert configuration.read_bytes() == original
    assert configuration.with_name("config-before-delegation-v1.toml").read_bytes() == original
    assert list((tmp_path / "tmp").glob("gateway-config-*")) == []


def test_legacy_database_backup_precedes_schema_and_running_state_changes(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    database = sqlite3.connect(path)
    database.execute("CREATE TABLE inbox_items(id TEXT PRIMARY KEY,chat_id TEXT,generation INTEGER,text TEXT,attachment_ids TEXT,turn_context TEXT,state TEXT,created_at REAL)")
    database.execute("INSERT INTO inbox_items VALUES ('old','9',0,'operação fictícia','[]','{}','running',0)")
    database.commit()
    try:
        store = GatewayStore(database, threading.RLock())
        backup = path.with_name("gateway-before-delegation-v0.sqlite3")
        assert backup.is_file()
        previous = sqlite3.connect(backup)
        try:
            assert previous.execute("SELECT state FROM inbox_items").fetchone() == ("running",)
            assert "sequence" not in {column[1] for column in previous.execute("PRAGMA table_info(inbox_items)")}
        finally:
            previous.close()
        assert database.execute("SELECT state FROM inbox_items").fetchone() == ("uncertain_legacy",)
        digest = sha256(backup.read_bytes()).hexdigest()
        store.migrate()
        assert sha256(backup.read_bytes()).hexdigest() == digest
    finally:
        database.close()


@pytest.mark.parametrize("configuration_bytes", [b"schema_version=99\n", b"schema_version=+1\n"])
def test_config_upgrade_refuses_unknown_or_non_preservable_version(tmp_path, configuration_bytes):
    configuration = tmp_path / "gateway.toml"
    configuration.write_bytes(configuration_bytes)
    with pytest.raises(GatewayPersistenceError):
        protect_legacy_gateway(tmp_path, configuration)
    assert configuration.read_bytes() == configuration_bytes
    assert not configuration.with_name("config-before-delegation-v1.toml").exists()


def test_terminal_output_recovery_delivers_without_model_and_without_duplicate(contract_gateway, monkeypatch):
    """Resultado persistido antes de enviar pode ser entregue após crash local."""
    _, active = reserve_active(contract_gateway)
    contract_gateway.store.save_terminal_output(active.execution_id, "completed", "resultado final", "final-item", "text", True)
    monkeypatch.setattr(contract_gateway, "_app_client", lambda: pytest.fail("A recuperação de entrega não deve iniciar modelo"))
    contract_gateway._restore_terminal_outputs()
    contract_gateway._restore_terminal_outputs()
    assert len(contract_gateway._contract_sent) == 1
    assert "resultado final" in contract_gateway._contract_sent[0][1]
    assert contract_gateway.database.execute("SELECT delivery_state FROM execution_turns WHERE execution_id=?", (active.execution_id,)).fetchone() == ("done",)


def test_uncertain_terminal_send_is_not_repeated_or_cleaned(contract_gateway):
    _, active = reserve_active(contract_gateway)
    owner = contract_gateway._owner(active)
    contract_gateway.deliveries.effect(owner, "thought-before-crash", "thought", "thinking", {}, lambda: {"message_id": 7})
    contract_gateway.store.save_terminal_output(active.execution_id, "completed", "final", "final-item", "text", True)
    calls = []
    def uncertain_send(*args, **kwargs):
        calls.append("effect")
        raise TimeoutError("fixture lost acknowledgement")
    contract_gateway.api.send = uncertain_send
    contract_gateway._restore_terminal_outputs()
    contract_gateway._restore_terminal_outputs()
    assert calls == ["effect"]
    assert contract_gateway._contract_deleted == []
    assert contract_gateway.database.execute("SELECT delivery_state FROM execution_turns WHERE execution_id=?", (active.execution_id,)).fetchone() == ("uncertain",)


def test_pending_thought_cleanup_survives_reopen_without_repeating_final(contract_gateway, monkeypatch):
    """Falha de exclusão não marca done nem obriga reenviar resposta já entregue."""
    _, active = reserve_active(contract_gateway)
    contract_gateway.deliveries.effect(contract_gateway._owner(active), "thought-pending-cleanup", "thought", "thinking", {}, lambda: {"message_id": 7})
    contract_gateway.store.save_terminal_output(active.execution_id, "completed", "final já entregue", "final-item", "text", True)
    def cannot_delete(*args):
        raise TimeoutError("fixture delete acknowledgement missing")
    contract_gateway.api.delete = cannot_delete
    contract_gateway._restore_terminal_outputs()
    assert len(contract_gateway._contract_sent) == 1
    assert contract_gateway.database.execute("SELECT delivery_state FROM execution_turns WHERE execution_id=?", (active.execution_id,)).fetchone() == ("sent",)
    assert contract_gateway.database.execute("SELECT cleanup_state FROM gateway_deliveries WHERE delivery_key='thought-pending-cleanup'").fetchone() == ("pending",)
    reopened = Gateway(contract_gateway.settings)
    deleted = []
    reopened.api = SimpleNamespace(send=lambda *_args, **_kwargs: pytest.fail("Final já confirmado não deve ser reenviado"), delete=lambda chat, message: deleted.append((chat, message)))
    monkeypatch.setattr(reopened, "_app_client", lambda: pytest.fail("Cleanup não deve iniciar geração"))
    try:
        reopened.store.recover()
        reopened._restore_terminal_outputs()
        assert deleted == [(9, 7)]
        assert reopened.database.execute("SELECT delivery_state FROM execution_turns WHERE execution_id=?", (active.execution_id,)).fetchone() == ("done",)
    finally:
        reopened.stop_event.set()
        reopened.store.release_lease()


def test_thought_http_finishing_after_terminal_is_known_before_final_cleanup(contract_gateway):
    """A barreira de entrega espera o recibo tardio antes de limpar pensamento."""
    _, active = reserve_active(contract_gateway)
    entered, release, final_delivered = threading.Event(), threading.Event(), threading.Event()
    failures = []
    sent = []
    def send(chat, text, **values):
        if text == "pensamento":
            entered.set()
            assert release.wait(5)
        sent.append(text)
        if text == "final":
            final_delivered.set()
        return {"message_id": len(sent)}
    contract_gateway.api.send = send
    def late_thought():
        try:
            contract_gateway.deliveries.effect(contract_gateway._owner(active), "late-thought", "thought", "thinking", {}, lambda: send(9, "pensamento"))
        except Exception as error:
            failures.append(error)
    thought = threading.Thread(target=late_thought)
    thought.start()
    assert entered.wait(5)
    contract_gateway.store.save_terminal_output(active.execution_id, "completed", "final", "final-item", "text", True)
    final = threading.Thread(target=contract_gateway._restore_terminal_outputs)
    final.start()
    assert not final_delivered.is_set()
    assert contract_gateway._contract_deleted == []
    release.set()
    thought.join(5)
    final.join(5)
    assert not thought.is_alive() and not final.is_alive() and not failures
    assert sent == ["pensamento", "final"]
    assert contract_gateway._contract_deleted == [(9, 1)]


def test_boolean_delivery_receipt_is_not_a_confirmed_message_id(contract_gateway):
    owner = DeliveryOwner("9", contract_gateway._generation("9"))
    with pytest.raises(DeliveryUncertain):
        contract_gateway.deliveries.effect(owner, "invalid-boolean-receipt", "final", "final", {}, lambda: {"message_id": True})
    assert contract_gateway.database.execute("SELECT state FROM gateway_deliveries WHERE delivery_key='invalid-boolean-receipt'").fetchone() == ("uncertain",)


def test_cancel_preserves_generation_and_pending_input_until_terminal(contract_gateway):
    item, active = reserve_active(contract_gateway)
    pending = contract_gateway.store.enqueue("9", 0, "depois do cancelamento", [], {"source_trust": "OWNER"})
    slot = ConversationExecution(active.execution_id, "9", 0, item, active=active)
    contract_gateway._slots["9"] = slot
    requested = threading.Event()
    calls = []
    timeouts = []
    def request(method, params, *, timeout=None):
        calls.append((method, params))
        timeouts.append(timeout)
        requested.set()
        return {}
    contract_gateway.app_server = SimpleNamespace(request=request, running=lambda: True)
    assert contract_gateway._cancel_conversation("9")
    assert requested.wait(5)
    assert active.cancel_requested and not active.completed.is_set()
    assert contract_gateway._generation("9") == 0
    assert contract_gateway.store.next_input("9", 0)["id"] == pending
    assert calls == [("turn/interrupt", {"threadId": "thread", "turnId": "turn"})]
    assert len(timeouts) == 1 and 0 < timeouts[0] <= contract_gateway.settings.interrupt_grace_seconds
    contract_gateway._app_event("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": "interrupted"}}, {"process_epoch": "epoch"})
    assert active.completed.is_set()
    assert contract_gateway.store.next_input("9", 0)["id"] == pending


def test_channel_tool_from_known_child_is_not_authorized_by_spawn_relationship(contract_gateway):
    """Registrar relação de runtime não concede ao filho as ferramentas do canal."""
    _, active = reserve_active(contract_gateway)
    contract_gateway._observe_work(active, {"senderThreadId": "thread", "receiverThreadIds": ["known-child"], "agentsStates": {"known-child": {"status": "running"}}})
    with pytest.raises(RuntimeError):
        contract_gateway._app_request_event("item/tool/call", {"threadId": "known-child", "turnId": "child-turn", "namespace": "telegram_gateway", "tool": "send_message", "arguments": {"text": "indevido"}}, {"process_epoch": "epoch", "rpc_id": 5})
    assert contract_gateway._contract_sent == []


@pytest.mark.parametrize("method", ["turn/start", "turn/steer"])
@pytest.mark.parametrize("marker_present", [False, True])
def test_lost_ack_reconciles_only_persisted_history_without_restarting_input(contract_gateway, monkeypatch, method, marker_present):
    """História positiva recupera entrega; ausência conserva incerteza sem retry."""
    gateway = contract_gateway
    generation = gateway._generation("9")
    first = gateway.store.enqueue("9", generation, "pedido original", [], {"source_trust": "OWNER"})
    execution = gateway.store.reserve("9", generation, first, 30)
    gateway.store.bind(execution, "old-epoch", "thread", "turn")
    start = gateway.store.operation(first, execution, "turn/start", None)
    operation, inbox = start, first
    if method == "turn/steer":
        gateway.store.operation_result(start, "accepted")
        inbox = gateway.store.enqueue("9", generation, "correção posterior", [], {"source_trust": "OWNER"}, route="steer")
        operation = gateway.store.operation(inbox, execution, "turn/steer", "turn")
    gateway.store.operation_result(operation, "uncertain", "ack_lost")
    with gateway.database_lock, gateway.database:
        gateway.database.execute("UPDATE conversations SET codex_thread_id='thread' WHERE chat_id='9'")
        markers = gateway.database.execute("SELECT client_message_id FROM input_operations WHERE execution_id=?", (execution,)).fetchall()
    items = [{"id": "message-" + str(index), "type": "userMessage", "clientId": marker} for index, (marker,) in enumerate(markers)] if marker_present else []
    history = {"thread": {"id": "thread", "turns": [{"id": "turn", "status": "completed", "items": items + [{"id": "history-final", "type": "agentMessage", "phase": "final_answer", "text": "resultado recuperado"}]}]}}
    requests = []
    def request(name, params, **values):
        requests.append((name, params))
        if name != "thread/read":
            pytest.fail("Reconciliação não pode iniciar ou repetir input de modelo")
        assert params == {"threadId": "thread", "includeTurns": True}
        return history
    gateway.capabilities = SimpleNamespace(supports=lambda feature: feature == "input_reconciliation")
    monkeypatch.setattr(gateway, "_app_client", lambda: SimpleNamespace(request=request))
    gateway._reconcile_conversation("9")
    gateway._restore_terminal_outputs()
    gateway._restore_terminal_outputs()
    with gateway.database_lock:
        operation_row = gateway.database.execute("SELECT state,reason FROM input_operations WHERE operation_id=?", (operation,)).fetchone()
        execution_row = gateway.database.execute("SELECT state,turn_id,final_item_id,delivery_state FROM execution_turns WHERE execution_id=?", (execution,)).fetchone()
        input_state = gateway.database.execute("SELECT state FROM inbox_items WHERE id=?", (inbox,)).fetchone()[0]
        assert gateway.database.execute("SELECT COUNT(*) FROM execution_turns").fetchone() == (1,)
        assert gateway.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone() == ("thread",)
    assert all(name == "thread/read" for name, _params in requests)
    assert gateway._generation("9") == generation
    if marker_present:
        assert operation_row == ("accepted", "history_confirmed")
        assert execution_row == ("terminal", "turn", "history-final", "done")
        assert input_state == "done" and not gateway.store.blocked("9")
        assert len(gateway._contract_sent) == 1 and "resultado recuperado" in gateway._contract_sent[0][1]
        assert gateway.database.execute("SELECT item_id,state FROM gateway_deliveries WHERE kind='final'").fetchall() == [("history-final", "sent")]
        previous_reads = len(requests)
        gateway._reconcile_conversation("9")
        assert len(requests) == previous_reads
    else:
        assert operation_row == ("uncertain", "ack_lost")
        assert execution_row[0] == "uncertain" and input_state == "uncertain"
        assert gateway.store.blocked("9") and gateway._contract_sent == []
        assert gateway.store.next_input("9", generation) is None


@pytest.mark.parametrize("delete_at_finish", [False, True])
def test_thought_sharing_toggles_without_retroactive_publication_or_early_cleanup(contract_gateway, delete_at_finish):
    """Preferências são lidas por item e no fechamento da mesma geração."""
    gateway = contract_gateway
    generation = gateway._generation("9")
    active = AppServerTurn(9, "thread", threading.Event(), turn_id="turn", conversation_id="9", generation=generation)
    gateway.app_turns["thread"] = active
    def thought(identifier, text):
        gateway._app_notification("item/completed", {"threadId": "thread", "turnId": "turn", "item": {"id": identifier, "type": "agentMessage", "phase": "commentary", "text": text}})
    thought("first", "mesmo comentário")
    with gateway.database_lock, gateway.database:
        gateway.database.execute("UPDATE conversations SET share_thoughts=0,delete_thoughts=? WHERE chat_id='9'", (not delete_at_finish,))
    thought("first", "mesmo comentário")
    thought("hidden", "não publicar retroativamente")
    assert len(gateway._contract_sent) == 1 and gateway._contract_deleted == []
    with gateway.database_lock, gateway.database:
        gateway.database.execute("UPDATE conversations SET share_thoughts=1 WHERE chat_id='9'")
    thought("hidden", "não publicar retroativamente")
    thought("second", "mesmo comentário")
    assert len(gateway._contract_sent) == 2
    assert all("não publicar retroativamente" not in row[1] for row in gateway._contract_sent)
    assert gateway._contract_deleted == []
    with gateway.database_lock, gateway.database:
        gateway.database.execute("UPDATE conversations SET delete_thoughts=? WHERE chat_id='9'", (delete_at_finish,))
    gateway._app_notification("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": "completed"}})
    active.terminal_announced = True
    gateway._cleanup_thoughts(active)
    gateway._cleanup_thoughts(active)
    assert len(gateway._contract_deleted) == (2 if delete_at_finish else 0)


@pytest.mark.parametrize("status", ["completed", "failed", "interrupted"])
@pytest.mark.parametrize("last_phase", [None, "final_answer"])
def test_multiple_final_items_preserve_history_and_select_last_with_honest_status(contract_gateway, monkeypatch, status, last_phase):
    """Último final é selecionado; phase ausente não inventa terminalidade."""
    gateway = contract_gateway
    _, active = reserve_active(gateway)
    monkeypatch.setattr(gateway, "_app_client", lambda: pytest.fail("Recuperar saída não deve executar modelo"))
    first = {"id": "first-final", "type": "agentMessage", "phase": "final_answer", "text": "primeira resposta"}
    last = {"id": "last-final", "type": "agentMessage", "text": "última resposta"}
    if last_phase is not None:
        last["phase"] = last_phase
    for item in (first, last, last):
        gateway._app_event("item/completed", {"threadId": "thread", "turnId": "turn", "item": item}, {"process_epoch": "epoch"})
    assert list(active.final_items.items()) == [("first-final", "primeira resposta"), ("last-final", "última resposta")]
    assert active.final_item_id == "last-final" and not active.completed.is_set()
    assert gateway._contract_sent == []
    gateway._app_event("turn/completed", {"threadId": "thread", "turn": {"id": "turn", "status": status}}, {"process_epoch": "epoch"})
    gateway._restore_terminal_outputs()
    gateway._restore_terminal_outputs()
    assert len(gateway._contract_sent) == 1
    text = gateway._contract_sent[0][1]
    assert "última resposta" in text and "primeira resposta" not in text
    assert ("Resposta parcial" in text) == (status != "completed")
    assert gateway.database.execute("SELECT result,final_item_id,delivery_state FROM execution_turns WHERE execution_id=?", (active.execution_id,)).fetchone() == (status, "last-final", "done")
    assert gateway.database.execute("SELECT item_id,state FROM gateway_deliveries WHERE kind='final'").fetchall() == [("last-final", "sent")]
