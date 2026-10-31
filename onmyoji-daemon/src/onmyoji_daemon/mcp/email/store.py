"""Registro de submissão SMTP sem corpos, anexos ou credenciais."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
from pathlib import Path
import sqlite3
import time

from .contracts import require, text


class SubmissionStore:
    def __init__(self, root: Path):
        self.path = root / "configs" / "mcp" / "email" / "submissions.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS submission (account TEXT, request_id TEXT, binding TEXT, digest TEXT, status TEXT, result TEXT, created REAL, PRIMARY KEY(account,request_id))")

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=30)) as con:
            con.row_factory = sqlite3.Row
            with con: yield con

    def begin(self, account: str, request_id: str, binding: str, fingerprint: str) -> dict | None:
        text(request_id, "request_id", 200)
        with self.connection() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM submission WHERE account=? AND request_id=?", (account, request_id)).fetchone()
            if row:
                require(row["binding"] == binding and row["digest"] == fingerprint, "request_conflict", "request_id já usado com outra conta ou conteúdo.")
                return json.loads(row["result"]) if row["result"] else {"status": "unknown", "request_id": request_id, "retry_allowed": False}
            con.execute("INSERT INTO submission VALUES(?,?,?,?,?,?,?)", (account, request_id, binding, fingerprint, "sending", None, time.time()))
        return None

    def previous(self, account: str, request_id: str, binding: str, fingerprint: str) -> dict | None:
        with self.connection() as con:
            row = con.execute("SELECT * FROM submission WHERE account=? AND request_id=?", (account, request_id)).fetchone()
        if row is None: return None
        require(row["binding"] == binding and row["digest"] == fingerprint, "request_conflict", "request_id já usado com outra conta ou conteúdo.")
        return json.loads(row["result"]) if row["result"] else {"status": "unknown", "request_id": request_id, "retry_allowed": False}

    def finish(self, account: str, request_id: str, result: dict) -> dict:
        with self.connection() as con:
            con.execute("UPDATE submission SET status=?,result=? WHERE account=? AND request_id=?", (result["status"], json.dumps(result), account, request_id))
        return result

    def status(self, account: str, request_id: str, binding: str) -> dict:
        with self.connection() as con:
            row = con.execute("SELECT * FROM submission WHERE account=? AND request_id=? AND binding=?", (account, request_id, binding)).fetchone()
        require(row is not None, "submission_not_found", "Submissão não encontrada nesta conta.")
        return json.loads(row["result"]) if row["result"] else {"status": "unknown", "request_id": request_id, "retry_allowed": False}
