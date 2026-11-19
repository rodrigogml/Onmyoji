"""Leitura prudente de cupons de renovação de franquia do Codex.

IDs opacos nunca entram no texto exibido. Uma consulta não consome créditos.
"""
from __future__ import annotations

from datetime import datetime, timezone as utc_timezone, tzinfo
import re
import time
from typing import Any, Mapping


MAX_VISIBLE_CREDITS = 20
_CREDIT_STATUS = {
    "available": "Disponível",
    "redeeming": "Em processamento",
    "redeemed": "Já resgatado",
    "unknown": "Desconhecido",
}
_CREDIT_TYPE = {"codexRateLimits": "Renovação de franquia Codex", "unknown": "Desconhecido"}
_NON_RATE_LIMIT_STATES = frozenset({
    "workspace_owner_credits_depleted", "workspace_member_credits_depleted",
    "workspace_owner_usage_limit_reached", "workspace_member_usage_limit_reached",
})


def reset_credit_count(limits: Any) -> int | None:
    summary = limits.get("rateLimitResetCredits") if isinstance(limits, Mapping) else None
    count = summary.get("availableCount") if isinstance(summary, Mapping) else None
    return count if type(count) is int and 0 <= count <= 1_000_000 else None


def reset_credit_rows(limits: Any) -> list[Any] | None:
    summary = limits.get("rateLimitResetCredits") if isinstance(limits, Mapping) else None
    rows = summary.get("credits") if isinstance(summary, Mapping) else None
    return rows if isinstance(rows, list) else None


def available_reset_credits(limits: Any, *, now: float | None = None) -> dict[int, str]:
    """Associa a posição exibida ao ID opaco, só para créditos utilizáveis."""
    if reset_credit_count(limits) in (None, 0):
        return {}
    rows = reset_credit_rows(limits)
    if rows is None:
        return {}
    now = time.time() if now is None else now
    result: dict[int, str] = {}
    seen: set[str] = set()
    for index, row in enumerate(rows[:MAX_VISIBLE_CREDITS], 1):
        if not isinstance(row, Mapping) or row.get("status") != "available" or row.get("resetType") != "codexRateLimits":
            continue
        identifier = row.get("id")
        grant = row.get("grantedAt")
        if (not isinstance(identifier, str) or not identifier or len(identifier) > 512
                or identifier in seen or type(grant) is not int or grant < 0):
            continue
        expires = row.get("expiresAt", object())
        if expires is not None and (type(expires) is not int or expires <= now):
            continue
        seen.add(identifier)
        result[index] = identifier
    count = reset_credit_count(limits)
    return result if count is not None and len(result) <= count else {}


def _buckets(limits: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    multiple = limits.get("rateLimitsByLimitId")
    if isinstance(multiple, dict):
        found = [bucket for bucket in multiple.values() if isinstance(bucket, dict)
                 and any(key in bucket for key in ("primary", "secondary", "rateLimitReachedType", "spendControlReached"))]
        if found:
            return found
    legacy = limits.get("rateLimits")
    return [legacy] if isinstance(legacy, dict) else []


def reset_needed(limits: Any, *, now: float | None = None) -> tuple[bool, str]:
    """Só libera resgate com evidência atual de esgotamento da franquia."""
    if not isinstance(limits, Mapping):
        return False, "O Codex não forneceu um retrato válido da franquia."
    if limits.get("ordinaryUsageAllowed") is True:
        return False, "O Codex ainda permite o uso incluído; não há necessidade confirmada de renovar."
    buckets = _buckets(limits)
    if any(bucket.get("spendControlReached") is True or
           (isinstance(bucket.get("rateLimitReachedType"), str) and
            bucket["rateLimitReachedType"] in _NON_RATE_LIMIT_STATES)
           for bucket in buckets):
        return False, "Há um bloqueio de gastos ou créditos que uma renovação de franquia pode não resolver."
    if any(bucket.get("rateLimitReachedType") == "rate_limit_reached" for bucket in buckets):
        return True, "O Codex informou que o limite de franquia foi atingido."
    now = time.time() if now is None else now
    for bucket in buckets:
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if not isinstance(window, Mapping):
                continue
            used, renewal = window.get("usedPercent"), window.get("resetsAt")
            if type(used) in (int, float) and 100 <= used <= 1_000 and type(renewal) is int and renewal > now:
                return True, "Uma janela está 100% consumida e sua renovação normal ainda não ocorreu."
    return False, "Não há esgotamento atual de franquia comprovado; nenhum cupom será preparado."


def _backend_text(value: Any, max_length: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > max_length:
        return None
    if not all(ch.isalnum() or ch in " _-().+,;:!?/%" for ch in text):
        return None
    if re.search(r"(?i)sk-|token|password|secret|api[_ -]?key|bearer|authorization|[A-Za-z0-9]{33,}", text):
        return None
    return text


def _date(value: Any, zone: tzinfo | None, *, now: float, expires: bool = False) -> str:
    if value is None and expires:
        return "não expira"
    if type(value) is not int or value < 0:
        return "não informada"
    try:
        instant = datetime.fromtimestamp(value, utc_timezone.utc).astimezone(zone)
        current = datetime.fromtimestamp(now, utc_timezone.utc).astimezone(zone)
        suffix = instant.strftime("%z")
        result = f"{instant:%d/%m/%Y %H:%M} (UTC{suffix[:3]}:{suffix[3:]})"
        if expires and instant.date() == current.date():
            result += " — vence hoje"
        if expires and value <= now:
            result += " — vencido"
        return result
    except (ValueError, OSError, OverflowError):
        return "não informada"


def format_reset_credits(limits: Any, *, timezone: tzinfo | None = None, now: float | None = None) -> str:
    count = reset_credit_count(limits)
    if count is None:
        return ""
    text = [f"Renovações extras de franquia disponíveis: {count}."]
    rows = reset_credit_rows(limits)
    if rows is None:
        text.append("O Codex informou apenas a quantidade: cupons e vencimentos individuais indisponíveis.")
        return "\n".join(text)
    if not rows:
        text.append("Nenhum detalhe individual retornado; a contagem continua sendo a referência.")
        return "\n".join(text)
    now = time.time() if now is None else now
    text.append(f"Cupons detalhados: {min(len(rows), MAX_VISIBLE_CREDITS)} de {len(rows)} registros retornados.")
    for index, row in enumerate(rows[:MAX_VISIBLE_CREDITS], 1):
        if not isinstance(row, Mapping):
            text.append(f"Cupom {index}: detalhes inválidos.")
            continue
        identifier = row.get("id") if isinstance(row.get("id"), str) else None
        name = _backend_text(row.get("title"), 100) or "Renovação extra"
        if identifier and identifier in name:
            name = "Renovação extra"
        status = _CREDIT_STATUS.get(row.get("status"), "Não informado") if isinstance(row.get("status"), str) else "Não informado"
        kind = _CREDIT_TYPE.get(row.get("resetType"), "Não informado") if isinstance(row.get("resetType"), str) else "Não informado"
        grant = _date(row.get("grantedAt"), timezone, now=now)
        expiry = _date(row.get("expiresAt", object()), timezone, now=now, expires=True)
        text.append(f"Cupom {index} — {name}\nSituação: {status}; Tipo: {kind}.\nConcedido em: {grant}.\nVencimento: {expiry}.")
        description = _backend_text(row.get("description"), 160)
        if description and not (identifier and identifier in description):
            text.append(f"Descrição: {description}.")
    if len(rows) > MAX_VISIBLE_CREDITS:
        text.append(f"Há {len(rows) - MAX_VISIBLE_CREDITS} registros além do limite de exibição; nenhum deles será selecionado automaticamente.")
    if len(rows) < count:
        text.append("O serviço pode omitir parte dos cupons; a contagem disponível é a referência.")
    return "\n".join(text)
