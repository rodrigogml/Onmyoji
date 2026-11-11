"""Persistência do atendimento e das fronteiras incertas do Gateway Telegram.

Uma confirmação local não torna RPC ou envio externo idempotente. Operações
possivelmente enviadas permanecem incertas até reconciliação ou decisão explícita.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import re
import tomllib
from typing import Any

from .config_publication import write_instance_bytes, write_instance_text


SCHEMA_VERSION = 4
NONTERMINAL = ("starting", "active", "interrupting", "reconciling", "uncertain")


class GatewayPersistenceError(RuntimeError):
    """Estado incompatível ou conflito de posse impede novo atendimento."""


def protect_legacy_gateway(root: Path, configuration: Path) -> None:
    """Eleva o contrato de configuração antes de introduzir o banco novo.

    O Gateway antigo exige schema 1 e assim recusa startup em vez de ignorar
    os registros de incerteza. O backup original é persistente e privado;
    somente o arquivo de publicação transitório fica em ``root/tmp``.
    """
    raw = configuration.read_bytes()
    text = raw.decode("utf-8")
    version = tomllib.loads(text).get("schema_version")
    if version == 2:
        return
    if version != 1 or configuration.is_symlink():
        raise GatewayPersistenceError("Configuração incompatível para migração do Gateway.")
    pattern = r'''(?m)^([ \t]*(?:schema_version|"schema_version"|'schema_version')[ \t]*=[ \t]*)1(?=[ \t]*(?:#|\r?$))'''
    updated, count = re.subn(pattern, lambda match: match[1] + "2", text, count=1)
    if count != 1:
        raise GatewayPersistenceError("Não foi possível preservar a configuração ao atualizar seu esquema.")
    backup = configuration.with_name("config-before-delegation-v1.toml")
    if not backup.exists():
        write_instance_bytes(root, backup, raw)
    write_instance_text(root, configuration, updated)


class GatewayStore:
    """Usa o banco e o mutex do Gateway; métodos nunca fazem I/O externo.

    Migrações preservam a inbox legada e não recolocam execução incerta na fila.
    O lease de processo é adquirido somente na abertura operacional do serviço.
    """

    def __init__(self, database: sqlite3.Connection, lock: threading.RLock):
        self.db, self.lock = database, lock
        self._lease: Any = None
        self.migrate()

    @staticmethod
    def validate_schema(database: sqlite3.Connection) -> None:
        """Recusa downgrade por consulta, antes de qualquer DDL do Gateway."""
        exists = database.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gateway_schema_migrations'").fetchone()
        if exists:
            version = database.execute("SELECT COALESCE(MAX(version),0) FROM gateway_schema_migrations").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise GatewayPersistenceError("Esquema do Gateway mais novo que esta versão; não iniciar downgrade.")

    def migrate(self) -> None:
        self.validate_schema(self.db)
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
            version = self.db.execute("SELECT COALESCE(MAX(version),0) FROM gateway_schema_migrations").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise GatewayPersistenceError("Esquema do Gateway mais novo que esta versão; não iniciar downgrade.")
            if version == SCHEMA_VERSION:
                return
            database_path = self.db.execute("PRAGMA database_list").fetchone()[2]
            if database_path and (version > 0 or self.db.execute("SELECT COUNT(*) FROM inbox_items").fetchone()[0]):
                backup = Path(database_path).with_name(f"gateway-before-delegation-v{version}.sqlite3")
                if not backup.exists():
                    self.backup(backup)
            if version == 3:
                self._migrate_multiorigin()
                return
            if version == 2:
                self._migrate_structured_content()
                self._migrate_multiorigin()
                return
            if version == 1:
                self._migrate_terminal_outputs()
                self._migrate_structured_content()
                self._migrate_multiorigin()
                return
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(inbox_items)")}
            for name, definition in (("source_key", "TEXT"), ("sequence", "INTEGER"), ("route", "TEXT NOT NULL DEFAULT 'auto'"), ("execution_id", "TEXT"), ("failure_reason", "TEXT")):
                if name not in columns:
                    self.db.execute(f"ALTER TABLE inbox_items ADD COLUMN {name} {definition}")
            self.db.execute("UPDATE inbox_items SET sequence=rowid WHERE sequence IS NULL")
            self.db.execute("UPDATE inbox_items SET state='uncertain_legacy', failure_reason='legacy_running' WHERE state='running'")
            self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS inbox_source ON inbox_items(source_key) WHERE source_key IS NOT NULL")
            self.db.execute("CREATE INDEX IF NOT EXISTS inbox_sequence ON inbox_items(chat_id,generation,state,sequence)")
            statements = (
                "CREATE TABLE ingress_updates(source_key TEXT PRIMARY KEY, bot_scope TEXT NOT NULL, update_id INTEGER, conversation_id TEXT, generation INTEGER, payload TEXT, state TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
                "CREATE TABLE gateway_checkpoints(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                "CREATE TABLE execution_turns(execution_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, generation INTEGER NOT NULL, process_epoch TEXT, thread_id TEXT, turn_id TEXT, state TEXT NOT NULL, result TEXT, resource_revision TEXT, created_at REAL NOT NULL, deadline REAL, completed_at REAL)",
                "CREATE UNIQUE INDEX execution_conversation_active ON execution_turns(conversation_id) WHERE state IN ('starting','active','interrupting','reconciling','uncertain')",
                "CREATE UNIQUE INDEX execution_runtime_id ON execution_turns(process_epoch,thread_id,turn_id) WHERE turn_id IS NOT NULL",
                "CREATE TABLE input_operations(operation_id TEXT PRIMARY KEY, inbox_id TEXT NOT NULL, execution_id TEXT NOT NULL, method TEXT NOT NULL, expected_turn_id TEXT, client_message_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
                "CREATE UNIQUE INDEX input_operation_accepted ON input_operations(inbox_id) WHERE state IN ('dispatching','accepted','uncertain')",
                "CREATE TABLE related_work(work_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL, parent_thread_id TEXT NOT NULL, child_thread_id TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1, state TEXT NOT NULL, result_hash TEXT, result_ref TEXT, result_version INTEGER NOT NULL DEFAULT 0, delivered_version INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL, UNIQUE(execution_id,child_thread_id,attempt))",
                "CREATE TABLE gateway_deliveries(delivery_key TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, generation INTEGER NOT NULL, execution_id TEXT, thread_id TEXT, turn_id TEXT, kind TEXT NOT NULL, item_id TEXT, part INTEGER NOT NULL DEFAULT 0, payload TEXT, state TEXT NOT NULL, telegram_message_id INTEGER, attempts INTEGER NOT NULL DEFAULT 0, reason TEXT, cleanup_state TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL)",
                "CREATE INDEX deliveries_owner ON gateway_deliveries(conversation_id,generation,execution_id,state)",
                "CREATE TABLE resource_references(resource TEXT NOT NULL, consumer TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(resource,consumer))",
            )
            for statement in statements:
                self.db.execute(statement)
            self.db.execute("INSERT INTO gateway_schema_migrations VALUES (?,?)", (1, time.time()))
            self._migrate_terminal_outputs()
            self._migrate_structured_content()
            self._migrate_multiorigin()

    def _migrate_multiorigin(self) -> None:
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(inbox_items)")}
        for name, definition in (("origin", "TEXT NOT NULL DEFAULT 'telegram'"), ("priority", "INTEGER NOT NULL DEFAULT 0"), ("global_sequence", "INTEGER")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE inbox_items ADD COLUMN {name} {definition}")
        ordered = self.db.execute("SELECT id FROM inbox_items ORDER BY created_at,sequence,id").fetchall()
        for index, (identifier,) in enumerate(ordered, 1):
            self.db.execute("UPDATE inbox_items SET global_sequence=? WHERE id=? AND global_sequence IS NULL", (index, identifier))
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS inbox_global_sequence ON inbox_items(global_sequence)")
        self.db.execute("CREATE TABLE IF NOT EXISTS notification_receipts(inbox_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,received_at TEXT NOT NULL,envelope TEXT NOT NULL,execution_state TEXT,delivery_state TEXT NOT NULL DEFAULT 'not_attempted',reason TEXT,incident_state TEXT NOT NULL DEFAULT 'pending')")
        self.db.execute("CREATE TABLE IF NOT EXISTS notification_log_journal(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,payload TEXT NOT NULL,flushed INTEGER NOT NULL DEFAULT 0)")
        self.db.execute("INSERT OR IGNORE INTO gateway_schema_migrations VALUES (4,?)", (time.time(),))

    def candidates(self) -> list[str]:
        with self.lock:
            return list(dict.fromkeys(row[0] for row in self.db.execute("SELECT chat_id FROM inbox_items WHERE state='pending' AND (origin!='event' OR NOT EXISTS (SELECT 1 FROM notification_log_journal WHERE flushed=0)) ORDER BY priority,global_sequence").fetchall()))

    def _migrate_terminal_outputs(self) -> None:
        attachment_columns = {row[1] for row in self.db.execute("PRAGMA table_info(conversation_attachments)")}
        if attachment_columns and "transcription" not in attachment_columns:
            self.db.execute("ALTER TABLE conversation_attachments ADD COLUMN transcription TEXT")
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(execution_turns)")}
        for name, definition in (("terminal_text", "TEXT"), ("final_item_id", "TEXT"), ("reply_mode", "TEXT"), ("delete_thoughts", "INTEGER"), ("delivery_state", "TEXT"), ("user_cancelled", "INTEGER NOT NULL DEFAULT 0"), ("silent_completion", "INTEGER NOT NULL DEFAULT 0")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE execution_turns ADD COLUMN {name} {definition}")
        self.db.execute("CREATE TABLE IF NOT EXISTS related_work_monitor(work_id TEXT PRIMARY KEY, deadline REAL NOT NULL, poll_after REAL NOT NULL, last_process_epoch TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS related_work_continuations(continuation_key TEXT PRIMARY KEY,work_id TEXT NOT NULL,attempt INTEGER NOT NULL,result_version INTEGER NOT NULL,state TEXT NOT NULL,enqueue_attempts INTEGER NOT NULL DEFAULT 0,updated_at REAL NOT NULL)")
        self.db.execute("INSERT OR IGNORE INTO gateway_schema_migrations VALUES (?,?)", (2, time.time()))

    def _migrate_structured_content(self) -> None:
        """Preserva localização da inbox e recusa leitores anteriores ao contrato."""
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(inbox_items)")}
        if "structured_content" not in columns:
            self.db.execute("ALTER TABLE inbox_items ADD COLUMN structured_content TEXT NOT NULL DEFAULT '{}'")
        self.db.execute("INSERT OR IGNORE INTO gateway_schema_migrations VALUES (?,?)", (3, time.time()))

    def recover(self) -> None:
        """Converte fronteiras interrompidas em incerteza, sem reenviar conteúdo."""
        with self.lock, self.db:
            # A operação é persistida antes de escrever no transporte. Sem ela,
            # a reserva interrompida não pode ter iniciado geração.
            self.db.execute("UPDATE inbox_items SET state='pending',execution_id=NULL WHERE execution_id IN (SELECT e.execution_id FROM execution_turns e WHERE e.state='starting' AND NOT EXISTS (SELECT 1 FROM input_operations o WHERE o.execution_id=e.execution_id)) AND state='running'")
            self.db.execute("UPDATE execution_turns SET state='terminal',result='not_sent',completed_at=? WHERE state='starting' AND NOT EXISTS (SELECT 1 FROM input_operations o WHERE o.execution_id=execution_turns.execution_id)", (time.time(),))
            self.db.execute("UPDATE input_operations SET state='uncertain', reason='gateway_restarted', updated_at=? WHERE state='dispatching'", (time.time(),))
            self.db.execute("UPDATE execution_turns SET state='uncertain', result='gateway_restarted' WHERE state IN ('starting','active','interrupting','reconciling')")
            self.db.execute("UPDATE inbox_items SET state='uncertain', failure_reason='gateway_restarted' WHERE state='running'")
            self.db.execute("UPDATE gateway_deliveries SET state='uncertain', reason='gateway_restarted' WHERE state='sending'")
            self.db.execute("UPDATE notification_receipts SET delivery_state='uncertain',reason='gateway_restarted' WHERE delivery_state='sending'")
            self.db.execute("UPDATE ingress_updates SET state='uncertain', updated_at=? WHERE state='preparing'", (time.time(),))
            for source, payload in self.db.execute("SELECT source_key,payload FROM ingress_updates WHERE state='uncertain'").fetchall():
                try:
                    value = json.loads(payload) if isinstance(payload, str) else None
                except ValueError:
                    value = None
                if isinstance(value, dict) and value.get("private_control"):
                    self.db.execute("UPDATE ingress_updates SET state='cancelled' WHERE source_key=?", (source,))

    def acquire_lease(self, path: Path) -> None:
        """Recusa outro escritor operacional, inclusive no mesmo processo."""
        if self._lease is not None:
            return
        self._lease = self.open_lease(path)

    @staticmethod
    def open_lease(path: Path) -> Any:
        """Reserva a instância antes de mutar configuração ou abrir migrações."""
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                if not path.stat().st_size:
                    stream.write(b"0"); stream.flush(); stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            stream.close()
            raise GatewayPersistenceError("Outro Gateway possui o banco desta instância.") from error
        return stream

    def backup(self, path: Path) -> None:
        """Cria snapshot SQLite consistente e privado, sem sobrescrever arquivo."""
        path = Path(path)
        if path.is_symlink() or path.exists():
            raise GatewayPersistenceError("Destino de backup deve ser novo e não pode ser link.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb"):
            pass
        target = sqlite3.connect(path)
        try:
            with self.lock:
                self.db.backup(target)
        finally:
            target.close()

    def summary(self) -> dict[str, int]:
        """Retorna somente contagens operacionais, nunca conteúdo de conversas."""
        queries = {
            "pending_inputs": "SELECT COUNT(*) FROM inbox_items WHERE state='pending'",
            "uncertain_inputs": "SELECT COUNT(*) FROM inbox_items WHERE state IN ('uncertain','uncertain_legacy')",
            "active_turns": "SELECT COUNT(*) FROM execution_turns WHERE state IN ('starting','active','interrupting')",
            "uncertain_turns": "SELECT COUNT(*) FROM execution_turns WHERE state IN ('uncertain','reconciling')",
            "uncertain_deliveries": "SELECT COUNT(*) FROM gateway_deliveries WHERE state='uncertain'",
            "pending_cleanup": "SELECT COUNT(*) FROM gateway_deliveries WHERE cleanup_state='pending'",
            "pending_preparation": "SELECT COUNT(*) FROM ingress_updates WHERE state IN ('received','preparing')",
        }
        with self.lock:
            return {name: self.db.execute(query).fetchone()[0] for name, query in queries.items()}

    def release_lease(self) -> None:
        if self._lease is not None:
            self._lease.close(); self._lease = None

    def checkpoint(self, scope: str) -> int:
        with self.lock:
            row = self.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", ("offset:" + scope,)).fetchone()
        return int(row[0]) if row else 0

    def record_private_prompt(self, conversation: str, generation: int, token: str, expires_at: float, message_id: int | None = None) -> bool:
        """Persiste só metadados do desafio; atualização de ID exige o mesmo nonce."""
        if not isinstance(token, str) or not token:
            raise GatewayPersistenceError("Desafio protegido exige identidade local.")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        key = "private_prompt:" + conversation
        with self.lock, self.db:
            row = self.db.execute("SELECT generation FROM conversations WHERE chat_id=?", (conversation,)).fetchone()
            if not row or row[0] != generation:
                return False
            existing = self.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", (key,)).fetchone()
            value = json.loads(existing[0]) if existing else None
            if message_id is not None and (not isinstance(value, dict) or value.get("generation") != generation or value.get("token_hash") != token_hash):
                return False
            self.db.execute("INSERT OR REPLACE INTO gateway_checkpoints VALUES (?,?)", (key, json.dumps({"generation": generation, "token_hash": token_hash, "expires_at": expires_at, "message_id": message_id})))
        return True

    def private_prompt(self, conversation: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", ("private_prompt:" + conversation,)).fetchone()
        try:
            value = json.loads(row[0]) if row else None
            if isinstance(value, dict) and isinstance(value.get("expires_at"), (int, float)) and value["expires_at"] > time.time():
                return value
        except (TypeError, ValueError):
            pass
        return None

    def clear_private_prompt(self, conversation: str, generation: int, token: str) -> bool:
        return self.close_private_prompt(conversation, generation, hashlib.sha256(token.encode()).hexdigest())

    def close_private_prompt(self, conversation: str, generation: int, token_hash: str) -> bool:
        """Fecha desafio por identidade já persistida, inclusive após perda de RAM."""
        key = "private_prompt:" + conversation
        with self.lock, self.db:
            row = self.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", (key,)).fetchone()
            value = json.loads(row[0]) if row else None
            if not isinstance(value, dict) or value.get("generation") != generation or value.get("token_hash") != token_hash:
                return False
            self.db.execute("DELETE FROM gateway_checkpoints WHERE key=?", (key,))
        return True

    def ingest(self, scope: str, update: dict[str, Any], conversation: str | None, generation: int | None) -> tuple[str, bool]:
        update_id = update.get("update_id")
        stable = isinstance(update_id, int) and not isinstance(update_id, bool)
        source = hashlib.sha256(f"{scope}:{update_id}".encode()).hexdigest() if stable else secrets.token_hex(20)
        now = time.time()
        with self.lock, self.db:
            if self.db.execute("SELECT COUNT(*) FROM ingress_updates WHERE state IN ('received','preparing')").fetchone()[0] >= 1024 and not self.db.execute("SELECT 1 FROM ingress_updates WHERE source_key=?", (source,)).fetchone():
                raise GatewayPersistenceError("Recepção temporariamente saturada; o update não foi confirmado.")
            if len(json.dumps(update, ensure_ascii=False).encode("utf-8")) > 512 * 1024:
                raise GatewayPersistenceError("Update excede o limite de recepção.")
            cursor = self.db.execute("INSERT OR IGNORE INTO ingress_updates VALUES (?,?,?,?,?,?, 'received',?,?)", (source, scope, update_id if stable else None, conversation, generation, json.dumps(update, ensure_ascii=False), now, now))
            if stable:
                value = max(self.checkpoint(scope), update_id + 1)
                self.db.execute("INSERT OR REPLACE INTO gateway_checkpoints VALUES (?,?)", ("offset:" + scope, str(value)))
        return source, bool(cursor.rowcount)

    def ingress_state(self, source: str, state: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE ingress_updates SET state=?, updated_at=? WHERE source_key=?", (state, time.time(), source))

    def ingress_claim(self, source: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE ingress_updates SET state='preparing',updated_at=? WHERE source_key=? AND state='received'", (time.time(), source))
        return bool(cursor.rowcount)

    def ingress_pending(self, conversation: str | None = None) -> list[tuple[Any, ...]]:
        where = "state='received'" + (" AND conversation_id=?" if conversation is not None else "")
        with self.lock:
            return self.db.execute(f"SELECT source_key,payload,conversation_id,generation FROM ingress_updates WHERE {where} ORDER BY rowid", (conversation,) if conversation is not None else ()).fetchall()

    def enqueue(self, conversation: str, generation: int, text: str, attachments: list[str], context: dict[str, Any], *, structured_content: dict[str, Any] | None = None, source: str | None = None, route: str = "auto", limit: int = 50, origin: str = "telegram", receipt: dict | None = None, global_limit: int = 1024) -> str | None:
        with self.lock, self.db:
            if source and self.db.execute("SELECT 1 FROM inbox_items WHERE source_key=?", (source,)).fetchone():
                return None
            count = self.db.execute("SELECT COUNT(*) FROM inbox_items WHERE chat_id=? AND generation=? AND state IN ('pending','running','accepted','uncertain','uncertain_legacy')", (conversation, generation)).fetchone()[0]
            if count >= limit:
                raise GatewayPersistenceError("A fila desta conversa atingiu o limite configurado.")
            total = self.db.execute("SELECT COUNT(*) FROM inbox_items WHERE state IN ('pending','running','accepted','uncertain','uncertain_legacy')").fetchone()[0]
            if total >= global_limit:
                raise GatewayPersistenceError("A fila global atingiu o limite configurado.")
            sequence = self.db.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM inbox_items WHERE chat_id=?", (conversation,)).fetchone()[0]
            identifier = secrets.token_urlsafe(18)
            self.db.execute("INSERT INTO inbox_items(id,chat_id,generation,text,attachment_ids,turn_context,structured_content,state,created_at,source_key,sequence,route) VALUES (?,?,?,?,?,?,?,'pending',?,?,?,?)", (identifier, conversation, generation, text, json.dumps(attachments), json.dumps(context, ensure_ascii=False), json.dumps(structured_content or {}, ensure_ascii=False, allow_nan=False), time.time(), source, sequence, route))
            previous = self.db.execute("SELECT value FROM gateway_checkpoints WHERE key='global_sequence'").fetchone()
            maximum = self.db.execute("SELECT COALESCE(MAX(global_sequence),0) FROM inbox_items").fetchone()[0]
            global_sequence = max(int(previous[0]) if previous else 0, maximum) + 1
            self.db.execute("INSERT OR REPLACE INTO gateway_checkpoints VALUES ('global_sequence',?)", (str(global_sequence),))
            self.db.execute("UPDATE inbox_items SET origin=?,priority=?,global_sequence=? WHERE id=?", (origin, 0 if origin == "telegram" else 1, global_sequence, identifier))
            if receipt:
                self.db.execute("INSERT INTO notification_receipts(inbox_id,event_id,received_at,envelope) VALUES (?,?,?,?)", (identifier, receipt["event_id"], receipt["received_at"], json.dumps(receipt["envelope"], ensure_ascii=False)))
                self.db.execute("INSERT INTO notification_log_journal(at,payload) VALUES (?,?)", (receipt["received_at"], json.dumps({"stage": "received", **receipt}, ensure_ascii=False)))
        return identifier

    def next_input(self, conversation: str, generation: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT id,text,attachment_ids,turn_context,route,sequence,structured_content,origin FROM inbox_items WHERE chat_id=? AND generation=? AND state='pending' ORDER BY priority,(route='result'),global_sequence LIMIT 1", (conversation, generation)).fetchone()
        value = dict(zip(("id", "text", "attachment_ids", "turn_context", "route", "sequence", "structured_content", "origin"), row)) if row else None
        if value and value["origin"] == "event":
            value["notification"] = True
        return value

    def blocked(self, conversation: str) -> bool:
        with self.lock:
            return bool(self.db.execute("SELECT 1 FROM execution_turns WHERE conversation_id=? AND (state IN ('uncertain','reconciling') OR state='terminal' AND delivery_state IN ('pending','sending','sent')) UNION ALL SELECT 1 FROM inbox_items WHERE chat_id=? AND state IN ('uncertain','uncertain_legacy') UNION ALL SELECT 1 FROM ingress_updates WHERE conversation_id=? AND state='uncertain' UNION ALL SELECT 1 FROM notification_receipts n JOIN inbox_items i ON i.id=n.inbox_id JOIN execution_turns e ON e.execution_id=i.execution_id WHERE i.chat_id=? AND n.execution_state IS NULL AND e.state='terminal' LIMIT 1", (conversation, conversation, conversation, conversation)).fetchone())

    def reserve(self, conversation: str, generation: int, inbox: str, timeout: float) -> str:
        identifier = secrets.token_urlsafe(18)
        with self.lock, self.db:
            row = self.db.execute("SELECT 1 FROM inbox_items i JOIN conversations c ON c.chat_id=i.chat_id WHERE i.id=? AND i.chat_id=? AND i.generation=? AND c.generation=? AND i.state='pending'", (inbox, conversation, generation, generation)).fetchone()
            if not row:
                raise GatewayPersistenceError("Entrada não está pronta na geração vigente.")
            self.db.execute("INSERT INTO execution_turns(execution_id,conversation_id,generation,state,created_at,deadline) VALUES (?,?,?,'starting',?,?)", (identifier, conversation, generation, time.time(), time.time() + timeout))
            self.db.execute("UPDATE inbox_items SET state='running',execution_id=? WHERE id=? AND state='pending'", (identifier, inbox))
        return identifier

    def bind(self, execution: str, epoch: str, thread: str, turn: str | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE execution_turns SET process_epoch=?,thread_id=?,turn_id=COALESCE(?,turn_id),state='active' WHERE execution_id=? AND state IN ('starting','active')", (epoch, thread, turn, execution))

    def operation(self, inbox: str, execution: str, method: str, turn: str | None) -> str:
        identifier = secrets.token_urlsafe(18)
        with self.lock, self.db:
            row = self.db.execute("SELECT 1 FROM inbox_items i JOIN conversations c ON c.chat_id=i.chat_id WHERE i.id=? AND i.generation=c.generation AND i.state IN ('pending','running')", (inbox,)).fetchone()
            if not row:
                raise GatewayPersistenceError("Entrada já aceita, cancelada ou de geração antiga.")
            self.db.execute("INSERT INTO input_operations VALUES (?,?,?,?,?,?,'dispatching',NULL,?,?)", (identifier, inbox, execution, method, turn, inbox, time.time(), time.time()))
        return identifier

    def operation_result(self, operation: str, state: str, reason: str | None = None) -> None:
        with self.lock, self.db:
            row = self.db.execute("SELECT inbox_id,execution_id,state FROM input_operations WHERE operation_id=?", (operation,)).fetchone()
            if not row:
                raise GatewayPersistenceError("Operação de input desconhecida.")
            if row[2] == "accepted" and state != "accepted":
                return
            self.db.execute("UPDATE input_operations SET state=?,reason=?,updated_at=? WHERE operation_id=?", (state, reason, time.time(), operation))
            inbox_state = {"accepted": "accepted", "rejected": "pending", "not_sent": "pending", "uncertain": "uncertain"}[state]
            if state == "accepted" and self.db.execute("SELECT 1 FROM execution_turns WHERE execution_id=? AND state='terminal'", (row[1],)).fetchone():
                inbox_state = "done"
            self.db.execute("UPDATE inbox_items SET state=?,failure_reason=?,execution_id=? WHERE id=? AND state!='cancelled'", (inbox_state, reason, row[1], row[0]))
            if state == "accepted":
                self.db.execute("UPDATE ingress_updates SET state='done',updated_at=? WHERE source_key=(SELECT source_key FROM inbox_items WHERE id=?) AND state='uncertain'", (time.time(), row[0]))
            if state == "uncertain":
                self.db.execute("UPDATE execution_turns SET state='uncertain',result=? WHERE execution_id=?", (reason or "input_unknown", row[1]))

    def terminal(self, execution: str, result: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE execution_turns SET state='terminal',result=?,completed_at=? WHERE execution_id=? AND state!='uncertain'", (result, time.time(), execution))
            self.db.execute("UPDATE inbox_items SET state='done' WHERE execution_id=? AND state='accepted'", (execution,))

    def save_terminal_output(self, execution: str, result: str, text: str | None, item: str, mode: str, delete_thoughts: bool) -> None:
        """Persiste terminalidade e conteúdo antes de liberar a espera do turno.

        A entrega pode ser planejada depois de um crash sem voltar a executar
        o modelo. Incerteza de input adicional permanece na operação/inbox.
        """
        with self.lock, self.db:
            self.db.execute("UPDATE execution_turns SET state='terminal',result=?,terminal_text=?,final_item_id=?,reply_mode=?,delete_thoughts=?,delivery_state=CASE WHEN delivery_state IN ('sent','done','uncertain','suppressed') THEN delivery_state ELSE ? END,completed_at=? WHERE execution_id=?", (result, text, item, mode, int(delete_thoughts), "pending" if text is not None else "suppressed", time.time(), execution))
            self.db.execute("UPDATE inbox_items SET state='done' WHERE execution_id=? AND state='accepted'", (execution,))

    def fail_input(self, inbox: str, reason: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE inbox_items SET state='failed',failure_reason=? WHERE id=? AND state IN ('pending','running')", (reason, inbox))

    def delivery_plan(self, key: str, conversation: str, generation: int, execution: str | None, thread: str | None, turn: str | None, kind: str, item: str, payload: dict[str, Any], part: int = 0) -> str:
        with self.lock, self.db:
            existing = self.db.execute("SELECT conversation_id,generation,execution_id,thread_id,turn_id,kind,item_id,part,payload,state FROM gateway_deliveries WHERE delivery_key=?", (key,)).fetchone()
            if existing:
                owner = (conversation, generation, execution, thread, turn, kind, item, part)
                if existing[:8] != owner or existing[8] is not None and json.loads(existing[8]) != payload:
                    raise GatewayPersistenceError("Chave de entrega já pertence a outro dono ou conteúdo.")
                return existing[9]
            self.db.execute("INSERT OR IGNORE INTO gateway_deliveries(delivery_key,conversation_id,generation,execution_id,thread_id,turn_id,kind,item_id,part,payload,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'planned',?,?)", (key, conversation, generation, execution, thread, turn, kind, item, part, json.dumps(payload, ensure_ascii=False), time.time(), time.time()))
            return str(self.db.execute("SELECT state FROM gateway_deliveries WHERE delivery_key=?", (key,)).fetchone()[0])

    def delivery_claim(self, key: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE gateway_deliveries SET state='sending',attempts=attempts+1,updated_at=? WHERE delivery_key=? AND state IN ('planned','retryable')", (time.time(), key))
        return bool(cursor.rowcount)

    def delivery_result(self, key: str, state: str, message_id: int | None = None, reason: str | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE gateway_deliveries SET state=?,telegram_message_id=COALESCE(?,telegram_message_id),reason=?,updated_at=? WHERE delivery_key=?", (state, message_id, reason, time.time(), key))

    def retain(self, resource: str, consumer: str) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO resource_references VALUES (?,?,'retained')", (resource, consumer))

    def release_resources(self, consumer: str) -> list[str]:
        with self.lock, self.db:
            resources = [row[0] for row in self.db.execute("SELECT resource FROM resource_references WHERE consumer=?", (consumer,))]
            self.db.execute("UPDATE resource_references SET state='released' WHERE consumer=?", (consumer,))
            return [resource for resource in resources if not self.db.execute("SELECT 1 FROM resource_references WHERE resource=? AND state='retained'", (resource,)).fetchone()]

    def reset(self, conversation: str, generation: int, source: str | None = None) -> None:
        """Invalida fila e entregas sem declarar trabalho antigo concluído."""
        with self.lock, self.db:
            key = "private_prompt:" + conversation
            prompt = self.db.execute("SELECT value FROM gateway_checkpoints WHERE key=?", (key,)).fetchone()
            if prompt and json.loads(prompt[0]).get("generation") == generation:
                self.db.execute("DELETE FROM gateway_checkpoints WHERE key=?", (key,))
            self.db.execute("UPDATE inbox_items SET state='cancelled' WHERE chat_id=? AND generation=? AND state IN ('pending','running','accepted')", (conversation, generation))
            self.db.execute("UPDATE gateway_deliveries SET state='suppressed',reason='generation_reset' WHERE conversation_id=? AND generation=? AND state IN ('planned','retryable')", (conversation, generation))
            self.db.execute("UPDATE ingress_updates SET state='cancelled' WHERE conversation_id=? AND generation=? AND state='received'", (conversation, generation))
            if source:
                self.db.execute("UPDATE ingress_updates SET state='done',updated_at=? WHERE source_key=?", (time.time(), source))

    def resolve(self, conversation: str, *, discard: bool) -> None:
        """Registra decisão explícita, sem repetir geração nem apagar efeito externo.

        A opção suportada é descartar a solicitação incerta. Repetição deve ser
        uma nova mensagem após reconciliação dos efeitos pelo responsável.
        """
        if not discard:
            raise GatewayPersistenceError("Reconcilie efeitos externos e envie uma nova solicitação; não há retry cego.")
        with self.lock, self.db:
            self.db.execute("UPDATE execution_turns SET state='terminal',result='owner_discarded',completed_at=? WHERE conversation_id=? AND state IN ('uncertain','reconciling')", (time.time(), conversation))
            self.db.execute("UPDATE inbox_items SET state='cancelled' WHERE chat_id=? AND state IN ('uncertain','uncertain_legacy')", (conversation,))
            self.db.execute("UPDATE input_operations SET state='resolved',reason='owner_discarded' WHERE execution_id IN (SELECT execution_id FROM execution_turns WHERE conversation_id=?) AND state='uncertain'", (conversation,))
            self.db.execute("UPDATE ingress_updates SET state='cancelled' WHERE conversation_id=? AND state='uncertain'", (conversation,))

    def purge(self, now: float | None = None) -> dict[str, int]:
        """Expurga conteúdo encerrado após sete dias e conserva tombstones.

        Chaves de replay e todas as incertezas permanecem. O estado não usa
        logs para armazenar payloads e não remove recursos ainda referenciados.
        """
        cutoff = (time.time() if now is None else now) - 7 * 86400
        with self.lock, self.db:
            ingress = self.db.execute("UPDATE ingress_updates SET payload=NULL WHERE state IN ('done','failed','cancelled') AND created_at<? AND payload IS NOT NULL", (cutoff,)).rowcount
            inbox = self.db.execute("UPDATE inbox_items SET text='',turn_context='{}',attachment_ids='[]',structured_content='{}' WHERE state IN ('done','failed','cancelled') AND created_at<? AND (text!='' OR structured_content!='{}')", (cutoff,)).rowcount
            self.db.execute("UPDATE gateway_deliveries SET payload=NULL WHERE state IN ('sent','suppressed','failed') AND updated_at<? AND COALESCE(cleanup_state,'done')='done'", (cutoff,))
            self.db.execute("UPDATE execution_turns SET terminal_text=NULL WHERE state='terminal' AND delivery_state IN ('done','suppressed') AND completed_at<?", (cutoff,))
        return {"ingress": ingress, "inbox": inbox}
