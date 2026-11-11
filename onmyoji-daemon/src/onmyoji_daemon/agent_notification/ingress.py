"""Adaptadores de entrada e saída; execução pertence ao núcleo compartilhado."""
from __future__ import annotations

import json
import re
import threading
import time

from .configuration import NotificationConfiguration
from .contract import validate_event
from .logging import NotificationLog, timestamp
from ..codex_execution import CodexLaunch
from ..codex_app_server import CodexProtocolError
from ..rpc import RpcError, disconnected


class NotificationIngress:
    def _init_notifications(self):
        self.notification_configuration = NotificationConfiguration(self.settings.root)
        self.notification_log = NotificationLog(self.store, self.settings.root / "configs" / "daemon" / "runtime" / "logs" / "agent-gateway")
        self._notification_delivery_lock = threading.Lock()
        self._notification_sync_lock = threading.Lock()
        self._notification_http = None

    def _notification_handle(self, method, params):
        configuration = self.notification_configuration.load()
        if method == "notification.transport-rejected":
            self._notification_transport_rejected(params.get("code"))
            return {"recorded": True}
        if method == "notification.configure":
            result = self.notification_configuration.configure(params)
            self._wake_worker("")
            return result
        if method in {"notification.status", "notification.diagnose"}:
            with self.database_lock:
                counts = dict(self.database.execute("SELECT COALESCE(execution_state,'pending'),COUNT(*) FROM notification_receipts GROUP BY execution_state").fetchall())
            checks = {"app_server_enabled": self.settings.app_server_enabled, "telegram_available": bool(self.api), "email_account_per_event": True}
            if method == "notification.diagnose":
                checks.update(self._notification_diagnose(configuration))
            return {"enabled": configuration["enabled"], "configuration": configuration, "counts": counts, "checks": checks, "execution": self.store.summary()}
        if method != "notification.submit":
            raise RpcError("Operação de notificação desconhecida.", "unknown_method")
        return self._notification_submit(params, wait=True)

    def _notification_submit(self, params, *, wait):
        configuration = self.notification_configuration.load()
        if not configuration["enabled"] or self.stop_event.is_set():
            raise RpcError("Ingresso de notificações desabilitado.", "ingress_disabled")
        try:
            event = validate_event(params)
        except RpcError as error:
            self.notification_log.record("rejected", code=error.code, details=error.details)
            raise
        channel = event["response_channel"]
        destination = self._notification_destination_key(channel)
        self._ensure_conversation(destination)
        generation = self._generation(destination)
        received = timestamp()
        receipt = {"event_id": event["event_id"], "received_at": received, "envelope": event}
        context = {"notification": event, "source_trust": "AUTHORIZED_USER", "has_text": True}
        if channel["type"] == "telegram":
            context["conversation_kind"] = self._conversation_kind(destination)
        identifier = self.store.enqueue(destination, generation, event["prompt"], [], context, origin="event", receipt=receipt, limit=50, global_limit=1024)
        try:
            self.notification_log.flush()
        except OSError:
            pass  # O journal conserva a recepção; watcher recupera antes do despacho.
        self._wake_worker(destination)
        if not wait:
            return {"event_id": event["event_id"], "received_at": received, "accepted": True}
        while True:
            with self.database_lock:
                row = self.database.execute("SELECT execution_state,delivery_state,reason FROM notification_receipts WHERE inbox_id=?", (identifier,)).fetchone()
            if row[0] is not None:
                status = "uncertain" if "uncertain" in {row[0], row[1]} else "completed" if row[0] == "completed" and row[1] == "confirmed" else "failed"
                return {"event_id": event["event_id"], "received_at": received, "status": status, "result": row[0], "execution": row[0], "delivery": row[1], **({"reason": row[2]} if row[2] else {})}
            if disconnected():
                raise RpcError("Acompanhamento abandonado; execução aceita continua.", "acceptance_unknown")
            if self.stop_event.wait(0.1):
                raise RpcError("Gateway encerrando; execução aceita permanece registrada.", "acceptance_unknown")

    @staticmethod
    def _notification_destination_key(channel):
        if channel["type"] == "telegram":
            return channel["value"]
        return "email:" + json.dumps([channel["account"].strip(), channel["value"].strip()], ensure_ascii=False, separators=(",", ":"))

    def _start_notification_http(self):
        from .http import NotificationHttpServer
        configuration = self.notification_configuration.load()
        self._notification_http = NotificationHttpServer(configuration["port"], self, configuration["max_request_bytes"], configuration["long_limit"])
        self._notification_http.start()

    def _stop_notification_http(self):
        server, self._notification_http = self._notification_http, None
        if server is not None:
            server.close()

    def _notification_diagnose(self, configuration):
        from .contract import SCHEMA, VALIDATOR
        VALIDATOR.check_schema(SCHEMA)
        checks = {"contract_valid": True, "schema_version": 1, "http_port": configuration["port"], "incident_available": None}
        if configuration["incident"]:
            try:
                self._notification_destination({"response_channel": configuration["incident"]})
            except Exception as error:
                checks.update(incident_available=False, incident_reason=getattr(error, "code", "destination_unavailable"))
            else:
                checks["incident_available"] = True
        return checks

    def _notification_thread(self, conversation, client, event):
        with self.database_lock:
            row = self.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone()
        previous = row[0] if row else None
        params = {"cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandbox": self.settings.sandbox, "developerInstructions": CodexLaunch.from_system(self.settings.root).developer_instructions()}
        if self.settings.model:
            params["model"] = self.settings.model
        if previous and event["thread_policy"] == "reuse":
            try:
                client.request("thread/resume", {"threadId": previous, **params})
                return previous
            except CodexProtocolError as error:
                if not self._thread_is_missing(error):
                    raise
        response = client.request("thread/start", {**params, "dynamicTools": [], "serviceName": "onmyoji_notification"})
        thread = response.get("thread", response)
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise CodexProtocolError("Criação de thread sem identificador.", kind="not_sent")
        return thread["id"]

    def _notification_transport_rejected(self, code):
        if code not in {"invalid_json", "request_too_large", "invalid_request", "unauthorized", "capacity_exceeded"}:
            raise RpcError("Motivo de rejeição inválido.", "invalid_argument")
        self.notification_log.record("transport_rejected", code=code)

    def _notification_destination(self, event):
        channel = event["response_channel"]
        if channel["type"] == "telegram":
            if not self.api or not self._channel_registered(channel["value"]):
                raise RpcError("Canal Telegram indisponível ou não cadastrado.", "destination_unavailable")
            self._telegram_address(channel["value"])
        else:
            if re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", channel["value"]) is None:
                raise RpcError("Endereço de e-mail inválido.", "destination_unavailable")
            account = channel.get("account")
            if not account:
                raise RpcError("Informe a conta SMTP do evento.", "smtp_unconfigured")
            from ..mcp.email.service import EmailService
            configured, _limits = EmailService(self.settings.root).account(account)
            if not configured.get("smtp"):
                raise RpcError("Conta sem SMTP.", "smtp_unconfigured")

    def _notification_send(self, channel, text, key, event_id):
        if channel["type"] == "telegram":
            if not self.api or not self._channel_registered(channel["value"]):
                return "failed"
            from ..telegram_formatting import render_html, split_html, MAX_MESSAGE_LENGTH
            address, topic = self._telegram_address(channel["value"])
            try:
                for chunk in split_html(render_html(text), MAX_MESSAGE_LENGTH):
                    self.api.send(address, chunk, parse_mode="HTML", **topic)
                return "confirmed"
            except Exception as error:
                return "failed" if getattr(error, "kind", "") == "not_sent" else "uncertain"
        from ..mcp.email.service import EmailService
        account = channel.get("account")
        if not account:
            return "failed"
        try:
            result = EmailService(self.settings.root).invoke(account, "send_message", {"confirm": True, "request_id": key, "to": [channel["value"]], "subject": channel["subject"], "text": text})
            return {"accepted": "confirmed", "rejected": "failed"}.get(result.get("status"), "uncertain")
        except Exception as error:
            # Falha em persistir o recibo pode acontecer após aceite SMTP.
            return "failed" if getattr(error, "code", "") in {"account_unavailable", "operation_unavailable", "sender_not_allowed", "invalid_argument", "recipient_required", "smtputf8_unavailable"} else "uncertain"

    def _notification_deliver(self, execution, event, text):
        with self._notification_delivery_lock:
            with self.database_lock, self.database:
                row = self.database.execute("SELECT n.inbox_id,n.delivery_state,i.turn_context FROM notification_receipts n JOIN inbox_items i ON i.id=n.inbox_id WHERE i.execution_id=? LIMIT 1", (execution,)).fetchone()
                if not row or row[1] != "not_attempted":
                    return
                self.database.execute("UPDATE notification_receipts SET delivery_state='sending' WHERE inbox_id=?", (row[0],))
                self.database.execute("UPDATE execution_turns SET delivery_state='sending' WHERE execution_id=?", (execution,))
            if event["response_channel"]["type"] == "telegram":
                from ..telegram import AppServerTurn
                from ..telegram_delivery import DeliverySuppressed, DeliveryUncertain
                with self.app_server_lock:
                    slot = next((slot for slot in self._slots.values() if slot.identifier == execution), None)
                    active = slot.active if slot else None
                if active is None:
                    with self.database_lock:
                        saved = self.database.execute("SELECT conversation_id,generation,thread_id,turn_id,reply_mode,final_item_id,user_cancelled FROM execution_turns WHERE execution_id=?", (execution,)).fetchone()
                    address, topic = self._telegram_address(saved[0])
                    active = AppServerTurn(address, saved[2] or "", threading.Event(), turn_id=saved[3], conversation_id=saved[0], generation=saved[1], execution_id=execution, reply_mode=saved[4] or "text", final_item_id=saved[5] or "final", telegram_topic_values=topic, user_cancelled=bool(saved[6]))
                    active.completed.set()
                try:
                    self._settle_visible(active)
                    self._deliver_terminal(active, text, active.reply_mode)
                    self._finish_resources(active)
                    state = "confirmed"
                except DeliverySuppressed:
                    state = "failed"
                except DeliveryUncertain:
                    state = "uncertain"
                except Exception:
                    state = "uncertain"
            else:
                state = self._notification_send(event["response_channel"], text, "gateway:" + row[0] + ":delivery", event["event_id"])
            with self.database_lock, self.database:
                self.database.execute("UPDATE notification_receipts SET delivery_state=? WHERE inbox_id=?", (state, row[0]))
                self.database.execute("UPDATE execution_turns SET delivery_state=? WHERE execution_id=?", ("done" if state == "confirmed" else state, execution))
                self.notification_log.record("delivery", transaction=self.database, event_id=event["event_id"], state=state)

    def _notification_sync(self):
        # Acompanhamentos RPC só observam. Um único sincronizador publica
        # associações e entregas, evitando snapshots concorrentes e rollback
        # tardio de uma thread já substituída por um evento posterior.
        if not self._notification_sync_lock.acquire(blocking=False):
            return
        try:
            self._notification_sync_serial()
        finally:
            self._notification_sync_lock.release()

    def _notification_sync_serial(self):
        with self.database_lock:
            rows = self.database.execute("SELECT n.inbox_id,n.event_id,n.envelope,n.execution_state,n.delivery_state,i.state,i.execution_id,e.state,e.result,e.terminal_text,e.thread_id,e.resource_revision,i.failure_reason FROM notification_receipts n JOIN inbox_items i ON i.id=n.inbox_id LEFT JOIN execution_turns e ON e.execution_id=i.execution_id WHERE n.execution_state IS NULL OR n.execution_state='uncertain'").fetchall()
        for inbox, event_id, raw, _state, delivery, input_state, execution, execution_state, result, text, thread, revision, failure_reason in rows:
            if execution_state not in {"terminal", "uncertain"} and input_state not in {"failed", "cancelled", "uncertain", "uncertain_legacy"}:
                continue
            event = json.loads(raw)
            empty_result = execution_state == "terminal" and result == "completed" and not text
            state = "uncertain" if execution_state == "uncertain" or input_state in {"uncertain", "uncertain_legacy"} else "failed" if empty_result else "completed" if result == "completed" else "not_started" if result in {None, "not_started", "not_sent"} else "failed"
            if execution_state == "terminal" and result == "completed" and text and thread:
                with self.database_lock, self.database:
                    self.database.execute("UPDATE conversations SET codex_thread_id=?,instruction_baseline_hash=? WHERE chat_id=(SELECT chat_id FROM inbox_items WHERE id=?) AND generation=(SELECT generation FROM inbox_items WHERE id=?)", (thread, revision, inbox, inbox))
            if text and execution_state == "terminal" and delivery == "not_attempted":
                self._notification_deliver(execution, event, text)
            with self.database_lock, self.database:
                current = self.database.execute("SELECT delivery_state FROM notification_receipts WHERE inbox_id=?", (inbox,)).fetchone()[0]
                if current == "sending":
                    continue
                reason = (None if current == "confirmed" else "delivery_" + current) if state == "completed" else "empty_agent_result" if empty_result else failure_reason or result or input_state
                changed = self.database.execute("UPDATE notification_receipts SET execution_state=?,reason=? WHERE inbox_id=? AND (execution_state IS NULL OR execution_state!=?)", (state, reason, inbox, state)).rowcount
                if changed:
                    self.notification_log.record("terminal", transaction=self.database, event_id=event_id, execution=state, delivery=current, output=text, reason=reason)
            if changed:
                self._notification_incident(inbox, event_id, state, current, reason)
        with self.database_lock:
            pending_incidents = self.database.execute("SELECT inbox_id,event_id,execution_state,delivery_state,reason FROM notification_receipts WHERE execution_state IS NOT NULL AND incident_state='pending'").fetchall()
        for values in pending_incidents:
            self._notification_incident(*values)

    def _notification_incident(self, inbox, event_id, state, delivery, reason):
        with self.database_lock, self.database:
            changed = self.database.execute("UPDATE notification_receipts SET incident_state='attempted' WHERE inbox_id=? AND incident_state='pending'", (inbox,)).rowcount
        channel = self.notification_configuration.load()["incident"]
        if changed and channel and (state != "completed" or delivery != "confirmed"):
            at = timestamp()
            log_ref = "agent-gateway-" + at[:10] + ".jsonl"
            summary = f"Onmyōji · {event_id} · {at} · execução={state} · entrega={delivery} · motivo={reason} · log={log_ref}"
            result = self._notification_send(channel, summary, "incident:" + inbox, event_id)
            self.notification_log.record("incident", event_id=event_id, state=result)

    def _notification_watch(self):
        last_purge = float("-inf")
        while not self.stop_event.is_set():
            try:
                self.notification_log.flush()
                self._notification_sync()
                if time.monotonic() - last_purge >= 86400:
                    self.notification_log.purge()
                    last_purge = time.monotonic()
                if self.store.candidates():
                    self._wake_worker("")
            except Exception:
                self._diagnostic("notification.maintenance_failed", reason="maintenance_failed")
            self.stop_event.wait(1)
