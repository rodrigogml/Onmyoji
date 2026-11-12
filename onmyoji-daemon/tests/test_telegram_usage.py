"""Comando de franquia na fronteira entre o chat e o App Server."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from onmyoji_daemon.codex_app_server import CodexProtocolError
from onmyoji_daemon.telegram import Gateway, Settings
from onmyoji_daemon.telegram_process import ProcessRecoveryError


@pytest.fixture
def usage_gateway(tmp_path, monkeypatch):
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "onmyoji-system.toml").write_text(
        '[codex]\nexecutable = "codex"\nproject_directory = "' + tmp_path.as_posix() + '"\n',
        encoding="utf-8",
    )
    data = configs / "daemon" / "services" / "telegram"
    data.mkdir(parents=True)
    (configs / "daemon" / "daemon.toml").write_text(
        '[app_server]\nenabled = true\nidle_timeout_seconds = 1800\n',
        encoding="utf-8",
    )
    (data / "config.toml").write_text(
        'schema_version = 1\n[telegram]\nvault_profile = "telegram"\ntoken_entry = "APIs/Telegram:Example"\n',
        encoding="utf-8",
    )
    gateway = Gateway(replace(Settings.load(tmp_path, data), app_server_enabled=True))
    gateway.contacts.add_owner({"id": 9})
    sent, deleted, calls = [], [], []

    def send(chat, text, **values):
        sent.append({"chat": chat, "text": text, "values": values})
        return {"message_id": 100 + len(sent)}

    gateway.api = SimpleNamespace(send=send, delete=lambda chat, message: deleted.append((chat, message)))
    monkeypatch.setattr(gateway, "_schedule_delete", lambda *_args: None)

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "private@example.test"}, "requiresOpenaiAuth": True}
        assert method == "account/rateLimits/read"
        return {"rateLimits": {"primary": {"usedPercent": 0, "windowDurationMins": 300, "resetsAt": None}}}

    client = SimpleNamespace(request=request)
    monkeypatch.setattr(gateway, "_app_client", lambda: client)
    yield gateway, client, sent, deleted, calls
    gateway.database.close()


def usage_message(**changes):
    message = {"message_id": 72, "chat": {"id": 9, "type": "private"}, "from": {"id": 9}, "text": "/usage"}
    message.update(changes)
    return {"message": message}


@pytest.mark.parametrize("topic, expected", [
    ({}, {}),
    ({"message_thread_id": 23}, {"message_thread_id": 23}),
    ({"direct_messages_topic": {"topic_id": 41}}, {"direct_messages_topic_id": 41}),
])
def test_usage_bot_suffix_and_private_topics_keep_the_reply_address(usage_gateway, topic, expected):
    gateway, _client, sent, deleted, calls = usage_gateway
    gateway._update(usage_message(text="/UsAgE@MeuBot", **topic))

    assert deleted == [(9, 72)]
    assert len(sent) == 1 and sent[0]["chat"] == 9
    assert "100% disponível" in sent[0]["text"]
    assert sent[0]["values"] == {"parse_mode": "HTML", **expected}
    assert [call[0] for call in calls] == ["account/read", "account/rateLimits/read"]


def test_usage_from_non_owner_never_reads_account_or_sends_allowance(usage_gateway):
    gateway, _client, sent, _deleted, calls = usage_gateway
    gateway._update(usage_message(**{"from": {"id": 8}}))
    assert calls == [] and sent == []


def test_usage_in_a_group_does_not_expose_the_account_allowance(usage_gateway):
    gateway, _client, sent, _deleted, calls = usage_gateway
    gateway._update(usage_message(chat={"id": -100, "type": "supergroup"}))
    assert calls == [] and sent == []


def test_usage_does_not_open_disabled_app_server(usage_gateway, monkeypatch):
    gateway, _client, sent, _deleted, calls = usage_gateway
    gateway.settings = replace(gateway.settings, app_server_enabled=False)

    def unexpected_client():
        pytest.fail("O App Server desabilitado não deve ser iniciado.")

    monkeypatch.setattr(gateway, "_app_client", unexpected_client)
    gateway._update(usage_message())
    assert calls == []
    assert "App Server habilitado" in sent[0]["text"]


@pytest.mark.parametrize("account_state, expected", [
    ({"account": {"type": "apiKey"}, "requiresOpenaiAuth": True}, "chave de API"),
    ({"account": {"type": "amazonBedrock"}, "requiresOpenaiAuth": False}, "Amazon Bedrock"),
    ({"account": None, "requiresOpenaiAuth": True}, "Faça login no Codex pelo setup"),
    ({"account": None, "requiresOpenaiAuth": False}, "provedor configurado"),
])
def test_usage_explains_authentication_modes_without_requesting_unavailable_limits(usage_gateway, account_state, expected):
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        assert method == "account/read"
        return account_state

    client.request = request
    gateway._update(usage_message())
    assert expected in sent[0]["text"]
    assert len(calls) == 1 and "%" not in sent[0]["text"]


@pytest.mark.parametrize("account_type", ["futureAuth", ["malformed"]])
def test_unknown_account_type_does_not_prevent_a_supported_limit_read(usage_gateway, account_type):
    gateway, client, sent, _deleted, _calls = usage_gateway
    original = client.request

    def request(method, params, timeout):
        if method == "account/read":
            return {"account": {"type": account_type}}
        return original(method, params, timeout)

    client.request = request
    gateway._update(usage_message())
    assert "100% disponível" in sent[0]["text"]


def test_missing_optional_account_read_still_attempts_supported_limits(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    original = client.request

    def request(method, params, timeout):
        if method == "account/read":
            raise CodexProtocolError("method not found", kind="rejected", rpc_code=-32601)
        return original(method, params, timeout)

    client.request = request
    gateway._update(usage_message())
    assert [call[0] for call in calls] == ["account/rateLimits/read"]
    assert "100% disponível" in sent[0]["text"]


@pytest.mark.parametrize("code", [-32600, -32601])
def test_old_codex_receives_update_guidance_without_raw_error_details(usage_gateway, code):
    gateway, client, sent, _deleted, _calls = usage_gateway
    original = client.request

    def request(method, params, timeout):
        if method == "account/rateLimits/read":
            raise CodexProtocolError("private@example.test token=private", kind="rejected", rpc_code=code)
        return original(method, params, timeout)

    client.request = request
    gateway._update(usage_message())
    assert "Atualize o Codex pelo setup" in sent[0]["text"]
    assert "private" not in sent[0]["text"] and "token" not in sent[0]["text"]


@pytest.mark.parametrize("failing_method", ["account/read", "account/rateLimits/read"])
def test_usage_protocol_failure_does_not_leak_details_or_report_full_allowance(usage_gateway, failing_method):
    gateway, client, sent, _deleted, _calls = usage_gateway
    original = client.request

    def request(method, params, timeout):
        if method == failing_method:
            raise CodexProtocolError("timeout: private@example.test token=private")
        return original(method, params, timeout)

    client.request = request
    gateway._update(usage_message())
    assert "Não foi possível consultar" in sent[0]["text"]
    assert "%" not in sent[0]["text"] and "private" not in sent[0]["text"]


def test_usage_process_recovery_failure_is_reported_without_diagnostics(usage_gateway, monkeypatch):
    gateway, _client, sent, _deleted, calls = usage_gateway

    def unavailable_client():
        raise ProcessRecoveryError("private_process_detail")

    monkeypatch.setattr(gateway, "_app_client", unavailable_client)
    gateway._update(usage_message())
    assert calls == [] and "Não foi possível consultar" in sent[0]["text"]
    assert "private_process_detail" not in sent[0]["text"]


@pytest.mark.parametrize("fails", [False, True])
def test_new_during_usage_query_suppresses_the_stale_success_or_error(usage_gateway, fails):
    gateway, client, sent, _deleted, _calls = usage_gateway
    original = client.request

    def request(method, params, timeout):
        response = original(method, params, timeout)
        if method == "account/rateLimits/read":
            gateway._new_conversation("9")
            if fails:
                raise CodexProtocolError("late failure")
        return response

    client.request = request
    gateway._update(usage_message())
    assert sent == []


def test_new_during_usage_delivery_removes_only_its_late_receipt(usage_gateway):
    gateway, _client, sent, deleted, _calls = usage_gateway
    original = gateway.api.send

    def send(chat, text, **values):
        gateway._new_conversation("9")
        return original(chat, text, **values)

    gateway.api.send = send
    gateway._update(usage_message())
    assert len(sent) == 1
    assert deleted == [(9, 72), (9, 101)]


def test_unknown_command_help_includes_usage(usage_gateway):
    gateway, _client, sent, _deleted, calls = usage_gateway
    gateway._update(usage_message(text="/desconhecido"))
    assert calls == []
    assert "Comando inválido" in sent[0]["text"] and "/usage" in sent[0]["text"] and "/resetusage" in sent[0]["text"]


def test_resetusage_owner_confirms_one_credit_and_refreshes_limits(usage_gateway):
    import re
    import uuid
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "account/rateLimits/read":
            return {"rateLimitResetCredits": {"availableCount": 2},
                    "rateLimits": {"primary": {"usedPercent": 30, "windowDurationMins": 300}}}
        assert method == "account/rateLimitResetCredit/consume"
        assert set(params) == {"idempotencyKey"}
        uuid.UUID(params["idempotencyKey"])
        return {"outcome": "reset"}

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    assert [item[0] for item in calls] == ["account/rateLimits/read"]
    match = re.search(r"/resetusage confirmar ([0-9a-f]{6})", sent[-1]["text"])
    assert match is not None
    gateway._update(usage_message(text=f"/resetusage confirmar {match.group(1)}"))
    assert [item[0] for item in calls] == [
        "account/rateLimits/read", "account/rateLimitResetCredit/consume", "account/rateLimits/read"]
    assert "Renovação extra aplicada" in sent[-1]["text"]
    gateway._update(usage_message(text=f"/resetusage confirmar {match.group(1)}"))
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1


@pytest.mark.parametrize("changes", [
    {"from": {"id": 8}},
    {"chat": {"id": -100, "type": "supergroup"}},
    {"chat": {"id": 8, "type": "private"}},
])
def test_resetusage_rejected_outside_owner_private_chat(usage_gateway, changes):
    gateway, _client, sent, _deleted, calls = usage_gateway
    gateway._update(usage_message(text="/resetusage", **changes))
    gateway._update(usage_message(text="/resetusage confirmar aabbcc", **changes))
    assert calls == [] and sent == []


def test_resetusage_ignores_missing_invalid_or_expired_confirmation(usage_gateway):
    import re
    gateway, client, sent, _deleted, calls = usage_gateway
    client.request = lambda method, params, timeout: (
        calls.append((method, params, timeout)) or {"rateLimitResetCredits": {"availableCount": 1}})
    gateway._update(usage_message(text="/resetusage confirmar abcdef"))
    assert calls == []
    gateway._update(usage_message(text="/resetusage"))
    token = re.search(r"/resetusage confirmar ([0-9a-f]{6})", sent[-1]["text"]).group(1)
    gateway._update(usage_message(text="/resetusage confirmar 000000"))
    assert len(calls) == 1
    gateway._reset_usage_pending["9"]["expires_at"] = 0
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert len(calls) == 1
    gateway._update(usage_message(text="/resetusage"))
    fresh = re.search(r"/resetusage confirmar ([0-9a-f]{6})", sent[-1]["text"]).group(1)
    gateway._new_conversation("9")
    gateway._update(usage_message(text=f"/resetusage confirmar {fresh}"))
    assert len(calls) == 2


def test_resetusage_cancel_or_missing_credit_never_consumes(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    client.request = lambda method, params, timeout: (
        calls.append((method, params, timeout)) or {"rateLimitResetCredits": {"availableCount": 0}})
    gateway._update(usage_message(text="/resetusage"))
    assert "não informou renovações" in sent[-1]["text"]
    assert len(calls) == 1 and not gateway._reset_usage_pending
    gateway._update(usage_message(text="/resetusage cancelar"))
    assert not gateway._reset_usage_pending
    assert len(calls) == 1


@pytest.mark.parametrize("outcome, expected", [
    ("alreadyRedeemed", "Renovação extra aplicada"),
    ("noCredit", "não há renovações"),
    ("nothingToReset", "nenhuma janela está elegível"),
    ("unexpected", "não confirmou o resgate"),
])
def test_resetusage_handles_service_outcomes_without_retry(usage_gateway, outcome, expected):
    import re
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        return ({"rateLimitResetCredits": {"availableCount": 1}} if method == "account/rateLimits/read"
                else {"outcome": outcome})

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    token = re.search(r"/resetusage confirmar ([0-9a-f]{6})", sent[-1]["text"]).group(1)
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert expected in sent[-1]["text"]
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1


def test_resetusage_unknown_consume_failure_is_not_retried(usage_gateway):
    import re
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "account/rateLimitResetCredit/consume":
            raise CodexProtocolError("private@example.test token=private")
        return {"rateLimitResetCredits": {"availableCount": 1}}

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    token = re.search(r"/resetusage confirmar ([0-9a-f]{6})", sent[-1]["text"]).group(1)
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert "incerto" in sent[-1]["text"] and "private" not in sent[-1]["text"]
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1
