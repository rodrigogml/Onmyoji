"""Fonte transacional única de definições, revisões e ocorrências."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading

from .calendar import initial_at, instant, iso, latest_due, next_at
from .contracts import validate_definition
from ..agent_notification.contract import uuid7
from ..rpc import RpcError
from ..telegram_persistence import GatewayStore

SORT_FIELDS = ("name", "state", "next_run_at", "created_at", "action", "channel", "destination", "thread_policy")


class TaskRepository:
    def __init__(self, path: Path, workspace: Path, clock=None):
        self.workspace = workspace.resolve()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.lease = GatewayStore.open_lease(path.with_suffix(".lock"))
        try:
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RpcError("Banco do agendador é mais novo que este runtime.", "schema_newer")
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS definitions(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL,enabled INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,next_run_at TEXT,deleted_at TEXT)")
                self.db.execute("CREATE TABLE IF NOT EXISTS occurrences(id TEXT PRIMARY KEY,definition_id TEXT NOT NULL,revision INTEGER NOT NULL,snapshot TEXT NOT NULL,scheduled_at TEXT NOT NULL,window_start TEXT NOT NULL,coalesced_until TEXT NOT NULL,event_id TEXT,state TEXT NOT NULL,started_at TEXT,completed_at TEXT,result TEXT,reason TEXT,resolution TEXT,UNIQUE(definition_id,revision,scheduled_at))")
                self.db.execute("CREATE INDEX IF NOT EXISTS occurrence_dispatch ON occurrences(state,scheduled_at)")
                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS occurrence_one_active ON occurrences(definition_id) WHERE state IN ('running','uncertain')")
                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS occurrence_one_pending ON occurrences(definition_id) WHERE state='pending'")
                self.db.execute("PRAGMA user_version=1")
                self.db.execute("UPDATE occurrences SET state='uncertain',reason='service_restarted' WHERE state='running'")
        except Exception:
            self.lease.close()
            if hasattr(self, "db"):
                self.db.close()
            raise

    def close(self):
        self.db.close()
        self.lease.close()

    def _row(self, identifier):
        row = self.db.execute("SELECT * FROM definitions WHERE id=? AND deleted_at IS NULL", (identifier,)).fetchone()
        if not row:
            raise RpcError("Agendamento não encontrado.", "not_found")
        return row

    @staticmethod
    def _view(row):
        document = json.loads(row["document"])
        action = document["action"]
        channel = action.get("event", {}).get("response_channel", {})
        return {**document, "enabled": bool(row["enabled"]), "id": row["id"], "revision": row["revision"], "created_at": row["created_at"], "updated_at": row["updated_at"], "next_run_at": row["next_run_at"], "state": "enabled" if row["enabled"] else "disabled", "action_type": action["type"], "channel": channel.get("type"), "destination": channel.get("value"), "thread_policy": action.get("event", {}).get("thread_policy")}

    def get(self, identifier):
        with self.lock:
            return self._view(self._row(identifier))

    def create(self, document):
        now = self.clock()
        value = validate_definition(document, self.workspace, now)
        identifier = uuid7()
        at = iso(initial_at(value["rule"], now)) if value["enabled"] else None
        with self.lock, self.db:
            self.db.execute("INSERT INTO definitions VALUES (?,1,?,?,?,?,?,NULL)", (identifier, json.dumps(value, ensure_ascii=False), int(value["enabled"]), iso(now), iso(now), at))
        return self.get(identifier)

    def update(self, identifier, revision, document):
        now = self.clock()
        value = validate_definition(document, self.workspace, now)
        with self.lock, self.db:
            row = self._row(identifier)
            self._check_revision(row, revision)
            at = iso(initial_at(value["rule"], now)) if value["enabled"] else None
            self.db.execute("UPDATE definitions SET revision=revision+1,document=?,enabled=?,updated_at=?,next_run_at=? WHERE id=?", (json.dumps(value, ensure_ascii=False), int(value["enabled"]), iso(now), at, identifier))
            if not value["enabled"]:
                self._discard_pending(identifier, "definition_disabled")
        return self.get(identifier)

    @staticmethod
    def _check_revision(row, expected):
        if type(expected) is not int or expected != row["revision"]:
            raise RpcError("Revisão divergente; consulte a definição novamente.", "revision_conflict")

    def _discard_pending(self, identifier, reason):
        self.db.execute("UPDATE occurrences SET state='discarded',reason=?,completed_at=? WHERE definition_id=? AND state='pending'", (reason, iso(self.clock()), identifier))

    def set_enabled(self, identifier, revision, enabled):
        with self.lock, self.db:
            row = self._row(identifier)
            self._check_revision(row, revision)
            value = json.loads(row["document"])
            value["enabled"] = enabled
            # Reabilitar ignora o período deliberadamente desabilitado.
            at = iso(next_at(value["rule"], self.clock())) if enabled else None
            self.db.execute("UPDATE definitions SET document=?,revision=revision+1,enabled=?,next_run_at=?,updated_at=? WHERE id=?", (json.dumps(value, ensure_ascii=False), int(enabled), at, iso(self.clock()), identifier))
            if not enabled:
                self._discard_pending(identifier, "definition_disabled")
        return self.get(identifier)

    def delete(self, identifier, revision):
        with self.lock, self.db:
            self._check_revision(self._row(identifier), revision)
            if self.db.execute("SELECT 1 FROM occurrences WHERE definition_id=? AND state IN ('running','uncertain')", (identifier,)).fetchone():
                raise RpcError("Resolva execução ativa ou incerta antes de excluir.", "definition_busy")
            self._discard_pending(identifier, "definition_deleted")
            self.db.execute("UPDATE definitions SET enabled=0,next_run_at=NULL,deleted_at=?,revision=revision+1 WHERE id=?", (iso(self.clock()), identifier))
        return {"id": identifier, "deleted": True}

    def list(self, sort="next_run_at", descending=False, offset=0, limit=20):
        if sort not in SORT_FIELDS or type(descending) is not bool or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise RpcError("Ordenação ou paginação inválida.", "invalid_argument")
        with self.lock:
            values = [self._view(row) for row in self.db.execute("SELECT * FROM definitions WHERE deleted_at IS NULL ORDER BY id")]
            for value in values:
                state = self.db.execute("SELECT state FROM occurrences WHERE definition_id=? AND state IN ('running','uncertain') LIMIT 1", (value["id"],)).fetchone()
                if state:
                    value["state"] = state[0]
        def key(value):
            return value["action_type"] if sort == "action" else value.get(sort)
        populated = [value for value in values if key(value) is not None]
        missing = [value for value in values if key(value) is None]
        ordered = sorted(populated, key=key, reverse=descending) + missing
        return {"items": ordered[offset:offset + limit], "total": len(ordered), "offset": offset, "limit": limit, "sort": sort, "descending": descending}

    def materialize(self):
        now = self.clock()
        with self.lock, self.db:
            due = self.db.execute("SELECT * FROM definitions WHERE deleted_at IS NULL AND enabled=1 AND next_run_at<=? ORDER BY next_run_at,id", (iso(now),)).fetchall()
            for row in due:
                value = json.loads(row["document"])
                rule = value["rule"]
                latest = latest_due(rule, now)
                if latest is None or latest < instant(row["next_run_at"]):
                    continue
                at = iso(latest)
                pending = self.db.execute("SELECT id FROM occurrences WHERE definition_id=? AND state='pending'", (row["id"],)).fetchone()
                if pending:
                    self.db.execute("UPDATE occurrences SET scheduled_at=?,coalesced_until=? WHERE id=?", (at, at, pending[0]))
                else:
                    event_id = uuid7() if value["action"]["type"] == "notification" else None
                    self.db.execute("INSERT INTO occurrences(id,definition_id,revision,snapshot,scheduled_at,window_start,coalesced_until,event_id,state) VALUES (?,?,?,?,?,?,?,?,'pending')", (uuid7(), row["id"], row["revision"], json.dumps(value, ensure_ascii=False), at, row["next_run_at"], at, event_id))
                self.db.execute("UPDATE definitions SET next_run_at=? WHERE id=?", (iso(next_at(rule, now)), row["id"]))

    def claim(self):
        with self.lock, self.db:
            row = self.db.execute("SELECT o.* FROM occurrences o JOIN definitions d ON d.id=o.definition_id WHERE o.state='pending' AND d.enabled=1 AND d.deleted_at IS NULL AND NOT EXISTS (SELECT 1 FROM occurrences a WHERE a.definition_id=o.definition_id AND a.state IN ('running','uncertain')) ORDER BY o.scheduled_at,o.id LIMIT 1").fetchone()
            if not row:
                return None
            self.db.execute("UPDATE occurrences SET state='running',started_at=? WHERE id=? AND state='pending'", (iso(self.clock()), row["id"]))
            return {**dict(row), "snapshot": json.loads(row["snapshot"])}

    def finish(self, identifier, state, result=None, reason=None):
        if state not in {"completed", "failed", "uncertain"}:
            raise ValueError("invalid terminal state")
        with self.lock, self.db:
            self.db.execute("UPDATE occurrences SET state=?,result=?,reason=?,completed_at=? WHERE id=? AND state='running'", (state, json.dumps(result, ensure_ascii=False), reason, iso(self.clock()), identifier))

    def resolve(self, identifier, note, confirm):
        if confirm is not True or not isinstance(note, str) or not note.strip():
            raise RpcError("Reconciliação exige motivo e confirm=true.", "confirmation_required")
        with self.lock, self.db:
            changed = self.db.execute("UPDATE occurrences SET state='resolved',resolution=?,completed_at=? WHERE id=? AND state='uncertain'", (note, iso(self.clock()), identifier)).rowcount
            if not changed:
                raise RpcError("Ocorrência não está incerta.", "not_uncertain")
        return {"id": identifier, "state": "resolved"}

    def history(self, identifier=None, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise RpcError("Paginação inválida.", "invalid_argument")
        where, params = (" WHERE definition_id=?", (identifier,)) if identifier else ("", ())
        with self.lock:
            count = self.db.execute("SELECT COUNT(*) FROM occurrences" + where, params).fetchone()[0]
            rows = self.db.execute("SELECT * FROM occurrences" + where + " ORDER BY scheduled_at DESC,id LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
        return {"items": [{**dict(row), "snapshot": json.loads(row["snapshot"]), "result": json.loads(row["result"]) if row["result"] else None} for row in rows], "total": count}

    def summary(self):
        with self.lock:
            return dict(self.db.execute("SELECT state,COUNT(*) FROM occurrences GROUP BY state").fetchall())

    def purge(self):
        cutoff = iso(self.clock() - timedelta(days=90))
        with self.lock, self.db:
            self.db.execute("DELETE FROM occurrences WHERE state IN ('completed','failed','resolved','discarded') AND completed_at<?", (cutoff,))
