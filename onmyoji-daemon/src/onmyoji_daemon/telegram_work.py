"""Acompanhamento opcional de filhos conhecidos depois do turno coordenador.

O monitor lê somente threads ligadas a um pai pelo protocolo autenticado. Seus
resultados são dados para uma continuação do principal, nunca autorização ou
envio direto ao Telegram. Não cria workers duráveis nem repete ações externas.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
import threading
import time
from typing import Any, Callable

from .codex_app_server import CodexProtocolError


WORK_MONITOR_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS related_work_monitor(work_id TEXT PRIMARY KEY, deadline REAL NOT NULL, poll_after REAL NOT NULL, last_process_epoch TEXT)",
    "CREATE TABLE IF NOT EXISTS related_work_continuations(continuation_key TEXT PRIMARY KEY, work_id TEXT NOT NULL, attempt INTEGER NOT NULL, result_version INTEGER NOT NULL, state TEXT NOT NULL, enqueue_attempts INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)",
)
_PENDING_STATES = ("pendingInit", "running", "unknown")
_CLOSED_STATES = ("cancelled", "consolidated", "indeterminate")


class RelatedWorkMonitor:
    """Consulta filhos com prazo finito e enfileira resultados idempotentes.

    ``client_getter`` devolve o cliente já existente sem iniciá-lo. O callback
    recebe conversa, geração, execução, trabalho, tentativa, dado e chave;
    ele deve apenas enfileirar a continuação com unicidade dessa chave. Essa
    propriedade permite reconciliar um crash entre enqueue e commit local sem
    repetir geração. Não é permitido executar efeitos externos no callback.
    """

    def __init__(self, store: Any, capabilities_getter: Callable[[], Any],
                 client_getter: Callable[[], Any], current_generation: Callable[[str], int],
                 enqueue_result_callback: Callable[..., Any], *, enabled: bool = False,
                 timeout_seconds: float = 900, poll_interval: float = 5,
                 clock: Callable[[], float] = time.time, rpc_timeout: float = 5,
                 batch_limit: int = 8, max_result_bytes: int = 128 * 1024,
                 max_enqueue_attempts: int = 8, diagnostic: Callable[..., Any] | None = None):
        numbers = (timeout_seconds, poll_interval, rpc_timeout)
        counts = (batch_limit, max_result_bytes, max_enqueue_attempts)
        if (any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in numbers)
                or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in counts)):
            raise ValueError("Related work monitor limits must be positive")
        self.store, self.capabilities_getter, self.client_getter = store, capabilities_getter, client_getter
        self.current_generation, self.enqueue_result_callback = current_generation, enqueue_result_callback
        self.enabled, self.timeout_seconds, self.poll_interval = enabled, timeout_seconds, poll_interval
        self.clock, self.rpc_timeout, self.batch_limit = clock, rpc_timeout, batch_limit
        self.max_result_bytes, self.max_enqueue_attempts = max_result_bytes, max_enqueue_attempts
        self.diagnostic = diagnostic
        self._tick_lock = threading.Lock()
        with store.lock, store.db:
            for statement in WORK_MONITOR_SCHEMA:
                store.db.execute(statement)

    def _supported(self, feature: str) -> bool:
        try:
            return bool(self.capabilities_getter().supports(feature))
        except (AttributeError, TypeError, ValueError, OSError):
            return False

    def _emit(self, event: str, *, work: str, reason: str) -> None:
        if self.diagnostic:
            self.diagnostic(event, work_id=work, reason=reason)

    def _rows(self) -> list[dict[str, Any]]:
        with self.store.lock:
            rows = self.store.db.execute("""SELECT w.work_id,w.execution_id,w.parent_thread_id,w.child_thread_id,
                w.attempt,w.state,w.result_hash,w.result_ref,w.result_version,w.delivered_version,w.updated_at,
                e.conversation_id,e.generation,e.process_epoch,e.state,e.result,e.deadline,e.completed_at
                FROM related_work w JOIN execution_turns e ON e.execution_id=w.execution_id
                WHERE w.state NOT IN ('cancelled','consolidated','indeterminate')
                ORDER BY w.updated_at,w.work_id"""
            ).fetchall()
        names = ("work_id", "execution_id", "parent_thread_id", "child_thread_id", "attempt", "state",
                 "result_hash", "result_ref", "result_version", "delivered_version", "updated_at",
                 "conversation_id", "generation", "process_epoch", "parent_state", "parent_result",
                 "parent_deadline", "parent_completed_at")
        return [dict(zip(names, row)) for row in rows]

    def _tracking(self, work: dict[str, Any], now: float) -> tuple[float, float, str | None]:
        deadline = work["parent_deadline"]
        if not isinstance(deadline, (int, float)) or deadline <= 0:
            deadline = float(work["updated_at"]) + self.timeout_seconds
        deadline = min(float(deadline), float(work["updated_at"]) + self.timeout_seconds)
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO related_work_monitor VALUES (?,?,?,?)",
                                  (work["work_id"], deadline, now, work["process_epoch"]))
            row = self.store.db.execute("SELECT deadline,poll_after,last_process_epoch FROM related_work_monitor WHERE work_id=?", (work["work_id"],)).fetchone()
        return float(row[0]), float(row[1]), row[2]

    def _close(self, work: dict[str, Any], state: str, reason: str) -> None:
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE related_work SET state=?,updated_at=? WHERE work_id=? AND attempt=?",
                                  (state, self.clock(), work["work_id"], work["attempt"]))
            self.store.db.execute("UPDATE related_work_continuations SET state='suppressed',updated_at=? WHERE work_id=? AND state!='enqueued'",
                                  (self.clock(), work["work_id"]))
        self._emit("work.closed", work=work["work_id"], reason=reason)

    def has_pending(self, process_epoch: str | None = None) -> bool:
        """Indica trabalho que ainda depende do processo vivo, não entrega salva."""
        if not self.enabled or not self._supported("post_turn"):
            return False
        for work in self._rows():
            if work["state"] not in _PENDING_STATES or work["result_ref"]:
                continue
            if process_epoch is not None and work["process_epoch"] != process_epoch:
                continue
            if self.current_generation(work["conversation_id"]) == work["generation"]:
                return True
        return False

    def recover(self) -> None:
        """Recupera claims locais; filhos sem resultado exigem G6 após restart.

        Resultado já persistido é dado durável, não execução sobrevivente. Seu
        enqueue pode ser retomado com a mesma chave se o callback for o contrato
        idempotente de inbox descrito na classe.
        """
        recover_workers = self._supported("process_resume")
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE related_work_continuations SET state='prepared',updated_at=? WHERE state IN ('enqueueing','uncertain')", (self.clock(),))
            self.store.db.execute("UPDATE related_work_monitor SET poll_after=? WHERE work_id IN (SELECT work_id FROM related_work_continuations WHERE state='prepared')", (self.clock(),))
            if not recover_workers:
                self.store.db.execute("UPDATE related_work SET state='indeterminate',updated_at=? WHERE result_ref IS NULL AND state IN ('pendingInit','running','unknown')", (self.clock(),))

    def process_lost(self, process_epoch: str) -> None:
        """Não presume sobrevivência dos filhos de um epoch perdido."""
        if self._supported("process_resume"):
            return
        with self.store.lock, self.store.db:
            self.store.db.execute("""UPDATE related_work SET state='indeterminate',updated_at=?
                WHERE result_ref IS NULL AND state IN ('pendingInit','running','unknown')
                AND execution_id IN (SELECT execution_id FROM execution_turns WHERE process_epoch=?)""",
                (self.clock(), process_epoch))

    def tick(self, *, enabled: bool | None = None) -> int:
        """Executa um lote limitado sem manter mutex de banco durante RPC/enqueue.

        Retorna quantas continuações foram confirmadas na inbox. Recusa
        execução concorrente de ticks; capacidade ou configuração ausente
        mantém o fallback sem consultar filhos ou iniciar processo.
        """
        if enabled is not None:
            self.enabled = bool(enabled)
        if not self.enabled or not self._supported("post_turn") or not self._tick_lock.acquire(blocking=False):
            return 0
        emitted = 0
        try:
            processed = 0
            for work in self._rows():
                if self.current_generation(work["conversation_id"]) != work["generation"]:
                    self._close(work, "cancelled", "generation_changed")
                    continue
                if work["parent_state"] != "terminal":
                    continue
                if work["parent_result"] != "completed":
                    self._close(work, "cancelled", "parent_not_completed")
                    continue
                # O principal já observou esse filho terminal antes de encerrar.
                if (not work["result_ref"] and work["state"] in {"completed", "errored", "interrupted", "shutdown", "notFound"}
                        and work["parent_completed_at"] is not None and work["updated_at"] <= work["parent_completed_at"]):
                    self._close(work, "consolidated", "observed_before_parent_completed")
                    continue
                now = self.clock()
                deadline, poll_after, _last_epoch = self._tracking(work, now)
                if work["result_ref"]:
                    if now >= poll_after:
                        emitted += self._enqueue_saved(work)
                    continue
                if now < poll_after:
                    continue
                if processed >= self.batch_limit:
                    break
                processed += 1
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE related_work_monitor SET poll_after=? WHERE work_id=?", (now + self.poll_interval, work["work_id"]))
                if now >= deadline:
                    self._save_result(work, {"status": "blocked", "text": "", "reason": "work_deadline_exceeded", "terminal_turn_id": None,
                                             "child_terminal": False, "requires_decision": True})
                    emitted += self._enqueue_saved(work)
                    continue
                client = self.client_getter()
                if client is None or not client.running():
                    if not self._supported("process_resume"):
                        self._close(work, "indeterminate", "process_unavailable")
                    continue
                epoch = getattr(client, "process_epoch", None) or getattr(client, "epoch", None)
                changed_epoch = not epoch or not work["process_epoch"] or epoch != work["process_epoch"]
                if changed_epoch and not self._supported("process_resume"):
                    self._close(work, "indeterminate", "process_changed")
                    continue
                try:
                    response = client.request("thread/read", {"threadId": work["child_thread_id"], "includeTurns": True}, timeout=self.rpc_timeout)
                except CodexProtocolError:
                    self._emit("work.poll_failed", work=work["work_id"], reason="read_failed")
                    continue
                # Revalidar depois da espera: /new, cancelamento e EOF podem ter ocorrido.
                if self.current_generation(work["conversation_id"]) != work["generation"]:
                    self._close(work, "cancelled", "generation_changed")
                    continue
                current_epoch = getattr(client, "process_epoch", None) or getattr(client, "epoch", None)
                if current_epoch != epoch or not client.running():
                    if not self._supported("process_resume"):
                        self._close(work, "indeterminate", "process_lost_during_read")
                    continue
                terminal = self._terminal_result(response, work["child_thread_id"])
                if terminal is None:
                    if changed_epoch:
                        # Resume de thread não reinicia trabalho não concluído.
                        self._close(work, "indeterminate", "restart_unfinished_child")
                    continue
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE related_work_monitor SET last_process_epoch=? WHERE work_id=?", (epoch, work["work_id"]))
                self._save_result(work, terminal)
                emitted += self._enqueue_saved(work)
            return emitted
        finally:
            self._tick_lock.release()

    @staticmethod
    def _terminal_result(response: Any, child: str) -> dict[str, Any] | None:
        if not isinstance(response, dict):
            return None
        thread = response.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != child:
            return None
        turns = thread.get("turns")
        if not isinstance(turns, list) or not turns or not isinstance(turns[-1], dict):
            return None
        turn = turns[-1]
        status = turn.get("status")
        if status not in {"completed", "failed", "interrupted"}:
            return None
        final = ""
        items = turn.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("type") == "agentMessage" and item.get("phase") in {None, "final_answer"} and isinstance(item.get("text"), str):
                    final = item["text"]
        return {"status": status, "text": final, "terminal_turn_id": turn.get("id") if isinstance(turn.get("id"), str) else None,
                "reason": "child_completed" if status == "completed" else "child_failed" if status == "failed" else "child_interrupted"}

    def _save_result(self, work: dict[str, Any], result: dict[str, Any]) -> None:
        raw_text = result["text"].encode("utf-8")
        if len(raw_text) > self.max_result_bytes:
            result = {**result, "text": raw_text[:self.max_result_bytes].decode("utf-8", errors="ignore"), "truncated": True,
                      "content_sha256": sha256(raw_text).hexdigest()}
        body = {**result, "provenance": {"kind": "subagent_result", "work_id": work["work_id"],
                                        "parent_execution_id": work["execution_id"], "parent_thread_id": work["parent_thread_id"],
                                        "child_thread_id": work["child_thread_id"], "attempt": work["attempt"]}}
        encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = sha256(encoded.encode("utf-8")).hexdigest()
        with self.store.lock, self.store.db:
            row = self.store.db.execute("SELECT attempt,result_hash,result_version,state FROM related_work WHERE work_id=?", (work["work_id"],)).fetchone()
            if not row or row[0] != work["attempt"] or row[3] in _CLOSED_STATES:
                return
            version = row[2] + (row[1] != fingerprint)
            self.store.db.execute("UPDATE related_work SET state=?,result_hash=?,result_ref=?,result_version=?,updated_at=? WHERE work_id=?",
                                  ("completed" if result["status"] == "completed" else "errored", fingerprint, encoded, version, self.clock(), work["work_id"]))
        work.update(result_hash=fingerprint, result_ref=encoded, result_version=version)

    def _enqueue_saved(self, work: dict[str, Any]) -> int:
        if work["delivered_version"] >= work["result_version"] or not work["result_ref"]:
            return 0
        if not isinstance(work["result_ref"], str):
            self._close(work, "indeterminate", "stored_result_invalid")
            return 0
        try:
            result = json.loads(work["result_ref"])
        except (TypeError, ValueError):
            self._close(work, "indeterminate", "stored_result_invalid")
            return 0
        if not isinstance(result, dict) or sha256(work["result_ref"].encode("utf-8")).hexdigest() != work["result_hash"]:
            self._close(work, "indeterminate", "stored_result_invalid")
            return 0
        key = "work-result:" + sha256(f"{work['work_id']}:{work['attempt']}:{work['result_version']}".encode()).hexdigest()
        now = self.clock()
        with self.store.lock, self.store.db:
            current = self.store.db.execute("SELECT w.state,w.attempt,w.result_version,e.state,e.result FROM related_work w JOIN execution_turns e ON e.execution_id=w.execution_id WHERE w.work_id=?", (work["work_id"],)).fetchone()
            if not current or current[0] in _CLOSED_STATES or current[1] != work["attempt"] or current[2] != work["result_version"]:
                return 0
            if current[3:] != ("terminal", "completed"):
                self.store.db.execute("UPDATE related_work SET state='cancelled',updated_at=? WHERE work_id=?", (now, work["work_id"]))
                return 0
            self.store.db.execute("INSERT OR IGNORE INTO related_work_continuations VALUES (?,?,?,?,'prepared',0,?)", (key, work["work_id"], work["attempt"], work["result_version"], now))
            state, attempts = self.store.db.execute("SELECT state,enqueue_attempts FROM related_work_continuations WHERE continuation_key=?", (key,)).fetchone()
            if state in {"enqueued", "suppressed", "blocked"}:
                return 0
            if attempts >= self.max_enqueue_attempts:
                self.store.db.execute("UPDATE related_work_continuations SET state='blocked',updated_at=? WHERE continuation_key=?", (now, key))
                return 0
            self.store.db.execute("UPDATE related_work_continuations SET state='enqueueing',enqueue_attempts=enqueue_attempts+1,updated_at=? WHERE continuation_key=?", (now, key))
        if self.current_generation(work["conversation_id"]) != work["generation"]:
            self._close(work, "cancelled", "generation_changed")
            return 0
        try:
            self.enqueue_result_callback(work["conversation_id"], work["generation"], work["execution_id"],
                                         work["work_id"], work["attempt"], result, key)
        except Exception:
            with self.store.lock, self.store.db:
                self.store.db.execute("UPDATE related_work_continuations SET state='uncertain',updated_at=? WHERE continuation_key=?", (self.clock(), key))
                self.store.db.execute("UPDATE related_work_monitor SET poll_after=? WHERE work_id=?", (self.clock() + min(300, self.poll_interval * 2 ** min(attempts, 6)), work["work_id"]))
            self._emit("work.enqueue_uncertain", work=work["work_id"], reason="callback_failed")
            return 0
        with self.store.lock, self.store.db:
            self.store.db.execute("UPDATE related_work_continuations SET state='enqueued',updated_at=? WHERE continuation_key=?", (self.clock(), key))
            self.store.db.execute("UPDATE related_work SET delivered_version=MAX(delivered_version,?) WHERE work_id=? AND attempt=?", (work["result_version"], work["work_id"], work["attempt"]))
        self._emit("work.result_enqueued", work=work["work_id"], reason="subagent_result")
        return 1
