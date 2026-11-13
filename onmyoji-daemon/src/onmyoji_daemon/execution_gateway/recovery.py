"""Reconcilia confirmações do protocolo sem repetir entradas ou efeitos."""
from __future__ import annotations

import time
from typing import Any


_TERMINAL = {"completed", "interrupted", "failed"}
_LABELS = {
    "uncertain_executions": "execuções sem conclusão confirmada",
    "reconciling_executions": "execuções em reconciliação",
    "uncertain_inputs": "entradas sem confirmação",
    "uncertain_ingress": "recebimentos interrompidos",
    "pending_deliveries": "respostas concluídas aguardando entrega ou limpeza",
    "pending_receipts": "recibos de eventos aguardando atualização",
    "uncertain_deliveries": "envios ao Telegram sem confirmação",
}


class ExecutionRecovery:
    """Operações de recuperação compartilhadas pelo coordenador do Gateway."""

    def _recovery_read_error(self, execution: str, error: Exception) -> None:
        reason = getattr(error, "kind", "history_unavailable")
        now = time.monotonic()
        with self.database_lock:
            recent = getattr(self, "_recovery_errors", {})
            previous = recent.get(execution)
            if previous and previous[0] == reason and now - previous[1] < 60:
                return
            if len(recent) >= 128:
                recent.clear()
            recent[execution] = (reason, now)
            self._recovery_errors = recent
        self._diagnostic("recovery.read_failed", execution_id=execution, reason=reason, code=type(error).__name__)

    def _request_input_operation(self, client: Any, operation: str | None, method: str, params: dict[str, Any]) -> dict[str, Any]:
        tracked = getattr(client, "request_tracked", None)
        if operation and callable(tracked):
            def observed(metadata: dict[str, Any]) -> None:
                if metadata.get("method") != method:
                    raise ValueError("Método da RPC divergente da operação persistida.")
                self.store.record_operation_rpc(operation, metadata["process_epoch"], metadata["rpc_id"])
            return tracked(method, params, observer=observed)
        return client.request(method, params)

    def _recovery_status_text(self, conversation: str) -> str:
        reasons = self.store.block_reasons(conversation)
        if not reasons:
            return "Não há pendências de recuperação nesta conversa."
        lines = ["Estado da conversa:"]
        lines.extend(f"• {count} {_LABELS[key]}." for key, count in reasons.items())
        if reasons.get("uncertain_executions") or reasons.get("uncertain_inputs"):
            gates = getattr(self.capabilities, "gates", {})
            history_method = gates.get("G2", {}).get("history_method") if isinstance(gates, dict) else None
            if self.capabilities.supports("input_reconciliation") and history_method in {"thread/items/list", "thread/read"}:
                lines.append("O Gateway consulta as confirmações disponíveis sem reenviar o pedido.")
            else:
                reason = getattr(self.capabilities, "reason", lambda _feature: "runtime_unverified")("input_reconciliation")
                if reason == "supported":
                    reason = "history_method_unverified"
                lines.append(f"A recuperação de entradas pelo histórico não está habilitada neste runtime ({reason}). Conclusões de turnos já identificados ainda podem ser consultadas.")
        if reasons.get("pending_deliveries") or reasons.get("pending_receipts"):
            lines.append("As respostas e os recibos pendentes são recuperados separadamente; descartar solicitações não elimina essas pendências.")
        if any(reasons.get(key) for key in ("uncertain_executions", "reconciling_executions", "uncertain_inputs", "uncertain_ingress")):
            lines.append("Se a incerteza persistir, confira os efeitos externos e use /resolver descartar para encerrar as solicitações incertas localmente, sem repeti-las.")
        if reasons.get("uncertain_deliveries"):
            lines.append("Envios sem confirmação não são repetidos automaticamente.")
        return "\n".join(lines)

    def _recovery_error_text(self, conversation: str, error: Exception) -> str:
        reasons = self.store.block_reasons(conversation)
        if any(reasons.get(key) for key in ("uncertain_executions", "reconciling_executions", "uncertain_inputs")):
            return "A confirmação de início ou término do atendimento não chegou. Estou verificando o estado sem reenviar o pedido. Use /resolver para consultar a pendência."
        if reasons.get("uncertain_ingress"):
            return "O recebimento da mensagem foi interrompido. Use /resolver para consultar a entrada pendente; ela não será reenviada automaticamente."
        if reasons.get("pending_deliveries"):
            return "O atendimento terminou, mas a entrega da resposta ainda está pendente. Vou recuperar a resposta salva; use /resolver para consultar a entrega."
        return self._public_turn_error(error)

    def _late_input_response(self, params: dict[str, Any], event: dict[str, Any] | None) -> None:
        # Somente o transporte local pode fornecer essa prova; notificações com
        # o mesmo nome vindas do subprocesso não recebem esta marca.
        if not event or event.get("transport_generated") is not True:
            return
        epoch, rpc_id = event.get("process_epoch"), params.get("id")
        if not isinstance(epoch, str) or not epoch or type(rpc_id) not in (str, int) or isinstance(rpc_id, str) and not rpc_id:
            return
        active = None
        with self.database_lock, self.database:
            operation = self.store.operation_for_rpc(epoch, rpc_id)
            if not operation or operation["state"] not in {"dispatching", "uncertain"}:
                return
            if operation["method"] not in {"turn/start", "turn/steer"}:
                return
            if operation["result"] == "owner_discarded":
                return
            input_state = self.database.execute("SELECT state FROM inbox_items WHERE id=?", (operation["inbox_id"],)).fetchone()
            if not input_state or input_state[0] in {"cancelled", "failed"}:
                return
            execution, conversation = operation["execution_id"], operation["conversation_id"]
            error = params.get("error")
            rejected = isinstance(error, dict) and type(error.get("code")) is int and isinstance(error.get("message"), str) and "result" not in params
            result = params.get("result")
            if rejected:
                self.store.operation_result(operation["operation_id"], "rejected", "late_rejection")
                if operation["method"] == "turn/start":
                    self.store.fail_input(operation["inbox_id"], "start_rejected")
                    # A rejeição definitiva confirma que este início não ocorreu.
                    self.store.save_terminal_output(execution, "not_started", None, "", "text", False)
                elif operation["user_cancelled"] or operation["generation"] != self._generation(conversation):
                    self.database.execute("UPDATE inbox_items SET state='cancelled' WHERE id=? AND state='pending'", (operation["inbox_id"],))
                else:
                    self.database.execute("UPDATE inbox_items SET route='queue' WHERE id=? AND state='pending'", (operation["inbox_id"],))
            elif isinstance(result, dict) and "error" not in params:
                turn = result.get("turn", result) if operation["method"] == "turn/start" else None
                identifier = turn.get("id") if isinstance(turn, dict) else result.get("turnId")
                expected = operation["turn_id"] if operation["method"] == "turn/start" else operation["expected_turn_id"]
                if not isinstance(identifier, str) or not identifier or expected and expected != identifier:
                    return
                self.database.execute("UPDATE execution_turns SET turn_id=COALESCE(turn_id,?) WHERE execution_id=?", (identifier, execution))
                self.store.operation_result(operation["operation_id"], "accepted", "late_ack_confirmed")
            else:
                return
        with self.app_server_lock:
            current = self.app_turns.get(operation["thread_id"])
            if current and current.execution_id == execution and current.process_epoch == epoch:
                active = current
                if rejected and operation["method"] == "turn/start":
                    active.status, active.failure_detail = "failed", "start_rejected"
                    active.completed.set()
                    if getattr(active, "detached", False):
                        self.app_turns.pop(active.thread_id, None)
                elif not rejected:
                    active.turn_id = identifier
        if rejected and operation["method"] == "turn/start":
            self._release_uncertain_permit(execution)
        self._diagnostic("input.reconciled", execution_id=execution, method=operation["method"], reason="late_rejection" if rejected else "late_ack_confirmed", code=error.get("code") if rejected else "accepted")
        self._wake_worker(conversation)

    def _history_marker_turn(self, client: Any, thread: str, marker: str, method: str) -> str | None:
        if method == "thread/items/list":
            cursor = None
            visited = set()
            for _ in range(8):
                params = {"threadId": thread, "limit": 100, "sortDirection": "desc"}
                if cursor is not None:
                    params["cursor"] = cursor
                result = client.request(method, params, timeout=5)
                entries = result.get("data") if isinstance(result, dict) else None
                if not isinstance(entries, list):
                    return None
                for entry in entries:
                    item = entry.get("item") if isinstance(entry, dict) else None
                    turn = entry.get("turnId") if isinstance(entry, dict) else None
                    if isinstance(item, dict) and item.get("type") == "userMessage" and item.get("clientId") == marker and isinstance(turn, str) and turn:
                        return turn
                cursor = result.get("nextCursor")
                if not isinstance(cursor, str) or not cursor or cursor in visited:
                    return None
                visited.add(cursor)
            return None
        if method == "thread/read":
            history = client.request(method, {"threadId": thread, "includeTurns": True}, timeout=5).get("thread", {})
            if not isinstance(history, dict) or history.get("id") != thread:
                return None
            for turn in history.get("turns", []):
                if isinstance(turn, dict) and isinstance(turn.get("id"), str) and any(isinstance(item, dict) and item.get("type") == "userMessage" and item.get("clientId") == marker for item in turn.get("items", [])):
                    return turn["id"]
        return None

    def _reconcile_conversation(self, conversation: str) -> None:
        supported = self.capabilities.supports("input_reconciliation")
        gates = getattr(self.capabilities, "gates", {})
        method = gates.get("G2", {}).get("history_method") if isinstance(gates, dict) else None
        with self.database_lock:
            operations = self.database.execute("""SELECT o.operation_id,o.client_message_id,e.thread_id,e.execution_id,o.method,o.expected_turn_id,e.turn_id,o.state
                FROM input_operations o JOIN execution_turns e ON e.execution_id=o.execution_id
                WHERE e.conversation_id=? AND o.state IN ('accepted','uncertain')
                AND (o.state='uncertain' OR e.state IN ('uncertain','reconciling'))""", (conversation,)).fetchall()
        for operation, marker, thread, execution, operation_method, expected_turn, known_turn, operation_state in operations:
            if not thread:
                continue
            target = known_turn if operation_state == "accepted" else None
            if target is None and (not supported or method not in {"thread/items/list", "thread/read"}):
                continue
            try:
                client = self._app_client()
                # Um ACK já recebido identifica o turno sem depender do marcador
                # opcional de G2. Isso não confirma um steering ainda incerto.
                if target is None:
                    target = self._history_marker_turn(client, thread, marker, method)
                if not target or expected_turn and target != expected_turn or known_turn and target != known_turn:
                    continue
                response = client.request("thread/read", {"threadId": thread, "includeTurns": True}, timeout=5)
                history = response.get("thread") if isinstance(response, dict) else None
                if not isinstance(history, dict) or history.get("id") != thread:
                    continue
                turn = next((value for value in history.get("turns", []) if isinstance(value, dict) and value.get("id") == target), None)
                if not turn:
                    continue
            except Exception as error:
                self._recovery_read_error(execution, error)
                continue
            with self.database_lock, self.database:
                # Revalidar após I/O: descarte, cancelamento e novas provas podem
                # ter vencido a consulta. Nenhum snapshot autoriza ressuscitar input.
                current = self.database.execute("""SELECT o.state,e.state,e.result,e.generation,e.reply_mode,e.final_item_id,e.user_cancelled,
                    COALESCE(i.origin,'telegram'),e.silent_completion,e.terminal_text,e.turn_id,i.state
                    FROM input_operations o JOIN execution_turns e ON e.execution_id=o.execution_id JOIN inbox_items i ON i.id=o.inbox_id
                    WHERE o.operation_id=? AND e.execution_id=? AND e.thread_id=?""", (operation, execution, thread)).fetchone()
                if not current or current[0] not in {"accepted", "uncertain"} or current[2] == "owner_discarded" or current[11] in {"cancelled", "failed"}:
                    continue
                if current[10] and current[10] != target:
                    continue
                self.database.execute("UPDATE execution_turns SET turn_id=COALESCE(turn_id,?) WHERE execution_id=?", (target, execution))
                if current[0] != "accepted":
                    self.store.operation_result(operation, "accepted", "history_confirmed")
                status = turn.get("status")
                if status not in _TERMINAL:
                    continue
                # Reconciliação de outra entrada não pode apagar um final salvo.
                if current[1] == "terminal" and current[2] in _TERMINAL | {"completed_silent"}:
                    terminal = True
                else:
                    items = turn.get("items") if isinstance(turn.get("items"), list) else []
                    final_item = next((item for item in reversed(items) if isinstance(item, dict) and item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"} and isinstance(item.get("text"), str)), {})
                    final = final_item.get("text", "")
                    silent = bool(current[8] and status == "completed")
                    answer = None if silent else (final or (None if current[7] == "event" else "O agente concluiu o turno sem uma resposta textual.")) if status == "completed" else "Resposta parcial; o turno foi interrompido ou falhou.\n\n" + final if final else "O agente não conseguiu concluir esta solicitação."
                    if current[6]:
                        answer = "O turno foi cancelado. As mensagens ainda na fila foram preservadas. Efeitos externos já iniciados podem exigir conferência."
                    answer = answer if current[3] == self._generation(conversation) else None
                    item_id = current[5] or final_item.get("id") or "recovered-final"
                    self.store.save_terminal_output(execution, "completed_silent" if silent else status, answer, item_id, current[4] or "text", self._conversation_settings(conversation)["delete_thoughts"])
                    terminal = True
                saved = self.database.execute("SELECT terminal_text,final_item_id,reply_mode FROM execution_turns WHERE execution_id=?", (execution,)).fetchone()
            if terminal:
                with self.app_server_lock:
                    active = self.app_turns.get(thread)
                    if active and active.execution_id == execution and active.turn_id in {None, target}:
                        active.turn_id, active.status = target, status
                        if saved:
                            active.recovered_answer, active.final_item_id, active.reply_mode = saved
                        active.completed.set()
                        if getattr(active, "detached", False):
                            self.app_turns.pop(thread, None)
                self._release_uncertain_permit(execution)
                self._wake_worker(conversation)
