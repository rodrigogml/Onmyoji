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
    assert "Conta Codex\nAutenticação: ChatGPT\nE-mail: private@example.test" in sent[0]["text"]
    assert sent[0]["values"] == {"parse_mode": "HTML", **expected}
    assert [call[0] for call in calls] == ["account/read", "account/rateLimits/read"]


def test_usage_shows_current_account_with_plan_and_email_from_login(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    original = client.request
    account = {"type": "chatgpt", "planType": "plus", "email": "private@example.test"}

    def request(method, params, timeout):
        if method == "account/read":
            calls.append((method, params, timeout))
            return {"account": account, "requiresOpenaiAuth": True}
        return original(method, params, timeout)

    client.request = request
    gateway._update(usage_message())
    result = sent[0]["text"]
    assert result.startswith("Consumo e franquia do Codex")
    assert "100% disponível" in result
    assert "Conta Codex\nAutenticação: ChatGPT\nPlano: plus\nE-mail: private@example.test" in result
    assert [call[0] for call in calls] == ["account/read", "account/rateLimits/read"]


def test_usage_does_not_expose_malformed_account_email(usage_gateway):
    gateway, client, sent, _deleted, _calls = usage_gateway
    original = client.request

    def request(method, params, timeout):
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "private@example.test\nsecret"}}
        return original(method, params, timeout)

    client.request = request
    gateway._update(usage_message())
    assert "E-mail: não informado pelo Codex" in sent[0]["text"]
    assert "private@example.test" not in sent[0]["text"]


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
    assert "Conta Codex\n" in sent[0]["text"]


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
    assert "Informações da conta: não fornecidas pelo Codex." in sent[0]["text"]


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


def _reset_snapshot(*, count=2, details=True, used=100):
    import time
    timestamp = int(time.time())
    summary = {"availableCount": count}
    if details:
        summary["credits"] = [
            {
                "id": "credit-a", "status": "available", "resetType": "codexRateLimits",
                "grantedAt": timestamp - 86400, "expiresAt": timestamp + 3600,
                "title": "Renovação A", "description": "Renovação da franquia Codex.",
            },
            {
                "id": "credit-b", "status": "available", "resetType": "codexRateLimits",
                "grantedAt": timestamp - 86400, "expiresAt": None,
                "title": "Renovação B", "description": None,
            },
        ][:count]
    return {"accountId": "account-one", "ordinaryUsageAllowed": False,
            "rateLimits": {"primary": {"usedPercent": used, "windowDurationMins": 300,
                                        "resetsAt": timestamp + 3600},
                           "rateLimitReachedType": "rate_limit_reached" if used >= 100 else None},
            "rateLimitResetCredits": summary}


def _reset_selected_token(sent):
    import re
    match = re.search(r"/resetusage confirmar ([0-9a-f]{6})", sent[-1]["text"])
    assert match is not None
    return match.group(1)


def test_resetusage_owner_selects_specific_credit_and_confirms_only_after_preflight(usage_gateway):
    import uuid
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "account/rateLimits/read":
            return _reset_snapshot()
        assert method == "account/rateLimitResetCredit/consume"
        assert set(params) == {"idempotencyKey", "creditId"}
        assert params["creditId"] == "credit-b"
        uuid.UUID(params["idempotencyKey"])
        return {"outcome": "reset"}

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    assert [item[0] for item in calls] == ["account/rateLimits/read"]
    assert "Cupom 1" in sent[-1]["text"] and "Cupom 2" in sent[-1]["text"]
    assert "Vencimento:" in sent[-1]["text"] and "Renovação B" in sent[-1]["text"]
    assert "Seleção" not in sent[-1]["text"] or "selecionar" in sent[-1]["text"]
    assert "/resetusage confirmar" not in sent[-1]["text"]

    gateway._update(usage_message(text="/resetusage selecionar 2"))
    assert [item[0] for item in calls] == ["account/rateLimits/read"] * 2
    token = _reset_selected_token(sent)
    assert "Cupom 2 selecionado" in sent[-1]["text"]
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert [item[0] for item in calls] == [
        "account/rateLimits/read", "account/rateLimits/read",
        "account/rateLimits/read", "account/rateLimitResetCredit/consume",
        "account/rateLimits/read",
    ]
    assert "Renovação extra aplicada" in sent[-1]["text"]
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1


@pytest.mark.parametrize("changes", [
    {"from": {"id": 8}},
    {"chat": {"id": -100, "type": "supergroup"}},
    {"chat": {"id": 8, "type": "private"}},
])
def test_resetusage_rejected_outside_owner_private_chat(usage_gateway, changes):
    gateway, _client, sent, _deleted, calls = usage_gateway
    for command in ("/resetusage", "/resetusage selecionar 1", "/resetusage confirmar aabbcc"):
        gateway._update(usage_message(text=command, **changes))
    assert calls == [] and sent == []


def test_resetusage_does_not_execute_from_edited_telegram_message(usage_gateway):
    gateway, _client, sent, _deleted, calls = usage_gateway
    edited = usage_message(text="/resetusage confirmar abcdef")
    gateway._update({"edited_message": edited["message"]})
    assert calls == [] and not gateway._reset_usage_pending
    assert "mensagem nova" in sent[-1]["text"]


def test_resetusage_cannot_confirm_without_selection_or_after_expiration(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    client.request = lambda method, params, timeout: (
        calls.append((method, params, timeout)) or _reset_snapshot())
    gateway._update(usage_message(text="/resetusage confirmar abcdef"))
    assert calls == []
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage confirmar abcdef"))
    assert len(calls) == 1
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    token = _reset_selected_token(sent)
    gateway._update(usage_message(text="/resetusage confirmar 000000"))
    assert len(calls) == 2
    gateway._reset_usage_pending["9"]["expires_at"] = 0
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert len(calls) == 2
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    fresh = _reset_selected_token(sent)
    gateway._new_conversation("9")
    gateway._update(usage_message(text=f"/resetusage confirmar {fresh}"))
    assert len(calls) == 4


def test_resetusage_cancel_and_missing_credit_never_consume(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    client.request = lambda method, params, timeout: (
        calls.append((method, params, timeout)) or _reset_snapshot(count=0))
    gateway._update(usage_message(text="/resetusage"))
    assert "não informou renovações" in sent[-1]["text"]
    assert len(calls) == 1 and not gateway._reset_usage_pending
    gateway._update(usage_message(text="/resetusage cancelar"))
    assert not gateway._reset_usage_pending and len(calls) == 1


@pytest.mark.parametrize("used", [0, 25, 99])
def test_resetusage_never_offers_confirmation_when_quota_is_not_exhausted(usage_gateway, used):
    gateway, client, sent, _deleted, calls = usage_gateway
    client.request = lambda method, params, timeout: (
        calls.append((method, params, timeout)) or _reset_snapshot(used=used))
    gateway._update(usage_message(text="/resetusage"))
    assert "Nenhuma confirmação será preparada" in sent[-1]["text"]
    assert not gateway._reset_usage_pending
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    assert len(calls) == 1


def test_resetusage_count_only_requires_explicit_automatic_selection(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "account/rateLimits/read":
            return _reset_snapshot(count=2, details=False)
        assert method == "account/rateLimitResetCredit/consume"
        assert set(params) == {"idempotencyKey"}
        return {"outcome": "reset"}

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    assert "vencimentos individuais indisponíveis" in sent[-1]["text"]
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    assert len(calls) == 1
    gateway._update(usage_message(text="/resetusage selecionar automatico"))
    token = _reset_selected_token(sent)
    assert len(calls) == 2
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1


def test_resetusage_revalidates_selected_credit_and_aborts_on_change(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    seen = 0

    def request(method, params, timeout):
        nonlocal seen
        calls.append((method, params, timeout))
        assert method == "account/rateLimits/read"
        seen += 1
        snapshot = _reset_snapshot()
        if seen == 2:
            snapshot["rateLimitResetCredits"]["credits"][0]["status"] = "redeemed"
        return snapshot

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    assert "venceu ou não está disponível" in sent[-1]["text"]
    assert not any(item[0].endswith("/consume") for item in calls)


def test_resetusage_second_preflight_prevents_unnecessary_consumption(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    seen = 0

    def request(method, params, timeout):
        nonlocal seen
        calls.append((method, params, timeout))
        assert method == "account/rateLimits/read"
        seen += 1
        return _reset_snapshot(used=20 if seen >= 3 else 100)

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    token = _reset_selected_token(sent)
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert "Nenhum cupom foi consumido" in sent[-1]["text"]
    assert len(calls) == 3


def test_resetusage_account_change_prevents_consumption(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway
    seen = 0

    def request(method, params, timeout):
        nonlocal seen
        calls.append((method, params, timeout))
        assert method == "account/rateLimits/read"
        seen += 1
        result = _reset_snapshot()
        if seen == 3:
            result["accountId"] = "another-account"
        return result

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    token = _reset_selected_token(sent)
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert "conta Codex mudou" in sent[-1]["text"]
    assert len(calls) == 3


@pytest.mark.parametrize("outcome, expected", [
    ("alreadyRedeemed", "Renovação extra aplicada"),
    ("noCredit", "não há renovações"),
    ("nothingToReset", "nenhuma janela está elegível"),
    ("unexpected", "não confirmou o resgate"),
])
def test_resetusage_handles_service_outcomes_without_retry(usage_gateway, outcome, expected):
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        return (_reset_snapshot() if method == "account/rateLimits/read" else {"outcome": outcome})

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    token = _reset_selected_token(sent)
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert expected in sent[-1]["text"]
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1


def test_resetusage_unknown_consume_failure_is_not_retried(usage_gateway):
    gateway, client, sent, _deleted, calls = usage_gateway

    def request(method, params, timeout):
        calls.append((method, params, timeout))
        if method == "account/rateLimitResetCredit/consume":
            raise CodexProtocolError("private@example.test token=private")
        return _reset_snapshot()

    client.request = request
    gateway._update(usage_message(text="/resetusage"))
    gateway._update(usage_message(text="/resetusage selecionar 1"))
    token = _reset_selected_token(sent)
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert "incerto" in sent[-1]["text"] and "private" not in sent[-1]["text"]
    gateway._update(usage_message(text=f"/resetusage confirmar {token}"))
    assert len([item for item in calls if item[0].endswith("/consume")]) == 1
