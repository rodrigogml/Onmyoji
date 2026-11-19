"""Regras locais de apresentação e seleção segura de créditos de reset Codex."""
from datetime import datetime, timedelta, timezone

import pytest

from onmyoji_daemon.codex_reset import (
    available_reset_credits, format_reset_credits, reset_credit_count, reset_needed,
)


BRAZIL = timezone(timedelta(hours=-3))
NOW = int(datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc).timestamp())


def sample(used=100, count=2):
    return {"ordinaryUsageAllowed": False,
            "rateLimits": {"primary": {"usedPercent": used, "resetsAt": NOW + 3600}},
            "rateLimitResetCredits": {"availableCount": count, "credits": [
                {"id": "private-1", "title": "Reset de franquia",
                 "description": "Renova a franquia Codex.", "status": "available",
                 "resetType": "codexRateLimits", "grantedAt": NOW - 86400, "expiresAt": NOW + 7200},
                {"id": "private-2", "status": "available", "resetType": "codexRateLimits",
                 "grantedAt": NOW - 86400, "expiresAt": None},
            ]}}


def test_details_count_expiry_and_privacy():
    limits = sample()
    text = format_reset_credits(limits, timezone=BRAZIL, now=NOW)
    assert "Renovações extras de franquia disponíveis: 2." in text
    assert "Cupom 1" in text and "Cupom 2" in text
    assert "19/11/2026 11:00 (UTC-03:00) — vence hoje" in text
    assert "Vencimento: não expira." in text
    assert "private-1" not in text and "private-2" not in text
    assert available_reset_credits(limits, now=NOW) == {1: "private-1", 2: "private-2"}


def test_unsafe_backend_text_is_not_exposed():
    limits = sample()
    limits["rateLimitResetCredits"]["credits"][0]["title"] = "sk-secret"
    limits["rateLimitResetCredits"]["credits"][0]["description"] = "private@example.test"
    text = format_reset_credits(limits, now=NOW)
    assert "sk-secret" not in text and "private@example" not in text


def test_expired_or_unsupported_credits_are_not_selectable():
    limits = sample()
    limits["rateLimitResetCredits"]["credits"][0]["expiresAt"] = NOW
    limits["rateLimitResetCredits"]["credits"][1]["resetType"] = "unknown"
    assert available_reset_credits(limits, now=NOW) == {}


def test_authoritative_count_and_optional_credit_details():
    limits = sample()
    limits["rateLimitResetCredits"]["availableCount"] = 9
    assert "contagem disponível" in format_reset_credits(limits, now=NOW)
    limits["rateLimitResetCredits"]["credits"] = None
    assert "vencimentos individuais indisponíveis" in format_reset_credits(limits)
    assert available_reset_credits(limits, now=NOW) == {}
    assert reset_credit_count({"rateLimitResetCredits": {"availableCount": True}}) is None
    inconsistent = sample()
    inconsistent["rateLimitResetCredits"]["availableCount"] = 1
    assert available_reset_credits(inconsistent, now=NOW) == {}


@pytest.mark.parametrize("limits,expected", [
    ({"ordinaryUsageAllowed": True,
      "rateLimits": {"primary": {"usedPercent": 100, "resetsAt": NOW + 10}}}, False),
    ({"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": NOW + 10}}}, True),
    ({"rateLimits": {"primary": {"usedPercent": 20}}}, False),
    ({"rateLimits": {"rateLimitReachedType": "rate_limit_reached"}}, True),
    ({"rateLimits": {"rateLimitReachedType": "workspace_owner_credits_depleted"}}, False),
    ({"rateLimits": {"spendControlReached": True, "rateLimitReachedType": "rate_limit_reached"}}, False),
    ({"rateLimits": {"primary": {"usedPercent": 100, "resetsAt": NOW - 10}}}, False),
    ({"rateLimits": {"rateLimitReachedType": ["invalid"]}}, False),
    (None, False),
])
def test_reset_needed_only_when_exhaustion_confirmed(limits, expected):
    assert reset_needed(limits, now=NOW)[0] is expected


def test_malformed_state_and_opaque_identifier_in_title():
    limits = sample()
    limits["rateLimits"]["rateLimitReachedType"] = ["unexpected"]
    assert reset_needed(limits, now=NOW)[0] is True
    limits["rateLimitResetCredits"]["credits"][0]["title"] = "private-1"
    limits["rateLimitResetCredits"]["credits"][0]["description"] = "private-1"
    assert "private-1" not in format_reset_credits(limits, now=NOW)
