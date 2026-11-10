"""Valida Gateway e App Server reais com provider loopback e Telegram falso.

Não lê conta/token de produção e não envia ações de negócio. Todas as fontes,
configurações, estado e saídas sintéticas do laboratório permanecem em tmp/.
O provider determinístico é compartilhado com o probe de protocolo E0.
"""
from pathlib import Path
import argparse
import importlib.util
import faulthandler
import json
import math
import os
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "onmyoji-daemon" / "src"))
spec = importlib.util.spec_from_file_location("codex_probe", ROOT / "onmyoji-daemon" / "tools" / "validate_codex_delegation.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
from onmyoji_daemon.telegram import Gateway, Settings


def run(output: Path | None = None, executable: str = "codex", samples: int = 20) -> dict:
    """Exercita steering, pensamentos, menus e continuação no pipeline real."""
    LAB = (output or ROOT / "tmp" / ("gateway-live-" + uuid.uuid4().hex)).resolve()
    if not LAB.is_relative_to(ROOT / "tmp"):
        raise ValueError("O laboratório deve permanecer em tmp/ do repositório.")
    if LAB.exists() and any(LAB.iterdir()):
        raise ValueError("O destino deve estar vazio; use uma nova pasta em tmp/.")
    if type(samples) is not int or not 1 <= samples <= 32:
        raise ValueError("Use de 1 a 32 amostras de direcionamento no laboratório.")
    previous_environment = dict(os.environ)
    INSTANCE, WORKSPACE = LAB / "instance", LAB / "workspace"
    INSTANCE.mkdir(parents=True)
    WORKSPACE.mkdir()
    for key in list(os.environ):
        if key.startswith(("OPENAI_", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY")):
            os.environ.pop(key, None)
    system_tmp = LAB / "system-tmp"
    system_tmp.mkdir()
    os.environ["TMP"] = os.environ["TEMP"] = str(system_tmp)
    provider = probe.LoopbackProvider()
    provider.hold_commentary = True
    provider.hold_after_steer = True
    config = f'''model="probe"
    model_provider="probe"
    sandbox_mode="read-only"
    approval_policy="never"
    [model_providers.probe]
    name="Loopback live Gateway probe"
    base_url="http://127.0.0.1:{provider.server.server_port}/v1"
    wire_api="responses"
    requires_openai_auth=false
    request_max_retries=0
    stream_max_retries=0
    [features]
    shell_tool=false
    apps=false
    plugins=false
    memories=false
    '''
    (INSTANCE / "config.toml").write_text(config, encoding="utf-8")
    configs = INSTANCE / "configs"
    configs.mkdir()
    mcp = configs / "mcp"
    mcp.mkdir()
    (mcp / "codex-registration.json").write_text('{"schema_version":2,"pending":false}\n', encoding="utf-8")
    (configs / "onmyoji-system.toml").write_text('[shikigami]\nidentity="Probe"\n[codex]\nexecutable=' + json.dumps(executable) + '\nproject_directory=' + json.dumps(WORKSPACE.as_posix()) + '\nmodel="probe"\nsandbox_mode="read-only"\napproval_policy="never"\n', encoding="utf-8")
    daemon = configs / "daemon"
    daemon.mkdir()
    (daemon / "daemon.toml").write_text('[app_server]\nenabled=true\nidle_seconds=600\n', encoding="utf-8")
    data = daemon / "services" / "telegram"
    data.mkdir(parents=True)
    (data / "config.toml").write_text('schema_version=1\n[telegram]\nvault_profile="fixture"\ntoken_entry="fixture"\n[agent]\nbusy_input_policy="steer"\npost_turn_results=true\nturn_timeout_seconds=60\nmax_parallel_conversations=2\n', encoding="utf-8")
    gateway = Gateway(Settings.load(INSTANCE, data))
    class FakeTelegram:
        def __init__(self):
            self.messages = []
            self.deleted = []
            self.lock = threading.Lock()
            self.progress = threading.Event()
            self.acknowledged = {}
        def send(self, chat, text, **values):
            with self.lock:
                ident = len(self.messages) + 1
                self.messages.append((ident, chat, text, values))
            if "PROBE_PROGRESS" in text:
                self.progress.set()
            return {"message_id": ident}
        def delete(self, chat, message):
            with self.lock:
                self.deleted.append((chat, message))
        def typing(self, *_args, **_kwargs):
            pass
        def answer_callback(self, callback_id):
            with self.lock:
                self.acknowledged[callback_id] = time.perf_counter()
    api = FakeTelegram()
    gateway.api = api
    gateway.bot_id = 777
    gateway._generation("9")
    report = {"scope": "real_gateway_real_app_server_loopback_provider_fake_telegram", "checks": []}
    phase = "start"
    def wait_for(predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                raise TimeoutError(phase)
            with gateway.workers_changed:
                gateway.workers_changed.wait(0.05)
    def query(sql, params=()):
        with gateway.database_lock:
            return gateway.database.execute(sql, params).fetchall()
    try:
        gateway._start_execution_state()
        assert gateway.capabilities.supports("steering") and gateway.capabilities.supports("post_turn")
        phase = "held_turn_progress"
        gateway._enqueue_turn("9", "PROBE_HOLD", [], {"source_trust": "OWNER", "conversation_kind": "private"})
        assert provider.entered.wait(30)
        process_checkpoint = json.loads(query("SELECT value FROM gateway_checkpoints WHERE key='app_server_process'")[0][0])
        assert process_checkpoint["state"] == "active"
        assert process_checkpoint["pid"] == gateway.app_server.process.pid
        assert process_checkpoint["process_epoch"] == gateway.app_server.process_epoch
        report["checks"].append("native_process_identity_is_durable_before_model_input")
        assert api.progress.wait(10)
        progress_id = next(row[0] for row in api.messages if "PROBE_PROGRESS" in row[2])
        phase = "steering_acceptance"
        steering_ms = []
        for index in range(samples):
            started = time.perf_counter()
            gateway._enqueue_turn("9", f"PROBE_STEER {index}", [], {"source_trust": "OWNER", "conversation_kind": "private"})
            wait_for(lambda: query("SELECT COUNT(*) FROM input_operations WHERE method='turn/steer' AND state='accepted'")[0][0] == index + 1)
            steering_ms.append((time.perf_counter() - started) * 1000)
        assert (9, progress_id) not in api.deleted
        report["checks"].append("new_input_steers_held_turn_without_cleaning_progress")
        provider.hold.set()
        phase = "first_terminal_delivery"
        wait_for(lambda: query("SELECT COUNT(*) FROM execution_turns WHERE state='terminal' AND delivery_state='done'")[0][0] == 1)
        assert (9, progress_id) in api.deleted
        report["checks"].append("progress_cleaned_only_after_own_terminal_delivery")
        provider.hold_commentary = False
        provider.hold_after_steer = False
        phase = "post_turn_parent"
        provider.child_hold.clear()
        provider.child_entered.clear()
        gateway._enqueue_turn("9", "PROBE_PARENT_LATE", [], {"source_trust": "OWNER", "conversation_kind": "private"})
        assert provider.child_entered.wait(10)
        wait_for(lambda: query("SELECT COUNT(*) FROM execution_turns WHERE state='terminal' AND delivery_state='done'")[0][0] == 2)
        assert query("SELECT COUNT(*) FROM related_work")[0][0] == 1
        report["checks"].append("parent_finishes_while_child_is_still_running")
        phase = "post_turn_continuation"
        provider.child_hold.set()
        wait_for(lambda: query("SELECT COUNT(*) FROM inbox_items WHERE route='result'")[0][0] == 1, timeout=20)
        wait_for(lambda: query("SELECT COUNT(*) FROM execution_turns WHERE state='terminal' AND delivery_state='done'")[0][0] == 3, timeout=20)
        assert query("SELECT COUNT(*) FROM inbox_items WHERE route='result'")[0][0] == 1
        row = query("SELECT turn_context FROM inbox_items WHERE route='result'")[0][0]
        assert json.loads(row)["source_trust"] == "SYSTEM_EVENT"
        assert json.loads(row)["system_result"]["authority"] == "data_only"
        continuation_thread, continuation_turn = query("SELECT thread_id,turn_id FROM execution_turns ORDER BY created_at DESC LIMIT 1")[0]
        history = gateway.app_server.request("thread/read", {"threadId": continuation_thread, "includeTurns": True})
        persisted = next(turn for turn in history["thread"]["turns"] if turn["id"] == continuation_turn)
        assert any(item.get("type") == "functionCallOutput" for item in persisted["items"])
        assert not any(item.get("type") == "userMessage" for item in persisted["items"])
        report["checks"].extend(["late_child_result_becomes_one_data_continuation", "continuation_runs_principal_without_owner_impersonation"])
        report["checks"].append("continuation_persisted_as_function_output")
        phase = "gateway_menu_and_callback"
        gateway.contacts.add_owner({"id": 19, "first_name": "Synthetic menu owner"})
        callback_ms = []
        for index in range(samples):
            gateway._enqueue_turn("19", f"PROBE_GATEWAY_MENU {index}", [], {"source_trust": "OWNER", "conversation_kind": "private"})
            def menu_for_owner():
                with gateway.menu_lock:
                    return next((session for session in gateway.menu_sessions.values() if session.conversation_id == "19"), None)
            wait_for(menu_for_owner)
            session = menu_for_owner()
            if index == 0:
                gateway._enqueue_turn("29", "PROBE_PARALLEL_WHILE_MENU", [], {"source_trust": "OWNER", "conversation_kind": "private"})
                wait_for(lambda: query("SELECT COUNT(*) FROM execution_turns WHERE conversation_id='29' AND state='terminal' AND delivery_state='done'")[0][0] == 1)
                assert not session.completed.is_set()
                report["checks"].append("other_conversation_finishes_while_real_menu_request_is_pending")
            callback_id = f"synthetic-callback-{index}"
            started = time.perf_counter()
            gateway._callback({"id": callback_id, "from": {"id": 19}, "message": {"message_id": session.message_id, "chat": {"id": 19, "type": "private"}}, "data": f"ag:{session.token}:1"})
            wait_for(lambda: callback_id in api.acknowledged)
            assert session.completed.is_set() and session.selected == {"id": "b", "label": "B"}
            callback_ms.append((api.acknowledged[callback_id] - started) * 1000)
            wait_for(lambda: query("SELECT COUNT(*) FROM execution_turns WHERE conversation_id='19' AND state='terminal' AND delivery_state='done'")[0][0] == index + 1)
            assert (19, session.message_id) in api.deleted
        with provider.lock:
            menu_outputs = [item for body in provider.requests for item in body.get("input", []) if item.get("type") == "function_call_output" and str(item.get("call_id", "")).startswith("gateway_menu_")]
        assert any('"selected"' in str(item.get("output")) and '"b"' in str(item.get("output")) for item in menu_outputs)
        report["checks"].append("real_menu_callback_selection_returns_to_model_and_cleans_menu")
        phase = "ingress_persistence_measurement"
        ingress_ms = []
        for index in range(max(samples, 20)):
            update_id = 1000 + index
            update = {"update_id": update_id, "message": {"message_id": update_id, "chat": {"id": 12345, "type": "private"}, "from": {"id": 99999}, "text": "PROBE_UNPAIRED_METRIC"}}
            started = time.perf_counter()
            gateway._receive_update(update)
            assert gateway.store.checkpoint("777") == update_id + 1
            ingress_ms.append((time.perf_counter() - started) * 1000)
        def p95(values):
            return round(sorted(values)[math.ceil(len(values) * 0.95) - 1], 3)
        report["metrics"] = {"steering_samples": len(steering_ms), "ready_input_to_accepted_ms_p95": p95(steering_ms), "ingress_samples": len(ingress_ms), "receive_to_persist_ms_p95": p95(ingress_ms), "callback_samples": len(callback_ms), "callback_to_local_ack_ms_p95": p95(callback_ms), "external_sla": False}
        report["execution_count"] = query("SELECT COUNT(*) FROM execution_turns")[0][0]
        report["thread_count"] = query("SELECT COUNT(DISTINCT thread_id) FROM execution_turns")[0][0]
        phase = "confirmed_process_shutdown"
        gateway._shutdown_execution()
        assert json.loads(query("SELECT value FROM gateway_checkpoints WHERE key='app_server_process'")[0][0])["state"] == "stopped"
        report["checks"].append("gateway_shutdown_marks_process_stopped_after_native_exit")
        report["passed"] = True
    except Exception as error:
        report.update(passed=False, failed_phase=phase, failure_type=type(error).__name__)
        report["executions"] = query("SELECT state,result,delivery_state FROM execution_turns")
        report["operations"] = query("SELECT method,state,reason FROM input_operations")
        report["related_work"] = query("SELECT state,result_version,delivered_version FROM related_work")
        report["provider_requests"] = len(provider.requests)
        report["app_server_present"] = gateway.app_server is not None
        with (LAB / "stacks.log").open("w", encoding="utf-8") as stream:
            faulthandler.dump_traceback(file=stream)
    finally:
        provider.hold.set()
        provider.child_hold.set()
        gateway.stop_event.set()
        with gateway.workers_changed:
            gateway.workers_changed.notify_all()
        if gateway.app_server:
            gateway.app_server.stop()
        gateway.store.release_lease()
        provider.close()
        (LAB / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        os.environ.clear()
        os.environ.update(previous_environment)
        print(json.dumps(report))
        print("LAB_REPORT", (LAB / "report.json").relative_to(ROOT).as_posix())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--samples", type=int, default=20)
    arguments = parser.parse_args()
    report = run(arguments.output, arguments.executable, arguments.samples)
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
