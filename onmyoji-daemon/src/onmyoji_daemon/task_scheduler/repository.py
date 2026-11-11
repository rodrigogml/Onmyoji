"""Catálogo TOML observado e histórico transacional de ocorrências."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import tomllib

from .calendar import initial_at, instant, iso, latest_due, next_at, validate_rule
from .contracts import validate_definition
from ..agent_notification.contract import uuid7
from ..rpc import RpcError
from ..telegram_persistence import GatewayStore

SORT_FIELDS = ("id", "status", "action", "expression", "timezone", "next_run_at", "created_at", "channel", "thread_policy", "last_result")


class TaskRepository:
    """Mantém arquivos como fonte de verdade e SQLite apenas como estado runtime."""

    def __init__(self, path: Path, workspace: Path, clock=None, schedules_dir: Path | None = None, script_timeout_seconds: int = 900):
        self.workspace = workspace.resolve()
        self.schedules_dir = schedules_dir or path.parent.parent / "schedules"
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.script_timeout_seconds = script_timeout_seconds
        self.lock = threading.RLock()
        self._reserved_here = set()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.schedules_dir.mkdir(parents=True, exist_ok=True)
        self.lease = GatewayStore.open_lease(path.with_suffix(".lock"))
        try:
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version > 3:
                raise RpcError("Banco do agendador é mais novo que este runtime.", "schema_newer")
            if version in {1, 2}:
                backup = path.with_name(f"scheduler-before-files-v{version}.sqlite3")
                if not backup.exists():
                    with sqlite3.connect(backup) as target:
                        self.db.backup(target)
            with self.db:
                self.db.execute("CREATE TABLE IF NOT EXISTS definitions(id TEXT PRIMARY KEY,revision INTEGER NOT NULL,document TEXT NOT NULL,enabled INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,next_run_at TEXT,deleted_at TEXT)")
                columns = {row[1] for row in self.db.execute("PRAGMA table_info(definitions)")}
                for name, definition in (("source_mtime_ns", "INTEGER"), ("diagnosis", "TEXT"), ("source_present", "INTEGER NOT NULL DEFAULT 1")):
                    if name not in columns:
                        self.db.execute(f"ALTER TABLE definitions ADD COLUMN {name} {definition}")
                self.db.execute("CREATE TABLE IF NOT EXISTS occurrences(id TEXT PRIMARY KEY,definition_id TEXT NOT NULL,revision INTEGER NOT NULL,snapshot TEXT NOT NULL,scheduled_at TEXT NOT NULL,window_start TEXT NOT NULL,coalesced_until TEXT NOT NULL,event_id TEXT,state TEXT NOT NULL,started_at TEXT,completed_at TEXT,result TEXT,reason TEXT,resolution TEXT,UNIQUE(definition_id,revision,scheduled_at))")
                self.db.execute("CREATE INDEX IF NOT EXISTS occurrence_dispatch ON occurrences(state,scheduled_at)")
                self.db.execute("DROP INDEX IF EXISTS occurrence_one_active")
                self.db.execute("CREATE UNIQUE INDEX occurrence_one_active ON occurrences(definition_id) WHERE state IN ('reserved','running','uncertain')")
                self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS occurrence_one_pending ON occurrences(definition_id) WHERE state='pending'")
                self.db.execute("PRAGMA user_version=3")
                self.db.execute("UPDATE occurrences SET state='uncertain',reason='service_restarted' WHERE state='running'")
        except Exception:
            self.lease.close()
            if hasattr(self, "db"):
                self.db.close()
            raise

    def close(self):
        self.db.close()
        self.lease.close()

    @staticmethod
    def _valid_identifier(path: Path) -> bool:
        return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", path.stem))

    def _discard_pending(self, identifier: str, reason: str) -> None:
        self.db.execute("UPDATE occurrences SET state='discarded',reason=?,completed_at=? WHERE definition_id=? AND state IN ('pending','reserved')", (reason, iso(self.clock()), identifier))

    def _record_invalid(self, identifier: str, mtime: int, diagnosis: str) -> None:
        now = iso(self.clock())
        row = self.db.execute("SELECT revision,document,created_at FROM definitions WHERE id=?", (identifier,)).fetchone()
        document = row["document"] if row else json.dumps({"schema_version": 1, "description": identifier, "enabled": False}, ensure_ascii=False)
        created = row["created_at"] if row else now
        revision = row["revision"] + 1 if row else 1
        self.db.execute("INSERT OR REPLACE INTO definitions(id,revision,document,enabled,created_at,updated_at,next_run_at,deleted_at,source_mtime_ns,diagnosis,source_present) VALUES (?,?,?,?,?,?,NULL,NULL,?,?,1)", (identifier, revision, document, 0, created, now, mtime, diagnosis))
        self._discard_pending(identifier, "definition_invalid")

    def reconcile(self, *, force: bool = False) -> None:
        """Revalida somente fontes novas ou cujo mtime mudou."""
        now = self.clock()
        paths = {path.stem: path for path in self.schedules_dir.iterdir() if path.is_file() and path.suffix.casefold() == ".toml" and self._valid_identifier(path)}
        with self.lock, self.db:
            known = {row["id"]: row for row in self.db.execute("SELECT * FROM definitions")}
            for identifier, path in paths.items():
                mtime = path.stat().st_mtime_ns
                row = known.get(identifier)
                if not force and row and row["source_present"] and row["source_mtime_ns"] == mtime:
                    continue
                try:
                    if path.is_symlink():
                        raise RpcError("Definição não pode ser link.", "invalid_schedule")
                    value = validate_definition(tomllib.loads(path.read_text(encoding="utf-8")), self.workspace, now, self.script_timeout_seconds)
                except (OSError, UnicodeError, tomllib.TOMLDecodeError, RpcError) as error:
                    self._record_invalid(identifier, mtime, getattr(error, "code", "invalid_toml"))
                    continue
                if row:
                    self._discard_pending(identifier, "definition_changed")
                revision = row["revision"] + 1 if row else 1
                next_run = iso(initial_at(value["time"], now)) if value["enabled"] else None
                self.db.execute("INSERT OR REPLACE INTO definitions(id,revision,document,enabled,created_at,updated_at,next_run_at,deleted_at,source_mtime_ns,diagnosis,source_present) VALUES (?,?,?,?,?,?,?,NULL,?,NULL,1)", (identifier, revision, json.dumps(value, ensure_ascii=False), int(value["enabled"]), value["created_at"], iso(now), next_run, mtime))
                if not value["enabled"]:
                    self._discard_pending(identifier, "definition_disabled")
            for identifier, row in known.items():
                if identifier in paths or not row["source_present"]:
                    continue
                self._discard_pending(identifier, "definition_deleted")
                self.db.execute("UPDATE definitions SET enabled=0,next_run_at=NULL,deleted_at=?,updated_at=?,source_present=0 WHERE id=?", (iso(now), iso(now), identifier))

    def _row(self, identifier: str):
        row = self.db.execute("SELECT * FROM definitions WHERE id=? AND source_present=1", (identifier,)).fetchone()
        if not row:
            raise RpcError("Agendamento não encontrado.", "not_found")
        return row

    def _view(self, row):
        document = json.loads(row["document"])
        action = document.get("action", {})
        event = action.get("event", {})
        channel = event.get("response_channel", {})
        rule = document.get("time", {})
        active = self.db.execute("SELECT state FROM occurrences WHERE definition_id=? AND state IN ('reserved','running','uncertain') ORDER BY scheduled_at LIMIT 1", (row["id"],)).fetchone()
        last = self.db.execute("SELECT state FROM occurrences WHERE definition_id=? AND state IN ('completed','failed','uncertain','resolved','discarded') ORDER BY COALESCE(completed_at,scheduled_at) DESC,id DESC LIMIT 1", (row["id"],)).fetchone()
        status = "invalid" if row["diagnosis"] else active[0] if active else "enabled" if row["enabled"] else "disabled"
        expression = rule.get("at") if rule.get("type") == "once" else f"{rule.get('every_seconds')}s" if rule.get("type") == "interval" else rule.get("expression")
        return {**document, "id": row["id"], "revision": row["revision"], "status": status, "diagnosis": row["diagnosis"], "next_run_at": row["next_run_at"], "created_at": row["created_at"], "updated_at": row["updated_at"], "action_type": action.get("type"), "expression": expression, "timezone": rule.get("timezone"), "channel": channel.get("type"), "thread_policy": event.get("thread_policy"), "last_result": last[0] if last else None}

    def get(self, identifier):
        with self.lock:
            return self._view(self._row(identifier))

    def list(self, sort="next_run_at", descending=False, offset=0, limit=20):
        if sort not in SORT_FIELDS or type(descending) is not bool or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise RpcError("Ordenação ou paginação inválida.", "invalid_argument")
        with self.lock:
            values = [self._view(row) for row in self.db.execute("SELECT * FROM definitions WHERE source_present=1 ORDER BY id")]
        key_name = "action_type" if sort == "action" else sort
        populated = [value for value in values if value.get(key_name) is not None]
        missing = [value for value in values if value.get(key_name) is None]
        ordered = sorted(populated, key=lambda value: value[key_name], reverse=descending) + missing
        return {"items": ordered[offset:offset + limit], "total": len(ordered), "offset": offset, "limit": limit, "sort": sort, "descending": descending}

    def materialize(self):
        now = self.clock()
        with self.lock, self.db:
            due = self.db.execute("SELECT * FROM definitions WHERE source_present=1 AND diagnosis IS NULL AND enabled=1 AND next_run_at<=? ORDER BY next_run_at,id", (iso(now),)).fetchall()
            for row in due:
                value = json.loads(row["document"])
                rule = value["time"]
                latest = latest_due(rule, now)
                if latest is None or latest < instant(row["next_run_at"]):
                    continue
                at = iso(latest)
                pending = self.db.execute("SELECT id FROM occurrences WHERE definition_id=? AND state='pending'", (row["id"],)).fetchone()
                if pending:
                    self.db.execute("UPDATE occurrences SET scheduled_at=?,coalesced_until=? WHERE id=?", (at, at, pending[0]))
                else:
                    event_id = uuid7() if value["action"]["type"] == "event" else None
                    self.db.execute("INSERT INTO occurrences(id,definition_id,revision,snapshot,scheduled_at,window_start,coalesced_until,event_id,state) VALUES (?,?,?,?,?,?,?,?,'pending')", (uuid7(), row["id"], row["revision"], json.dumps(value, ensure_ascii=False), at, row["next_run_at"], at, event_id))
                self.db.execute("UPDATE definitions SET next_run_at=? WHERE id=?", (iso(next_at(rule, now)), row["id"]))

    def claim(self):
        with self.lock, self.db:
            owned = tuple(self._reserved_here)
            exclusion = " AND o.id NOT IN (" + ",".join("?" for _ in owned) + ")" if owned else ""
            row = self.db.execute("SELECT o.* FROM occurrences o JOIN definitions d ON d.id=o.definition_id WHERE o.state IN ('pending','reserved') AND d.enabled=1 AND d.source_present=1 AND d.diagnosis IS NULL AND NOT EXISTS (SELECT 1 FROM occurrences a WHERE a.definition_id=o.definition_id AND a.id!=o.id AND a.state IN ('reserved','running','uncertain'))" + exclusion + " ORDER BY o.scheduled_at,o.id LIMIT 1", owned).fetchone()
            if not row:
                return None
            self.db.execute("UPDATE occurrences SET state='reserved',started_at=NULL WHERE id=? AND state IN ('pending','reserved')", (row["id"],))
            self._reserved_here.add(row["id"])
            return {**dict(row), "state": "reserved", "snapshot": json.loads(row["snapshot"])}

    def begin(self, identifier):
        """Grava a fronteira imediatamente antes do primeiro efeito externo."""
        with self.lock, self.db:
            changed = self.db.execute("UPDATE occurrences SET state='running',started_at=? WHERE id=? AND state='reserved' AND EXISTS (SELECT 1 FROM definitions d WHERE d.id=occurrences.definition_id AND d.enabled=1 AND d.source_present=1 AND d.diagnosis IS NULL)", (iso(self.clock()), identifier)).rowcount
            if not changed:
                self.db.execute("UPDATE occurrences SET state='discarded',reason='definition_withdrawn',completed_at=? WHERE id=? AND state='reserved'", (iso(self.clock()), identifier))
                self._reserved_here.discard(identifier)
            return bool(changed)

    def finish(self, identifier, state, result=None, reason=None):
        if state not in {"completed", "failed", "uncertain"}:
            raise ValueError("invalid terminal state")
        with self.lock, self.db:
            self.db.execute("UPDATE occurrences SET state=?,result=?,reason=?,completed_at=? WHERE id=? AND state IN ('reserved','running')", (state, json.dumps(result, ensure_ascii=False), reason, iso(self.clock()), identifier))
            self._reserved_here.discard(identifier)

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

    def preview(self, rule, count):
        now = self.clock()
        value = validate_rule(rule, now)
        instants = []
        for _ in range(count):
            at = next_at(value, now)
            if at is None:
                break
            instants.append(iso(at))
            now = at
        return {"instants": instants, "timezone": value.get("timezone")}

    def summary(self):
        with self.lock:
            return dict(self.db.execute("SELECT state,COUNT(*) FROM occurrences GROUP BY state").fetchall())

    def purge(self):
        cutoff = iso(self.clock() - timedelta(days=20))
        with self.lock, self.db:
            self.db.execute("DELETE FROM occurrences WHERE state IN ('completed','failed','resolved','discarded') AND completed_at<?", (cutoff,))
