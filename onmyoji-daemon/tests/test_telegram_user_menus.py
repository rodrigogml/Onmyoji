"""Phase 3: Owner-only Telegram menus, callbacks and SQLite state without real I/O."""
from __future__ import annotations

from dataclasses import replace
import time

import pytest

from onmyoji_daemon.telegram_user_policy import RegistrationStatus, UserRole
from test_telegram_user_registration import gateway, update, registered_messages


def press(gw, action, owner_id=9, *, sender_id=None, message_id=None, topic=None, token=None):
    session = gw._users_menu_sessions.get(owner_id)
    assert session is not None or token is not None
    data = f"usr:{token or session.token}:{action}"
    body = {
        "id": f"test-{owner_id}-{action}",
        "data": data,
        "from": {"id": owner_id if sender_id is None else sender_id},
        "message": {
            "chat": {"id": owner_id, "type": "private"},
            "message_id": session.message_id if message_id is None else message_id,
        },
    }
    if topic is not None:
        body["message"]["message_thread_id"] = topic
    gw._update({"callback_query": body})
    return gw._users_menu_sessions.get(owner_id)


def open_menu(gw, owner=9, command="/users"):
    gw._update(update(user_id=owner, text=command))
    return gw._users_menu_sessions[owner]


def pending(gw, user_id=41):
    gw._update(update(user_id=user_id))
    assert gw.users.self_status(user_id).status == RegistrationStatus.PENDING


def test_owner_can_approve_chat_and_notification_roles_separately(gateway):
    pending(gateway)
    menu = open_menu(gateway)
    assert menu.view == "home"
    assert "Pendentes: 1" in registered_messages(gateway, 9)[-1][1]
    assert registered_messages(gateway, 9)[-1][2]["reply_markup"]["inline_keyboard"]
    press(gateway, "p")
    assert gateway._users_menu_sessions[9].item_ids == (41,)
    press(gateway, "d0")
    press(gateway, "c")
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    press(gateway, "o")
    press(gateway, "v")
    result = gateway.users.self_status(41)
    assert result.status == RegistrationStatus.ACTIVE
    assert result.roles == (UserRole.CHAT_USER, UserRole.NOTIFY_TARGET)
    assert result.decided_by == 9
    assert any("aprovado/atualizado" in value[1] for value in registered_messages(gateway, 41))
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_user_audit WHERE telegram_user_id=41 AND event='ROLES_SET'").fetchone()[0] == 1
    press(gateway, "c")
    press(gateway, "v")
    assert gateway.users.self_status(41).roles == (UserRole.NOTIFY_TARGET,)
    assert gateway._channels_list() == []  # Phase 5 has not registered destinations.


def test_save_requires_a_role_and_suspend_confirmation_is_one_shot(gateway):
    pending(gateway)
    open_menu(gateway)
    press(gateway, "p"); press(gateway, "d0")
    press(gateway, "v")
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    press(gateway, "c"); press(gateway, "v")
    press(gateway, "s")
    assert gateway.users.self_status(41).status == RegistrationStatus.ACTIVE
    press(gateway, "z")
    assert gateway.users.self_status(41).status == RegistrationStatus.ACTIVE
    press(gateway, "s")
    prompt = gateway._users_menu_sessions[9]
    press(gateway, "k")
    assert gateway.users.self_status(41).status == RegistrationStatus.SUSPENDED
    assert gateway.users.self_status(41).roles == ()
    previous_audit = gateway.database.execute("SELECT COUNT(*) FROM telegram_user_audit").fetchone()[0]
    press(gateway, "k", token=prompt.token)
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_user_audit").fetchone()[0] == previous_audit
    press(gateway, "o"); press(gateway, "v")
    assert gateway.users.self_status(41).status == RegistrationStatus.ACTIVE
    assert gateway.users.self_status(41).roles == (UserRole.NOTIFY_TARGET,)


def test_reject_requires_confirmation_and_outbound_failure_does_not_rollback(gateway):
    pending(gateway)
    open_menu(gateway)
    press(gateway, "p"); press(gateway, "d0"); press(gateway, "r")
    gateway.api.fail_to.add(41)
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    press(gateway, "k")
    assert gateway.users.self_status(41).status == RegistrationStatus.REJECTED
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_user_audit WHERE event='STATUS_SET'").fetchone()[0] == 1


def test_second_owner_stale_menu_cannot_overwrite_approved_roles(gateway):
    pending(gateway)
    open_menu(gateway, owner=9)
    open_menu(gateway, owner=19)
    for owner in (9, 19):
        press(gateway, "p", owner); press(gateway, "d0", owner); press(gateway, "c", owner)
    press(gateway, "v", 9)
    press(gateway, "o", 19)
    press(gateway, "v", 19)
    assert gateway.users.self_status(41).roles == (UserRole.CHAT_USER,)
    assert gateway._users_menu_sessions[19].view == "stale"
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_user_audit WHERE event='ROLES_SET'").fetchone()[0] == 1
    press(gateway, "f", 19)
    assert gateway._users_menu_sessions[19].view == "detail"
    assert gateway._users_menu_sessions[19].target_revision == 2


def test_nonowner_and_forged_old_callback_do_not_mutate(gateway):
    pending(gateway)
    initial = open_menu(gateway)
    press(gateway, "p", sender_id=41)
    press(gateway, "p", message_id=initial.message_id + 1)
    press(gateway, "p", topic=912)
    assert gateway._users_menu_sessions[9] == initial
    press(gateway, "p")
    assert gateway._users_menu_sessions[9].view == "list"
    press(gateway, "p", token=initial.token)
    assert gateway._users_menu_sessions[9].view == "list"
    gateway._update(update(user_id=41, text="/users"))
    assert 41 not in gateway._users_menu_sessions
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert gateway.database.execute("SELECT COUNT(*) FROM telegram_user_audit WHERE event='ROLES_SET'").fetchone()[0] == 0


def test_expiry_generation_and_out_of_scope_commands_do_not_authorize(gateway):
    pending(gateway)
    initial = open_menu(gateway)
    gateway._users_menu_sessions[9] = replace(initial, expires_at=time.monotonic() - 1)
    press(gateway, "p")
    assert 9 not in gateway._users_menu_sessions
    initial = open_menu(gateway)
    gateway._new_conversation("9")
    press(gateway, "p")
    assert gateway._users_menu_sessions[9].view == "home"
    gateway._update(update(user_id=9, text="/users@outro_bot"))
    assert gateway._users_menu_sessions[9].view == "home"
    gateway._update({"edited_message": update(user_id=9, text="/users")["message"]})
    assert gateway._users_menu_sessions[9].view == "home"
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING


def test_filter_pagination_search_and_owner_exclusion(gateway):
    for user_id in range(41, 53):
        pending(gateway, user_id)
    open_menu(gateway)
    press(gateway, "p")
    assert len(gateway._users_menu_sessions[9].item_ids) == 8
    press(gateway, "n")
    assert len(gateway._users_menu_sessions[9].item_ids) == 4
    press(gateway, "b")
    assert len(gateway._users_menu_sessions[9].item_ids) == 8
    open_menu(gateway, command="/users 41")
    assert gateway._users_menu_sessions[9].item_ids == (41,)
    open_menu(gateway, command="/users @maria")
    assert len(gateway._users_menu_sessions[9].item_ids) == 8
    gateway.contacts.add_owner({"id": 41, "first_name": "Nova Owner"})
    open_menu(gateway, command="/users 41")
    assert gateway._users_menu_sessions[9].item_ids == ()


def test_owner_menu_does_not_create_codex_or_notification_channel(gateway):
    pending(gateway)
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    open_menu(gateway); press(gateway, "p"); press(gateway, "d0"); press(gateway, "o"); press(gateway, "v")
    assert gateway.users.self_status(41).roles == (UserRole.NOTIFY_TARGET,)
    gateway._update(update(user_id=41, text="olá"))
    assert calls == []
    assert not gateway._channels_list()
    with pytest.raises(ValueError, match="não cadastrado"):
        gateway._outbound_send({"channel": "41", "text": "notificação"})
