"""Memória transacional de pagamentos; eventos preservados e envios sem repetição."""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Iterator

from .contracts import BtgError, date, digest, future, json_exact, now, require


SCHEMA = """
CREATE TABLE IF NOT EXISTS payment (
 id TEXT PRIMARY KEY, context TEXT NOT NULL, profile TEXT NOT NULL,
 item TEXT NOT NULL, metadata TEXT NOT NULL, preparationHash TEXT NOT NULL,
 amountCents INTEGER NOT NULL, createdAt TEXT NOT NULL, updatedAt TEXT NOT NULL,
 dueDate TEXT, requestedPaymentDate TEXT NOT NULL,
 submissionState TEXT NOT NULL DEFAULT 'prepared', approvalState TEXT NOT NULL DEFAULT 'unknown',
 bankStatus TEXT, bankPaymentId TEXT, bankBatchId TEXT, bankContractId TEXT,
 userAuthorizedAt TEXT, submittedAt TEXT, submissionObservedAt TEXT,
 approvalObservedAt TEXT, bankPaymentDate TEXT, confirmationObservedAt TEXT,
 lastSuccessfulCheckAt TEXT, lastCheckAt TEXT, nextCheckAt TEXT,
 failures INTEGER NOT NULL DEFAULT 0, lastError TEXT, receiptPath TEXT,
 leaseOwner TEXT, leaseUntil TEXT, operatorId TEXT, idempotencyKey TEXT,
 cancellationState TEXT, cancellationKey TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS payment_bank_id ON payment(context, bankPaymentId);
CREATE INDEX IF NOT EXISTS payment_due ON payment(context,dueDate);
CREATE INDEX IF NOT EXISTS payment_schedule ON payment(context,nextCheckAt);
CREATE TABLE IF NOT EXISTS paymentAttempt (
 id TEXT PRIMARY KEY, paymentId TEXT NOT NULL REFERENCES payment(id), operation TEXT NOT NULL,
 startedAt TEXT NOT NULL, completedAt TEXT, idempotencyKey TEXT NOT NULL, operatorId TEXT NOT NULL,
 result TEXT NOT NULL, evidence TEXT
);
CREATE TABLE IF NOT EXISTS paymentEvent (
 id INTEGER PRIMARY KEY AUTOINCREMENT, paymentId TEXT NOT NULL REFERENCES payment(id),
 observedAt TEXT NOT NULL, kind TEXT NOT NULL, evidence TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS event_no_update BEFORE UPDATE ON paymentEvent BEGIN SELECT RAISE(ABORT,'immutable event'); END;
CREATE TRIGGER IF NOT EXISTS event_no_delete BEFORE DELETE ON paymentEvent BEGIN SELECT RAISE(ABORT,'immutable event'); END;
PRAGMA user_version=1;
"""


class Store:
    def __init__(self, root: Path, context: str, profile: str):
        self.root = root.resolve() / "memory"
        require(self.root.resolve().is_relative_to(root.resolve()), "invalid_storage", "Memória deve permanecer no estado MCP BTG.")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path, self.context, self.profile = self.root / "payments.sqlite3", context, profile
        require(not self.path.is_symlink() and self.path.resolve().is_relative_to(self.root.resolve()), "invalid_storage", "A memória SQLite deve permanecer no estado MCP BTG.")
        with self.connection() as con:
            require(con.execute("PRAGMA user_version").fetchone()[0] in (0, 1), "schema_version", "Versão de memória não suportada.")
            con.executescript(SCHEMA)

    @contextlib.contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def row(self, con: sqlite3.Connection, identifier: str) -> dict[str, Any]:
        row = con.execute("SELECT * FROM payment WHERE id=? AND context=?", (identifier, self.context)).fetchone()
        require(row is not None, "payment_not_found", "Pagamento não encontrado neste contexto.")
        return dict(row)

    def get(self, identifier: str) -> dict[str, Any]:
        with self.connection() as con:
            return self.row(con, identifier)

    @staticmethod
    def event(con: sqlite3.Connection, identifier: str, kind: str, evidence: Any) -> None:
        con.execute("INSERT INTO paymentEvent(paymentId,observedAt,kind,evidence) VALUES(?,?,?,?)", (identifier, now(), kind, json_exact(evidence)))

    def prepare(self, identifier: str, item: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
        stamp = now()
        revision = digest({"context": self.context, "item": item, "metadata": metadata})
        with self.connection() as con:
            con.execute("INSERT INTO payment(id,context,profile,item,metadata,preparationHash,amountCents,createdAt,updatedAt,dueDate,requestedPaymentDate) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (identifier, self.context, self.profile, json_exact(item), json_exact(metadata), revision, int(item["amount"] * 100), stamp, stamp, metadata.get("dueDate"), item["paymentDate"]))
            self.event(con, identifier, "prepared", {"preparationHash": revision})
        return self.get(identifier)

    def reserve(self, identifier: str, revision: str, operator: str, operation: str = "submit") -> tuple[dict[str, Any], str]:
        attempt, key, stamp = str(uuid.uuid4()), str(uuid.uuid4()), now()
        with self.connection() as con:
            row = self.row(con, identifier)
            require(row["preparationHash"] == revision, "preparation_changed", "Hash da preparação diverge da autorização.")
            require(not row["leaseUntil"] or row["leaseUntil"] < stamp, "payment_busy", "Pagamento em atualização por outro processo.")
            if operation == "submit":
                require(row["submissionState"] == "prepared", "already_submitted", "Preparação já enviada ou incerta; consulte o histórico.")
                con.execute("UPDATE payment SET submissionState='sending',userAuthorizedAt=?,submittedAt=?,updatedAt=?,operatorId=?,idempotencyKey=?,nextCheckAt=? WHERE id=?", (stamp, stamp, stamp, operator, key, future(300), identifier))
            else:
                require(row["submissionState"] == "accepted" and row["bankPaymentId"] is not None, "cancel_unavailable", "Pagamento não possui ID bancário confirmado.")
                require(row["bankStatus"] in {"SCHEDULED", "CREATED", "VALIDATED", "RETAINED"} or row["approvalState"] == "pending", "cancel_unavailable", "Cancelamento só pode ser solicitado antes de execução; o banco verifica a elegibilidade.")
                require(row["cancellationState"] is None, "already_cancel_requested", "Cancelamento já solicitado; consulte antes de repetir.")
                con.execute("UPDATE payment SET cancellationState='sending',cancellationKey=?,updatedAt=? WHERE id=?", (key, stamp, identifier))
            con.execute("UPDATE payment SET leaseOwner=?,leaseUntil=? WHERE id=?", (attempt, future(300), identifier))
            con.execute("INSERT INTO paymentAttempt VALUES(?,?,?,?,?,?,?,?,?)", (attempt, identifier, operation, stamp, None, key, operator, "unknown", None))
            self.event(con, identifier, operation + ".started", {"attemptId": attempt, "preparationHash": revision})
            return self.row(con, identifier), attempt

    def finish(self, identifier: str, attempt: str, result: str, evidence: dict[str, Any]) -> dict[str, Any]:
        with self.connection() as con:
            row = self.row(con, identifier)
            require(row["leaseOwner"] == attempt, "lease_lost", "Reserva de envio expirou; resultado exige recuperação.")
            op = con.execute("SELECT operation FROM paymentAttempt WHERE id=? AND paymentId=?", (attempt, identifier)).fetchone()[0]
            con.execute("UPDATE paymentAttempt SET completedAt=?,result=?,evidence=? WHERE id=?", (now(), result, json_exact(evidence), attempt))
            if op == "submit":
                approval = "pending" if evidence.get("operationNeedsApproval") is True else "unknown"
                con.execute("UPDATE payment SET submissionState=?,approvalState=?,bankBatchId=?,bankContractId=?,submissionObservedAt=?,lastError=?,nextCheckAt=? WHERE id=?",
                            (result, approval, evidence.get("batchId"), evidence.get("contractGuid"), now() if result == "accepted" else None, evidence.get("errorCode"), future(300) if result in {"accepted", "unknown"} else None, identifier))
            else:
                con.execute("UPDATE payment SET cancellationState=?,lastError=?,nextCheckAt=? WHERE id=?", (result, evidence.get("errorCode"), now(), identifier))
            con.execute("UPDATE payment SET leaseOwner=NULL,leaseUntil=NULL,updatedAt=? WHERE id=?", (now(), identifier))
            self.event(con, identifier, op + "." + result, evidence)
        return self.get(identifier)

    def claim(self, identifier: str) -> tuple[dict[str, Any], str]:
        owner, stamp = str(uuid.uuid4()), now()
        with self.connection() as con:
            row = self.row(con, identifier)
            require(row["submissionState"] in {"sending", "accepted", "unknown"}, "not_submitted", "Pagamento não foi enviado.")
            require(not row["leaseUntil"] or row["leaseUntil"] < stamp, "payment_busy", "Pagamento reservado por outro processo.")
            con.execute("UPDATE payment SET leaseOwner=?,leaseUntil=?,lastCheckAt=? WHERE id=?", (owner, future(300), stamp, identifier))
            if row["submissionState"] == "sending":
                con.execute("UPDATE payment SET submissionState='unknown' WHERE id=?", (identifier,))
                self.event(con, identifier, "interrupted_submission", {})
            if row["cancellationState"] == "sending":
                con.execute("UPDATE payment SET cancellationState='unknown' WHERE id=?", (identifier,))
            return self.row(con, identifier), owner

    def update_check(self, identifier: str, owner: str, fields: dict[str, Any], evidence: dict[str, Any], error: str | None = None) -> dict[str, Any]:
        allowed = {"bankPaymentId", "bankStatus", "approvalState", "bankPaymentDate", "confirmationObservedAt", "approvalObservedAt", "nextCheckAt", "submissionState", "cancellationState"}
        require(set(fields) <= allowed, "invalid_update", "Atualização de monitor inválida.")
        with self.connection() as con:
            row = self.row(con, identifier)
            require(row["leaseOwner"] == owner, "lease_lost", "Atualização perdeu a reserva.")
            fields.update(updatedAt=now(), leaseOwner=None, leaseUntil=None, lastError=error, failures=row["failures"] + 1 if error else 0)
            if not error:
                fields["lastSuccessfulCheckAt"] = now()
            con.execute("UPDATE payment SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?", (*fields.values(), identifier))
            self.event(con, identifier, "check.failed" if error else "bank.observed", evidence)
        return self.get(identifier)

    def list(self, filters: dict[str, Any]) -> dict[str, Any]:
        require(isinstance(filters, dict), "invalid_filter", "Filtros devem ser objeto.")
        require(type(filters.get("dueOnly", False)) is bool, "invalid_filter", "dueOnly deve ser booleano.")
        clauses, values = ["context=?"], [self.context]
        exact = {"submissionState", "approvalState", "bankStatus", "id"}
        ranges = {"createdFrom": ("createdAt", ">="), "createdTo": ("createdAt", "<="), "dueFrom": ("dueDate", ">="), "dueTo": ("dueDate", "<="), "paymentFrom": ("bankPaymentDate", ">="), "paymentTo": ("bankPaymentDate", "<=")}
        require(set(filters) <= exact | set(ranges) | {"limit", "offset", "dueOnly"}, "invalid_filter", "Filtro desconhecido.")
        for key, value in filters.items():
            if key in exact:
                require(isinstance(value, str), "invalid_filter", "Filtro deve ser texto.")
                clauses.append(key + "=?"); values.append(value)
            elif key in ranges:
                require(isinstance(value, str), "invalid_filter", "Filtro deve ser texto.")
                column, operator = ranges[key]
                if column == "createdAt" and len(value) == 10:
                    civil = dt.date.fromisoformat(date(value))
                    if key == "createdTo":
                        civil += dt.timedelta(days=1)
                        operator = "<"
                    value = dt.datetime.combine(civil, dt.time(), tzinfo=dt.timezone(dt.timedelta(hours=-3))).astimezone(dt.timezone.utc).isoformat(timespec="seconds")
                elif column == "createdAt":
                    try:
                        stamp = dt.datetime.fromisoformat(value)
                        require(stamp.tzinfo is not None, "invalid_filter", "Instante de lançamento deve informar fuso.")
                        value = stamp.astimezone(dt.timezone.utc).isoformat(timespec="seconds")
                    except ValueError as error:
                        raise BtgError("invalid_filter", "Instante de lançamento inválido.") from error
                else:
                    value = date(value)
                clauses.append(column + operator + "?"); values.append(value)
        if filters.get("dueOnly"):
            clauses.append("nextCheckAt<=?"); values.append(now())
            clauses.append("(leaseUntil IS NULL OR leaseUntil<?)"); values.append(now())
        limit, offset = filters.get("limit", 100), filters.get("offset", 0)
        require(type(limit) is int and 1 <= limit <= 500 and type(offset) is int and offset >= 0, "invalid_filter", "Paginação local inválida.")
        with self.connection() as con:
            rows = con.execute("SELECT * FROM payment WHERE " + " AND ".join(clauses) + " ORDER BY createdAt,id LIMIT ? OFFSET ?", (*values, limit + 1, offset)).fetchall()
        return {"items": [dict(r) for r in rows[:limit]], "hasMore": len(rows) > limit, "nextOffset": offset + limit if len(rows) > limit else None}

    def history(self, identifier: str) -> list[dict[str, Any]]:
        with self.connection() as con:
            self.row(con, identifier)
            return [dict(r) for r in con.execute("SELECT * FROM paymentEvent WHERE paymentId=? ORDER BY id", (identifier,))]

    def receipt(self, identifier: str, content: bytes) -> str:
        require(content.startswith(b"%PDF-"), "invalid_receipt", "Comprovante não é PDF.")
        self.get(identifier)
        directory = self.root / "receipts"
        require(not directory.is_symlink(), "invalid_path", "Diretório de comprovantes não pode ser link.")
        directory.mkdir(exist_ok=True)
        target = directory / (str(uuid.UUID(identifier)) + "-" + str(uuid.uuid4()) + ".pdf")
        require(target.resolve().is_relative_to(self.root.resolve()), "invalid_path", "Comprovante fora do estado MCP BTG.")
        with target.open("xb") as output:
            output.write(content)
        relative = str(target.relative_to(self.root))
        with self.connection() as con:
            con.execute("UPDATE payment SET receiptPath=?,updatedAt=? WHERE id=? AND context=?", (relative, now(), identifier, self.context))
            self.event(con, identifier, "receipt.saved", {"path": relative, "sha256": __import__("hashlib").sha256(content).hexdigest()})
        return str(target)
