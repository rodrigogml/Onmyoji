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

    def record(self, stage, *, transaction=None, **values):
        at = timestamp()
        arguments = (at, json.dumps({"stage": stage, **values}, ensure_ascii=False))
        if transaction is self.store.db:
            transaction.execute("INSERT INTO notification_log_journal(at,payload) VALUES (?,?)", arguments)
            return
        if transaction is not None:
            raise ValueError("O journal deve usar a transação do gateway.")
        with self.store.lock, self.store.db:
            self.store.db.execute("INSERT INTO notification_log_journal(at,payload) VALUES (?,?)", arguments)

    def _validate_directory(self):
        if self.directory.is_symlink() or getattr(self.directory, "is_junction", lambda: False)() or self.directory.resolve().parent != self.directory.parent.resolve():
            raise OSError("Diretório de logs não pode redirecionar a operação para outro local.")

    @staticmethod
    def _recover_entries(path):
        if path.is_symlink():
            raise OSError("Arquivo de log não pode ser um link.")
        entries = set()
        if not path.exists():
            return entries
        with path.open("r+b") as stream:
            while True:
                start = stream.tell()
                raw = stream.readline()
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    # Queda durante append: remover apenas a cauda incompleta.
                    # O journal permanece pendente e repõe a linha inteira.
                    stream.seek(start); stream.truncate()
                    stream.flush(); os.fsync(stream.fileno())
                    break
                try:
                    value = json.loads(raw.decode("utf-8"))
                    entries.add(value["journal_entry"])
                except (ValueError, KeyError, TypeError) as error:
                    raise OSError("Log contém uma linha inválida; journal preservado.") from error
        return entries

    def flush(self):
        with self.lock:
            self._validate_directory()
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.store.lock:
                rows = self.store.db.execute("SELECT id,at,payload FROM notification_log_journal WHERE flushed=0 ORDER BY id").fetchall()
            entries_by_day = {}
            for identifier, at, payload in rows:
                path = self.directory / ("agent-gateway-" + at[:10] + ".jsonl")
                if path not in entries_by_day:
                    entries_by_day[path] = self._recover_entries(path)
                if identifier not in entries_by_day[path]:
                    with path.open("a", encoding="utf-8") as stream:
                        stream.write(json.dumps({"journal_entry": identifier, "at": at, **json.loads(payload)}, ensure_ascii=False) + "\n")
                        stream.flush(); os.fsync(stream.fileno())
                    entries_by_day[path].add(identifier)
                with self.store.lock, self.store.db:
                    self.store.db.execute("UPDATE notification_log_journal SET flushed=1 WHERE id=?", (identifier,))

    def purge(self, now=None):
        self._validate_directory()
        now = now or datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=20)).date().isoformat()
        if self.directory.is_dir():
            for path in self.directory.iterdir():
                match = re.fullmatch(r"agent-gateway-(\d{4}-\d{2}-\d{2})\.jsonl", path.name)
                if match and match[1] < cutoff and path.is_file() and not path.is_symlink():
                    path.unlink()
        with self.store.lock, self.store.db:
            self.store.db.execute("DELETE FROM notification_log_journal WHERE flushed=1 AND substr(at,1,10)<?", (cutoff,))
