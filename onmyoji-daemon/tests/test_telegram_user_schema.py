"""Phase 1 exact SQLite DDL, upgrade, constraints and rollback."""
from __future__ import annotations

import sqlite3

import pytest

from onmyoji_daemon.telegram_user_schema import TELEGRAM_USERS_SCHEMA_VERSION, migrate_telegram_users
from onmyoji_daemon.telegram_users import TelegramUserRegistry
import threading


def test_schema_v5_preserves_existing_legacy_conversations(tmp_path):
    path = tmp_path / "gateway.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    with db:
        db.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        db.execute("INSERT INTO gateway_schema_migrations VALUES(4, 1.0)")
        db.execute("CREATE TABLE conversations(chat_id TEXT PRIMARY KEY, codex_thread_id TEXT)")
        db.execute("INSERT INTO conversations VALUES('group:-100:42', 'old-thread')")
        migrate_telegram_users(db)
    assert TELEGRAM_USERS_SCHEMA_VERSION == 5
    assert [row[0] for row in db.execute("SELECT version FROM gateway_schema_migrations ORDER BY version")] == [4, 5]
    assert db.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='group:-100:42'").fetchone()[0] == "old-thread"
    for table in ("telegram_users", "telegram_user_roles", "telegram_user_audit"):
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    indexes = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"telegram_users_pending", "telegram_users_recency", "telegram_roles_by_role", "telegram_user_audit_by_user", "telegram_user_audit_by_age"} <= indexes
    db.close()


def test_storage_survives_registry_reinitialization(tmp_path):
    path = tmp_path / "gateway.sqlite3"
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    with db:
        db.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        migrate_telegram_users(db)
    users = TelegramUserRegistry(db, threading.RLock(), lambda: {9})
    users.register_from_dm({"id": 41, "first_name": "Mary"}, "41")
    users.set_roles(9, 41, ["CHAT_USER"], 1)
    db.close()
    reopened = sqlite3.connect(path)
    reopened.execute("PRAGMA foreign_keys=ON")
    again = TelegramUserRegistry(reopened, threading.RLock(), lambda: {9}, user_roles_enabled=True)
    assert again.self_status(41).revision == 2
    assert again.can_chat(41)
    assert reopened.execute("SELECT COUNT(*) FROM gateway_schema_migrations WHERE version=5").fetchone()[0] == 1
    reopened.close()


def test_invalid_role_and_orphan_rows_are_rejected_by_database(tmp_path):
    db = sqlite3.connect(tmp_path / "gateway.sqlite3")
    db.execute("PRAGMA foreign_keys=ON")
    with db:
        db.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        migrate_telegram_users(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO telegram_user_roles VALUES(42,'CHAT_USER',1,9)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO telegram_users(telegram_user_id,dm_conversation_id,status,requested_at,updated_at) VALUES(42,'42','ADMIN',1,1)")
    db.execute("INSERT INTO telegram_users(telegram_user_id,dm_conversation_id,status,requested_at,updated_at) VALUES(42,'42','PENDING',1,1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO telegram_user_roles VALUES(42,'OWNER',1,9)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO telegram_user_roles VALUES(99,'CHAT_USER',1,9)")
    db.rollback()
    db.close()


def test_migration_failure_rolled_back_with_ledger(tmp_path):
    db = sqlite3.connect(tmp_path / "gateway.sqlite3")
    db.execute("PRAGMA foreign_keys=ON")
    with db:
        db.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        db.execute("INSERT INTO gateway_schema_migrations VALUES (4, 1.0)")
        # A conflicting preexisting table indicates corrupted partial schema.
        db.execute("CREATE TABLE telegram_users(broken TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        with db:
            migrate_telegram_users(db)
    assert [row[0] for row in db.execute("SELECT version FROM gateway_schema_migrations")] == [4]
    assert db.execute("SELECT name FROM sqlite_master WHERE name='telegram_user_roles'").fetchone() is None
    db.close()


def test_registry_refuses_foreign_keys_off(tmp_path):
    db = sqlite3.connect(tmp_path / "gateway.sqlite3")
    with db:
        db.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
        migrate_telegram_users(db)
    with pytest.raises(ValueError, match="foreign keys"):
        TelegramUserRegistry(db, threading.RLock(), lambda: set())
    db.close()
