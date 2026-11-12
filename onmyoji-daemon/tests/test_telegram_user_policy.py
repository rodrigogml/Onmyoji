"""Phase 0: executable, fail-closed contract (no Telegram credentials)."""
import pytest

from onmyoji_daemon.telegram_user_policy import (
    ChatScope, RegistrationStatus, UserAction, UserRole,
    authorize, authorize_command,
)


@pytest.mark.parametrize("action,scope,explicit", [
    (UserAction.CHAT_DM, ChatScope.OWN_DM, False),
    (UserAction.CHAT_GROUP, ChatScope.GROUP, True),
])
def test_chat_turn_only_owner_or_approved_chat_role(action, scope, explicit):
    arguments = {"scope": scope, "explicit_group_invocation": explicit}
    assert authorize(action, is_owner=True, **arguments).allowed
    assert not authorize(action, is_owner=False, **arguments).allowed
    assert not authorize(action, user_roles_enabled=False, status="ACTIVE", roles=["CHAT_USER"], **arguments).allowed
    assert not authorize(action, user_roles_enabled=True, status="PENDING", roles=["CHAT_USER"], **arguments).allowed
    assert not authorize(action, user_roles_enabled=True, status="SUSPENDED", roles=["CHAT_USER"], **arguments).allowed
    assert authorize(action, user_roles_enabled=True, status=RegistrationStatus.ACTIVE,
                     roles=[UserRole.CHAT_USER], **arguments).allowed
    assert not authorize(action, user_roles_enabled=True, status="ACTIVE", roles=["NOTIFY_TARGET"], **arguments).allowed


def test_group_requires_explicit_invocation_for_owner_and_registered_user():
    for is_owner in (False, True):
        assert not authorize(UserAction.CHAT_GROUP, scope=ChatScope.GROUP,
                             is_owner=is_owner, user_roles_enabled=True, status="ACTIVE",
                             roles=["CHAT_USER"], explicit_group_invocation=False).allowed
    assert not authorize(UserAction.CHAT_GROUP, scope=ChatScope.OWN_DM,
                         is_owner=True, explicit_group_invocation=True).allowed


def test_notification_role_is_independent_and_does_not_register_owner_automatically():
    kwargs = {"scope": ChatScope.OWN_DM, "status": "ACTIVE", "user_roles_enabled": True}
    assert authorize(UserAction.NOTIFY_REGISTERED_DM, roles=["NOTIFY_TARGET"], **kwargs).allowed
    assert not authorize(UserAction.NOTIFY_REGISTERED_DM, roles=["CHAT_USER"], **kwargs).allowed
    assert authorize(UserAction.CHAT_DM, roles=["NOTIFY_TARGET", "CHAT_USER"], **kwargs).allowed
    assert not authorize(UserAction.NOTIFY_REGISTERED_DM, scope=ChatScope.GROUP,
                         status="ACTIVE", roles=["NOTIFY_TARGET"], user_roles_enabled=True).allowed
    assert not authorize(UserAction.NOTIFY_REGISTERED_DM, scope=ChatScope.OWN_DM,
                         is_owner=True).allowed


@pytest.mark.parametrize("status", [None, "PENDING", "SUSPENDED", "REJECTED", "WITHDRAWN", "UNEXPECTED"])
def test_status_is_not_authority_even_if_role_is_stored(status):
    assert not authorize(UserAction.CHAT_DM, scope=ChatScope.OWN_DM, user_roles_enabled=True,
                         status=status, roles=["CHAT_USER"]).allowed


def test_bad_role_records_fail_closed_and_username_cannot_be_inferred():
    for roles in (["CHAT_USER", "OWNER"], ["CHAT_USER", "made_up"], None):
        assert not authorize(UserAction.CHAT_DM, scope=ChatScope.OWN_DM,
                             user_roles_enabled=True, status="ACTIVE", roles=roles).allowed
    assert not authorize(UserAction.CHAT_DM, scope="own_dm", is_owner=True).allowed
    assert not authorize("chat_dm", scope=ChatScope.OWN_DM, is_owner=True).allowed
    assert not authorize(UserAction.CHAT_DM, scope=ChatScope.OWN_DM, is_owner=42).allowed


@pytest.mark.parametrize("command", [
    "/config", "/channel", "/usage", "/resetusage", "/login", "/totp",
    "/fila", "/direcionar", "/cancel", "/users",
])
def test_admin_commands_owner_only_and_private(command):
    assert authorize_command(command, scope=ChatScope.OWN_DM, is_owner=True).allowed
    assert not authorize_command(command, scope=ChatScope.OWN_DM, is_owner=False).allowed
    if command not in {"/channel", "/config"}:
        assert not authorize_command(command, scope=ChatScope.GROUP, is_owner=True).allowed


@pytest.mark.parametrize("command", ["/new", "/resolver"])
def test_legacy_private_controls_stay_available_but_not_group_controls(command):
    assert authorize_command(command, scope=ChatScope.OWN_DM).allowed
    assert not authorize_command(command, scope=ChatScope.GROUP).allowed
    assert authorize_command(command, scope=ChatScope.GROUP, is_owner=True).allowed


@pytest.mark.parametrize("command", ["/register", "/status", "/unregister"])
def test_self_service_contract_is_own_dm_only(command):
    assert authorize_command(command, scope=ChatScope.OWN_DM).allowed
    assert not authorize_command(command, scope=ChatScope.GROUP, is_owner=True).allowed
    assert not authorize_command(command, scope=ChatScope.INVALID).allowed


def test_existing_pair_uses_only_its_separate_one_time_pin_protocol():
    assert not authorize_command("/pair", scope=ChatScope.OWN_DM, is_owner=True).allowed
    assert not authorize_command("/pair", scope=ChatScope.OWN_DM).allowed
    assert not authorize_command("/help", scope=ChatScope.OWN_DM, is_owner=True).allowed


def test_owner_group_commands_stay_separate_from_user_chat():
    for command in ("/new", "/resolver", "/config", "/channel"):
        assert authorize_command(command, scope=ChatScope.GROUP, is_owner=True).allowed
        assert not authorize_command(command, scope=ChatScope.GROUP).allowed


def test_flag_off_preserves_owner_and_legacy_but_blocks_new_roles():
    assert authorize(UserAction.CHAT_DM, scope=ChatScope.OWN_DM, is_owner=True).allowed
    assert authorize(UserAction.LEGACY_DM_CONTROL, scope=ChatScope.OWN_DM).allowed
    assert not authorize(UserAction.CHAT_DM, scope=ChatScope.OWN_DM,
                         user_roles_enabled=False, status="ACTIVE", roles=["CHAT_USER"]).allowed
    assert not authorize(UserAction.NOTIFY_REGISTERED_DM, scope=ChatScope.OWN_DM,
                         user_roles_enabled=False, status="ACTIVE", roles=["NOTIFY_TARGET"]).allowed
