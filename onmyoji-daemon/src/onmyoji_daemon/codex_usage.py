"""Apresentação restrita dos limites de conta retornados pelo Codex App Server.

Não estima franquia a partir de tokens nem transforma campos ausentes em zero.
O contrato de entrada é ``account/rateLimits/read``; somente campos conhecidos
participam da resposta pública.
"""
from __future__ import annotations

from datetime import datetime, timezone as datetime_timezone, tzinfo
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Any


_PLANS = {
    "free": "Free", "go": "Go", "plus": "Plus", "pro": "Pro",
    "prolite": "Pro Lite", "promax": "Pro Max", "team": "Team",
    "self_serve_business_prolite": "Business Pro Lite",
    "self_serve_business_usage_based": "Business por uso", "business": "Business",
    "ent26": "Enterprise", "enterprise_cbp_automation": "Enterprise Automation",
    "enterprise_cbp_usage_based": "Enterprise por uso", "enterprise": "Enterprise",
    "edu": "Edu", "edu_plus": "Edu Plus", "edu_pro": "Edu Pro",
    "sci": "Sci", "sci_plus": "Sci Plus",
}
_LIMIT_STATES = {
    "rate_limit_reached": "Limite de uso atingido.",
    "workspace_owner_credits_depleted": "Créditos do workspace esgotados.",
    "workspace_member_credits_depleted": "Créditos do membro esgotados.",
    "workspace_owner_usage_limit_reached": "Limite de uso do workspace atingido.",
    "workspace_member_usage_limit_reached": "Limite de uso do membro atingido.",
}
_BUCKET_FIELDS = frozenset({"primary", "secondary", "credits", "planType", "rateLimitReachedType", "spendControlReached"})
_BALANCE = re.compile(r"[0-9]{1,24}(?:\.[0-9]{1,16})?\Z")


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _number_text(value: int | float) -> str:
    return format(value, ".12g").replace(".", ",")


def _label(value: Any) -> str | None:
    """Aceita nomes curtos, sem controles, marcação ou aparência de segredo."""
    if not isinstance(value, str) or not value.strip() or len(value) > 72:
        return None
    value = value.strip()
    if not all(char.isalnum() or char in " _-().+/" for char in value):
        return None
    if re.search(r"(?i)(?:sk-|token|password|secret|api[_-]?key)|[A-Za-z0-9]{33,}", value):
        return None
    return "Codex" if value.casefold() == "codex" else value


def _duration(value: Any) -> str:
    minutes = _number(value)
    if minutes is None or minutes == 0:
        return "duração não informada"
    if minutes % 1440 == 0:
        count, unit = minutes / 1440, "dia"
    elif minutes % 60 == 0:
        count, unit = minutes / 60, "hora"
    else:
        count, unit = minutes, "minuto"
    return f"{_number_text(count)} {unit}{'' if count == 1 else 's'}"


def _reset(value: Any, zone: tzinfo | None) -> str:
    timestamp = _number(value)
    if timestamp is None:
        return "não informada"
    try:
        instant = datetime.fromtimestamp(timestamp, datetime_timezone.utc).astimezone(zone)
        offset = instant.strftime("%z")
        return f"{instant:%d/%m/%Y %H:%M} (UTC{offset[:3]}:{offset[3:5]})"
    except (OverflowError, OSError, ValueError):
        return "não informada"


def _window(name: str, value: Any, zone: tzinfo | None) -> list[str]:
    if not isinstance(value, dict):
        return [f"{name}: consumo e franquia não informados."]
    used = _number(value.get("usedPercent"))
    heading = f"{name} ({_duration(value.get('windowDurationMins'))})"
    if used is None:
        summary = "consumo e franquia não informados"
    else:
        remaining = max(0, 100 - used)
        summary = f"{_number_text(used)}% consumido; {_number_text(remaining)}% disponível"
    return [f"{heading}: {summary}.", f"Renovação: {_reset(value.get('resetsAt'), zone)}."]


def _credits(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    has_credits, unlimited = value.get("hasCredits"), value.get("unlimited")
    if not isinstance(has_credits, bool) or not isinstance(unlimited, bool):
        return None
    if unlimited:
        return "Créditos adicionais: ilimitados."
    balance = value.get("balance")
    if isinstance(balance, str) and _BALANCE.fullmatch(balance):
        try:
            amount = Decimal(balance)
        except InvalidOperation:
            amount = None
        if amount is not None and (has_credits or amount == 0):
            text = format(amount, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            return f"Saldo de créditos adicionais: {text.replace('.', ',')}."
    if has_credits:
        return "Créditos adicionais disponíveis; saldo não informado."
    return "Créditos adicionais: não disponíveis."


def _buckets(response: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    multiple = response.get("rateLimitsByLimitId")
    if isinstance(multiple, dict):
        buckets = [(key, value) for key, value in multiple.items()
                   if isinstance(value, dict) and _BUCKET_FIELDS.intersection(value)]
        if buckets:
            return buckets
    legacy = response.get("rateLimits")
    return [(None, legacy)] if isinstance(legacy, dict) and _BUCKET_FIELDS.intersection(legacy) else []


def format_rate_limits(response: dict[str, Any], *, timezone: tzinfo | None = None) -> str:
    """Formata limites atuais; ``timezone=None`` usa o fuso local do host.

    A visão por bucket tem prioridade sobre a visão legada. Percentuais acima
    de 100 são preservados como consumo, com disponibilidade mínima de zero.
    Campos nulos, tipos inválidos e números não finitos não representam saldo.
    """
    lines = ["Consumo e franquia do Codex"]
    response = response if isinstance(response, dict) else {}
    if response.get("ordinaryUsageAllowed") is False:
        lines.append("Uso incluído bloqueado pelo Codex. Percentuais restantes e horários de renovação não confirmam sua liberação.")
    elif "ordinaryUsageAllowed" in response and response["ordinaryUsageAllowed"] is not True:
        lines.append("Permissão para uso incluído: não informada pelo Codex.")
    buckets = _buckets(response)
    if not buckets:
        lines.append("O Codex não informou dados de consumo ou franquia.")
    for index, (key, bucket) in enumerate(buckets, 1):
        name = _label(bucket.get("limitName")) or _label(bucket.get("limitId")) or _label(key) or f"Limite {index}"
        lines.extend(["", name])
        plan = bucket.get("planType")
        if isinstance(plan, str) and plan in _PLANS:
            lines.append(f"Plano: {_PLANS[plan]}.")
        windows = [("Janela principal", bucket.get("primary")), ("Janela secundária", bucket.get("secondary"))]
        reported = False
        for title, window in windows:
            if window is not None:
                lines.extend(_window(title, window, timezone))
                reported = True
        if not reported:
            lines.append("Consumo e franquia: não informados pelo Codex.")
        credit_text = _credits(bucket.get("credits"))
        if credit_text:
            lines.append(credit_text)
        state = bucket.get("rateLimitReachedType")
        if isinstance(state, str) and state in _LIMIT_STATES:
            lines.append(_LIMIT_STATES[state])
        if bucket.get("spendControlReached") is True:
            lines.append("Limite de gastos atingido. O percentual restante da janela não elimina esse bloqueio.")
    resets = response.get("rateLimitResetCredits")
    if isinstance(resets, dict):
        count = resets.get("availableCount")
        if type(count) is int and 0 <= count <= 1_000_000:
            lines.extend(["", f"Renovações extras de franquia disponíveis: {count}."])
    return "\n".join(lines)
