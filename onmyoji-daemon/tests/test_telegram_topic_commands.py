"""Contratos dos controles locais de conversa e do título do tópico Telegram."""
from __future__ import annotations

from copy import deepcopy
import threading
import time

import pytest

from onmyoji_daemon.telegram import AppServerTurn, Gateway, Settings


class TopicApi:
    def __init__(self):
        self.calls = []
        self.sent = []
        self.result = True

    def call(self, method, params=None, **_kwargs):
        self.calls.append((method, params))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def send(self, chat_id, text, **values):
        self.sent.append((chat_id, text, values))
        return {"message_id": len(self.sent) + 100}

    def delete(self, _chat_id, _message_id):
        pass


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (tmp_path / "configs" / "onmyoji-system.toml").write_text(
        '[codex]\nproject_directory = "' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    (data / "config.toml").write_text(
        'schema_version = 1\n[telegram]\nvault_profile="telegram"\ntoken_entry="token"\n',
        encoding="utf-8")
    (tmp_path / "configs" / "daemon" / "daemon.toml").write_text(
        '[app_server]\nenabled = true\n\n[instructions]\nenabled = true\n', encoding="utf-8")
    instance = Gateway(Settings.load(tmp_path, data))
    instance.contacts.add_owner({"id": 9, "first_name": "Proprietário"})
    instance.bot_username = "testbot"
    instance.api = TopicApi()
    monkeypatch.setattr(instance, "_schedule_delete", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(instance, "_enqueue_turn", lambda *_args, **_kwargs: pytest.fail("Controle local enfileirou um turno Codex."))
    monkeypatch.setattr(instance, "_app_client", lambda: pytest.fail("Controle local iniciou o App Server."))
    yield instance
    instance.database.close()


def private_message(text, *, topic=42, sender=9):
    message = {"message_id": 1, "chat": {"id": sender, "type": "private"},
               "from": {"id": sender, "first_name": "Pessoa"}, "text": text}
    if topic is not None:
        message["message_thread_id"] = topic
    return message


def group_message(text, *, topic=42, sender=9, forum=True):
    message = {"message_id": 1, "chat": {"id": -1001, "type": "supergroup", "is_forum": forum},
               "from": {"id": sender, "first_name": "Pessoa"}, "text": text}
    if topic is not None:
        message["message_thread_id"] = topic
    return message


@pytest.mark.parametrize("message, conversation, address", [
    (private_message("/new", topic=None), "9", (9, {})),
    (private_message("/new"), "private:9:42", (9, {"message_thread_id": 42})),
    (group_message("/new", topic=None), "group:-1001:0", (-1001, {})),
    (group_message("/new"), "group:-1001:42", (-1001, {"message_thread_id": 42})),
])
@pytest.mark.parametrize("binding", [None, "", "known-codex-thread"])
def test_new_distinguishes_an_existing_codex_binding_and_only_resets_the_current_conversation(
        gateway, message, conversation, address, binding):
    """O vínculo anterior define o aviso; reset e resposta ficam no mesmo contexto."""
    gateway._ensure_conversation(conversation)
    gateway.database.execute(
        "UPDATE conversations SET generation=3, codex_thread_id=? WHERE chat_id=?", (binding, conversation))
    gateway._ensure_conversation("private:9:99")
    gateway.database.execute("UPDATE conversations SET codex_thread_id='sibling' WHERE chat_id='private:9:99'")
    gateway.database.commit()

    gateway._update({"message": deepcopy(message)})

    expected = "Conversa reiniciada." if binding else "Nova conversa criada."
    assert gateway.api.sent[-1] == (address[0], expected, address[1])
    assert gateway.database.execute(
        "SELECT generation, codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone() == (4, None)
    assert gateway.database.execute(
        "SELECT generation, codex_thread_id FROM conversations WHERE chat_id='private:9:99'").fetchone() == (0, "sibling")
    assert gateway.api.calls == []


def test_new_without_a_binding_allows_the_following_message_to_start_the_conversation(gateway, monkeypatch):
    gateway._update({"message": private_message("/new")})
    queued = []
    monkeypatch.setattr(gateway, "_enqueue_turn", lambda *args: queued.append(args))

    gateway._update({"message": private_message("Vamos conversar")})

    assert queued[0][:3] == ("private:9:42", "Vamos conversar", [])
    assert gateway.database.execute(
        "SELECT codex_thread_id FROM conversations WHERE chat_id='private:9:42'").fetchone() == (None,)


@pytest.mark.parametrize("binding", [None, "known-codex-thread"])
@pytest.mark.parametrize("message, conversation, chat_id", [
    (private_message("/topic amendoim"), "private:9:42", 9),
    (group_message("/topic amendoim"), "group:-1001:42", -1001),
])
def test_topic_renames_an_existing_telegram_topic_without_changing_codex_state(
        gateway, message, conversation, chat_id, binding):
    """O tópico pode ter sido criado pelo usuário e ainda não ter thread Codex."""
    gateway._ensure_conversation(conversation)
    gateway.database.execute(
        "UPDATE conversations SET generation=3, codex_thread_id=? WHERE chat_id=?", (binding, conversation))
    gateway.database.commit()
    gateway._remember_channel_label(conversation, "Conversa", "Nome anterior")

    gateway._update({"message": deepcopy(message)})

    assert gateway.api.calls == [("editForumTopic", {"chat_id": chat_id, "message_thread_id": 42, "name": "amendoim"})]
    assert gateway.database.execute(
        "SELECT generation, codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone() == (3, binding)
    assert gateway._channel_label(conversation)[1] == "amendoim"
    assert gateway.api.sent[-1][0] == chat_id
    assert gateway.api.sent[-1][2].get("message_thread_id") == 42


@pytest.mark.parametrize("title", ["a", "á" * 128, "Amendoim  e castanha", "🥜 Amendoim", "<b>Amendoim</b>"])
def test_topic_accepts_valid_titles_and_preserves_internal_spacing(gateway, title):
    gateway._update({"message": private_message("/topic@TESTBOT   " + title + "  ")})

    assert gateway.api.calls == [("editForumTopic", {"chat_id": 9, "message_thread_id": 42, "name": title})]
    assert "parse_mode" not in gateway.api.sent[-1][2]


def test_private_topic_one_uses_the_regular_topic_api(gateway):
    gateway._update({"message": private_message("/topic Primeira conversa", topic=1)})

    assert gateway.api.calls == [("editForumTopic", {"chat_id": 9, "message_thread_id": 1, "name": "Primeira conversa"})]


def test_topic_before_the_first_codex_message_keeps_thread_creation_lazy(gateway, monkeypatch):
    conversation = "private:9:42"
    assert gateway.database.execute(
        "SELECT codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone() is None

    gateway._update({"message": private_message("/topic amendoim")})

    assert gateway.api.calls == [("editForumTopic", {"chat_id": 9, "message_thread_id": 42, "name": "amendoim"})]
    assert gateway.database.execute(
        "SELECT generation, codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone() == (0, None)
    queued = []
    monkeypatch.setattr(gateway, "_enqueue_turn", lambda *args: queued.append(args))

    gateway._update({"message": private_message("Agora podemos conversar")})

    assert queued[0][:3] == (conversation, "Agora podemos conversar", [])


@pytest.mark.parametrize("command", ["/topic", "/topic    ", "/topic " + "a" * 129])
def test_topic_rejects_missing_or_oversized_names_without_calling_telegram(gateway, command):
    gateway._update({"message": private_message(command)})

    assert gateway.api.calls == []
    assert gateway.api.sent
    assert "/topic <nome>" in gateway.api.sent[-1][1]
    assert "128" in gateway.api.sent[-1][1]
    assert gateway._channel_label("private:9:42")[1] == ""


@pytest.mark.parametrize("message", [
    private_message("/topic amendoim", topic=None),
    group_message("/topic amendoim", topic=None, forum=False),
])
def test_topic_without_a_telegram_topic_reports_the_limitation_without_creating_one(gateway, message):
    gateway._update({"message": message})

    assert gateway.api.calls == []
    assert gateway.api.sent
    assert "tópico" in gateway.api.sent[-1][1].casefold()


@pytest.mark.parametrize("topic", [None, 0, 1])
def test_topic_renames_a_group_general_topic_with_its_specific_api_method(gateway, topic):
    gateway._update({"message": group_message("/topic Geral da equipe", topic=topic)})

    assert gateway.api.calls == [("editGeneralForumTopic", {"chat_id": -1001, "name": "Geral da equipe"})]
    expected_address = {"message_thread_id": topic} if topic else {}
    assert gateway.api.sent[-1] == (-1001, "Tópico renomeado para Geral da equipe.", expected_address)


def test_topic_success_preserves_a_manually_assigned_channel_catalog_alias(gateway):
    conversation = "private:9:42"
    gateway._ensure_conversation(conversation)
    gateway._remember_channel_label(conversation, "Conversa", "Nome anterior")
    gateway._channel_register(conversation, "Conversa / Nome anterior", 9)
    gateway._channel_set_alias(conversation, "Meu nome local")

    gateway._update({"message": private_message("/topic amendoim")})

    assert gateway._channel_label(conversation)[1:] == ("amendoim", "Meu nome local")
    assert gateway._channels_list()[0]["name"] == "Meu nome local"


def test_topic_rejects_a_channel_direct_message_topic_without_calling_a_forum_api(gateway):
    message = private_message("/topic amendoim", topic=None)
    message["direct_messages_topic"] = {"topic_id": 123}

    gateway._update({"message": message})

    assert gateway.api.calls == []
    assert "não tem um tópico renomeável" in gateway.api.sent[-1][1]
    assert gateway.api.sent[-1][2] == {"direct_messages_topic_id": 123}


def test_topic_is_a_priority_control_and_preserves_an_active_codex_turn(gateway):
    conversation = "private:9:42"
    gateway._ensure_conversation(conversation)
    active = AppServerTurn(9, "known-thread", threading.Event(), conversation_id=conversation)
    gateway.app_turns["known-thread"] = active
    update = {"message": private_message("/topic amendoim")}

    assert gateway._ingress_control(update) is True
    gateway._update(update)

    assert gateway.api.calls == [("editForumTopic", {"chat_id": 9, "message_thread_id": 42, "name": "amendoim"})]
    assert gateway.app_turns["known-thread"] is active
    assert not active.completed.is_set()
    assert not active.suppressed


@pytest.mark.parametrize("message", [
    private_message("/topic amendoim", sender=7),
    group_message("/topic amendoim", sender=7),
])
def test_topic_from_another_user_cannot_rename_a_topic(gateway, message):
    gateway._update({"message": message})

    assert gateway.api.calls == []
    assert gateway.api.sent == []


def test_topic_addressed_to_another_bot_is_ignored(gateway):
    gateway._update({"message": private_message("/topic@anotherbot amendoim")})

    assert gateway.api.calls == []
    assert gateway.api.sent == []


def test_topic_rejects_a_private_chat_that_does_not_belong_to_the_sender(gateway):
    message = private_message("/topic amendoim")
    message["chat"]["id"] = 99

    gateway._update({"message": message})

    assert gateway.api.calls == []
    assert gateway.api.sent == []


def test_topic_is_not_consumed_as_a_channel_catalog_alias(gateway):
    conversation = "private:9:42"
    gateway._ensure_conversation(conversation)
    gateway.channel_identify_sessions[conversation] = {
        "owner_id": 9, "generation": 0, "expires_at": time.time() + 60,
    }

    gateway._update({"message": private_message("/topic amendoim")})

    assert gateway.api.calls == [("editForumTopic", {"chat_id": 9, "message_thread_id": 42, "name": "amendoim"})]
    assert gateway._channel_label(conversation)[1:] == ("amendoim", "")


@pytest.mark.parametrize("result", [False, None, {}, RuntimeError("Sem permissão para editar o tópico")])
def test_topic_failure_keeps_the_previous_name_and_codex_binding(gateway, result):
    """Falha remota não deve ser anunciada ou persistida como renomeação concluída."""
    conversation = "private:9:42"
    gateway._ensure_conversation(conversation)
    gateway.database.execute(
        "UPDATE conversations SET codex_thread_id='known-codex-thread' WHERE chat_id=?", (conversation,))
    gateway.database.commit()
    gateway._remember_channel_label(conversation, "Conversa", "Nome anterior")
    gateway.api.result = result

    gateway._update({"message": private_message("/topic amendoim")})

    assert len(gateway.api.calls) == 1
    assert gateway._channel_label(conversation)[1] == "Nome anterior"
    assert gateway.database.execute(
        "SELECT generation, codex_thread_id FROM conversations WHERE chat_id=?", (conversation,)).fetchone() == (0, "known-codex-thread")
    assert gateway.api.sent
    assert "não" in gateway.api.sent[-1][1].casefold()


def test_topic_reports_remote_success_when_only_the_local_label_update_fails(gateway, monkeypatch):
    remember_label = gateway._remember_channel_label

    def fail_only_the_renamed_label(conversation, *args, **kwargs):
        if kwargs.get("topic_name") == "amendoim":
            raise OSError("Catálogo local indisponível")
        return remember_label(conversation, *args, **kwargs)

    monkeypatch.setattr(gateway, "_remember_channel_label", fail_only_the_renamed_label)

    gateway._update({"message": private_message("/topic amendoim")})

    assert gateway.api.calls == [("editForumTopic", {"chat_id": 9, "message_thread_id": 42, "name": "amendoim"})]
    assert gateway.api.sent[-1][1] == "Tópico renomeado, mas o nome local do canal não pôde ser atualizado."
