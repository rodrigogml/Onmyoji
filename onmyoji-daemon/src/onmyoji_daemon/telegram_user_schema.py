"""Transactional SQLite schema for Telegram user registry (gateway schema v5).

GatewayStore invokes this only inside its versioned migration transaction,
after existing gateway v1-v4 tables have been created or upgraded. Keeping the
DDL here lets registry tests exercise the exact production schema without
starting the bot, Codex or the gateway process.
"""
from __future__ import annotations

import sqlite3
import time


TELEGRAM_USERS_SCHEMA_VERSION = 5


def migrate_telegram_users(db: sqlite3.Connection) -> None:
    """Apply the v5 migration; caller owns the transaction and writer lease.

    This is intentionally not called as an ad-hoc table initialization.
    No arbitrary legacy user records are promoted to authorized roles.
    """
    statements = (
        """CREATE TABLE telegram_users (
            telegram_user_id INTEGER PRIMARY KEY CHECK(telegram_user_id > 0),
            username TEXT,
            display_name TEXT NOT NULL DEFAULT '',
            language_code TEXT,
            dm_conversation_id TEXT NOT NULL CHECK(length(dm_conversation_id)>0),
            status TEXT NOT NULL CHECK(status IN
                ('PENDING','ACTIVE','SUSPENDED','REJECTED','WITHDRAWN')),
            revision INTEGER NOT NULL DEFAULT 1 CHECK(revision>=1),
            requested_at REAL NOT NULL,
            decided_at REAL,
            decided_by INTEGER,
            updated_at REAL NOT NULL
        )""",
        """CREATE TABLE telegram_user_roles (
            telegram_user_id INTEGER NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('CHAT_USER','NOTIFY_TARGET')),
            granted_at REAL NOT NULL,
            granted_by INTEGER NOT NULL,
            PRIMARY KEY(telegram_user_id, role),
            FOREIGN KEY(telegram_user_id) REFERENCES telegram_users(telegram_user_id)
        )""",
        """CREATE TABLE telegram_user_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_user_id INTEGER NOT NULL,
            actor_user_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            previous_status TEXT,
            new_status TEXT NOT NULL,
            previous_roles TEXT NOT NULL,
            new_roles TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(telegram_user_id) REFERENCES telegram_users(telegram_user_id)
        )""",
        "CREATE INDEX telegram_users_pending ON telegram_users(status, requested_at DESC, telegram_user_id DESC)",
        "CREATE INDEX telegram_users_recency ON telegram_users(requested_at DESC, telegram_user_id DESC)",
        "CREATE INDEX telegram_roles_by_role ON telegram_user_roles(role, telegram_user_id)",
        "CREATE INDEX telegram_user_audit_by_user ON telegram_user_audit(telegram_user_id, id DESC)",
        "CREATE INDEX telegram_user_audit_by_age ON telegram_user_audit(created_at)",
    )
    for statement in statements:
        db.execute(statement)
    db.execute("INSERT INTO gateway_schema_migrations(version,applied_at) VALUES (?,?)",
               (TELEGRAM_USERS_SCHEMA_VERSION, time.time()))
