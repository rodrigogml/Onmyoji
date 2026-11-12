"""Phase 2: real registration SQLite with a fake Telegram Bot API (no Codex)."""
from __future__ import annotations

import sqlite3
import threading

import pytest

from onmyoji_daemon.telegram_user_policy import RegistrationStatus, UserRole
from onmyoji_daemon.telegram_user_schema import migrate_telegram_users
from onmyoji_daemon.telegram_users import TelegramUserRegistry
from onmyoji_daemon.telegram_user_controls import (
    TelegramUserControls, own_dm_sender, public_command_name,
    PUBLIC_REGISTRATION_COMMANDS, UNREGISTER_MENU_TTL,
)


class FakeApi:
    def __init__(self):
        self.sent = []
        self.answered = []
        self.fail_to = set()
        self.counter = 100

    def send(self, chat_id, text, **kwargs):
        if chat_id in self.fail_to:
            raise RuntimeError('network error with token secret')
        self.counter += 1
        data = {'message_id': self.counter}
        self.sent.append((chat_id, text, kwargs, data))
        return data

    def call(self, name, arguments):
        assert name == 'answerCallbackQuery'
        self.answered.append(arguments['callback_query_id'])
        return True


class FakeContacts:
    def __init__(self, owners): self._owners = owners
    def owners(self): return set(self._owners)


class GatewayHarness(TelegramUserControls):
    def __init__(self, db, owners={9, 19}):
        self.api = FakeApi()
        self.contacts = FakeContacts(owners)
        self.users = TelegramUserRegistry(db, threading.RLock(), self.contacts.owners,
                                          user_roles_enabled=False)
        self.generation = {}
        self.errors = []
        self._init_user_controls()

    @staticmethod
    def _telegram_address(conversation_id):
        if ':' not in conversation_id: return int(conversation_id), {}
        label, user, topic = conversation_id.split(':')
        return int(user), {('direct_messages_topic_id' if label == 'direct' else 'message_thread_id'): int(topic)}

    @staticmethod
    def _conversation_id(chat, message):
        if message.get('direct_messages_topic'):
            return f"direct:{chat['id']}:{message['direct_messages_topic']['topic_id']}"
        if message.get('message_thread_id'):
            return f"private:{chat['id']}:{message['message_thread_id']}"
        return str(chat['id'])

    def _generation(self, conversation_id):
        return self.generation.get(conversation_id, 0)

    def _record_error(self, error):
        self.errors.append(error)


@pytest.fixture
def gw(tmp_path):
    db = sqlite3.connect(tmp_path / 'phase2.sqlite3', check_same_thread=False)
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('CREATE TABLE gateway_schema_migrations(version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)')
    db.execute('INSERT INTO gateway_schema_migrations VALUES(4,1)')
    with db:
        migrate_telegram_users(db)
    gateway = GatewayHarness(db)
    yield gateway, db
    db.close()


def sender(id=41, **kwargs):
    return {'id': id, 'first_name': 'Maria', 'username': 'maria', **kwargs}


def command(gateway, name, user_id=41, conversation_id='41', **meta):
    gateway._registration_command(name, name, sender(user_id, **meta), conversation_id)


def user_messages(gateway, user=41):
    return [msg for msg in gateway.api.sent if msg[0] == user]


def owner_messages(gateway):
    return [msg for msg in gateway.api.sent if msg[0] in {9, 19}]


def menu(gateway):
    item = user_messages(gateway)[-1]
    keyboard = item[2]['reply_markup']['inline_keyboard'][0]
    return item[3]['message_id'], keyboard[0]['callback_data'], keyboard[1]['callback_data']


def callback(user, message_id, data, **extra):
    message = {'chat': {'id': user, 'type': 'private'}, 'message_id': message_id, **extra}
    return {'id': 'ack-1', 'from': {'id': user}, 'message': message, 'data': data}


def test_register_creates_pending_once_and_notifies_all_owners(gw):
    gateway, db = gw
    command(gateway, '/register')
    record = gateway.users.self_status(41)
    assert record.status == RegistrationStatus.PENDING and record.revision == 1 and not record.roles
    assert len(owner_messages(gateway)) == 2
    assert all('ID: 41' in notice[1] and 'prompt' not in notice[1] for notice in owner_messages(gateway))
    command(gateway, '/register')
    assert len(owner_messages(gateway)) == 2
    assert len(db.execute('SELECT * FROM telegram_user_audit').fetchall()) == 1
    assert 'já está aguardando' in user_messages(gateway)[-1][1]
    assert not gateway.users.can_chat(41)
    assert not gateway.users.can_notify(41, '41')


def test_owners_not_registered_and_status_is_self_only(gw):
    gateway, _ = gw
    command(gateway, '/register', user_id=9, conversation_id='9')
    command(gateway, '/status', user_id=9, conversation_id='9')
    command(gateway, '/unregister', user_id=9, conversation_id='9')
    assert all('Owner' in item[1] for item in user_messages(gateway, 9))
    assert gateway.users.self_status(9) is None
    assert not owner_messages(gateway)[:-3]
    command(gateway, '/status')
    assert '/register' in user_messages(gateway)[-1][1]


def test_suspended_rejected_and_active_register_do_not_reopen(gw):
    gateway, _ = gw
    command(gateway, '/register')
    user = gateway.users.self_status(41)
    active = gateway.users.set_roles(9, 41, [UserRole.CHAT_USER], user.revision).user
    command(gateway, '/register')
    assert 'ativo' in user_messages(gateway)[-1][1]
    command(gateway, '/status')
    assert 'Conversar' in user_messages(gateway)[-1][1]
    suspended = gateway.users.set_status(9, 41, 'SUSPENDED', active.revision).user
    command(gateway, '/register')
    assert 'não está habilitado' in user_messages(gateway)[-1][1]
    gateway.users.set_status(9, 41, 'REJECTED', suspended.revision)
    command(gateway, '/register')
    assert 'rejeitado' in user_messages(gateway)[-1][1]
    assert len(owner_messages(gateway)) == 2
    assert gateway.users.self_status(41).status == RegistrationStatus.REJECTED


def test_notification_failure_does_not_roll_back_request(gw):
    gateway, db = gw
    gateway.api.fail_to.add(19)
    command(gateway, '/register')
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert len(owner_messages(gateway)) == 1
    assert gateway.errors == ['telegram_user_owner_notice_failed']
    command(gateway, '/register')
    assert len(owner_messages(gateway)) == 1
    assert len(db.execute('SELECT * FROM telegram_user_audit').fetchall()) == 1


def test_unregister_requires_menu_confirm_and_re_registration_starts_pending(gw):
    gateway, db = gw
    command(gateway, '/register')
    prior = gateway.users.set_roles(9, 41, ['CHAT_USER', 'NOTIFY_TARGET'], 1).user
    assert prior.roles == (UserRole.CHAT_USER, UserRole.NOTIFY_TARGET)
    command(gateway, '/unregister')
    assert gateway.users.self_status(41).status == RegistrationStatus.ACTIVE
    message_id, yes, no = menu(gateway)
    assert len(yes.encode('ascii')) <= 64 and len(no.encode('ascii')) <= 64
    assert gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.api.answered == ['ack-1']
    assert gateway.users.self_status(41).status == RegistrationStatus.WITHDRAWN
    assert not gateway.users.self_status(41).roles
    assert gateway.users.self_status(41).revision == prior.revision + 1
    assert 'permissões revogadas' in user_messages(gateway)[-1][1]
    assert gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.users.self_status(41).revision == prior.revision + 1
    command(gateway, '/register')
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert gateway.users.self_status(41).roles == ()
    assert len(owner_messages(gateway)) == 4
    assert [row[0] for row in db.execute('SELECT event FROM telegram_user_audit ORDER BY id')] == [
        'REGISTERED', 'ROLES_SET', 'WITHDRAWN', 'REREGISTERED']


def test_unregister_cancel_has_no_effect(gw):
    gateway, _ = gw
    command(gateway, '/register')
    command(gateway, '/unregister')
    message_id, yes, no = menu(gateway)
    assert gateway._registration_callback(callback(41, message_id, no))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING


def test_unregister_missing_already_withdrawn_is_idempotent(gw):
    gateway, _ = gw
    command(gateway, '/unregister')
    assert 'não possui cadastro' in user_messages(gateway)[-1][1]
    command(gateway, '/register')
    command(gateway, '/unregister')
    message_id, yes, _ = menu(gateway)
    gateway._registration_callback(callback(41, message_id, yes))
    command(gateway, '/unregister')
    assert 'já foi retirado' in user_messages(gateway)[-1][1]


def test_stale_revision_rejects_withdrawal_without_mutation(gw):
    gateway, _ = gw
    command(gateway, '/register')
    command(gateway, '/unregister')
    message_id, yes, _ = menu(gateway)
    gateway.users.set_roles(9, 41, ['NOTIFY_TARGET'], 1)
    assert gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.ACTIVE
    assert 'mudou' in user_messages(gateway)[-1][1]


def test_callback_rejects_other_user_wrong_message_wrong_chat_and_token(gw):
    gateway, _ = gw
    command(gateway, '/register')
    command(gateway, '/unregister')
    message_id, yes, _ = menu(gateway)
    assert gateway._registration_callback(callback(42, message_id, yes))
    assert gateway._registration_callback(callback(41, message_id + 1, yes))
    assert gateway._registration_callback(callback(41, message_id, 'ur:wrong:confirm'))
    assert gateway._registration_callback(callback(41, message_id, yes, direct_messages_topic={'topic_id': 91}))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    # A wrong topic, although it cannot commit, invalidates the one-time prompt.
    command(gateway, '/unregister')
    message_id, yes, _ = menu(gateway)
    assert gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.WITHDRAWN


def test_callback_expiration_and_new_generation_are_fail_closed(gw):
    gateway, _ = gw
    command(gateway, '/register')
    command(gateway, '/unregister')
    message_id, yes, _ = menu(gateway)
    prompt = gateway._withdrawal_prompts[41]
    gateway._withdrawal_prompts[41] = prompt.__class__(
        prompt.token, prompt.user_id, prompt.conversation_id, prompt.message_id,
        prompt.revision, prompt.generation, 0.0,
    )
    gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    command(gateway, '/unregister')
    message_id, yes, _ = menu(gateway)
    gateway.generation['41'] = 1
    gateway._registration_callback(callback(41, message_id, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING


def test_callback_malformed_or_foreign_are_absorbed(gw):
    gateway, _ = gw
    assert not gateway._registration_callback({'data': 'ch:fake:confirm'})
    assert gateway._registration_callback({'data': 'ur:garbage:confirm', 'id': 'ack', 'from': {'id': 9},
                                           'message': {'chat': {'type': 'private', 'id': 41}, 'message_id': 15}})
    assert gateway._registration_callback({'data': 'ur:garbage', 'id': 'ack', 'from': {'id': True},
                                           'message': {'chat': {'type': 'private', 'id': True}, 'message_id': 15}})
    assert gateway.users.self_status(41) is None


def test_private_topics_are_retained_and_callback_validated(gw):
    gateway, _ = gw
    command(gateway, '/register', conversation_id='direct:41:27')
    assert gateway.users.self_status(41).dm_conversation_id == 'direct:41:27'
    command(gateway, '/unregister', conversation_id='direct:41:27')
    message_id, yes, _ = menu(gateway)
    assert user_messages(gateway)[-1][2]['direct_messages_topic_id'] == 27
    assert gateway._registration_callback(callback(41, message_id, yes,
        direct_messages_topic={'topic_id': 27}))
    assert gateway.users.self_status(41).status == RegistrationStatus.WITHDRAWN


def test_public_commands_only_exact_bot_target_and_own_dm_metadata():
    assert PUBLIC_REGISTRATION_COMMANDS == {'/register', '/status', '/unregister'}
    assert public_command_name('/REGISTER', '') == '/register'
    assert public_command_name('/register@Shiki_Bot', 'shiki_bot') == '/register'
    assert public_command_name('/register@other_bot', 'shiki_bot') is None
    assert public_command_name('/register@other_bot', '') is None
    assert public_command_name('/pair', '') is None
    assert own_dm_sender({'id': 41}, {'id': 41, 'type': 'private'})
    for invalid in ({'id': True}, {'id': '41'}, {'id': -1}, {'id': 42}, {'id': 41, 'is_bot': True}):
        assert not own_dm_sender(invalid, {'id': 41, 'type': 'private'})
    for chat in ({'id': True, 'type': 'private'}, {'id': 42, 'type': 'private'},
                 {'id': 41, 'type': 'group'}, {'type': 'private'}):
        assert not own_dm_sender({'id': 41}, chat)


def test_invalid_registration_profile_does_not_grant_or_notify(gw):
    gateway, _ = gw
    command(gateway, '/register', username='bad user')
    assert gateway.users.self_status(41) is None
    assert not owner_messages(gateway)
    assert 'Não foi possível' in user_messages(gateway)[-1][1]
    assert gateway.errors == ['telegram_user_registry_operation_rejected']


def test_unregistration_menu_receipt_failure_cannot_withdraw(gw):
    gateway, _ = gw
    command(gateway, '/register')
    gateway.api.fail_to.add(41)
    command(gateway, '/unregister')
    assert not gateway._withdrawal_prompts
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert 'telegram_user_controls_send_failed' in gateway.errors


def test_no_automatic_role_grant_even_if_other_role_was_approved(gw):
    gateway, _ = gw
    command(gateway, '/register')
    gateway.users.set_roles(9, 41, ['NOTIFY_TARGET'], 1)
    command(gateway, '/status')
    assert 'notificações' in user_messages(gateway)[-1][1]
    assert 'Conversar' not in user_messages(gateway)[-1][1]
    assert not gateway.users.can_chat(41)  # flag remains OFF
    assert not gateway.users.can_notify(41, '41')
