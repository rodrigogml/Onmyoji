"""Pure permission contract for the planned Telegram user roles.

Phase 0 deliberately does not integrate this policy with message ingress,
callbacks, queues, tool execution, or outbound transport. The feature flag is
OFF by default. Existing owner and legacy private controls remain unchanged.

Callers must establish Telegram sender identity and conversation scope from
trusted Bot API metadata; a username, user-provided text, or callback payload
must never be treated as proof of authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class UserRole(str, Enum):
    CHAT_USER = "CHAT_USER"
    NOTIFY_TARGET = "NOTIFY_TARGET"


class RegistrationStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"


class ChatScope(str, Enum):
    OWN_DM = "own_dm"
    GROUP = "group"
    INVALID = "invalid"


class UserAction(str, Enum):
    CHAT_DM = "chat_dm"
    CHAT_GROUP = "chat_group"
    NOTIFY_REGISTERED_DM = "notify_registered_dm"
    SELF_SERVICE = "self_service"
    LEGACY_DM_CONTROL = "legacy_dm_control"
    OWNER_DM_CONTROL = "owner_dm_control"
    OWNER_GROUP_CONTROL = "owner_group_control"


# This list documents the current Gateway command surfaces as of Phase 0.
# /pair is intentionally absent: the separate one-time PIN protocol controls it.
SELF_SERVICE_COMMANDS = frozenset({"/register", "/status", "/unregister"})
LEGACY_DM_COMMANDS = frozenset({"/new", "/resolver"})
OWNER_DM_COMMANDS = frozenset({
    "/new", "/resolver", "/config", "/channel", "/usage",
    "/resetusage", "/login", "/fila", "/direcionar", "/cancel",
    "/totp", "/users",
})
OWNER_GROUP_COMMANDS = frozenset({"/new", "/resolver", "/config", "/channel"})


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str


_ALLOW = AccessDecision(True, "allowed")


def _deny(reason: str) -> AccessDecision:
    return AccessDecision(False, reason)


def _effective_roles(
    status: RegistrationStatus | str | None,
    roles: Iterable[UserRole | str],
    user_roles_enabled: bool,
) -> frozenset[str]:
    """Reject invalid role sets rather than partially granting bad records."""
    if user_roles_enabled is not True or status != RegistrationStatus.ACTIVE:
        return frozenset()
    try:
        names = frozenset(role.value if isinstance(role, UserRole) else role for role in roles)
    except (TypeError, ValueError):
        return frozenset()
    if not names.issubset({role.value for role in UserRole}):
        return frozenset()
    return names


def authorize(
    action: UserAction,
    *,
    scope: ChatScope,
    is_owner: bool = False,
    user_roles_enabled: bool = False,
    status: RegistrationStatus | str | None = None,
    roles: Iterable[UserRole | str] = (),
    explicit_group_invocation: bool = False,
) -> AccessDecision:
    """Return a conservative action decision without reading mutable state.

    Owner identity must already be verified through Contacts.owners().
    `scope=OWN_DM` requires chat.type=private AND chat.id=from.id.
    `scope=GROUP` requires a Telegram group/supergroup update. A group turn
    additionally requires a validated mention/reply to this bot.
    """
    if not isinstance(action, UserAction) or not isinstance(scope, ChatScope):
        return _deny("invalid_action_or_scope")
    owner = is_owner is True
    if action == UserAction.SELF_SERVICE:
        return _ALLOW if scope == ChatScope.OWN_DM else _deny("own_dm_required")
    if action == UserAction.LEGACY_DM_CONTROL:
        return _ALLOW if scope == ChatScope.OWN_DM else _deny("own_dm_required")
    if action == UserAction.OWNER_DM_CONTROL:
        return _ALLOW if owner and scope == ChatScope.OWN_DM else _deny("owner_dm_required")
    if action == UserAction.OWNER_GROUP_CONTROL:
        return _ALLOW if owner and scope == ChatScope.GROUP else _deny("owner_group_required")
    if action == UserAction.CHAT_GROUP and (scope != ChatScope.GROUP or explicit_group_invocation is not True):
        return _deny("explicit_group_invocation_required")
    if action == UserAction.CHAT_DM and scope != ChatScope.OWN_DM:
        return _deny("own_dm_required")
    if action == UserAction.NOTIFY_REGISTERED_DM and scope != ChatScope.OWN_DM:
        return _deny("registered_dm_required")
    if action in {UserAction.CHAT_DM, UserAction.CHAT_GROUP} and owner:
        return _ALLOW
    effective = _effective_roles(status, roles, user_roles_enabled)
    needed = {
        UserAction.CHAT_DM: UserRole.CHAT_USER.value,
        UserAction.CHAT_GROUP: UserRole.CHAT_USER.value,
        UserAction.NOTIFY_REGISTERED_DM: UserRole.NOTIFY_TARGET.value,
    }.get(action)
    if needed and needed in effective and not (owner and action == UserAction.NOTIFY_REGISTERED_DM):
        return _ALLOW
    return _deny("role_not_authorized")


def authorize_command(command: str, *, scope: ChatScope, is_owner: bool = False) -> AccessDecision:
    """Describe command policy; routing stays unchanged until later phases.

    New public commands have contracts here but are not installed as handlers
    in Phase 0. /pair always uses its existing PIN validation, not this matrix.
    """
    if not isinstance(command, str) or not isinstance(scope, ChatScope):
        return _deny("invalid_command_or_scope")
    if command == "/pair":
        return _deny("pair_requires_pin")
    if command in SELF_SERVICE_COMMANDS:
        return authorize(UserAction.SELF_SERVICE, scope=scope)
    if command in LEGACY_DM_COMMANDS and scope == ChatScope.OWN_DM:
        return authorize(UserAction.LEGACY_DM_CONTROL, scope=scope)
    if is_owner is True and scope == ChatScope.OWN_DM and command in OWNER_DM_COMMANDS:
        return _ALLOW
    if is_owner is True and scope == ChatScope.GROUP and command in OWNER_GROUP_COMMANDS:
        return _ALLOW
    return _deny("command_not_authorized")
