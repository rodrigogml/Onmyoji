"""Phase 0 regression: current Telegram routing must not change yet."""
from __future__ import annotations

import time

import pytest

from onmyoji_daemon.telegram import Gateway, Settings


class FakeApi:
    def __init__(self):
        self.sent = []
        self.deleted = []
        self.next_id = 100

    def send(self, chat_id, text, **kwargs):
        self.next_id += 1
        self.sent.append((chat_id, text, kwargs))
        return {"message_id": self.next_id}

    def delete(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


def gateway_for(tmp_path, *, feature=False):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text(
        '[codex]\nproject_directory = "' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    daemon = tmp_path / "configs" / "daemon" / "daemon.toml"
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_text('[app_server]\nenabled = false\nidle_timeout_seconds = 1800\n'
                      '\n[instructions]\nenabled = true\nshikigami_file = "instructions.md"\n', encoding="utf-8")
    (data / "config.toml").write_text(
        'schema_version = 1\n[telegram]\nvault_profile = "telegram"\n'
        'token_entry = "token"\nuser_roles_enabled = ' + ('true' if feature else 'false') + '\n',
        encoding="utf-8")
    gateway = Gateway(Settings.load(tmp_path, data))
    gateway.api = FakeApi()
    gateway.contacts.add_owner({"id": 9, "first_name": "Owner"})
    gateway.bot_username = "shiki_bot"
    gateway.bot_id = 900
    return gateway


def message(sender_id, chat_id, text, *, chat_type="private", message_id=1, **extra):
    return {"message": {"message_id": message_id, "from": {"id": sender_id, "first_name": "Test"},
                        "chat": {"id": chat_id, "type": chat_type}, "text": text, **extra}}


@pytest.mark.parametrize("feature", [False, True])
def test_phase0_does_not_enable_nonowner_turns_even_if_setting_is_true(tmp_path, feature):
    gateway = gateway_for(tmp_path, feature=feature)
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    gateway._update(message(41, 41, "hello"))
    gateway._update(message(41, -100, "hello @shiki_bot", chat_type="supergroup", message_id=2,
                            entities=[{"type": "mention", "offset": 6, "length": 10}]))
    assert gateway.settings.user_roles_enabled is feature
    assert not calls and not gateway.api.sent
    assert gateway.database.execute("SELECT COUNT(*) FROM conversation_messages WHERE chat_id='group:-100:0'").fetchone()[0] == 1


def test_owner_dm_and_explicit_group_invocations_still_route(tmp_path):
    gateway = gateway_for(tmp_path)
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    gateway._update(message(9, 9, "hello", message_id=1))
    gateway._update(message(9, -100, "hello everyone", chat_type="group", message_id=2))
    gateway._update(message(9, -100, "hi @shiki_bot", chat_type="group", message_id=3,
                            entities=[{"type": "mention", "offset": 3, "length": 10}]))
    gateway._update(message(9, -100, "reply", chat_type="group", message_id=4,
                            reply_to_message={"message_id": 20, "from": {"id": 900}}))
    assert len(calls) == 3  # private, mention, reply; plain group message never triggers
    assert calls[0][0] == "9"
    assert calls[1][0] == calls[2][0] == "group:-100:0"


def test_topic_id_is_part_of_group_conversation_isolation(tmp_path):
    gateway = gateway_for(tmp_path)
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    gateway._update(message(9, -100, "hi @shiki_bot", chat_type="supergroup", message_id=10,
                            message_thread_id=30, entities=[{"type": "mention", "offset": 3, "length": 10}]))
    gateway._update(message(9, -100, "hi @shiki_bot", chat_type="supergroup", message_id=11,
                            message_thread_id=31, entities=[{"type": "mention", "offset": 3, "length": 10}]))
    assert [call[0] for call in calls] == ["group:-100:30", "group:-100:31"]


def test_legacy_nonowner_commands_are_private_and_admin_commands_are_silent(tmp_path):
    gateway = gateway_for(tmp_path)
    calls = []
    gateway._enqueue_turn = lambda *args, **kwargs: calls.append(args)
    generation = gateway._generation("41")
    for index, forbidden in enumerate(("/channel", "/config", "/login", "/usage", "/resetusage", "/users"), 1):
        gateway._update(message(41, 41, forbidden, message_id=index))
    assert not gateway.api.sent
    assert gateway._generation("41") == generation
    gateway._update(message(41, 41, "/new", message_id=20))
    assert gateway._generation("41") == generation + 1
    assert gateway.api.sent[-1][0] == 41
    gateway._update(message(41, -100, "/new", chat_type="group", message_id=21))
    assert gateway._generation("group:-100:0") == 0
    gateway._update(message(41, 41, "/resolver", message_id=22))
    assert not calls


def test_pair_remains_a_separate_one_time_owner_procedure(tmp_path):
    gateway = gateway_for(tmp_path)
    gateway.pair = ("secret-pin", time.time() + 120)
    gateway._configure_owner_commands = lambda: {}
    gateway._update(message(41, 41, "/pair secret-pin", message_id=80))
    assert gateway.contacts.owners() == {9, 41}
    assert gateway.pair is None
    gateway._update(message(42, 42, "/pair secret-pin", message_id=81))
    assert gateway.contacts.owners() == {9, 41}


def test_manual_channel_is_not_created_by_an_unregistered_user(tmp_path):
    gateway = gateway_for(tmp_path)
    gateway._update(message(41, 41, "/channel", message_id=90))
    assert gateway._channels_list() == []
    gateway._channel_register("9", "Owner", 9)
    assert gateway._channels_list()[0]["id"] == "9"
    with pytest.raises(ValueError, match="não cadastrado"):
        gateway._outbound_send({"channel": "41", "text": "notification"})
