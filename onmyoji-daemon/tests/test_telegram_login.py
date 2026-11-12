"""Login headless do Codex: autorização e sequência RPC isoladas do bot real."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from onmyoji_daemon.codex_app_server import CodexProtocolError
from onmyoji_daemon.telegram import Gateway, Settings


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
    sent, deleted, calls = [], [], []

    def send(chat, text, **values):
        sent.append((chat, text, values))
        return {"message_id": len(sent) + 100}

    gateway.api = SimpleNamespace(send=send, delete=lambda chat, msg: deleted.append((chat, msg)))

    def request(method, params, timeout):
        calls.append((method, params))
        if method == "account/login/start":
            return {"type": "chatgptDeviceCode", "loginId": "login-1",
                    "verificationUrl": "https://auth.openai.com/codex/device", "userCode": "ABCD-1234"}
        if method == "account/login/cancel":
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


def test_device_login_private_owner_and_completion(lab):
    gateway, _client, sent, deleted, calls = lab
    gateway._update(update(text="/LoGiN@bot", topic={"direct_messages_topic": {"topic_id": 13}}))
    assert calls == [("account/login/start", {"type": "chatgptDeviceCode"})]
    assert deleted == [(9, 42)]
    assert "ABCD-1234" in sent[0][1]
    assert "https://auth.openai.com/codex/device" in sent[0][1]
    assert sent[0][2]["protect_content"] is True
    assert sent[0][2]["direct_messages_topic_id"] == 13

    gateway._app_event("account/login/completed", {"loginId": "other", "success": True}, {"process_epoch": "epoch-1"})
    assert len(sent) == 1
    gateway._app_event("account/login/completed", {"loginId": "login-1", "success": True}, {"process_epoch": "other"})
    assert len(sent) == 1
    gateway._app_event("account/login/completed", {"loginId": "login-1", "success": True}, {"process_epoch": "epoch-1"})
    assert "concluída" in sent[-1][1]
    assert sent[-1][2]["protect_content"] is True
    assert not gateway._login_pending()


@pytest.mark.parametrize("change", [{"sender": 8}, {"chat": {"id": -100, "type": "group"}},
                                    {"chat": {"id": 8, "type": "private"}}])
def test_non_owner_group_and_foreign_dm_cannot_start_login(lab, change):
    gateway, _client, sent, _deleted, calls = lab
    gateway._update(update(**change))
    assert calls == [] and sent == []


def test_login_status_redacts_identity_and_does_not_generate_a_turn(lab):
    gateway, _client, sent, _deleted, calls = lab
    gateway._update(update("/login status"))
    assert calls == [("account/read", {"refreshToken": False})]
    assert "ChatGPT" in sent[-1][1] and "plus" in sent[-1][1]
    assert "private@example.test" not in sent[-1][1]
    assert sent[-1][2]["protect_content"] is True


def test_cancel_calls_app_server_and_allows_another_login(lab):
    gateway, _client, sent, _deleted, calls = lab
    gateway._update(update())
    gateway._update(update("/login cancelar"))
    assert calls[-1] == ("account/login/cancel", {"loginId": "login-1"})
    assert not gateway._login_pending()
    gateway._update(update())
    assert sum(method == "account/login/start" for method, _ in calls) == 2
    assert "cancelada" in sent[-2][1]


def test_early_completion_notification_is_buffered_until_code_is_sent(lab):
    gateway, client, sent, _deleted, _calls = lab
    original = client.request

    def request(method, params, timeout):
        if method == "account/login/start":
            gateway._app_event("account/login/completed", {"loginId": "login-1", "success": True}, {"process_epoch": "epoch-1"})
        return original(method, params, timeout)

    client.request = request
    gateway._update(update())
    assert len(sent) == 2
    assert "ABCD-1234" in sent[0][1] and "concluída" in sent[1][1]
    assert not gateway._login_pending()


def test_login_rejects_running_execution_without_starting_rpc(lab):
    gateway, _client, sent, _deleted, calls = lab
    gateway.app_turns["thread"] = object()
    gateway._update(update())
    assert not calls
    assert "execuções" in sent[-1][1]


def test_login_rejects_untrusted_device_url_and_scrubs_errors(lab):
    gateway, client, sent, _deleted, _calls = lab
    original = client.request

    def request(method, params, timeout):
        if method == "account/login/start":
            return {**original(method, params, timeout), "verificationUrl": "https://evil.example/steal"}
        return original(method, params, timeout)

    client.request = request
    gateway._update(update())
    assert "evil.example" not in sent[-1][1]
    assert "Não foi possível iniciar" in sent[-1][1]
    assert not gateway._login_pending()


def test_app_server_failure_does_not_expose_raw_credentials(lab):
    gateway, client, sent, _deleted, _calls = lab
    def request(method, params, timeout):
        raise CodexProtocolError("token=PRIVATE email=private@example.test", kind="rejected", rpc_code=-32601)
    client.request = request
    gateway._update(update())
    assert "PRIVATE" not in sent[-1][1]
    assert "private@example.test" not in sent[-1][1]
    assert not gateway._login_pending()


def test_login_unblocks_when_process_is_lost(lab):
    gateway, _client, sent, _deleted, _calls = lab
    gateway._update(update())
    assert gateway._login_pending()
    gateway._app_event("transport/lost", {}, {"process_epoch": "epoch-1"})
    assert not gateway._login_pending()
    assert "interrompida" in sent[-1][1]


def test_app_server_disabled_does_not_call_codex(lab):
    gateway, _client, sent, _deleted, calls = lab
    gateway.settings = replace(gateway.settings, app_server_enabled=False)
    gateway._update(update())
    assert calls == []
    assert "App Server habilitado" in sent[-1][1]


def test_delivery_failure_cancels_challenge_and_releases_dispatch(lab):
    gateway, _client, _sent, _deleted, calls = lab

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("Telegram temporariamente indisponível")

    gateway.api.send = unavailable
    gateway._update(update())
    assert not gateway._login_pending()
    assert calls[-1] == ("account/login/cancel", {"loginId": "login-1"})
