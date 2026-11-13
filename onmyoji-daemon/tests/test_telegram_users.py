"""Phase 1: registry state, role transitions and concurrency without Telegram API."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import threading

import pytest

from onmyoji_daemon.telegram_user_policy import RegistrationStatus, UserAction, UserRole
from onmyoji_daemon.telegram_user_schema import migrate_telegram_users
from onmyoji_daemon.telegram_users import (
    TelegramUserRegistry,
    UserAccessDenied,
    UserNotFound,
    UserRegistryError,
    UserRevisionConflict,
    UserTransitionError,
)


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "users.sqlite3"
    database = sqlite3.connect(path, check_same_thread=False)
    database.execute("PRAGMA foreign_keys=ON")
    database.execute("CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)")
    database.execute("INSERT INTO gateway_schema_migrations VALUES(4,1)")
    with database:
        migrate_telegram_users(database)
    owners = {9, 19}
    current_time = [1_800_000_000.0]
    users = TelegramUserRegistry(database, threading.RLock(), lambda: set(owners),
                                 user_roles_enabled=True, clock=lambda: current_time[0])
    yield users, database, owners, current_time
    database.close()


def sender(identifier=41, username="mary", first_name="Mary", **kwargs):
    return {"id": identifier, "username": username, "first_name": first_name, **kwargs}


def pending(users, identifier=41, **kwargs):
    return users.register_from_dm(sender(identifier, **kwargs), str(identifier)).user


def audit(db, user=41):
    return db.execute("SELECT actor_user_id,event,previous_status,new_status,previous_roles,new_roles "
                      "FROM telegram_user_audit WHERE telegram_user_id=? ORDER BY id", (user,)).fetchall()


def test_registration_is_pending_and_does_not_grant_permissions(registry):
    users, db, _owners, _clock = registry
    result = users.register_from_dm(sender(), "41")
    assert result.changed and result.outcome == "PENDING"
    assert result.user.revision == 1 and result.user.status == RegistrationStatus.PENDING
    assert result.user.roles == ()
    assert not users.can_chat(41) and not users.can_notify(41, "41")
    assert audit(db) == [(41, "REGISTERED", None, "PENDING", "[]", "[]")]


def test_duplicate_registration_idempotent_and_profile_is_not_identity(registry):
    users, db, _owners, _clock = registry
    user = pending(users)
    duplicate = users.register_from_dm(sender(username="different", first_name="New name"), "41")
    assert not duplicate.changed and duplicate.user.revision == user.revision
    assert duplicate.user.username == "different" and duplicate.user.display_name == "New name"
    assert duplicate.user.dm_conversation_id == user.dm_conversation_id
    assert len(audit(db)) == 1


def test_dm_topic_accepted_and_not_retargeted_without_reregistration(registry):
    users, _db, _owners, _clock = registry
    first = users.register_from_dm(sender(), "private:41:123").user
    second = users.register_from_dm(sender(), "direct:41:234").user
    assert first.dm_conversation_id == second.dm_conversation_id == "private:41:123"
    assert not users.can_notify(41, "direct:41:234")


@pytest.mark.parametrize("conversation_id", [
    "42", "group:-100:0", "private:42:5", "direct:42:1", "41/other",
    "private:41:-2", "private:41:abc", "direct:41:01", "private:041:1",
    "41\n", "private:41:9223372036854775808",
])
def test_registration_rejects_non_owned_dm(registry, conversation_id):
    users = registry[0]
    with pytest.raises(UserRegistryError):
        users.register_from_dm(sender(), conversation_id)
    assert users.self_status(41) is None


@pytest.mark.parametrize("id_value", [None, 0, -1, True, 3.5, "41", 2**63])
def test_invalid_sender_id_rejected(registry, id_value):
    with pytest.raises(UserRegistryError):
        registry[0].register_from_dm({"id": id_value}, "41")


def test_suspension_rejection_cannot_be_reopened_by_register(registry):
    users, db, _owners, _clock = registry
    user = pending(users)
    suspended = users.set_status(9, 41, "SUSPENDED", user.revision).user
    assert suspended.roles == () and suspended.revision == 2
    again = users.register_from_dm(sender(), "41")
    assert not again.changed and again.user.status == RegistrationStatus.SUSPENDED
    rejected = users.set_status(9, 41, "REJECTED", suspended.revision).user
    again = users.register_from_dm(sender(), "41")
    assert not again.changed and again.user.status == RegistrationStatus.REJECTED
    assert [entry[1] for entry in audit(db)] == ["REGISTERED", "STATUS_SET", "STATUS_SET"]


def test_owner_cannot_be_registered_managed_or_withdrawn(registry):
    users, _db, owners, _clock = registry
    assert users.register_from_dm(sender(9), "9").outcome == "OWNER"
    assert users.self_status(9) is None and users.can_chat(9)
    pending(users)
    with pytest.raises(UserAccessDenied):
        users.set_roles(41, 41, ["CHAT_USER"], 1)
    with pytest.raises(UserAccessDenied):
        users.set_roles(9, 9, ["CHAT_USER"], 1)
    with pytest.raises(UserAccessDenied):
        users.withdraw(9, 1)
    owners.add(41)  # A later /pair takes precedence over any previous registration.
    assert users.can_chat(41)
    assert users.self_status(41) is None
    with pytest.raises(UserAccessDenied):
        users.set_status(19, 41, "SUSPENDED", 1)
    assert users.list_users(9).users == ()


def test_approval_both_roles_and_precise_destination(registry):
    users, db, _owners, _clock = registry
    user = pending(users)
    result = users.set_roles(9, 41, [UserRole.NOTIFY_TARGET, UserRole.CHAT_USER], user.revision)
    assert result.changed and result.user.revision == 2
    assert result.user.roles == (UserRole.CHAT_USER, UserRole.NOTIFY_TARGET)
    assert result.user.decided_by == 9 and result.user.decided_at is not None
    assert users.can_chat(41) and users.can_notify(41, "41")
    assert not users.can_notify(41, "private:41:12")
    assert [entry[1] for entry in audit(db)] == ["REGISTERED", "ROLES_SET"]
    assert audit(db)[-1][-2:] == ('[]', '["CHAT_USER", "NOTIFY_TARGET"]')
    assert users.resolve_authority(41, "41", UserAction.CHAT_DM).allowed
    assert users.resolve_authority(41, "group:-100:0", UserAction.CHAT_GROUP, explicit_group_invocation=True).allowed
    assert not users.resolve_authority(41, "group:-100:0", UserAction.CHAT_GROUP).allowed
    assert not users.resolve_authority(41, "private:41:12", UserAction.NOTIFY_REGISTERED_DM).allowed
    assert users.resolve_authority(41, "41", UserAction.NOTIFY_REGISTERED_DM).allowed


def test_decision_is_atomic_and_owner_retry_does_not_duplicate_audit(registry):
    users, db, _owners, _clock = registry
    user = pending(users)
    first = users.set_roles(9, 41, ["CHAT_USER"], user.revision)
    assert first.changed
    repeated = users.set_roles(9, 41, ["CHAT_USER"], first.user.revision)
    assert not repeated.changed
    with pytest.raises(UserRevisionConflict):
        users.set_roles(19, 41, ["NOTIFY_TARGET"], user.revision)
    assert len(audit(db)) == 2 and users.can_chat(41) and not users.can_notify(41, "41")


def test_parallel_owner_reviews_only_one_wins(registry):
    users, db, _owners, _clock = registry
    pending(users)
    barrier = threading.Barrier(2)
    def approve(owner, role):
        barrier.wait()
        try:
            return users.set_roles(owner, 41, [role], 1)
        except UserRevisionConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda pair: approve(*pair),
                                    [(9, "CHAT_USER"), (19, "NOTIFY_TARGET")]))
    assert sum(result == "CONFLICT" for result in results) == 1
    assert len(audit(db)) == 2
    assert len(users.self_status(41).roles) == 1


def test_notification_role_never_grants_chat_and_revocation_clears_roles(registry):
    users, db, _owners, _clock = registry
    user = pending(users)
    user = users.set_roles(9, 41, ["NOTIFY_TARGET"], user.revision).user
    assert not users.can_chat(41) and users.can_notify(41, "41")
    user = users.set_status(19, 41, "SUSPENDED", user.revision).user
    assert user.roles == () and not users.can_notify(41, "41")
    assert db.execute("SELECT COUNT(*) FROM telegram_user_roles WHERE telegram_user_id=41").fetchone()[0] == 0
    with pytest.raises(UserTransitionError):
        users.set_status(9, 41, "ACTIVE", user.revision)
    user = users.set_roles(9, 41, ["CHAT_USER"], user.revision).user
    assert users.can_chat(41) and not users.can_notify(41, "41")


def test_remove_last_role_suspends_without_reactivation(registry):
    users, _db, _owners, _clock = registry
    user = pending(users)
    user = users.set_roles(9, 41, ["CHAT_USER"], user.revision).user
    user = users.set_roles(9, 41, [], user.revision).user
    assert user.status == RegistrationStatus.SUSPENDED and not user.roles and not users.can_chat(41)


def test_withdraw_clears_roles_and_reregister_new_dm_is_pending(registry):
    users, db, _owners, _clock = registry
    user = pending(users)
    user = users.set_roles(9, 41, ["CHAT_USER", "NOTIFY_TARGET"], user.revision).user
    removed = users.withdraw(41, user.revision)
    assert removed.changed and removed.user.status == RegistrationStatus.WITHDRAWN
    assert removed.user.roles == () and not users.can_chat(41)
    assert not users.can_notify(41, "41")
    assert not users.withdraw(41, user.revision).changed  # Duplicate delivery / retry.
    rejoined = users.register_from_dm(sender(), "direct:41:77")
    assert rejoined.changed and rejoined.user.dm_conversation_id == "direct:41:77"
    assert rejoined.user.status == RegistrationStatus.PENDING and rejoined.user.roles == ()
    assert rejoined.user.decided_by is None
    assert [entry[1] for entry in audit(db)] == ["REGISTERED", "ROLES_SET", "WITHDRAWN", "REREGISTERED"]
    with pytest.raises(UserRevisionConflict):
        users.withdraw(41, user.revision)


def test_withdrawn_user_owner_cannot_reapprove_without_reregister(registry):
    users, db, _owners, _clock = registry
    pending(users)
    user = users.withdraw(41, 1).user
    with pytest.raises(UserTransitionError):
        users.set_roles(9, 41, ["NOTIFY_TARGET"], user.revision)
    with pytest.raises(UserTransitionError):
        users.set_status(9, 41, "REJECTED", user.revision)
    assert len(audit(db)) == 2


@pytest.mark.parametrize("roles", ["CHAT_USER", ["CHAT_USER", "CHAT_USER"], ["OWNER"], ["CHAT_USER", "OWNER"], None, [True]])
def test_invalid_roles_have_no_side_effects(registry, roles):
    users, db, _owners, _clock = registry
    pending(users)
    with pytest.raises(UserRegistryError):
        users.set_roles(9, 41, roles, 1)
    assert len(audit(db)) == 1


@pytest.mark.parametrize("desired", ["PENDING", "WITHDRAWN", "random", True])
def test_owner_status_cannot_self_register_or_withdraw(registry, desired):
    users, _db, _owners, _clock = registry
    pending(users)
    with pytest.raises(UserRegistryError):
        users.set_status(9, 41, desired, 1)


def test_flag_off_allows_storage_but_never_authorizes_roles(registry):
    users, db, owners, clock = registry
    pending(users)
    users.set_roles(9, 41, ["CHAT_USER", "NOTIFY_TARGET"], 1)
    off = TelegramUserRegistry(db, threading.RLock(), lambda: owners,
                               user_roles_enabled=False, clock=lambda: clock[0])
    assert not off.can_chat(41) and not off.can_notify(41, "41")
    assert off.can_chat(9)
    assert not off.resolve_authority(41, "41", UserAction.CHAT_DM).allowed


def test_listing_has_filters_search_pagination_and_owner_only(registry):
    users, _db, owners, clock = registry
    for identifier in (41, 42, 43, 44, 45):
        clock[0] += 1
        users.register_from_dm(sender(identifier=identifier, username="name" + str(identifier),
                                      first_name="Test" + str(identifier)), str(identifier))
    users.set_roles(9, 41, ["CHAT_USER"], 1)
    users.set_roles(9, 42, ["NOTIFY_TARGET"], 1)
    with pytest.raises(UserAccessDenied):
        users.list_users(41)
    one = users.list_users(9, limit=2)
    assert len(one.users) == 2 and one.next_cursor
    two = users.list_users(9, limit=2, cursor=one.next_cursor)
    three = users.list_users(19, limit=2, cursor=two.next_cursor)
    assert sorted(user.telegram_user_id for user in (*one.users, *two.users, *three.users)) == [41, 42, 43, 44, 45]
    assert len(users.list_users(9, status="ACTIVE").users) == 2
    assert [user.telegram_user_id for user in users.list_users(9, query="name4").users] == [45, 44, 43, 42, 41]
    with pytest.raises(UserRegistryError):
        users.list_users(9, cursor=one.next_cursor, status="ACTIVE")
    with pytest.raises(UserRegistryError):
        users.list_users(9, limit=51)
    with pytest.raises(UserRegistryError):
        users.list_users(9, cursor="$$notbase64$$")
    owners.add(45)
    assert 45 not in [user.telegram_user_id for user in users.list_users(9).users]


def test_audit_only_exposes_minimal_fields_and_is_owner_only(registry):
    users, db, _owners, _clock = registry
    pending(users)
    users.set_roles(9, 41, ["CHAT_USER"], 1)
    entries = users.audit_for(19, 41)
    assert [entry["event"] for entry in entries] == ["ROLES_SET", "REGISTERED"]
    assert entries[0]["actor_user_id"] == 9
    with pytest.raises(UserAccessDenied):
        users.audit_for(41, 41)
    serialized = json.dumps(entries)
    assert "mary" not in serialized and "Test" not in serialized
    assert "token" not in serialized and "PIN" not in serialized


def test_registry_prunes_audit_older_than_one_year(registry):
    users, db, _owners, clock = registry
    pending(users)
    clock[0] += 366 * 24 * 3600
    users.set_roles(9, 41, ["CHAT_USER"], 1)
    assert len(audit(db)) == 1
    assert audit(db)[0][1] == "ROLES_SET"


def test_missing_user_and_invalid_revision_are_rejected(registry):
    users, _db, _owners, _clock = registry
    with pytest.raises(UserNotFound):
        users.withdraw(99, 1)
    with pytest.raises(UserNotFound):
        users.set_roles(9, 99, ["CHAT_USER"], 1)
    pending(users)
    for revision in (0, None, True, "1"):
        with pytest.raises(UserRegistryError):
            users.set_roles(9, 41, ["CHAT_USER"], revision)


def test_rollback_preserves_roles_revision_and_audit_on_failure(registry, monkeypatch):
    users, db, _owners, _clock = registry
    pending(users)
    original = users._audit
    def fail_audit(*_args):
        raise RuntimeError("injected audit failure")
    monkeypatch.setattr(users, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="audit failure"):
        users.set_roles(9, 41, ["CHAT_USER"], 1)
    monkeypatch.setattr(users, "_audit", original)
    current = users.self_status(41)
    assert current.status == RegistrationStatus.PENDING and current.roles == () and current.revision == 1
    assert len(audit(db)) == 1


def test_concurrent_register_attempts_are_idempotent(registry):
    users, db, _owners, _clock = registry
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: users.register_from_dm(sender(), "41"), range(24)))
    assert sum(value.changed for value in results) == 1
    assert len(audit(db)) == 1


def test_invalid_notification_address_returns_false_instead_of_permission(registry):
    users, _db, _owners, _clock = registry
    pending(users)
    users.set_roles(9, 41, ["NOTIFY_TARGET"], 1)
    assert not users.can_notify(41, "group:-100:0")
    assert not users.can_notify(41, "direct:42:1")


def test_malformed_cursor_is_rejected_and_does_not_raise_decode_error(registry):
    users, _db, _owners, _clock = registry
    pending(users)
    with pytest.raises(UserRegistryError):
        users.list_users(9, cursor="A")


def test_multiline_profile_metadata_is_invalid(registry):
    with pytest.raises(UserRegistryError):
        registry[0].register_from_dm(sender(first_name="sneaky\nname"), "41")

def test_owner_only_status_counts_exclude_later_paired_contacts(registry):
    users, _database, owners, _clock = registry
    first = pending(users, 41)
    pending(users, 42)
    activated = users.set_roles(9, 41, ["CHAT_USER"], first.revision).user
    counts = users.status_counts(9)
    assert counts[RegistrationStatus.PENDING] == 1
    assert counts[RegistrationStatus.ACTIVE] == 1
    assert counts[RegistrationStatus.SUSPENDED] == 0
    with pytest.raises(UserAccessDenied):
        users.status_counts(41)
    owners.add(42)
    counts = users.status_counts(19)
    assert counts[RegistrationStatus.PENDING] == 0
    assert counts[RegistrationStatus.ACTIVE] == 1
    users.set_status(9, 41, "SUSPENDED", activated.revision)
    counts = users.status_counts(9)
    assert counts[RegistrationStatus.ACTIVE] == 0
    assert counts[RegistrationStatus.SUSPENDED] == 1
