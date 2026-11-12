from datetime import datetime, timedelta, timezone

import pytest

from onmyoji_daemon.codex_usage import format_rate_limits


RESET = int(datetime(2026, 11, 11, 18, 30, tzinfo=timezone.utc).timestamp())
BRAZIL = timezone(timedelta(hours=-3))


def window(used=25, minutes=300, reset=RESET):
    return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": reset}


def test_legacy_reports_consumption_remaining_windows_plan_and_local_reset():
    result = format_rate_limits({"rateLimits": {"limitId": "codex", "planType": "pro",
        "primary": window(), "secondary": window(62.5, 10080)}}, timezone=BRAZIL)

    assert result.startswith("Consumo e franquia do Codex\n\nCodex\nPlano: Pro.")
    assert "Janela principal (5 horas): 25% consumido; 75% disponível." in result
    assert "Janela secundária (7 dias): 62,5% consumido; 37,5% disponível." in result
    assert "Renovação: 11/11/2026 15:30 (UTC-03:00)." in result


def test_multiple_buckets_supersede_legacy_and_keep_distinct_dynamic_windows():
    result = format_rate_limits({"rateLimits": {"limitName": "stale", "primary": window(99)},
        "rateLimitsByLimitId": {
            "codex": {"primary": window(0, 15)},
            "codex_other": {"limitName": "Outro modelo", "primary": window(100, 60)},
        }}, timezone=timezone.utc)

    assert "stale" not in result and "99%" not in result
    assert "Codex\nJanela principal (15 minutos): 0% consumido; 100% disponível." in result
    assert "Outro modelo\nJanela principal (1 hora): 100% consumido; 0% disponível." in result
    assert "UTC+00:00" in result


@pytest.mark.parametrize("modern", [None, {}, [], {"codex": None}, {"codex": {"unexpected": "secret"}}])
def test_invalid_or_empty_modern_view_falls_back_to_legacy(modern):
    result = format_rate_limits({"rateLimitsByLimitId": modern, "rateLimits": {"primary": window(10)}})
    assert "10% consumido; 90% disponível" in result


@pytest.mark.parametrize("payload", [{}, {"rateLimits": None}, {"rateLimits": []}, None,
    {"rateLimits": {"primary": None, "secondary": None}}])
def test_missing_data_never_claims_zero_usage_or_full_allowance(payload):
    result = format_rate_limits(payload)
    assert "não inform" in result
    assert "%" not in result


@pytest.mark.parametrize("used", [None, True, "25", -1, float("nan"), float("inf"), 10 ** 500])
def test_malformed_percent_is_not_available_allowance(used):
    result = format_rate_limits({"rateLimits": {"primary": window(used, None, None)}})
    assert "consumo e franquia não informados" in result
    assert "duração não informada" in result and "Renovação: não informada." in result
    assert "%" not in result


def test_overage_preserves_usage_without_negative_remaining():
    result = format_rate_limits({"rateLimits": {"primary": window(120)}})
    assert "120% consumido; 0% disponível" in result


@pytest.mark.parametrize("reset", [None, True, "123", -1, float("nan"), 10 ** 100])
def test_unknown_or_unrepresentable_reset_does_not_invent_time(reset):
    result = format_rate_limits({"rateLimits": {"primary": window(reset=reset)}})
    assert "Renovação: não informada." in result


def test_zero_timestamp_is_valid_and_timezone_can_have_half_hour_offset():
    result = format_rate_limits({"rateLimits": {"primary": window(0, 1, 0)}},
        timezone=timezone(timedelta(hours=5, minutes=30)))
    assert "Janela principal (1 minuto)" in result
    assert "01/01/1970 05:30 (UTC+05:30)" in result


@pytest.mark.parametrize("credits, expected", [
    ({"hasCredits": True, "unlimited": False, "balance": "12.5000"}, "Saldo de créditos adicionais: 12,5."),
    ({"hasCredits": False, "unlimited": False, "balance": "0"}, "Saldo de créditos adicionais: 0."),
    ({"hasCredits": True, "unlimited": True, "balance": None}, "Créditos adicionais: ilimitados."),
    ({"hasCredits": True, "unlimited": False, "balance": None}, "Créditos adicionais disponíveis; saldo não informado."),
    ({"hasCredits": False, "unlimited": False, "balance": None}, "Créditos adicionais: não disponíveis."),
])
def test_credit_snapshot_respects_reported_balance_and_flags(credits, expected):
    result = format_rate_limits({"rateLimits": {"credits": credits}})
    assert expected in result
    assert "%" not in result and "R$" not in result and "USD" not in result


@pytest.mark.parametrize("balance", ["NaN", "Infinity", "-1", "api_key=secret", 12.5, True])
def test_invalid_credit_balance_is_not_forwarded_or_interpreted(balance):
    result = format_rate_limits({"rateLimits": {"credits": {
        "hasCredits": True, "unlimited": False, "balance": balance}}})
    assert "saldo não informado" in result
    assert "Saldo de créditos adicionais:" not in result
    assert "api_key" not in result


def test_unrecognized_fields_and_unsafe_labels_are_never_exposed():
    result = format_rate_limits({"token": "secret-value", "error": "private-diagnostic",
        "rateLimitsByLimitId": {"sk-do-not-expose": {
            "limitName": "<script>private-diagnostic</script>", "limitId": "api_key=secret",
            "planType": "secret-value", "primary": [], "credits": {
                "hasCredits": "true", "unlimited": "false", "balance": "900"},
            "rateLimitReachedType": "private-diagnostic"}}})
    assert "Limite 1" in result and "consumo e franquia não informados" in result
    for private in ("secret", "private-diagnostic", "script", "sk-do-not-expose", "900"):
        assert private not in result


def test_reset_credit_count_is_separate_from_remaining_allowance_and_uses_authoritative_count():
    result = format_rate_limits({"rateLimits": {"primary": window(100),
        "rateLimitReachedType": "rate_limit_reached"}, "rateLimitResetCredits": {
        "availableCount": 2, "credits": [{"id": "not-for-display"}]}})
    assert "100% consumido; 0% disponível" in result
    assert "Limite de uso atingido." in result
    assert "Renovações extras de franquia disponíveis: 2." in result
    assert "not-for-display" not in result


def test_boolean_reset_count_is_not_a_number():
    result = format_rate_limits({"rateLimitResetCredits": {"availableCount": True}})
    assert "Renovações extras" not in result


def test_included_usage_block_is_preserved_despite_remaining_percent_and_past_reset():
    result = format_rate_limits({"ordinaryUsageAllowed": False,
        "rateLimits": {"primary": window(20, reset=0)}}, timezone=timezone.utc)
    assert "20% consumido; 80% disponível" in result
    assert "Uso incluído bloqueado pelo Codex." in result
    assert "Percentuais restantes e horários de renovação não confirmam sua liberação." in result
    assert result.index("Uso incluído bloqueado") < result.index("80% disponível")


@pytest.mark.parametrize("allowed", [None, "true", 1, 0])
def test_unknown_included_usage_permission_is_not_inferred_from_zero_usage(allowed):
    result = format_rate_limits({"ordinaryUsageAllowed": allowed,
        "rateLimits": {"primary": window(0)}})
    assert "100% disponível" in result
    assert "Permissão para uso incluído: não informada pelo Codex." in result
    assert "Uso incluído bloqueado" not in result


def test_spend_control_block_is_reported_even_when_windows_show_remaining_quota():
    result = format_rate_limits({"ordinaryUsageAllowed": True, "rateLimits": {
        "primary": window(25), "spendControlReached": True,
        "individualLimit": {"id": "private-identifier", "remaining": 0}}})
    assert "75% disponível" in result
    assert "Limite de gastos atingido." in result
    assert "O percentual restante da janela não elimina esse bloqueio." in result
    assert "private-identifier" not in result


def test_spend_control_only_bucket_keeps_block_without_inventing_allowance():
    result = format_rate_limits({"rateLimitsByLimitId": {"codex": {"spendControlReached": True}}})
    assert "Limite de gastos atingido." in result
    assert "Consumo e franquia: não informados pelo Codex." in result
    assert "%" not in result


@pytest.mark.parametrize("reached", [None, False, "true", 1])
def test_spend_control_state_requires_explicit_true_and_does_not_infer_recovery(reached):
    result = format_rate_limits({"rateLimits": {"primary": window(0), "spendControlReached": reached}})
    assert "Limite de gastos atingido" not in result
    assert "liberado" not in result and "restabelecido" not in result
