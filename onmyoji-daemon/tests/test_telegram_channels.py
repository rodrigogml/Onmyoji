"""Testes em desenvolvimento: catálogo e saída sem Bot API real."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from onmyoji_daemon.telegram import Gateway, Settings


def gateway_for(tmp_path: Path) -> Gateway:
    data = tmp_path / "configs" / "daemon" / "services" / "telegram"
    data.mkdir(parents=True, exist_ok=True)
    root_config = tmp_path / "configs" / "onmyoji-system.toml"
    root_config.write_text('[codex]\nproject_directory = "' + tmp_path.as_posix() + '"\n', encoding="utf-8")
    cfg = tmp_path / "shikigami" / "daemon" / "telegram.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text('schema_version = 1\n[telegram]\nkeepass_profile="telegram"\ntoken_entry="token"\n', encoding="utf-8")
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


def test_group_outsider_is_denied_without_approval_menu(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    gw._channel_menu("group:-1000:24", {"id": -1000, "type": "supergroup", "title": "Equipe"}, {"id": 42})
    assert "apenas ao proprietário" in api.sent[-1][1]
    assert gw._channels_list() == []
    assert not gw.database.execute("SELECT 1 FROM telegram_channel_requests").fetchone()


def test_third_party_private_request_requires_owner_approval(tmp_path):
    gw = gateway_for(tmp_path)
    api = gw.api = FakeApi()
    maria = {"id": 42, "first_name": "Maria"}
    chat = {"id": 42, "type": "private"}
    gw._channel_menu("42", chat, maria)
    menu = api.sent[-1]
    action = menu[2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    request_cb = {"id": "req", "data": action, "from": maria, "message": {"message_id": menu[3]["message_id"], "chat": chat}}
    assert gw._channel_callback(request_cb)
    assert not gw._channels_list()
    owner_menu = [m for m in api.sent if m[0] == 9][-1]
    approve = owner_menu[2]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert approve.startswith("cha:")
    approval_message = {"chat": {"id": 9, "type": "private"}, "message_id": owner_menu[3]["message_id"]}
    assert gw._channel_callback({"id": "approval", "data": approve, "from": {"id": 42}, "message": {"chat": chat}})
    assert not gw._channels_list()
    assert gw._channel_callback({"id": "forged", "data": approve, "from": {"id": 9},
                                 "message": {"chat": {"id": 9, "type": "private"}, "message_id": 123}})
    assert not gw._channels_list()
    gw._channel_callback({"id": "approval2", "data": approve, "from": {"id": 9},
                          "message": approval_message})
    assert gw._channels_list()[0]["id"] == "42"
    assert gw._channel_callback({"id": "replay", "data": approve, "from": {"id": 9},
                                 "message": approval_message})
    assert len(gw._channels_list()) == 1


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


def test_file_is_limited_to_workspace_and_retained_by_destination(tmp_path):
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
        with pytest.raises(ValueError, match="fora do workspace"):
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
