"""Menus privados de login do Codex: permissões, callbacks e RPCs."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import pytest

from onmyoji_daemon.codex_app_server import CodexProtocolError
from onmyoji_daemon.telegram import Gateway, Settings, TelegramApi


@pytest.fixture
def lab(tmp_path, monkeypatch):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "onmyoji-system.toml").write_text(
        '[codex]\nexecutable = "codex"\nproject_directory = "' + tmp_path.as_posix() + '"\n',
        encoding="utf-8",
    )
    data = configs / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (configs / "daemon" / "daemon.toml").write_text(
        '[app_server]\nenabled = true\nidle_timeout_seconds = 1800\n', encoding="utf-8"
    )
    (data / "config.toml").write_text(
        'schema_version = 1\n[telegram]\nvault_profile = "telegram"\ntoken_entry = "token"\n',
        encoding="utf-8",
    )
    gateway = Gateway(replace(Settings.load(tmp_path, data), app_server_enabled=True))
    gateway.contacts.add_owner({"id": 9})
    sent, deleted, calls, edits, acknowledgements = [], [], [], [], []

    def send(chat, text, **values):
        result = {"message_id": len(sent) + 101}
        sent.append((chat, text, values, result["message_id"]))
        return result

    gateway.api = SimpleNamespace(
        send=send, delete=lambda chat, msg: deleted.append((chat, msg)),
        edit=lambda chat, msg, text, keyboard: edits.append((chat, msg, text, keyboard)),
        answer_callback=lambda identifier: acknowledgements.append(identifier),
    )
    gateway._login_test_edits = edits

    def request(method, params, timeout):
        calls.append((method, params))
        if method == "account/login/start":
            return {"type": "chatgptDeviceCode", "loginId": "login-1",
                    "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "ABCD-1234"}
        if method in {"account/login/cancel", "account/logout"}:
            return {}
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "private@example.test", "planType": "plus"}}
        pytest.fail("RPC inesperado: " + method)

    client = SimpleNamespace(request=request, process_epoch="epoch-1")
    monkeypatch.setattr(gateway, "_app_client", lambda: client)
    yield gateway, client, sent, deleted, calls
    gateway.stop_event.set()
    gateway.database.close()


def update(text="/login", *, sender=9, chat=None, topic=None):
    msg = {"message_id": 42, "chat": chat or {"id": 9, "type": "private"},
           "from": {"id": sender}, "text": text}
    if topic:
        msg.update(topic)
    return {"message": msg}


def open_menu(gateway, *, topic=None, text="/login"):
    gateway._update(update(text, topic=topic))
    token, session = next(reversed(gateway._login_menus.items()))
    return token, session


def click(gateway, token, session, action, *, sender=9, topic=None, message_id=None):
    msg = {"chat": {"id": 9, "type": "private"},
           "message_id": session["message_id"] if message_id is None else message_id}
    if topic:
        msg.update(topic)
    gateway._callback({"id": "cb-" + action, "from": {"id": sender},
                       "message": msg, "data": f"lg:{token}:{action}"})


def test_login_command_opens_menu_without_starting_authentication(lab):
    gateway, _client, sent, deleted, calls = lab
    token, session = open_menu(gateway, text="/LoGiN@bot")
    assert calls == []
    assert deleted == [(9, 42)]
    assert "Selecione uma opção" in sent[-1][1]
    assert "protect_content" not in sent[-1][2]
    keyboard = sent[-1][2]["reply_markup"]["inline_keyboard"]
    actions = [row[0]["callback_data"] for row in keyboard]
    assert actions == [f"lg:{token}:{name}" for name in ("start", "status", "account", "logout", "close")]
    assert session["page"] == "root"


def test_device_login_private_owner_and_completion(lab):
    gateway, _client, sent, deleted, calls = lab
    topic = {"direct_messages_topic": {"topic_id": 13}}
    token, session = open_menu(gateway, topic=topic)
    click(gateway, token, session, "start", topic=topic)
    assert calls == [("account/login/start", {"type": "chatgptDeviceCode"})]
    assert (9, session["message_id"]) in deleted
    challenge = sent[-1]
    assert "ABCD-1234" in challenge[1]
    assert "https://auth.openai.com/codex/device" in challenge[1]
    assert "protect_content" not in challenge[2]
    assert challenge[2]["direct_messages_topic_id"] == 13
    gateway._app_event("account/login/completed", {"loginId": "other", "success": True}, {"process_epoch": "epoch-1"})
    assert len(sent) == 2
    gateway._app_event("account/login/completed", {"loginId": "login-1", "success": True}, {"process_epoch": "other"})
    assert len(sent) == 2
    gateway._app_event("account/login/completed", {"loginId": "login-1", "success": True}, {"process_epoch": "epoch-1"})
    assert "concluída" in sent[-1][1]
    assert not gateway._login_pending()


@pytest.mark.parametrize("change", [{"sender": 8}, {"chat": {"id": -100, "type": "group"}},
                                    {"chat": {"id": 8, "type": "private"}}])
def test_non_owner_group_and_foreign_dm_cannot_open_login(lab, change):
    gateway, _client, sent, _deleted, calls = lab
    gateway._update(update(**change))
    assert calls == [] and sent == [] and not gateway._login_menus


def test_status_redacts_email_and_account_info_shows_it_only_on_request(lab):
    gateway, _client, sent, _deleted, calls = lab
    token, session = open_menu(gateway)
    click(gateway, token, session, "status")
    assert calls == [("account/read", {"refreshToken": False})]
    assert "ChatGPT" in gateway._login_test_edits[-1][2]
    assert "plus" in gateway._login_test_edits[-1][2]
    assert "private@example.test" not in gateway._login_test_edits[-1][2]
    click(gateway, token, session, "back")
    click(gateway, token, session, "account")
    assert "private@example.test" in gateway._login_test_edits[-1][2]
    assert "protect_content" not in sent[0][2]


def test_argument_is_not_a_login_action(lab):
    gateway, _client, sent, _deleted, calls = lab
    open_menu(gateway, text="/login cancelar")
    open_menu(gateway, text="/login status")
    assert len(sent) == 2 and calls == []


def test_cancel_from_menu_allows_another_login(lab):
    gateway, _client, sent, _deleted, calls = lab
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    token2, session2 = open_menu(gateway)
    assert "cancel" in [row[0]["callback_data"].rsplit(":", 1)[1]
                        for row in sent[-1][2]["reply_markup"]["inline_keyboard"]]
    click(gateway, token2, session2, "cancel")
    assert calls[-1] == ("account/login/cancel", {"loginId": "login-1"})
    assert not gateway._login_pending()
    token3, session3 = open_menu(gateway)
    click(gateway, token3, session3, "start")
    assert sum(method == "account/login/start" for method, _ in calls) == 2
    assert any("cancelada" in row[1] for row in sent)


def test_early_completion_notification_is_buffered_until_code_is_sent(lab):
    gateway, client, sent, _deleted, _calls = lab
    original = client.request

    def request(method, params, timeout):
        if method == "account/login/start":
            gateway._app_event("account/login/completed", {"loginId": "login-1", "success": True}, {"process_epoch": "epoch-1"})
        return original(method, params, timeout)

    client.request = request
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    assert len(sent) == 3
    assert "ABCD-1234" in sent[1][1] and "concluída" in sent[2][1]
    assert not gateway._login_pending()


def test_login_rejects_running_execution_without_starting_rpc(lab):
    gateway, _client, sent, _deleted, calls = lab
    gateway.app_turns["thread"] = object()
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    assert not calls and "execuções" in sent[-1][1]


def test_login_rejects_untrusted_device_url_and_scrubs_errors(lab):
    gateway, client, sent, _deleted, _calls = lab
    original = client.request

    def request(method, params, timeout):
        if method == "account/login/start":
            return {**original(method, params, timeout), "verificationUrl": "https://evil.example/steal"}
        return original(method, params, timeout)

    client.request = request
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    assert "evil.example" not in sent[-1][1]
    assert "Não foi possível iniciar" in sent[-1][1]
    assert not gateway._login_pending()


def test_app_server_failure_does_not_expose_raw_credentials(lab):
    gateway, client, sent, _deleted, _calls = lab

    def request(method, params, timeout):
        raise CodexProtocolError("token=PRIVATE email=private@example.test", kind="rejected", rpc_code=-32601)

    client.request = request
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    assert "PRIVATE" not in sent[-1][1]
    assert "private@example.test" not in sent[-1][1]
    assert not gateway._login_pending()


def test_login_unblocks_when_process_is_lost(lab):
    gateway, _client, sent, _deleted, _calls = lab
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    assert gateway._login_pending()
    gateway._app_event("transport/lost", {}, {"process_epoch": "epoch-1"})
    assert not gateway._login_pending()
    assert "interrompida" in sent[-1][1]


def test_app_server_disabled_keeps_menu_without_rpc(lab):
    gateway, _client, sent, _deleted, calls = lab
    gateway.settings = replace(gateway.settings, app_server_enabled=False)
    token, session = open_menu(gateway)
    click(gateway, token, session, "start")
    assert calls == []
    assert "App Server habilitado" in sent[-1][1]


def test_delivery_failure_cancels_challenge_and_releases_dispatch(lab):
    gateway, _client, _sent, _deleted, calls = lab
    token, session = open_menu(gateway)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Telegram temporariamente indisponível")

    gateway.api.send = unavailable
    click(gateway, token, session, "start")
    assert not gateway._login_pending()
    assert calls[-1] == ("account/login/cancel", {"loginId": "login-1"})


def test_logout_requires_explicit_confirmation_and_rejects_busy_runtime(lab):
    gateway, _client, _sent, _deleted, calls = lab
    token, session = open_menu(gateway)
    click(gateway, token, session, "logout")
    assert not calls and session["page"] == "logout"
    gateway.app_turns["thread"] = object()
    click(gateway, token, session, "logout-confirm")
    assert "execuções" in gateway._login_test_edits[-1][2]
    assert all(method != "account/logout" for method, _ in calls)
    gateway.app_turns.clear()
    click(gateway, token, session, "back")
    click(gateway, token, session, "logout")
    click(gateway, token, session, "logout-confirm")
    assert calls[-1] == ("account/logout", {})
    assert "Logoff concluído" in gateway._login_test_edits[-1][2]


def test_stale_callback_or_forged_confirmation_has_no_side_effect(lab):
    gateway, _client, _sent, _deleted, calls = lab
    token, session = open_menu(gateway)
    click(gateway, token, session, "logout-confirm")
    click(gateway, token, session, "start", sender=8)
    click(gateway, token, session, "start", message_id=999)
    assert not calls
    session["expires"] = time.time() - 1
    click(gateway, token, session, "start")
    assert not calls
    assert not gateway._login_pending()


def test_topic_callbacks_do_not_cross_private_topics(lab):
    gateway, _client, _sent, _deleted, calls = lab
    topic = {"direct_messages_topic": {"topic_id": 13}}
    token, session = open_menu(gateway, topic=topic)
    click(gateway, token, session, "start", topic={"direct_messages_topic": {"topic_id": 14}})
    assert not calls
    click(gateway, token, session, "start", topic=topic)
    assert calls == [("account/login/start", {"type": "chatgptDeviceCode"})]


def test_slow_telegram_deletion_does_not_hold_up_login_menu(lab):
    gateway, _client, sent, _deleted, _calls = lab
    entered, release = threading.Event(), threading.Event()

    class SlowTelegram(TelegramApi):
        def __init__(self):
            pass

        def send(self, chat, text, **values):
            sent.append((chat, text, values, 102))
            return {"message_id": 102}

        def delete(self, chat, message_id):
            entered.set()
            release.wait(3)

    gateway.api = SlowTelegram()
    try:
        started = time.monotonic()
        open_menu(gateway)
        assert time.monotonic() - started < 0.7
        assert entered.wait(1)
        assert "Selecione" in sent[-1][1]
    finally:
        release.set()
