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
        self.notification_log = NotificationLog(self.store, self.settings.data_dir / "notification-logs")
        self._notification_delivery_lock = threading.Lock()

    def _notification_handle(self, method, params):
        configuration = self.notification_configuration.load()
        if method == "notification.configure":
            result = self.notification_configuration.configure(params)
            self._wake_worker("")
            return result
        if method in {"notification.status", "notification.diagnose"}:
            with self.database_lock:
                counts = dict(self.database.execute("SELECT COALESCE(execution_state,'pending'),COUNT(*) FROM notification_receipts GROUP BY execution_state").fetchall())
            checks = {"app_server_enabled": self.settings.app_server_enabled, "telegram_available": bool(self.api), "smtp_configured": bool(configuration["smtp_account"])}
            return {"enabled": configuration["enabled"], "configuration": configuration, "counts": counts, "checks": checks, "execution": self.store.summary()}
        if method != "notification.submit":
            raise RpcError("Operação de notificação desconhecida.", "unknown_method")
        if not configuration["enabled"] or self.stop_event.is_set():
            raise RpcError("Ingresso de notificações desabilitado.", "ingress_disabled")
        try:
            event = validate_event(params)
        except RpcError as error:
            self.notification_log.record("rejected", code=error.code, details=error.details)
            raise
        channel = event["response_channel"]
        destination = channel["value"] if channel["type"] == "telegram" else "email:" + channel["value"].strip()
        self._ensure_conversation(destination)
        generation = self._generation(destination)
        received = timestamp()
        receipt = {"event_id": event["event_id"], "received_at": received, "envelope": event}
        identifier = self.store.enqueue(destination, generation, event["prompt"], [], {"notification": event}, origin="event", receipt=receipt, limit=self.settings.max_pending_items)
        try:
            self.notification_log.flush()
        except OSError:
            pass  # O journal conserva a recepção; watcher recupera antes do despacho.
        self._wake_worker(destination)
        while True:
            self._notification_sync()
            with self.database_lock:
                row = self.database.execute("SELECT execution_state,delivery_state,reason FROM notification_receipts WHERE inbox_id=?", (identifier,)).fetchone()
            if row[0] is not None:
                return {"event_id": event["event_id"], "received_at": received, "execution": row[0], "delivery": row[1], **({"reason": row[2]} if row[2] else {})}
            if disconnected():
                raise RpcError("Acompanhamento abandonado; execução aceita continua.", "acceptance_unknown")
            if self.stop_event.wait(0.1):
                raise RpcError("Gateway encerrando; execução aceita permanece registrada.", "acceptance_unknown")

    def _notification_thread(self, conversation, client, event):
        with self.database_lock:
            row = self.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone()
        previous = row[0] if row else None
        params = {"cwd": str(self.settings.project), "approvalPolicy": self.settings.approval, "sandbox": self.settings.sandbox, "developerInstructions": CodexLaunch.from_system(self.settings.root).developer_instructions(), "dynamicTools": []}
        if self.settings.model:
            params["model"] = self.settings.model
        if previous and event["thread_policy"] == "reuse":
            try:
                client.request("thread/resume", {"threadId": previous, **params})
                return previous
            except CodexProtocolError as error:
                if not self._thread_is_missing(error):
                    raise
        response = client.request("thread/start", {**params, "serviceName": "onmyoji_notification"})
        thread = response.get("thread", response)
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str) or not thread["id"]:
            raise CodexProtocolError("Criação de thread sem identificador.", kind="not_sent")
        return thread["id"]

    def _notification_destination(self, event):
        channel = event["response_channel"]
        if channel["type"] == "telegram":
            if not self.api or not self._channel_registered(channel["value"]):
                raise RpcError("Canal Telegram indisponível ou não cadastrado.", "destination_unavailable")
            self._telegram_address(channel["value"])
        else:
            if re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", channel["value"]) is None:
                raise RpcError("Endereço de e-mail inválido.", "destination_unavailable")
            account = self.notification_configuration.load()["smtp_account"]
            if not account:
                raise RpcError("Selecione a conta SMTP padrão.", "smtp_unconfigured")
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
        account = self.notification_configuration.load()["smtp_account"]
        if not account:
            return "failed"
        try:
            result = EmailService(self.settings.root).invoke(account, "send_message", {"confirm": True, "request_id": key, "to": [channel["value"]], "subject": "Onmyōji · evento " + event_id, "text": text})
            return {"accepted": "confirmed", "rejected": "failed"}.get(result.get("status"), "uncertain")
        except Exception:
            return "failed"

    def _notification_deliver(self, execution, event, text):
        with self._notification_delivery_lock:
            with self.database_lock, self.database:
                row = self.database.execute("SELECT inbox_id,delivery_state FROM notification_receipts WHERE inbox_id=(SELECT id FROM inbox_items WHERE execution_id=? LIMIT 1)", (execution,)).fetchone()
                if not row or row[1] != "not_attempted":
                    return
                self.database.execute("UPDATE notification_receipts SET delivery_state='sending' WHERE inbox_id=?", (row[0],))
                self.database.execute("UPDATE execution_turns SET delivery_state='sending' WHERE execution_id=?", (execution,))
            state = self._notification_send(event["response_channel"], text, "notification:" + row[0], event["event_id"])
            with self.database_lock, self.database:
                self.database.execute("UPDATE notification_receipts SET delivery_state=? WHERE inbox_id=?", (state, row[0]))
                self.database.execute("UPDATE execution_turns SET delivery_state=? WHERE execution_id=?", ("done" if state == "confirmed" else state, execution))
            self.notification_log.record("delivery", event_id=event["event_id"], state=state)

    def _notification_sync(self):
        # Uma única rotina faz entrega/registro de finais recuperados, sem executar agente.
        with self.database_lock:
            rows = self.database.execute("SELECT n.inbox_id,n.event_id,n.envelope,n.execution_state,n.delivery_state,i.state,i.execution_id,e.state,e.result,e.terminal_text,e.thread_id FROM notification_receipts n JOIN inbox_items i ON i.id=n.inbox_id LEFT JOIN execution_turns e ON e.execution_id=i.execution_id WHERE n.execution_state IS NULL OR n.execution_state='uncertain'").fetchall()
        for inbox, event_id, raw, _state, delivery, input_state, execution, execution_state, result, text, thread in rows:
            if execution_state not in {"terminal", "uncertain"} and input_state not in {"failed", "cancelled", "uncertain", "uncertain_legacy"}:
                continue
            event = json.loads(raw)
            state = "uncertain" if execution_state == "uncertain" or input_state in {"uncertain", "uncertain_legacy"} else "completed" if result == "completed" else "not_started" if result in {None, "not_started", "not_sent"} else "failed"
            if execution_state == "terminal" and result == "completed" and thread:
                with self.database_lock, self.database:
                    self.database.execute("UPDATE conversations SET codex_thread_id=?,instruction_baseline_hash=NULL WHERE chat_id=(SELECT chat_id FROM inbox_items WHERE id=?)", (thread, inbox))
            if text and execution_state == "terminal" and delivery == "not_attempted":
                self._notification_deliver(execution, event, text)
            with self.database_lock, self.database:
                current = self.database.execute("SELECT delivery_state FROM notification_receipts WHERE inbox_id=?", (inbox,)).fetchone()[0]
                if current == "sending":
                    continue
                reason = None if state == "completed" and current == "confirmed" else result or input_state
                changed = self.database.execute("UPDATE notification_receipts SET execution_state=?,reason=? WHERE inbox_id=? AND (execution_state IS NULL OR execution_state!=?)", (state, reason, inbox, state)).rowcount
            if changed:
                self.notification_log.record("terminal", event_id=event_id, execution=state, delivery=current, output=text, reason=reason)
                self._notification_incident(inbox, event_id, state, current, reason)

    def _notification_incident(self, inbox, event_id, state, delivery, reason):
        with self.database_lock, self.database:
            changed = self.database.execute("UPDATE notification_receipts SET incident_state='attempted' WHERE inbox_id=? AND incident_state='pending'", (inbox,)).rowcount
        channel = self.notification_configuration.load()["incident"]
        if changed and channel and (state != "completed" or delivery != "confirmed"):
            summary = f"Onmyōji · {event_id} · {timestamp()} · execução={state} · entrega={delivery} · motivo={reason}"
            result = self._notification_send(channel, summary, "incident:" + inbox, event_id)
            self.notification_log.record("incident", event_id=event_id, state=result)

    def _notification_watch(self):
        last_purge = 0
        while not self.stop_event.is_set():
            try:
                self.notification_log.flush()
                self._notification_sync()
                if time.monotonic() - last_purge >= 3600:
                    self.notification_log.purge()
                    last_purge = time.monotonic()
                if self.store.candidates():
                    self._wake_worker("")
            except Exception:
                self._diagnostic("notification.maintenance_failed", reason="maintenance_failed")
            self.stop_event.wait(1)
