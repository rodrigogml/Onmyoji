"""Journal transacional e arquivos JSONL diários recuperáveis."""
from datetime import datetime, timedelta, timezone
import json
import os
import re
import threading


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class NotificationLog:
    def __init__(self, store, directory):
        self.store, self.directory = store, directory
        self.lock = threading.Lock()

    def record(self, stage, **values):
        at = timestamp()
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO notification_log_journal(at,payload) VALUES (?,?)", (at, json.dumps({"stage": stage, **values}, ensure_ascii=False)))

    def flush(self):
        with self.lock:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.store.lock:
                rows = self.store.db.execute("SELECT id,at,payload FROM notification_log_journal WHERE flushed=0 ORDER BY id").fetchall()
            for identifier, at, payload in rows:
                path = self.directory / ("notification-" + at[:10] + ".jsonl")
                # Uma queda depois de fsync e antes do commit pode repetir a
                # escrita; o ID privado do journal permite reconhecer a linha.
                marker = '"journal_entry": ' + str(identifier) + ','
                already = False
                if path.exists():
                    with path.open(encoding="utf-8") as source:
                        already = any(line.startswith("{" + marker) for line in source)
                if not already:
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"journal_entry": identifier, "at": at, **json.loads(payload)}, ensure_ascii=False) + "\n")
                        stream.flush(); os.fsync(stream.fileno())
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE notification_log_journal SET flushed=1 WHERE id=?", (identifier,))

    def purge(self, now=None):
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=20)).date().isoformat()
        if self.directory.is_dir():
            for path in self.directory.iterdir():
                match = re.fullmatch(r"notification-(\d{4}-\d{2}-\d{2})\.jsonl", path.name)
                if match and match[1] < cutoff and path.is_file() and not path.is_symlink():
                    path.unlink()
        with self.store.lock, self.store.db:
            self.store.db.execute("DELETE FROM notification_log_journal WHERE flushed=1 AND substr(at,1,10)<?", (cutoff,))
