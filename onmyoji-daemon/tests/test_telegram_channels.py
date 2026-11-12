"""Testes em desenvolvimento: catálogo e saída sem Bot API real."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from onmyoji_daemon.telegram import Gateway, Settings


def gateway_for(tmp_path: Path) -> Gateway:
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True, exist_ok=True)
    root_config = tmp_path / "configs" / "onmyoji-system.toml"
    root_config.write_text('[codex]\nproject_directory = "' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    daemon = tmp_path / "configs" / "daemon" / "daemon.toml"
    daemon.parent.mkdir(parents=True, exist_ok=True)
    daemon.write_text('[app_server]\nenabled = false\nidle_timeout_seconds = 1800\n\n[instructions]\nenabled = true\nshikigami_file = "instructions.md"\n', encoding="utf-8")
    cfg = tmp_path / "configs" / "daemon" / "services" / "telegram" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('schema_version = 1\n[telegram]\nvault_profile="telegram"\ntoken_entry="token"\n', encoding="utf-8")
    gw = Gateway(Settings.load(tmp_path, data))
    gw.contacts.add_owner({"id": 9, "first_name": "Proprietário"})
    return gw


class FakeApi:
    def __init__(self):
        self.sent = []
        self.calls = []
        self.files = []
        self.next_id = 100

    def send(self, chat_id, text, **kwargs):
        self.next_id += 1
        result = {"message_id": self.next_id}
        self.sent.append((chat_id, text, kwargs, result))
        return result

    def delete(self, *args):
        pass

    def call(self, method, params=None):
        self.calls.append((method, params))
        if method == "createForumTopic":
            return {"message_thread_id": 17}
        return True

    def send_file(self, method, chat_id, path, field, caption="", **kwargs):
        self.next_id += 1
        self.files.append((method, chat_id, path, field, caption, kwargs))
        return {"message_id": self.next_id}


def test_only_channel_command_registers_a_chat_and_catalog_persists(tmp_path):
    gw = gateway_for(tmp_path)
    gw.api = FakeApi()
    owner = {"id": 9, "first_name": "Proprietário"}
    chat = {"id": 9, "type": "private"}
    assert gw._channels_list() == []
    gw._channel_menu("9", chat, owner)
    menu = gw.api.sent[-1]
    token = menu[2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert token.startswith("ch:")
    assert gw._channels_list() == []
    callback = {"id": "1", "data": token, "from": owner, "message": {"message_id": menu[3]["message_id"], "chat": chat}}
    assert gw._channel_callback(callback)
    assert gw._channels_list()[0]["id"] == "9"
    restarted = gateway_for(tmp_path)
    assert restarted._channels_list()[0]["id"] == "9"
    assert restarted._channels_remove("9")["removed"]
    assert restarted._channels_list() == []


def test_channel_list_uses_visual_topic_name_and_keeps_technical_id(tmp_path):
    gw = gateway_for(tmp_path)
    gw._remember_channel_label("group:-1000:24", "Equipe Financeira", "Fechamento mensal")
    gw._channel_register("group:-1000:24", "Equipe Financeira / tópico 24", 9)
    entry = gw._channels_list()[0]
    assert entry["id"] == "group:-1000:24"
    assert entry["name"] == "Equipe Financeira / Fechamento mensal"


def test_topic_event_label_is_available_before_registration(tmp_path):
    gw = gateway_for(tmp_path)
    gw._remember_channel_label("group:-1000:24", "Equipe Financeira", "Fechamento mensal")
    assert gw._channel_display({"id": -1000, "type": "supergroup", "title": "Equipe Financeira"}, {"id": 9}, "group:-1000:24") == "Equipe Financeira / Fechamento mensal"


def test_registered_channel_menu_allows_visual_identification_and_closes(tmp_path):
    gw = gateway_for(tmp_path)
    gw.api = FakeApi()
    gw._channel_register("group:-1000:24", "Equipe / tópico 24", 9)
    gw._channel_menu("group:-1000:24", {"id": -1000, "type": "supergroup", "title": "Equipe"}, {"id": 9})
    menu = gw.api.sent[-1]
    identify = menu[2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    close = menu[2]["reply_markup"]["inline_keyboard"][1][0]
    assert menu[1].startswith("Canal: Equipe")
    assert close["text"] == "Fechar"
    assert gw._channel_callback({"id": "identify", "data": identify, "from": {"id": 9}, "message": {"message_id": menu[3]["message_id"], "chat": {"id": -1000, "type": "supergroup"}}})
    assert "Nome atual do canal:" in gw.api.sent[-1][1]


def test_group_outsider_is_denied_without_approval_menu(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    gw._channel_menu("group:-1000:24", {"id": -1000, "type": "supergroup", "title": "Equipe"}, {"id": 42})
    assert "apenas ao proprietário" in api.sent[-1][1]
    assert gw._channels_list() == []
    assert not gw.database.execute("SELECT 1 FROM telegram_channel_requests").fetchone()


def test_third_party_private_channel_is_rejected_even_with_legacy_callbacks(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    maria = {"id": 42, "first_name": "Maria"}
    chat = {"id": 42, "type": "private"}

    gw._channel_menu("42", chat, maria)
    assert "apenas ao proprietário" in api.sent[-1][1]
    assert not gw.database.execute("SELECT 1 FROM telegram_channel_menus").fetchone()
    assert not gw.database.execute("SELECT 1 FROM telegram_channel_requests").fetchone()

    # Backwards-compatible callbacks may still be present in old Telegram messages.
    assert gw._channel_callback({"id": "old-request", "data": "ch:obsolete:request",
                                 "from": maria, "message": {"message_id": 100, "chat": chat}})
    assert gw._channel_callback({"id": "old-approval", "data": "cha:obsolete:approve",
                                 "from": maria, "message": {"message_id": 100, "chat": chat}})
    assert not gw._channels_list()


def test_non_owner_channel_command_is_silent_and_does_not_open_a_request(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    maria = {"id": 42, "first_name": "Maria"}
    gw._update({"message": {"message_id": 23, "chat": {"id": 42, "type": "private"},
                             "from": maria, "text": "/channel"}})

    assert not api.sent
    assert not gw.database.execute("SELECT 1 FROM telegram_channel_menus").fetchone()
    assert not gw.database.execute("SELECT 1 FROM telegram_channel_requests").fetchone()


def test_send_requires_catalog_and_does_not_create_codex_thread(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    with pytest.raises(ValueError, match="não cadastrado"):
        gw._outbound_send({"channel": "9", "text": "oi"})
    gw._channel_register("9", "Proprietário", 9)
    result = gw._outbound_send({"channel": "9", "text": "Relatório enviado."})
    assert result["sent"] and result["channel"] == "9"
    assert not gw.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone()[0]
    context, ids = gw._pending_external("9")
    assert ids == [result["message_id"]]
    assert "Relatório enviado." in context
    gw._mark_external("9", ids)
    assert gw._pending_external("9") == ("", [])


def test_create_topic_never_registers_generated_destination(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    gw._channel_register("group:-1000:12", "Equipe / tópico 12", 9)
    result = gw._outbound_send({"channel": "group:-1000:12", "new_topic": "Relatórios", "text": "Bem-vindos"})
    assert result["channel"] == "group:-1000:17"
    assert result["topic_created"]
    assert len(gw._channels_list()) == 1
    assert api.sent[-1][2] == {"message_thread_id": 17}
    assert gw._pending_external("group:-1000:17")[1] == [result["message_id"]]


def test_file_is_limited_to_authorized_roots_and_retained_by_destination(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    gw._channel_register("9", "Owner", 9)
    allowed = tmp_path / "report.txt"
    allowed.write_text("conteúdo", encoding="utf-8")
    res = gw._outbound_send({"channel": "9", "path": str(allowed), "kind": "document", "text": "Relatório"})
    assert res["sent"]
    context, ids = gw._pending_external("9")
    assert "report.txt" in context
    retained = gw._external_attachment_rows("9", ids)
    assert retained and Path(retained[0]["archive_path"]).read_text(encoding="utf-8") == "conteúdo"
    forbidden = tmp_path.parent / "forbidden-file-test.txt"
    forbidden.write_text("não publicar", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="fora das raízes autorizadas"):
            gw._outbound_send({"channel": "9", "path": str(forbidden), "kind": "document"})
    finally:
        forbidden.unlink(missing_ok=True)


def test_removal_does_not_destroy_existing_conversation(tmp_path):
    gw = gateway_for(tmp_path)
    gw._channel_register("9", "Owner", 9)
    gw._ensure_conversation("9")
    gw.database.execute("UPDATE conversations SET codex_thread_id='keep-me' WHERE chat_id='9'")
    gw.database.commit()
    gw._channels_remove("9")
    assert gw.database.execute("SELECT codex_thread_id FROM conversations WHERE chat_id='9'").fetchone()[0] == "keep-me"



def test_confirmed_send_returns_warning_instead_of_ambiguous_error_if_archive_fails(tmp_path):
    gw = gateway_for(tmp_path)
    gw.api = FakeApi()
    gw._channel_register("9", "Owner", 9)
    doc = tmp_path / "to-send.txt"
    doc.write_text("exemplo", encoding="utf-8")
    def fail_archive(*_args):
        raise OSError("disco indisponível")
    gw._archive_external_file = fail_archive
    result = gw._outbound_send({"channel": "9", "path": str(doc), "kind": "document"})
    assert result["sent"] and "warning" in result
    assert len(gw.api.files) == 1


def test_outbound_confirmation_after_new_never_enters_the_new_generation(tmp_path):
    gw = gateway_for(tmp_path)
    gw._channel_register("9", "Owner", 9)
    class ResetApi(FakeApi):
        def send(self, *args, **kwargs):
            result = super().send(*args, **kwargs)
            gw._new_conversation("9")
            return result
    gw.api = ResetApi()
    result = gw._outbound_send({"channel": "9", "text": "Resultado antigo"})
    assert result["sent"] is True and "warning" in result
    assert gw._generation("9") == 1
    assert gw._pending_external("9") == ("", [])
    assert not gw.database.execute("SELECT 1 FROM bot_messages WHERE generation=1").fetchone()


def test_external_file_copy_revalidates_generation_before_inserting_attachment(tmp_path, monkeypatch):
    from onmyoji_daemon import telegram_channels
    gw = gateway_for(tmp_path); gw.api = FakeApi()
    gw._channel_register("9", "Owner", 9)
    document = tmp_path / "report.txt"; document.write_text("Fictício", encoding="utf-8")
    original = telegram_channels.shutil.copy2
    def reset_during_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        gw._new_conversation("9")
        return result
    monkeypatch.setattr(telegram_channels.shutil, "copy2", reset_during_copy)
    result = gw._outbound_send({"channel": "9", "path": str(document), "kind": "document"})
    assert result["sent"] is True and "warning" in result
    assert not gw.database.execute("SELECT 1 FROM conversation_attachments WHERE generation=1").fetchone()
    assert gw._pending_external("9") == ("", [])


def test_new_after_topic_creation_does_not_send_message_or_create_again(tmp_path):
    gw = gateway_for(tmp_path)
    gw._channel_register("group:-1000:12", "Equipe", 9)
    class ResetApi(FakeApi):
        def call(self, method, params=None):
            result = super().call(method, params)
            if method == "createForumTopic": gw._new_conversation("group:-1000:12")
            return result
    gw.api = ResetApi()
    result = gw._outbound_send({"channel": "group:-1000:12", "new_topic": "Fictício", "text": "Exemplo"})
    assert result["sent"] is False and result["topic_created"] is True
    assert result["retry_safe"] is False and len(gw.api.calls) == 1
    assert gw.api.sent == []


def test_explicit_key_reuses_confirmed_delivery_without_rearchiving(tmp_path):
    gw = gateway_for(tmp_path); gw.api = FakeApi()
    gw._channel_register("9", "Owner", 9)
    document = tmp_path / "report.txt"; document.write_text("Fictício", encoding="utf-8")
    params = {"channel": "9", "path": str(document), "kind": "document", "idempotency_key": "request-123"}
    first = gw._outbound_send(params)
    second = gw._outbound_send(params)
    assert first["sent"] is True and second["already_sent"] is True
    assert first["message_id"] == second["message_id"]
    assert len(gw.api.files) == 1
    assert gw.database.execute("SELECT COUNT(*) FROM conversation_attachments").fetchone()[0] == 1


def test_identical_text_without_explicit_key_is_two_legitimate_sends(tmp_path):
    gw = gateway_for(tmp_path); gw.api = FakeApi(); gw._channel_register("9", "Owner", 9)
    assert gw._outbound_send({"channel": "9", "text": "Mesmo texto"})["sent"]
    assert gw._outbound_send({"channel": "9", "text": "Mesmo texto"})["sent"]
    assert len(gw.api.sent) == 2


@pytest.mark.parametrize("key", ["", "bad key", "../outside", "a" * 129, 1, []])
def test_invalid_explicit_key_does_not_send(tmp_path, key):
    gw = gateway_for(tmp_path); gw.api = FakeApi(); gw._channel_register("9", "Owner", 9)
    with pytest.raises(ValueError, match="idempotência"):
        gw._outbound_send({"channel": "9", "text": "Fictício", "idempotency_key": key})
    assert gw.api.sent == []


def test_key_reuse_with_different_payload_is_rejected(tmp_path):
    gw = gateway_for(tmp_path); gw.api = FakeApi(); gw._channel_register("9", "Owner", 9)
    gw._outbound_send({"channel": "9", "text": "Primeiro", "idempotency_key": "request-123"})
    with pytest.raises(ValueError, match="solicitação diferente"):
        gw._outbound_send({"channel": "9", "text": "Outro", "idempotency_key": "request-123"})
    assert len(gw.api.sent) == 1


def test_timeout_is_uncertain_and_same_key_never_resends(tmp_path):
    gw = gateway_for(tmp_path); gw._channel_register("9", "Owner", 9)
    class TimeoutApi(FakeApi):
        def send(self, *args, **kwargs):
            super().send(*args, **kwargs)
            raise TimeoutError("ACK perdido")
    gw.api = TimeoutApi()
    params = {"channel": "9", "text": "Fictício", "idempotency_key": "request-123"}
    for _index in range(2):
        result = gw._outbound_send(params)
        assert result["sent"] is None and result["uncertain"] is True and result["retry_safe"] is False
    assert len(gw.api.sent) == 1
    assert gw._pending_external("9") == ("", [])


def test_topic_creation_timeout_does_not_create_a_second_topic(tmp_path):
    gw = gateway_for(tmp_path); gw._channel_register("group:-1000:12", "Equipe", 9)
    class TimeoutApi(FakeApi):
        def call(self, method, params=None):
            super().call(method, params)
            raise TimeoutError("ACK perdido")
    gw.api = TimeoutApi()
    params = {"channel": "group:-1000:12", "new_topic": "Fictício", "text": "Fictício", "idempotency_key": "request-123"}
    for _index in range(2):
        result = gw._outbound_send(params)
        assert result["sent"] is None and result["topic_created"] is None and result["stage"] == "topic"
    assert len(gw.api.calls) == 1 and gw.api.sent == []


def test_topic_and_message_confirmation_are_reused_separately(tmp_path):
    gw = gateway_for(tmp_path); gw.api = FakeApi(); gw._channel_register("group:-1000:12", "Equipe", 9)
    params = {"channel": "group:-1000:12", "new_topic": "Fictício", "text": "Fictício", "idempotency_key": "request-123"}
    first = gw._outbound_send(params); second = gw._outbound_send(params)
    assert first["message_id"] == second["message_id"] and second["already_sent"]
    assert len(gw.api.calls) == 1 and len(gw.api.sent) == 1
    assert len(gw._channels_list()) == 1


def test_simultaneous_replay_of_explicit_key_has_one_local_send(tmp_path):
    gw = gateway_for(tmp_path); gw.api = FakeApi(); gw._channel_register("9", "Owner", 9)
    params = {"channel": "9", "text": "Fictício", "idempotency_key": "request-123"}
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _index: gw._outbound_send(params), range(4)))
    assert all(result["sent"] for result in results)
    assert len(gw.api.sent) == 1


def test_missing_confirmation_is_not_reported_as_success(tmp_path):
    gw = gateway_for(tmp_path); gw.api = FakeApi(); gw._channel_register("9", "Owner", 9)
    gw.api.send = lambda *_args, **_kwargs: {"message_id": True}
    result = gw._outbound_send({"channel": "9", "text": "Fictício", "idempotency_key": "request-123"})
    assert result["sent"] is None and result["uncertain"]
