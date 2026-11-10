"""Entregas do Gateway com posse e resultado incerto explícitos.

O ledger evita repetição local de envios confirmados. Ele não fornece
exactly-once no Telegram: perda da confirmação impede reenvio automático.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import threading
from typing import Any, Callable

from .telegram_formatting import render_html, split_html
from .telegram_persistence import GatewayStore


class DeliveryUncertain(RuntimeError):
    """O destino pode ter recebido o envio; repetir exigiria reconciliação."""


class DeliverySuppressed(RuntimeError):
    """O dono foi invalidado antes do envio, por exemplo por /new."""


class DeliveryQueueFull(RuntimeError):
    """A fila não admitiu o efeito; nenhuma chamada externa foi iniciada."""

    kind = "not_sent"


@dataclass(frozen=True)
class DeliveryOwner:
    conversation: str
    generation: int
    execution: str | None = None
    thread: str | None = None
    turn: str | None = None


class GatewayDelivery:
    """Serializa efeitos por conversa sem manter mutex do banco durante HTTP."""

    def __init__(self, store: GatewayStore, generation: Callable[[str], int], *,
                 pending_limit: int = 1024, max_parallel_conversations: int = 16,
                 can_send: Callable[[DeliveryOwner, str], bool] | None = None):
        if (isinstance(pending_limit, bool) or not isinstance(pending_limit, int) or pending_limit < 1
                or isinstance(max_parallel_conversations, bool) or not isinstance(max_parallel_conversations, int)
                or not 1 <= max_parallel_conversations <= 16):
            raise ValueError("Gateway delivery dispatcher limits are invalid")
        self.store, self.generation = store, generation
        self.can_send = can_send
        self._lock = threading.Lock()
        self._conversations: dict[str, Any] = {}
        self._dispatch_lock = threading.Lock()
        self._dispatch_queues: dict[str, deque[tuple[Future, Callable[[], Any]]]] = {}
        self._dispatch_runners: set[str] = set()
        self._dispatch_counts: dict[str, int] = {}
        self._pending_count = 0
        self._pending_limit = pending_limit
        self._effect_slots = threading.BoundedSemaphore(max_parallel_conversations)
        self._executor = ThreadPoolExecutor(max_workers=max_parallel_conversations, thread_name_prefix="telegram-delivery")

    @property
    def pending_count(self) -> int:
        """Número admitido de efeitos em fila ou execução, limitado globalmente."""
        with self._dispatch_lock:
            return self._pending_count

    def pending_for(self, conversation: str) -> int:
        """Conta somente a conversa solicitada, sem aguardar seu HTTP em curso."""
        with self._dispatch_lock:
            return self._dispatch_counts.get(str(conversation), 0)

    def submit(self, conversation: str, operation: Callable[[], Any]) -> Future:
        """Enfileira efeito FIFO sem executar I/O no chamador.

        Conversas distintas usam runners independentes; uma conversa possui um
        único runner, mesmo com várias mensagens aguardando. ``DeliveryQueueFull``
        indica recusa anterior à admissão. A contagem inclui itens cancelados
        ainda na fila, para que cancelamento não contorne o limite de memória.
        """
        if not isinstance(conversation, str) or not conversation:
            raise ValueError("Conversation identifier is required")
        if not callable(operation):
            raise TypeError("Delivery operation must be callable")
        future = Future()
        with self._dispatch_lock:
            if self._pending_count >= self._pending_limit:
                raise DeliveryQueueFull("A fila de entregas atingiu o limite; o efeito não foi iniciado.")
            self._pending_count += 1
            self._dispatch_counts[conversation] = self._dispatch_counts.get(conversation, 0) + 1
            self._dispatch_queues.setdefault(conversation, deque()).append((future, operation))
            if conversation not in self._dispatch_runners:
                self._dispatch_runners.add(conversation)
                try:
                    self._executor.submit(self._run_conversation, conversation)
                except RuntimeError:
                    self._dispatch_queues[conversation].pop()
                    self._release_dispatch_count(conversation)
                    self._dispatch_queues.pop(conversation, None)
                    self._dispatch_runners.discard(conversation)
                    raise DeliveryQueueFull("O executor de entregas não está disponível; o efeito não foi iniciado.") from None
        return future

    def _release_dispatch_count(self, conversation: str) -> None:
        self._pending_count -= 1
        count = self._dispatch_counts[conversation] - 1
        if count:
            self._dispatch_counts[conversation] = count
        else:
            self._dispatch_counts.pop(conversation)

    def _run_conversation(self, conversation: str) -> None:
        # Nenhum worker adicional aguarda a mesma conversa; o semáforo limita
        # conversas em efeito, sem manter mutex de estado durante a chamada.
        with self._effect_slots:
            while True:
                with self._dispatch_lock:
                    lane = self._dispatch_queues[conversation]
                    if not lane:
                        self._dispatch_queues.pop(conversation)
                        self._dispatch_runners.remove(conversation)
                        return
                    future, operation = lane.popleft()
                result, failure = None, None
                running = future.set_running_or_notify_cancel()
                if running:
                    try:
                        result = operation()
                    except BaseException as error:
                        failure = error
                with self._dispatch_lock:
                    self._release_dispatch_count(conversation)
                if running:
                    if failure is not None:
                        future.set_exception(failure)
                    else:
                        future.set_result(result)

    def effect(self, owner: DeliveryOwner, key: str, kind: str, item: str, payload: dict[str, Any], send: Callable[[], dict[str, Any]], *, part: int = 0) -> dict[str, Any]:
        """Envia uma vez quando comprovadamente ainda não foi enviado.

        Exceções de transporte são incertas por padrão. Uma recusa só permite
        retry quando o adaptador a classifica explicitamente como ``not_sent``.
        Nenhuma mensagem de erro contém o payload da entrega.
        """
        with self._lock:
            lane = self._conversations.setdefault(owner.conversation, threading.RLock())
        with lane:
            state = self.store.delivery_plan(key, owner.conversation, owner.generation, owner.execution, owner.thread, owner.turn, kind, item, payload, part)
            if state == "sent":
                with self.store.lock:
                    row = self.store.db.execute("SELECT telegram_message_id FROM gateway_deliveries WHERE delivery_key=?", (key,)).fetchone()
                if not row or not isinstance(row[0], int) or isinstance(row[0], bool) or row[0] <= 0:
                    self.store.delivery_result(key, "uncertain", reason="receipt_invalid")
                    raise DeliveryUncertain("Entrega persistida sem identificador válido; não será repetida.")
                return {"message_id": row[0], "already_sent": True}
            if state == "suppressed":
                raise DeliverySuppressed("Entrega pertence a uma geração encerrada.")
            if state in {"sending", "uncertain"}:
                raise DeliveryUncertain("Entrega sem confirmação; não será repetida automaticamente.")
            if self.generation(owner.conversation) != owner.generation or self.can_send is not None and not self.can_send(owner, kind):
                self.store.delivery_result(key, "suppressed", reason="owner_invalidated")
                raise DeliverySuppressed("Entrega pertence a uma geração encerrada.")
            if not self.store.delivery_claim(key):
                raise DeliveryUncertain("Entrega já reservada por outra operação.")
            try:
                result = send()
                if not isinstance(result, dict) or not isinstance(result.get("message_id"), int) or isinstance(result.get("message_id"), bool) or result["message_id"] <= 0:
                    raise DeliveryUncertain("Destino não confirmou o identificador da entrega.")
            except Exception as error:
                not_sent = getattr(error, "kind", "") == "not_sent"
                self.store.delivery_result(key, "retryable" if not_sent else "uncertain", reason="not_sent" if not_sent else "confirmation_missing")
                if not_sent:
                    raise
                raise DeliveryUncertain("O envio pode ter ocorrido; não repita antes de reconciliar.") from error
            self.store.delivery_result(key, "sent", result["message_id"])
            return result

    def text(self, owner: DeliveryOwner, key: str, kind: str, item: str, api: Any, chat: int, text: str, **values: Any) -> list[dict[str, Any]]:
        result = []
        for index, chunk in enumerate(split_html(render_html(text))):
            payload = {"chat_id": chat, "text": chunk, "values": {**values, "parse_mode": "HTML"}}
            result.append(self.effect(owner, f"{key}:{index}", kind, item, payload, lambda chunk=chunk: api.send(chat, chunk, parse_mode="HTML", **values), part=index))
        return result

    def plan_text(self, owner: DeliveryOwner, key: str, kind: str, item: str, chat: int, text: str, **values: Any) -> list[str]:
        """Persiste partes antes de admitir trabalho no dispatcher de HTTP."""
        keys = []
        for index, chunk in enumerate(split_html(render_html(text))):
            part_key = f"{key}:{index}"
            payload = {"chat_id": chat, "text": chunk, "values": {**values, "parse_mode": "HTML"}}
            self.store.delivery_plan(part_key, owner.conversation, owner.generation, owner.execution, owner.thread, owner.turn, kind, item, payload, index)
            keys.append(part_key)
        return keys

    def cleanup(self, owner: DeliveryOwner, api: Any, chat: int) -> None:
        """Remove somente pensamentos do dono, após saída terminal confirmada."""
        with self.store.lock:
            rows = self.store.db.execute("SELECT delivery_key,telegram_message_id FROM gateway_deliveries WHERE conversation_id=? AND generation=? AND execution_id IS ? AND kind='thought' AND state='sent' AND COALESCE(cleanup_state,'')!='done'", (owner.conversation, owner.generation, owner.execution)).fetchall()
        for key, message in rows:
            try:
                api.delete(chat, message)
            except Exception as error:
                # Resposta estruturada 'já ausente' é terminal; timeout continua pendente.
                if getattr(error, "kind", "") != "already_absent":
                    with self.store.lock, self.store.db:
                        self.store.db.execute("UPDATE gateway_deliveries SET cleanup_state='pending' WHERE delivery_key=?", (key,))
                    continue
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE gateway_deliveries SET cleanup_state='done' WHERE delivery_key=?", (key,))

    @staticmethod
    def result_key(work_id: str, attempt: int, result: Any) -> str:
        """Identifica uma versão de resultado, sem considerar texto autorização."""
        digest = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return f"result:{work_id}:{attempt}:{digest}"
