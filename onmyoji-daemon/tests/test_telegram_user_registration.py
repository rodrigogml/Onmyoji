"""Phase 2 Gateway ingress and callback acceptance without network or Codex."""
from __future__ import annotations

import time

import pytest

from onmyoji_daemon.telegram import Gateway, Settings
from onmyoji_daemon.telegram_user_policy import RegistrationStatus


class FakeApi:
    def __init__(self):
        self.sent = []
        self.deleted = []
        self.acknowledged = []
        self.fail_to = set()
        self.next_message_id = 100

    def send(self, chat_id, text, **kwargs):
        if chat_id in self.fail_to:
            raise OSError('fake Telegram delivery failure')
        self.next_message_id += 1
        sent = {'message_id': self.next_message_id}
        self.sent.append((chat_id, text, kwargs, sent))
        return sent

    def delete(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))

    def call(self, method, params=None):
        if method == 'answerCallbackQuery':
            self.acknowledged.append(params['callback_query_id'])
            return True
        raise AssertionError(f'Unexpected Bot API operation: {method}')


@pytest.fixture
def gateway(tmp_path):
    data = tmp_path / 'configs' / 'daemon' / 'services' / 'telegram'
    data.mkdir(parents=True)
    (tmp_path / 'configs' / 'onmyoji-system.toml').write_text(
        '[codex]\nproject_directory = "' + tmp_path.as_posix() + '"\n', encoding='utf-8')
    config = tmp_path / 'configs' / 'daemon' / 'daemon.toml'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('[app_server]\nenabled = false\nidle_timeout_seconds = 1800\n\n'
                      '[instructions]\nenabled = true\nshikigami_file = "instructions.md"\n', encoding='utf-8')
    (data / 'config.toml').write_text('schema_version = 1\n[telegram]\nvault_profile="telegram"\n'
                                        'token_entry="token"\nuser_roles_enabled=true\n', encoding='utf-8')
    gw = Gateway(Settings.load(tmp_path, data))
    gw.api = FakeApi()
    gw.bot_username = 'shikigami_test'
    gw.bot_id = 900
    gw.contacts.add_owner({'id': 9, 'first_name': 'Proprietário'})
    gw.contacts.add_owner({'id': 19, 'first_name': 'Outra Owner'})
    try:
        yield gw
    finally:
        gw.database.close()


def update(user_id=41, text='/register', *, chat_id=None, kind='private', **extra):
    return {'message': {'message_id': 10, 'chat': {'id': user_id if chat_id is None else chat_id, 'type': kind},
                        'from': {'id': user_id, 'first_name': 'Maria', 'username': 'maria'},
                        'text': text, **extra}}


def registered_messages(gw, recipient):
    return [item for item in gw.api.sent if item[0] == recipient]


def callback(gw, data, *, user_id=41, msg_id=None, conversation_id=None):
    message_id = msg_id
    if message_id is None:
        menus = [item for item in gw.api.sent if item[0] == user_id and 'reply_markup' in item[2]]
        message_id = menus[-1][3]['message_id']
    message = {'chat': {'id': user_id, 'type': 'private'}, 'message_id': message_id}
    if conversation_id is not None:
        if conversation_id.startswith('direct:'):
            message['direct_messages_topic'] = {'topic_id': int(conversation_id.split(':')[-1])}
        elif conversation_id.startswith('private:'):
            message['message_thread_id'] = int(conversation_id.split(':')[-1])
    return {'callback_query': {'id': 'callback-1', 'data': data,
                               'from': {'id': user_id}, 'message': message}}


def test_register_status_and_duplicate_owner_notification(gateway):
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    gateway._update(update())
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert len(registered_messages(gateway, 9)) == 1
    assert len(registered_messages(gateway, 19)) == 1
    gateway._update(update())
    assert len(registered_messages(gateway, 9)) == 1
    gateway._update(update(text='/status'))
    assert 'aguardando' in registered_messages(gateway, 41)[-1][1]
    assert not calls
    assert gateway._channels_list() == []
    assert gateway.database.execute('SELECT COUNT(*) FROM inbox_items').fetchone()[0] == 0


def test_unregister_confirm_cancel_and_reregister(gateway):
    gateway._update(update())
    gateway._update(update(text='/unregister'))
    confirmation = registered_messages(gateway, 41)[-1]
    buttons = confirmation[2]['reply_markup']['inline_keyboard'][0]
    yes, no = (button['callback_data'] for button in buttons)
    gateway._update(callback(gateway, no))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    gateway._update(update(text='/unregister'))
    yes = registered_messages(gateway, 41)[-1][2]['reply_markup']['inline_keyboard'][0][0]['callback_data']
    gateway._update(callback(gateway, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.WITHDRAWN
    assert gateway.users.self_status(41).roles == ()
    gateway._update(callback(gateway, yes))
    assert gateway.users.self_status(41).status == RegistrationStatus.WITHDRAWN
    gateway._update(update(text='/register'))
    assert gateway.users.self_status(41).status == RegistrationStatus.PENDING
    assert len(registered_messages(gateway, 9)) == 2


def test_invalid_sender_group_wrong_bot_and_edited_update_do_not_register(gateway):
    for item in [
        update(user_id=True),
        update(user_id=41, chat_id=42),
        update(user_id=41, text='/register', kind='group', chat_id=-100),
        update(user_id=41, text='/register@someone_else'),
        update(user_id=41, text='', caption='/register', photo=[{'file_id': '123'}]),
    ]:
        gateway._update(item)
    edited = update()
    gateway._update({'edited_message': edited['message']})
    assert gateway.users.self_status(41) is None
    assert not registered_messages(gateway, 9)


def test_private_topic_is_recorded_without_inferred_chat(gateway):
    gateway._update(update(message_thread_id=120))
    assert gateway.users.self_status(41).dm_conversation_id == 'private:41:120'
    gateway._update(update(text='/unregister', message_thread_id=120))
    yes = registered_messages(gateway, 41)[-1][2]['reply_markup']['inline_keyboard'][0][0]['callback_data']
    gateway._update(callback(gateway, yes, conversation_id='private:41:120'))
    assert gateway.users.self_status(41).status == RegistrationStatus.WITHDRAWN


def test_owner_controls_not_affected_and_registered_user_still_cannot_chat(gateway):
    gateway._update(update(user_id=9, text='/register'))
    assert gateway.users.self_status(9) is None
    gateway._update(update(user_id=9, text='/users'))
    assert 'Administração de usuários' in registered_messages(gateway, 9)[-1][1]
    gateway._update(update())
    gateway.users.set_roles(9, 41, ['CHAT_USER'], 1)
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    gateway._update(update(text='Olá, Shikigami'))
    gateway._update(update(text='oi @shikigami_test', chat_id=-100, kind='supergroup',
                           entities=[{'type': 'mention', 'offset': 3, 'length': 16}]))
    assert calls == []  # Ingress authorization only changes in Phase 4.
    gateway._update(update(text='/login'))
    gateway._update(update(text='/users'))
    gateway._update(update(text='/channel'))
    assert not registered_messages(gateway, 41)[-1][1].startswith('Login')
    assert gateway._channels_list() == []
