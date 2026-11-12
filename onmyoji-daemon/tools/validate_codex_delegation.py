"""Laboratório reprodutível do App Server com provider exclusivamente loopback.

Não usa conta, token, MCP ou Telegram real. Um provider Responses determinístico
exercita o binário real; o relatório contém apenas hashes e resultados, nunca
prompts, caminhos locais ou IDs de sessão. Nenhum certificado é instalado numa
instância automaticamente. Execute da raiz com Python -B e --output em tmp/.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "onmyoji-daemon" / "src"))
from onmyoji_daemon.codex_capabilities import ADAPTER_REVISION, executable_digest


def _texts(body: dict) -> str:
    texts = []
    for item in body.get("input", []):
        if item.get("role") != "user":
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            texts.extend(part.get("text", "") for part in content if isinstance(part, dict))
    return "\n".join(texts)


def _message(text: str, ident: str) -> dict:
    return {"type": "message", "id": ident, "role": "assistant", "status": "completed", "phase": "final_answer", "content": [{"type": "output_text", "text": text, "annotations": []}]}


def _call(name: str, arguments: dict, ident: str) -> dict:
    return {"type": "function_call", "id": "fc_" + ident, "call_id": ident, "name": name, "namespace": "multi_agent_v1", "arguments": json.dumps(arguments)}


class LoopbackProvider:
    """Provider falso que só devolve texto e chamadas de colaboração inofensivas."""

    def __init__(self):
        self.hold = threading.Event()
        self.entered = threading.Event()
        self.child_hold = threading.Event()
        self.child_entered = threading.Event()
        self.hold_commentary = False
        self.hold_after_steer = False
        self.requests: list[dict] = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                with owner.lock:
                    owner.requests.append(body)
                    number = len(owner.requests)
                text = _texts(body)
                history = body.get("input", [])
                last_user = max((index for index, item in enumerate(history) if _texts({"input": [item]}) in {"PROBE_PROFILE_PARENT", "PROBE_PROFILE_PARENT_AGAIN"}), default=-1)
                outputs = [item for item in history[last_user + 1:] if item.get("type") == "function_call_output"]
                prefix = None
                sequence_offset = 0
                if "PROBE_HOLD" in text and ("PROBE_STEER" not in text or owner.hold_after_steer):
                    if owner.hold_commentary:
                        prefix = _message("PROBE_PROGRESS", f"progress_{number}")
                        prefix["phase"] = "commentary"
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        prefix_events = [
                            {"type": "response.created", "response": {"id": f"resp_{number}", "status": "in_progress", "output": []}},
                            {"type": "response.output_item.added", "output_index": 0, "item": {**prefix, "status": "in_progress", "content": []}},
                            {"type": "response.output_text.delta", "item_id": prefix["id"], "output_index": 0, "content_index": 0, "delta": "PROBE_PROGRESS"},
                            {"type": "response.output_item.done", "output_index": 0, "item": prefix},
                        ]
                        for sequence, event in enumerate(prefix_events):
                            event["sequence_number"] = sequence
                            self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                            self.wfile.flush()
                        sequence_offset = len(prefix_events)
                    owner.entered.set()
                    owner.hold.wait(20)
                if "PROBE_CHILD_ERROR" in text:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "Controlled local probe failure", "type": "invalid_request_error", "code": "probe_failure"}}).encode())
                    self.close_connection = True
                    return
                if "PROBE_CHILD_LATE" in text and "PROBE_CHILD_INTERRUPTED" not in text:
                    owner.child_entered.set()
                    owner.child_hold.wait(20)
                result = [_message("PROBE_OK", f"msg_{number}")]
                if "PROBE_PHASE_NONE" in text:
                    result[0].pop("phase")
                if "PROBE_DYNAMIC_TOOL" in text:
                    if not outputs:
                        call = _call("ask_menu", {"question": "Escolha fictícia de laboratório", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}, f"dynamic_{number}")
                        call["namespace"] = "telegram_gateway"
                        result = [call]
                    else:
                        result = [_message("PROBE_DYNAMIC_RETURNED", f"msg_{number}")]
                if "PROBE_PARENT_TWO" in text or "PROBE_PARENT_LATE" in text or "PROBE_PROFILE_PARENT" in text:
                    if not outputs:
                        children = ["PROBE_CHILD_ALPHA", "PROBE_CHILD_BETA"] if "PROBE_PARENT_TWO" in text else ["PROBE_CHILD_LATE"]
                        if "PROBE_PARENT_TWO_ERROR" in text:
                            children[1] = "PROBE_CHILD_ERROR"
                        if "PROBE_PARENT_TWO_INTERRUPT" in text:
                            children[1] = "PROBE_CHILD_LATE"
                        if "PROBE_PROFILE_PARENT" in text:
                            children = ["PROBE_PROFILE_CHILD"]
                        result = [_call("spawn_agent", {"message": child, **({"agent_type": "probe_profile"} if "PROBE_PROFILE_PARENT" in text else {})}, f"spawn_{number}_{index}") for index, child in enumerate(children)]
                    elif "PROBE_PARENT_TWO" in text or "PROBE_PROFILE_PARENT" in text:
                        ids = []
                        statuses = {}
                        for item in outputs:
                            try:
                                value = json.loads(item.get("output", "{}"))
                                if isinstance(value, dict) and value.get("agent_id"):
                                    ids.append(value["agent_id"])
                                if isinstance(value, dict) and isinstance(value.get("status"), dict):
                                    statuses.update(value["status"])
                            except (TypeError, ValueError):
                                pass
                        remaining = [ident for ident in ids if ident not in statuses]
                        if "PROBE_PARENT_TWO_INTERRUPT" in text and ids and not any(item.get("call_id", "").startswith("interrupt_") for item in outputs):
                            owner.child_entered.wait(5)
                            result = [_call("send_input", {"target": ids[-1], "interrupt": True, "message": "PROBE_CHILD_INTERRUPTED"}, f"interrupt_{number}")]
                        elif remaining:
                            result = [_call("wait_agent", {"targets": remaining, "timeout_ms": 10000}, f"wait_{number}")]
                        elif ids and not any(item.get("call_id", "").startswith("close_") for item in outputs):
                            result = [_call("close_agent", {"target": ident}, f"close_{number}_{index}") for index, ident in enumerate(ids)]
                        else:
                            result = [_message("PROBE_CONSOLIDATED" if ids else "PROBE_CHILD_UNAVAILABLE", f"msg_{number}")]
                    else:
                        result = [_message("PROBE_CONSOLIDATED", f"msg_{number}")]
                # O runner do Gateway repete menus na mesma thread. A decisão
                # usa apenas o último input, sem reutilizar outputs de outro menu.
                latest_user = max((index for index, item in enumerate(history) if item.get("role") == "user"), default=-1)
                if latest_user >= 0 and "PROBE_GATEWAY_MENU" in _texts({"input": [history[latest_user]]}):
                    menu_outputs = [item for item in history[latest_user + 1:] if item.get("type") == "function_call_output"]
                    if menu_outputs:
                        result = [_message("PROBE_GATEWAY_MENU_RETURNED", f"msg_{number}")]
                    else:
                        call = _call("ask_menu", {"question": "Menu sintético do Gateway", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}, f"gateway_menu_{number}")
                        call["namespace"] = "telegram_gateway"
                        result = [call]
                response_id = f"resp_{number}"
                events = [] if prefix else [{"type": "response.created", "response": {"id": response_id, "status": "in_progress", "output": []}}]
                for index, item in enumerate(result, start=1 if prefix else 0):
                    initial = dict(item)
                    if item["type"] == "message":
                        initial["content"] = []
                        initial["status"] = "in_progress"
                    events.append({"type": "response.output_item.added", "output_index": index, "item": initial})
                    if item["type"] == "message":
                        events.append({"type": "response.output_text.delta", "item_id": item["id"], "output_index": index, "content_index": 0, "delta": item["content"][0]["text"]})
                    events.append({"type": "response.output_item.done", "output_index": index, "item": item})
                events.append({"type": "response.completed", "response": {"id": response_id, "status": "completed", "output": ([prefix] if prefix else []) + result, "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}})
                try:
                    if not prefix:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Connection", "close")
                        self.end_headers()
                    for sequence, event in enumerate(events, start=sequence_offset):
                        event["sequence_number"] = sequence
                        self.wfile.write(("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode())
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                self.close_connection = True

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.hold.set()
        self.child_hold.set()
        self.server.shutdown()
        self.server.server_close()


class ProbeClient:
    """Cliente de laboratório com respostas e eventos preservados separadamente."""

    def __init__(self, executable: str, home: Path, workspace: Path):
        environment = {key: value for key, value in os.environ.items() if not key.startswith(("OPENAI_", "CODEX_ACCESS_TOKEN", "CODEX_API_KEY"))}
        environment["CODEX_HOME"] = str(home)
        environment["TMP"] = environment["TEMP"] = str(home / "tmp")
        (home / "tmp").mkdir(exist_ok=True)
        self.process = subprocess.Popen([executable, "app-server", "--stdio"], cwd=workspace, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.condition = threading.Condition()
        self.responses: dict[int, dict] = {}
        self.events: list[dict] = []
        self.next_id = 0
        self.stderr_lines = 0
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()
        self.rpc("initialize", {"clientInfo": {"name": "onmyoji_local_capability_probe", "version": "1"}, "capabilities": {"experimentalApi": True}})
        self.send({"method": "initialized"})

    def send(self, message: dict):
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def _read(self):
        assert self.process.stdout is not None
        for raw in self.process.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            with self.condition:
                if "id" in message and ("result" in message or "error" in message):
                    self.responses[message["id"]] = message
                else:
                    self.events.append(message)
                self.condition.notify_all()

    def _stderr(self):
        assert self.process.stderr is not None
        for _ in self.process.stderr:
            self.stderr_lines += 1

    def rpc(self, method: str, params: dict, *, tolerate_error: bool = False) -> dict:
        self.next_id += 1
        ident = self.next_id
        self.send({"id": ident, "method": method, "params": params})
        deadline = time.monotonic() + 20
        with self.condition:
            while ident not in self.responses:
                if not self.condition.wait(max(0, deadline - time.monotonic())):
                    raise TimeoutError("probe_rpc_timeout")
            response = self.responses.pop(ident)
        if "error" in response and not tolerate_error:
            raise RuntimeError("probe_rpc_rejected_" + method.replace("/", "_"))
        return response

    def wait(self, predicate, *, after: int = 0, timeout: float = 15) -> dict:
        deadline = time.monotonic() + timeout
        with self.condition:
            while True:
                for event in self.events[after:]:
                    if predicate(event):
                        return event
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("probe_event_timeout")
                self.condition.wait(remaining)

    def thread(self, workspace: Path) -> str:
        return self.rpc("thread/start", {"cwd": str(workspace), "model": "probe", "approvalPolicy": "never", "sandbox": "read-only"})["result"]["thread"]["id"]

    def turn(self, thread: str, text: str, marker: str | None = None) -> str:
        params = {"threadId": thread, "input": [{"type": "text", "text": text}]}
        if marker:
            params["clientUserMessageId"] = marker
        return self.rpc("turn/start", params)["result"]["turn"]["id"]

    def completed(self, thread: str, turn: str | None = None, *, after: int = 0) -> dict:
        return self.wait(lambda event: event.get("method") == "turn/completed" and event.get("params", {}).get("threadId") == thread and (turn is None or event["params"].get("turn", {}).get("id") == turn), after=after)

    def close(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        if self.process.stdin:
            self.process.stdin.close()


def _gate(status: str, reason: str, checks: list[str], **extra) -> dict:
    return {"status": status, "reason": reason, "evidence_kind": "runtime", "checks": checks, **extra}


def run(executable: str, output: Path) -> dict:
    """Investiga gates em runtime real; toda falha conserva fallback explícito."""
    output = output.resolve()
    if not output.is_relative_to(ROOT / "tmp"):
        raise ValueError("O laboratório deve permanecer em tmp/ do repositório.")
    if output.exists() and any(output.iterdir()):
        raise ValueError("O destino do laboratório deve estar vazio; use uma nova pasta em tmp/.")
    output.mkdir(parents=True, exist_ok=True)
    (output / ".onmyoji-loopback-probe").write_text("1\n", encoding="utf-8")
    home, workspace = output / "home", output / "workspace"
    home.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    if (home / "auth.json").exists():
        raise ValueError("O laboratório não aceita autenticação de conta.")
    provider = LoopbackProvider()
    config = f'''model = "probe"
model_provider = "probe"
sandbox_mode = "read-only"
approval_policy = "never"
[model_providers.probe]
name = "Loopback capability probe"
base_url = "http://127.0.0.1:{provider.server.server_port}/v1"
wire_api = "responses"
requires_openai_auth = false
request_max_retries = 0
stream_max_retries = 0
[features]
shell_tool = false
apps = false
plugins = false
memories = false
'''
    (home / "config.toml").write_text(config, encoding="utf-8")
    schema = output / "schema"
    subprocess.run([executable, "app-server", "generate-json-schema", "--out", str(schema)], check=True, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    digest = hashlib.sha256()
    for path in sorted(schema.rglob("*.json"), key=lambda item: item.relative_to(schema).as_posix()):
        digest.update(path.relative_to(schema).as_posix().encode())
        digest.update(path.read_bytes())
    report = {"schema_version": 1, "validated_at": datetime.now(timezone.utc).isoformat(), "runtime": {"executable_sha256": executable_digest(executable), "schema_sha256": digest.hexdigest(), "transport": "stdio", "platform": platform.system(), "adapter_revision": ADAPTER_REVISION, "version": subprocess.run([executable, "--version"], capture_output=True, text=True, check=True).stdout.strip()}, "scope": "real_app_server_with_deterministic_loopback_provider_no_account_or_business_effects", "gates": {f"G{i}": _gate("unverified", "not_exercised", []) for i in range(8)}}
    gates = report["gates"]
    client = None
    phase = "base"
    try:
        client = ProbeClient(executable, home, workspace)
        thread = client.thread(workspace)
        turn = client.turn(thread, "PROBE_BASE", "probe_start_marker")
        assert client.completed(thread, turn)["params"]["turn"]["status"] == "completed"
        assert client.process.poll() is None
        client.rpc("thread/resume", {"threadId": thread, "cwd": str(workspace)})
        second = client.turn(thread, "PROBE_SECOND")
        client.completed(thread, second)
        gates["G0"] = _gate("supported", "loopback_lifecycle_verified", ["handshake", "start_complete", "process_retained", "resume_then_new_turn"])

        phase = "phase_compatibility"
        compatibility_thread = client.thread(workspace)
        compatibility_turn = client.turn(compatibility_thread, "PROBE_PHASE_NONE")
        assert client.completed(compatibility_thread, compatibility_turn)["params"]["turn"]["status"] == "completed"
        compatibility_history = client.rpc("thread/read", {"threadId": compatibility_thread, "includeTurns": True})["result"]["thread"]
        compatibility_items = [item for turn in compatibility_history["turns"] if turn["id"] == compatibility_turn for item in turn["items"] if item.get("type") == "agentMessage"]
        assert len(compatibility_items) == 1 and "phase" in compatibility_items[0] and compatibility_items[0]["phase"] is None and compatibility_items[0].get("text") == "PROBE_OK"
        gates["G0"]["checks"].append("missing_response_phase_persisted_as_null")

        phase = "dynamic_tools"
        dynamic_tools = [{"type": "namespace", "name": "telegram_gateway", "description": "Ferramentas fictícias do canal de laboratório.", "tools": [{"type": "function", "name": "ask_menu", "description": "Menu fictício sem Telegram real.", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "label": {"type": "string"}}, "required": ["id", "label"], "additionalProperties": False}}}, "required": ["question", "options"], "additionalProperties": False}}]}]
        for successful in (True, False):
            dynamic_thread = client.rpc("thread/start", {"cwd": str(workspace), "model": "probe", "approvalPolicy": "never", "sandbox": "read-only", "dynamicTools": dynamic_tools})["result"]["thread"]["id"]
            before_events, before_requests = len(client.events), len(provider.requests)
            dynamic_turn = client.turn(dynamic_thread, "PROBE_DYNAMIC_TOOL")
            request = client.wait(lambda event: event.get("method") == "item/tool/call" and "id" in event, after=before_events)
            params = request.get("params", {})
            assert params.get("threadId") == dynamic_thread and params.get("turnId") == dynamic_turn
            assert params.get("namespace") == "telegram_gateway" and params.get("tool") == "ask_menu"
            marker = "PROBE_DYNAMIC_SELECTION" if successful else "PROBE_DYNAMIC_CONTROLLED_ERROR"
            client.send({"id": request["id"], "result": {"contentItems": [{"type": "inputText", "text": marker}], "success": successful}})
            client.completed(dynamic_thread, dynamic_turn)
            assert any(marker in json.dumps(body) for body in provider.requests[before_requests:])
        gates["G0"]["checks"].extend(["gateway_dynamic_namespace_declared", "gateway_server_request_scoped", "dynamic_selection_returned_to_model", "dynamic_error_returned_to_model"])

        phase = "steering"
        thread = client.thread(workspace)
        provider.hold.clear()
        provider.entered.clear()
        before = len(client.events)
        turn = client.turn(thread, "PROBE_HOLD", "probe_hold_marker")
        assert provider.entered.wait(5)
        stale = client.rpc("turn/steer", {"threadId": thread, "expectedTurnId": "wrong_turn", "input": [{"type": "text", "text": "PROBE_STALE"}]}, tolerate_error=True)
        assert "error" in stale
        first = client.rpc("turn/steer", {"threadId": thread, "expectedTurnId": turn, "clientUserMessageId": "probe_steer_marker", "input": [{"type": "text", "text": "PROBE_STEER"}]})
        second = client.rpc("turn/steer", {"threadId": thread, "expectedTurnId": turn, "clientUserMessageId": "probe_steer_second", "input": [{"type": "text", "text": "PROBE_STEER_SECOND"}]})
        assert first["result"]["turnId"] == second["result"]["turnId"] == turn
        image = workspace / "probe.png"
        image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE1sAAAAASUVORK5CYII="))
        attachment_inputs = [{"type": "localImage", "path": str(image)}]
        for kind in ("document", "voice", "audio"):
            attachment_inputs.append({"type": "text", "text": json.dumps({"telegram_attachment": {"kind": kind, "local_path": str(image), "provenance": "untrusted_attachment", "transcription": {"status": "available", "text": "PROBE_TRANSCRIPTION_AUTOMATIC"} if kind == "voice" else None}})})
        attachment = client.rpc("turn/steer", {"threadId": thread, "expectedTurnId": turn, "clientUserMessageId": "probe_steer_attachment", "input": attachment_inputs})
        assert attachment["result"]["turnId"] == turn
        provider.hold.set()
        client.completed(thread, turn)
        rejected = client.rpc("turn/steer", {"threadId": thread, "expectedTurnId": turn, "input": [{"type": "text", "text": "PROBE_AFTER"}]}, tolerate_error=True)
        assert "error" in rejected
        started = [event for event in client.events[before:] if event.get("method") == "turn/started" and event.get("params", {}).get("threadId") == thread]
        assert len(started) == 1
        gates["G1"] = _gate("supported", "loopback_steering_verified", ["active_turn_input", "expected_turn_id", "stale_turn_rejected", "multiple_inputs_same_turn", "local_image_and_attachment_envelope", "voice_transcription_envelope", "no_new_started", "completed_turn_rejected"], input_kinds=["text", "photo", "document", "voice", "audio"])
        items = client.rpc("thread/items/list", {"threadId": thread, "limit": 100})["result"]
        serialized = json.dumps(items)
        assert "probe_hold_marker" in serialized and "probe_steer_marker" in serialized and "probe_steer_second" in serialized
        gates["G2"] = _gate("supported", "client_id_persisted", ["start_marker_in_history", "steer_markers_in_history", "ack_ignored_history_reconciliation"], history_method="thread/items/list", marker_field="clientId")

        phase = "children"
        thread = client.thread(workspace)
        before = len(client.events)
        turn = client.turn(thread, "PROBE_PARENT_TWO")
        final = client.completed(thread, turn)
        calls = [event["params"]["item"] for event in client.events[before:] if event.get("method") == "item/completed" and event.get("params", {}).get("threadId") == thread and event.get("params", {}).get("item", {}).get("type") == "collabAgentToolCall"]
        children = [child for call in calls if call.get("tool") == "spawnAgent" for child in call.get("receiverThreadIds", [])]
        waits = [call for call in calls if call.get("tool") == "wait"]
        if len(children) == 2 and waits and all(state.get("status") == "completed" for state in waits[-1].get("agentsStates", {}).values()):
            gates["G3"] = _gate("supported", "coordinator_children_verified", ["two_children", "runtime_parent_child_link", "wait_results", "principal_consolidation"], item_type="collabAgentToolCall", child_ids_field="receiverThreadIds", child_states_field="agentsStates")
        else:
            gates["G3"] = _gate("unverified", "children_completion_unverified", ["spawn_attempted"])

        phase = "child_error"
        thread = client.thread(workspace)
        before = len(client.events)
        turn = client.turn(thread, "PROBE_PARENT_TWO_ERROR")
        client.completed(thread, turn)
        error_states = [state.get("status") for event in client.events[before:] if event.get("method") == "item/completed" and event.get("params", {}).get("threadId") == thread and event.get("params", {}).get("item", {}).get("tool") == "wait" for state in event["params"]["item"].get("agentsStates", {}).values()]
        report["child_error_statuses"] = error_states
        assert "errored" in error_states
        gates["G3"]["checks"].append("child_failure_reported_to_principal")

        phase = "child_interrupt"
        provider.child_hold.clear()
        provider.child_entered.clear()
        thread = client.thread(workspace)
        before = len(client.events)
        turn = client.turn(thread, "PROBE_PARENT_TWO_INTERRUPT")
        assert provider.child_entered.wait(5)
        client.completed(thread, turn)
        # Select the running late child using its runtime spawn relationship.
        spawns = [event["params"]["item"] for event in client.events[before:] if event.get("method") == "item/completed" and event.get("params", {}).get("threadId") == thread and event.get("params", {}).get("item", {}).get("tool") == "spawnAgent"]
        late_id = next(item["receiverThreadIds"][0] for item in spawns if item.get("prompt") == "PROBE_CHILD_LATE")
        client.rpc("thread/resume", {"threadId": late_id})
        child_turns = client.rpc("thread/turns/list", {"threadId": late_id, "limit": 10})["result"]["data"]
        provider.child_hold.set()
        assert any(item.get("status") == "interrupted" for item in child_turns)
        gates["G3"]["checks"].append("child_interruption_reported")

        phase = "tool_results"
        thread = client.thread(workspace)
        turn = client.rpc("turn/start", {"threadId": thread, "input": [], "toolOutput": {"name": "onmyoji_related_work", "output": json.dumps({"provenance": "runtime_child_result", "result": "PROBE_RESULT"})}})["result"]["turn"]["id"]
        client.completed(thread, turn)
        items = client.rpc("thread/items/list", {"threadId": thread, "limit": 100})["result"]
        assert "functionCallOutput" in json.dumps(items) and "PROBE_RESULT" in json.dumps(items)
        gates["G7"] = _gate("supported", "tool_result_provenance_verified", ["standalone_tool_output_turn", "result_persisted_as_tool_output"])

        # Post-turn survival is measured separately from the base/steering path.
        phase = "post_turn"
        provider.child_hold.clear()
        provider.child_entered.clear()
        thread = client.thread(workspace)
        before = len(client.events)
        turn = client.turn(thread, "PROBE_PARENT_LATE")
        client.completed(thread, turn)
        late_calls = [event["params"]["item"] for event in client.events[before:] if event.get("method") == "item/completed" and event.get("params", {}).get("threadId") == thread and event.get("params", {}).get("item", {}).get("tool") == "spawnAgent"]
        late_children = late_calls[-1].get("receiverThreadIds", []) if late_calls else []
        if late_children and provider.child_entered.wait(5):
            provider.child_hold.set()
            try:
                client.completed(late_children[0], after=before)
                child_items = client.rpc("thread/items/list", {"threadId": late_children[0], "limit": 100})
                if "PROBE_OK" in json.dumps(child_items):
                    gates["G5"] = _gate("supported", "child_survives_parent_turn", ["parent_completed", "child_completed_later", "late_result_readable"], automatic_wakeup=False)
            except (RuntimeError, TimeoutError):
                gates["G5"] = _gate("unsupported", "late_child_not_recoverable", ["parent_completed", "late_child_observation_attempted"])
        else:
            provider.child_hold.set()
            gates["G5"] = _gate("unsupported", "child_not_running_after_parent", ["parent_completed", "child_start_observation_attempted"])

        # Kill a process while a child is still running, then inspect its history.
        phase = "process_restart"
        provider.child_hold.clear()
        provider.child_entered.clear()
        restart_thread = client.thread(workspace)
        before = len(client.events)
        restart_turn = client.turn(restart_thread, "PROBE_PARENT_LATE_RESTART")
        client.completed(restart_thread, restart_turn)
        assert provider.child_entered.wait(5)
        restart_spawn = client.wait(lambda event: event.get("method") == "item/completed" and event.get("params", {}).get("threadId") == restart_thread and event.get("params", {}).get("item", {}).get("tool") == "spawnAgent", after=before)["params"]["item"]
        restart_child = restart_spawn["receiverThreadIds"][0]
        client.close()
        provider.child_hold.set()
        client = ProbeClient(executable, home, workspace)
        client.rpc("thread/resume", {"threadId": thread, "cwd": str(workspace)})
        resumed = client.turn(thread, "PROBE_AFTER_RESTART")
        client.completed(thread, resumed)
        gates["G0"]["checks"].append("process_restart_resume_start")
        client.rpc("thread/resume", {"threadId": restart_child, "cwd": str(workspace)})
        recovered = client.rpc("thread/turns/list", {"threadId": restart_child, "itemsView": "full", "limit": 100})["result"]
        recovered_turns = recovered.get("data", [])
        interrupted = any(item.get("status") == "interrupted" for item in recovered_turns)
        gates["G6"] = _gate("unsupported" if interrupted else "unverified", "active_child_interrupted_by_process_loss" if interrupted else "active_child_recovery_unproven", ["active_child_process_terminated", "principal_history_resumed_after_restart", "child_history_resumed_without_generation"], fallback="reconcile_interrupted_work_no_automatic_retry")
        phase = "profiles"
        client.close()
        client = None
        profile_observations = []
        for git_workspace in (False, True):
            target = output / ("profile-git" if git_workspace else "profile-plain")
            target.mkdir(exist_ok=True)
            if git_workspace:
                subprocess.run(["git", "init", "--quiet", str(target)], check=True, capture_output=True)
            else:
                (target / ".probe-project-root").write_text("", encoding="utf-8")
            profile_dir = target / ".codex" / "agents"
            profile_dir.mkdir(parents=True, exist_ok=True)
            profile_path = profile_dir / "probe_profile.toml"
            profile_path.write_text('name="probe_profile"\ndescription="Local probe specialist"\ndeveloper_instructions="PROBE_PROFILE_INSTRUCTIONS_V1"\n', encoding="utf-8")
            common = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True, check=True).stdout.strip()
            trusts = {str(target), str(ROOT), str(Path(common).parent)}
            (home / "config.toml").write_text('project_root_markers=[".probe-project-root", ".git"]\n' + config + "\n" + "\n".join("[projects." + json.dumps(path) + "]\ntrust_level=\"trusted\"\n" for path in sorted(trusts)), encoding="utf-8")
            client = ProbeClient(executable, home, target)
            before_requests = len(provider.requests)
            marker_thread = client.thread(target)
            marker_turn = client.turn(marker_thread, "PROBE_PROFILE_PARENT")
            client.completed(marker_thread, marker_turn)
            selected_v1 = any("PROBE_PROFILE_CHILD" in _texts(request) and "PROBE_PROFILE_INSTRUCTIONS_V1" in json.dumps(request) for request in provider.requests[before_requests:])
            profile_path.write_text('name="probe_profile"\ndescription="Local probe specialist"\ndeveloper_instructions="PROBE_PROFILE_INSTRUCTIONS_V2"\n', encoding="utf-8")
            before_requests = len(provider.requests)
            marker_turn = client.turn(marker_thread, "PROBE_PROFILE_PARENT_AGAIN")
            client.completed(marker_thread, marker_turn)
            selected_v2 = any("PROBE_PROFILE_CHILD" in _texts(request) and "PROBE_PROFILE_INSTRUCTIONS_V2" in json.dumps(request) for request in provider.requests[before_requests:])
            client.close()
            client = None
            profile_observations.append({"workspace": "git" if git_workspace else "plain", "selected_v1_in_child": selected_v1, "selected_v2_in_same_thread_child": selected_v2})
        report["profile_observations"] = profile_observations
        dynamic = any(item["selected_v2_in_same_thread_child"] for item in profile_observations)
        gates["G4"] = _gate("unsupported" if dynamic else "unverified", "profile_dynamic_reload_without_lease" if dynamic else "profile_clients_snapshot_unverified", ["standalone_toml_in_plain_workspace", "standalone_toml_in_git_workspace", "trusted_project_configuration", "named_spawn_selection_observed", "profile_changed_in_live_thread"], fallback="do_not_materialize_profiles", observations=profile_observations)
    except (AssertionError, RuntimeError, TimeoutError, KeyError, OSError, StopIteration) as error:
        report["probe_failure"] = type(error).__name__
        report["failure_phase"] = phase
        if phase in {"child_error", "child_interrupt"}:
            gates["G3"] = _gate("unverified", "child_failure_or_interrupt_unverified", gates["G3"]["checks"])
        if phase in {"dynamic_tools", "phase_compatibility"}:
            gates["G0"] = _gate("unsupported", "gateway_dynamic_tools_unverified" if phase == "dynamic_tools" else "missing_phase_compatibility_unverified", gates["G0"]["checks"])
    finally:
        provider.hold.set()
        provider.child_hold.set()
        if client is not None and client.process.poll() is None:
            client.close()
        provider.close()
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", default="codex")
    parser.add_argument("--output", type=Path, default=ROOT / "tmp" / ("codex-probe-" + uuid.uuid4().hex))
    args = parser.parse_args()
    report = run(args.executable, args.output)
    print(json.dumps({"version": report["runtime"]["version"], "gates": {gate: {"status": value["status"], "reason": value["reason"]} for gate, value in report["gates"].items()}, "probe_failure": report.get("probe_failure")}, ensure_ascii=False))
    return 0 if report["gates"]["G0"]["status"] == "supported" and "probe_failure" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
