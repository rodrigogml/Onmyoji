"""Transactional Telegram registration and authorization state (phase 1).

This module deliberately never handles Telegram updates or sends messages.
Callers provide verified Bot API sender metadata and the canonical conversation
ID computed by Gateway._conversation_id, not values from message text.
The owner source of truth remains Contacts.owners() and the pairing PIN flow.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import json
import math
import re
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable

from .telegram_user_policy import (
    AccessDecision,
    ChatScope,
    RegistrationStatus,
    UserAction,
    UserRole,
    authorize,
)


MAX_TELEGRAM_ID = 2**63 - 1
MAX_DISPLAY_NAME = 160
MAX_USERNAME = 32
MAX_LANGUAGE = 32
MAX_QUERY = 100
MAX_PAGE_SIZE = 50
AUDIT_RETENTION_SECONDS = 365 * 24 * 3600
_USERNAME = re.compile(r"[A-Za-z0-9_]{1,32}\Z")
_LANGUAGE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8}){0,3}\Z")
_GROUP = re.compile(r"group:(-?[0-9]+):([0-9]+)\Z")
_TOPIC = re.compile(r"(private|direct):([0-9]+):([0-9]+)\Z")


class UserRegistryError(ValueError):
    """Invalid or unauthorized registry operation."""


class UserAccessDenied(UserRegistryError):
    """Caller is not the owner or target is now an owner."""


class UserNotFound(UserRegistryError):
    """No such registered user."""


class UserRevisionConflict(UserRegistryError):
    """The menu saw an earlier revision of this user."""


class UserTransitionError(UserRegistryError):
    """A status transition would violate the registration state machine."""


@dataclass(frozen=True)
class TelegramUser:
    telegram_user_id: int
    username: str | None
    display_name: str
    language_code: str | None
    dm_conversation_id: str
    status: RegistrationStatus
    roles: tuple[UserRole, ...]
    revision: int
    requested_at: float
    decided_at: float | None
    decided_by: int | None
    updated_at: float


@dataclass(frozen=True)
class RegistrationResult:
    user: TelegramUser | None
    outcome: str
    changed: bool


@dataclass(frozen=True)
class UserMutation:
    user: TelegramUser
    changed: bool


@dataclass(frozen=True)
class UserPage:
    users: tuple[TelegramUser, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class UserAuthority:
    telegram_user_id: int
    is_owner: bool
    status: RegistrationStatus | None
    roles: tuple[UserRole, ...]
    revision: int | None
    decision: AccessDecision

    @property
    def allowed(self) -> bool:
        return self.decision.allowed


def _user_id(value: object) -> int:
    if type(value) is not int or not 1 <= value <= MAX_TELEGRAM_ID:
        raise UserRegistryError("Invalid Telegram user identifier.")
    return value


def _revision(value: object) -> int:
    if type(value) is not int or value < 1:
        raise UserRegistryError("An expected positive revision is required.")
    return value


def _name(value: object, maximum: int, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise UserRegistryError("Invalid Telegram profile metadata.")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise UserRegistryError("Telegram profile metadata contains control characters.")
    stripped = " ".join(value.split())
    if len(stripped) > maximum:
        raise UserRegistryError("Telegram profile metadata exceeds the allowed format.")
    if not stripped and optional:
        return None
    return stripped


def _identity(sender: dict[str, Any]) -> tuple[int, str | None, str, str | None]:
    if not isinstance(sender, dict):
        raise UserRegistryError("Telegram sender metadata is required.")
    identifier = _user_id(sender.get("id"))
    username = _name(sender.get("username"), MAX_USERNAME, optional=True)
    if username is not None and not _USERNAME.fullmatch(username):
        raise UserRegistryError("Invalid Telegram username.")
    first = _name(sender.get("first_name", ""), MAX_DISPLAY_NAME, optional=False) or ""
    last = _name(sender.get("last_name", ""), MAX_DISPLAY_NAME, optional=False) or ""
    display = (first + " " + last).strip()
    if len(display) > MAX_DISPLAY_NAME:
        raise UserRegistryError("Telegram display name exceeds the allowed format.")
    language = _name(sender.get("language_code"), MAX_LANGUAGE, optional=True)
    if language is not None:
        if not _LANGUAGE.fullmatch(language):
            raise UserRegistryError("Invalid Telegram language tag.")
        language = language.replace("_", "-")
    return identifier, username, display, language


def _dm_id(conversation_id: object, identifier: int) -> str:
    if not isinstance(conversation_id, str) or len(conversation_id) > 128:
        raise UserRegistryError("A canonical private Telegram conversation is required.")
    if conversation_id == str(identifier):
        return conversation_id
    matched = _TOPIC.fullmatch(conversation_id)
    if matched and matched.group(2) == str(identifier):
        topic = matched.group(3)
        if int(topic) <= MAX_TELEGRAM_ID and str(int(topic)) == topic:
            return conversation_id
    raise UserRegistryError("The Telegram DM does not belong to this sender.")


def _scope(conversation_id: object, identifier: int) -> ChatScope:
    try:
        _dm_id(conversation_id, identifier)
    except UserRegistryError:
        if isinstance(conversation_id, str):
            match = _GROUP.fullmatch(conversation_id) if len(conversation_id) <= 128 else None
            if match and str(int(match.group(1))) == match.group(1) and str(int(match.group(2))) == match.group(2):
                return ChatScope.GROUP
        return ChatScope.INVALID
    return ChatScope.OWN_DM


def _role_set(roles: Iterable[UserRole | str]) -> tuple[UserRole, ...]:
    if isinstance(roles, (str, bytes)):
        raise UserRegistryError("Roles must be an iterable, not a string.")
    try:
        raw = tuple(roles)
    except TypeError as error:
        raise UserRegistryError("Invalid role collection.") from error
    if len(raw) > len(UserRole):
        raise UserRegistryError("Duplicate or unsupported roles.")
    try:
        values = tuple(UserRole(value) for value in raw)
    except (TypeError, ValueError) as error:
        raise UserRegistryError("Unknown role.") from error
    if len(set(values)) != len(values):
        raise UserRegistryError("Duplicate roles.")
    return tuple(sorted(values, key=lambda item: item.value))


def _status(value: RegistrationStatus | str) -> RegistrationStatus:
    try:
        return RegistrationStatus(value)
    except (TypeError, ValueError) as error:
        raise UserRegistryError("Invalid registration status.") from error


class TelegramUserRegistry:
    """Single-writer registry sharing the Gateway SQLite connection and RLock.

    The Gateway writer lease prevents multiple operational processes, and each
    mutation performs status, roles, revision and audit updates in one SQLite
    transaction. Every authorization rereads SQLite: no stale permission cache.
    """

    def __init__(
        self,
        database: sqlite3.Connection,
        lock: threading.RLock,
        owners: Callable[[], set[int]],
        *,
        user_roles_enabled: bool = False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if type(user_roles_enabled) is not bool or not callable(owners):
            raise UserRegistryError("Invalid registry configuration.")
        if database.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise UserRegistryError("SQLite foreign keys must be enabled before registry startup.")
        self.db = database
        self.lock = lock
        self.owners = owners
        self.user_roles_enabled = user_roles_enabled
        self.clock = clock

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value) or value < 0:
            raise UserRegistryError("Invalid registry timestamp.")
        return value

    def _is_owner(self, identifier: int) -> bool:
        return identifier in self.owners()

    def _assert_not_owner(self, identifier: int) -> None:
        if self._is_owner(identifier):
            raise UserAccessDenied("Owners cannot be managed by the user registry.")

    def _require_owner(self, owner_id: int) -> None:
        _user_id(owner_id)
        if not self._is_owner(owner_id):
            raise UserAccessDenied("Only paired owners can manage users.")

    def _fetch(self, user_id: int) -> TelegramUser | None:
        row = self.db.execute(
            "SELECT telegram_user_id, username, display_name, language_code, dm_conversation_id, "
            "status, revision, requested_at, decided_at, decided_by, updated_at "
            "FROM telegram_users WHERE telegram_user_id=?", (user_id,)
        ).fetchone()
        if row is None:
            return None
        roles = tuple(UserRole(value[0]) for value in self.db.execute(
            "SELECT role FROM telegram_user_roles WHERE telegram_user_id=? ORDER BY role", (user_id,)
        ).fetchall())
        return TelegramUser(
            telegram_user_id=row[0], username=row[1], display_name=row[2], language_code=row[3],
            dm_conversation_id=row[4], status=RegistrationStatus(row[5]), revision=row[6],
            requested_at=row[7], decided_at=row[8], decided_by=row[9], updated_at=row[10],
            roles=roles,
        )

    def _need(self, user_id: int, expected_revision: int) -> TelegramUser:
        user = self._fetch(user_id)
        if user is None:
            raise UserNotFound("Telegram user not registered.")
        if user.revision != expected_revision:
            raise UserRevisionConflict("User record has changed; reload the menu.")
        return user

    def _audit(
        self, user_id: int, actor: int, event: str, previous_status: RegistrationStatus | None,
        new_status: RegistrationStatus, previous_roles: Iterable[UserRole], new_roles: Iterable[UserRole], now: float,
    ) -> None:
        self.db.execute(
            "INSERT INTO telegram_user_audit(telegram_user_id, actor_user_id, event, previous_status, "
            "new_status, previous_roles, new_roles, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, actor, event, previous_status.value if previous_status else None, new_status.value,
             json.dumps(sorted(value.value for value in previous_roles)),
             json.dumps(sorted(value.value for value in new_roles)), now),
        )
        self.db.execute("DELETE FROM telegram_user_audit WHERE created_at<?", (now - AUDIT_RETENTION_SECONDS,))

    def register_from_dm(self, sender: dict[str, Any], conversation_id: str) -> RegistrationResult:
        identifier, username, display_name, language = _identity(sender)
        address = _dm_id(conversation_id, identifier)
        with self.lock, self.db:
            if self._is_owner(identifier):
                return RegistrationResult(None, "OWNER", False)
            previous = self._fetch(identifier)
            if previous is None:
                now = self._now()
                self.db.execute(
                    "INSERT INTO telegram_users(telegram_user_id, username, display_name, language_code, "
                    "dm_conversation_id, status, revision, requested_at, updated_at) "
                    "VALUES (?,?,?,?,?,'PENDING',1,?,?)",
                    (identifier, username, display_name, language, address, now, now),
                )
                self._audit(identifier, identifier, "REGISTERED", None, RegistrationStatus.PENDING, (), (), now)
                user = self._fetch(identifier)
                assert user is not None
                return RegistrationResult(user, "PENDING", True)
            if previous.status == RegistrationStatus.WITHDRAWN:
                now = self._now()
                self.db.execute(
                    "UPDATE telegram_users SET username=?, display_name=?, language_code=?, "
                    "dm_conversation_id=?, status='PENDING', revision=revision+1, "
                    "requested_at=?, decided_at=NULL, decided_by=NULL, updated_at=? "
                    "WHERE telegram_user_id=? AND revision=?",
                    (username, display_name, language, address, now, now, identifier, previous.revision),
                )
                self.db.execute("DELETE FROM telegram_user_roles WHERE telegram_user_id=?", (identifier,))
                self._audit(identifier, identifier, "REREGISTERED", RegistrationStatus.WITHDRAWN,
                            RegistrationStatus.PENDING, previous.roles, (), now)
                user = self._fetch(identifier)
                assert user is not None
                return RegistrationResult(user, "PENDING", True)
            # Refresh harmless profile data without changing authorization or
            # the reviewed DM address. No extra audit or approval notification.
            if (username, display_name, language) != (previous.username, previous.display_name, previous.language_code):
                self.db.execute(
                    "UPDATE telegram_users SET username=?, display_name=?, language_code=?, updated_at=? "
                    "WHERE telegram_user_id=?",
                    (username, display_name, language, self._now(), identifier),
                )
            user = self._fetch(identifier)
            assert user is not None
            return RegistrationResult(user, user.status.value, False)

    def self_status(self, telegram_user_id: int) -> TelegramUser | None:
        identifier = _user_id(telegram_user_id)
        with self.lock:
            if self._is_owner(identifier):
                return None
            return self._fetch(identifier)

    def withdraw(self, telegram_user_id: int, expected_revision: int) -> UserMutation:
        identifier = _user_id(telegram_user_id)
        expected = _revision(expected_revision)
        with self.lock, self.db:
            self._assert_not_owner(identifier)
            user = self._fetch(identifier)
            if user is None:
                raise UserNotFound("Telegram user not registered.")
            if user.status == RegistrationStatus.WITHDRAWN:
                return UserMutation(user, False)
            if user.revision != expected:
                raise UserRevisionConflict("User record has changed; reload status.")
            now = self._now()
            changed = self.db.execute(
                "UPDATE telegram_users SET status='WITHDRAWN', revision=revision+1, "
                "updated_at=? WHERE telegram_user_id=? AND revision=?",
                (now, identifier, expected),
            ).rowcount
            if not changed:
                raise UserRevisionConflict("User record has changed.")
            self.db.execute("DELETE FROM telegram_user_roles WHERE telegram_user_id=?", (identifier,))
            self._audit(identifier, identifier, "WITHDRAWN", user.status,
                        RegistrationStatus.WITHDRAWN, user.roles, (), now)
            updated = self._fetch(identifier)
            assert updated is not None
            return UserMutation(updated, True)

    def set_roles(
        self, owner_id: int, user_id: int, roles: Iterable[UserRole | str], expected_revision: int,
    ) -> UserMutation:
        target = _user_id(user_id)
        expected = _revision(expected_revision)
        wanted = _role_set(roles)
        with self.lock, self.db:
            self._require_owner(owner_id)
            self._assert_not_owner(target)
            previous = self._need(target, expected)
            if previous.status == RegistrationStatus.WITHDRAWN:
                raise UserTransitionError("A withdrawn user must register again before approval.")
            status = RegistrationStatus.ACTIVE if wanted else RegistrationStatus.SUSPENDED
            if previous.status == status and previous.roles == wanted:
                return UserMutation(previous, False)
            now = self._now()
            changed = self.db.execute(
                "UPDATE telegram_users SET status=?, revision=revision+1, decided_at=?, "
                "decided_by=?, updated_at=? WHERE telegram_user_id=? AND revision=?",
                (status.value, now, owner_id, now, target, expected),
            ).rowcount
            if not changed:
                raise UserRevisionConflict("User record has changed.")
            self.db.execute("DELETE FROM telegram_user_roles WHERE telegram_user_id=?", (target,))
            for role in wanted:
                self.db.execute(
                    "INSERT INTO telegram_user_roles(telegram_user_id, role, granted_at, granted_by) "
                    "VALUES (?,?,?,?)", (target, role.value, now, owner_id),
                )
            self._audit(target, owner_id, "ROLES_SET", previous.status, status,
                        previous.roles, wanted, now)
            updated = self._fetch(target)
            assert updated is not None
            return UserMutation(updated, True)

    def set_status(
        self, owner_id: int, user_id: int, status: RegistrationStatus | str, expected_revision: int,
    ) -> UserMutation:
        target = _user_id(user_id)
        expected = _revision(expected_revision)
        desired = _status(status)
        if desired in {RegistrationStatus.PENDING, RegistrationStatus.WITHDRAWN}:
            raise UserTransitionError("Only self registration and withdrawal can set this status.")
        with self.lock, self.db:
            self._require_owner(owner_id)
            self._assert_not_owner(target)
            previous = self._need(target, expected)
            if previous.status == RegistrationStatus.WITHDRAWN:
                raise UserTransitionError("A withdrawn user must register again before review.")
            if desired == RegistrationStatus.ACTIVE and not previous.roles:
                raise UserTransitionError("Use set_roles to activate with at least one role.")
            if previous.status == desired:
                return UserMutation(previous, False)
            now = self._now()
            changed = self.db.execute(
                "UPDATE telegram_users SET status=?, revision=revision+1, decided_at=?, "
                "decided_by=?, updated_at=? WHERE telegram_user_id=? AND revision=?",
                (desired.value, now, owner_id, now, target, expected),
            ).rowcount
            if not changed:
                raise UserRevisionConflict("User record has changed.")
            roles = previous.roles if desired == RegistrationStatus.ACTIVE else ()
            if desired != RegistrationStatus.ACTIVE:
                self.db.execute("DELETE FROM telegram_user_roles WHERE telegram_user_id=?", (target,))
            self._audit(target, owner_id, "STATUS_SET", previous.status, desired,
                        previous.roles, roles, now)
            updated = self._fetch(target)
            assert updated is not None
            return UserMutation(updated, True)

    def can_chat(self, telegram_user_id: int) -> bool:
        identifier = _user_id(telegram_user_id)
        with self.lock:
            if self._is_owner(identifier):
                return True
            user = self._fetch(identifier)
            return bool(self.user_roles_enabled and user is not None and
                        user.status == RegistrationStatus.ACTIVE and UserRole.CHAT_USER in user.roles)

    def can_notify(self, telegram_user_id: int, conversation_id: str) -> bool:
        identifier = _user_id(telegram_user_id)
        try:
            address = _dm_id(conversation_id, identifier)
        except UserRegistryError:
            return False
        with self.lock:
            if self._is_owner(identifier):
                return False  # Owners use the existing manual channel registry.
            user = self._fetch(identifier)
            return bool(self.user_roles_enabled and user is not None and
                        user.status == RegistrationStatus.ACTIVE and UserRole.NOTIFY_TARGET in user.roles and
                        user.dm_conversation_id == address)

    def resolve_authority(
        self, sender_id: int, conversation_id: str, action: UserAction,
        *, explicit_group_invocation: bool = False,
    ) -> UserAuthority:
        identifier = _user_id(sender_id)
        scope = _scope(conversation_id, identifier)
        with self.lock:
            owner = self._is_owner(identifier)
            user = None if owner else self._fetch(identifier)
            if action == UserAction.NOTIFY_REGISTERED_DM and (not user or user.dm_conversation_id != conversation_id):
                return UserAuthority(identifier, owner, user.status if user else None,
                                     user.roles if user else (), user.revision if user else None,
                                     AccessDecision(False, "registered_dm_required"))
            decision = authorize(
                action, scope=scope, is_owner=owner, user_roles_enabled=self.user_roles_enabled,
                status=user.status if user else None, roles=user.roles if user else (),
                explicit_group_invocation=explicit_group_invocation,
            )
            return UserAuthority(identifier, owner, user.status if user else None,
                                 user.roles if user else (), user.revision if user else None, decision)

    def list_users(
        self, owner_id: int, *, status: RegistrationStatus | str | None = None,
        cursor: str | None = None, query: str = "", limit: int = 20,
    ) -> UserPage:
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE:
            raise UserRegistryError("Invalid page size.")
        if not isinstance(query, str) or len(query) > MAX_QUERY:
            raise UserRegistryError("Invalid search text.")
        query = query.strip()
        desired = _status(status) if status is not None else None
        position = None
        if cursor is not None:
            if not isinstance(cursor, str) or len(cursor) > 512:
                raise UserRegistryError("Invalid cursor.")
            try:
                decoded = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
                parts = json.loads(decoded)
                if (not isinstance(parts, list) or len(parts) != 4 or
                        parts[2] != (desired.value if desired else "") or parts[3] != query or
                        type(parts[0]) not in (int, float) or not math.isfinite(parts[0])):
                    raise ValueError("Invalid cursor payload.")
                position = (float(parts[0]), _user_id(parts[1]))
            except (ValueError, TypeError, UnicodeDecodeError, binascii.Error) as error:
                raise UserRegistryError("Invalid cursor.") from error
        with self.lock:
            self._require_owner(owner_id)
            excluded = sorted(self.owners())
            where = []
            arguments: list[Any] = []
            if excluded:
                where.append("telegram_user_id NOT IN (" + ",".join("?" for _ in excluded) + ")")
                arguments.extend(excluded)
            if desired:
                where.append("status=?")
                arguments.append(desired.value)
            if query:
                # instr() treats %, _ and backslash as literal characters.
                where.append("(instr(lower(display_name),lower(?))>0 OR "
                             "instr(lower(coalesce(username,'')),lower(?))>0 OR "
                             "instr(CAST(telegram_user_id AS TEXT),?)>0)")
                arguments.extend((query, query, query))
            if position:
                where.append("(requested_at<? OR (requested_at=? AND telegram_user_id<?))")
                arguments.extend((position[0], position[0], position[1]))
            condition = " WHERE " + " AND ".join(where) if where else ""
            records = self.db.execute(
                "SELECT telegram_user_id, requested_at FROM telegram_users" + condition +
                " ORDER BY requested_at DESC, telegram_user_id DESC LIMIT ?",
                (*arguments, limit + 1),
            ).fetchall()
            page = records[:limit]
            result = tuple(self._fetch(int(row[0])) for row in page)
            if any(entry is None for entry in result):
                raise UserRegistryError("Registry changed during pagination.")
            more = len(records) > limit
            next_cursor = None
            if more and page:
                latest = page[-1]
                payload = [latest[1], latest[0], desired.value if desired else "", query]
                next_cursor = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
            return UserPage(result, next_cursor)  # type: ignore[arg-type]

    def audit_for(self, owner_id: int, user_id: int, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        target = _user_id(user_id)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise UserRegistryError("Invalid audit page size.")
        with self.lock:
            self._require_owner(owner_id)
            self._assert_not_owner(target)
            rows = self.db.execute(
                "SELECT actor_user_id,event,previous_status,new_status,previous_roles,new_roles,created_at "
                "FROM telegram_user_audit WHERE telegram_user_id=? ORDER BY id DESC LIMIT ?", (target, limit),
            ).fetchall()
        return tuple({"actor_user_id": row[0], "event": row[1], "previous_status": row[2],
                      "new_status": row[3], "previous_roles": tuple(json.loads(row[4])),
                      "new_roles": tuple(json.loads(row[5])), "created_at": row[6]} for row in rows)
