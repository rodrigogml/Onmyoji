"""Regras temporais ancoradas em UTC e cron civil com DST explícito."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from ..rpc import RpcError

UTC = timezone.utc


def instant(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, AttributeError, TypeError):
        raise RpcError("Informe instante ISO8601 com offset.", "invalid_schedule") from None


def iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z") if value else None


def validate_rule(rule: dict, now: datetime) -> dict:
    if not isinstance(rule, dict):
        raise RpcError("Regra temporal inválida.", "invalid_schedule")
    kind = rule.get("type")
    allowed = {"once": {"type", "at", "timezone"}, "interval": {"type", "anchor_at", "every_seconds", "timezone"}, "cron": {"type", "expression", "timezone"}}
    if not isinstance(kind, str) or kind not in allowed or set(rule) - allowed[kind]:
        raise RpcError("Tipo ou campos de regra inválidos.", "invalid_schedule")
    result = dict(rule)
    result["timezone"] = rule.get("timezone", "America/Sao_Paulo")
    try:
        ZoneInfo(result["timezone"])
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise RpcError("Timezone IANA inválido.", "invalid_schedule") from None
    if kind == "once":
        result["at"] = iso(instant(rule.get("at")))
    elif kind == "interval":
        seconds = rule.get("every_seconds")
        if type(seconds) is not int or not 1 <= seconds <= 2_147_483_647:
            raise RpcError("Intervalo deve ser inteiro de pelo menos um segundo.", "invalid_schedule")
        result["anchor_at"] = iso(instant(rule.get("anchor_at")))
    else:
        expression = rule.get("expression")
        if not isinstance(expression, str) or len(expression) > 256 or len(expression.split()) != 5 or any(re.fullmatch(r"[0-9*,/\-]+", part) is None for part in expression.split()) or not croniter.is_valid(expression):
            raise RpcError("Cron exige cinco campos numéricos, listas, intervalos ou passos.", "invalid_schedule")
        result["expression"] = " ".join(expression.split())
        if next_at(result, now) is None:
            raise RpcError("Cron sem ocorrência nos próximos dez anos.", "invalid_schedule")
    if kind != "cron":
        candidate = instant(result["at"]) if kind == "once" else next_at(result, now)
        try:
            horizon = now.replace(year=min(9999, now.year + 10))
        except ValueError:
            horizon = now.replace(year=min(9999, now.year + 10), day=28)
        if candidate is None or candidate > horizon:
            raise RpcError("Regra sem ocorrência nos próximos dez anos.", "invalid_schedule")
    return result


def _cron_at(rule: dict, reference: datetime, forward: bool) -> datetime | None:
    zone = ZoneInfo(rule["timezone"])
    local = reference.astimezone(zone).replace(tzinfo=None)
    civil = reference.astimezone(zone)
    if not forward and civil.fold:
        local += civil.replace(fold=0).utcoffset() - civil.utcoffset()
    iterator = croniter(rule["expression"], local, day_or=True, max_years_between_matches=10)
    year = max(1, min(9999, reference.year + (10 if forward else -10)))
    try:
        horizon = reference.replace(year=year)
    except ValueError:
        horizon = reference.replace(year=year, day=28)
    try:
        while True:
            wall = iterator.get_next(datetime) if forward else iterator.get_prev(datetime)
            civil = wall.replace(tzinfo=zone, fold=0)
            candidate = civil.astimezone(UTC)
            if (forward and candidate > horizon) or (not forward and candidate < horizon):
                return None
            if candidate.astimezone(zone).replace(tzinfo=None) != wall:
                continue
            if (forward and candidate > reference) or (not forward and candidate <= reference):
                return candidate
    except (ValueError, OverflowError):
        return None


def next_at(rule: dict, after: datetime) -> datetime | None:
    if rule["type"] == "once":
        at = instant(rule["at"])
        return at if at > after else None
    if rule["type"] == "interval":
        anchor = instant(rule["anchor_at"])
        index = max(0, math.floor((after - anchor).total_seconds() / rule["every_seconds"]) + 1)
        return anchor + timedelta(seconds=index * rule["every_seconds"])
    return _cron_at(rule, after, True)


def latest_due(rule: dict, now: datetime) -> datetime | None:
    if rule["type"] == "once":
        at = instant(rule["at"])
        return at if at <= now else None
    if rule["type"] == "interval":
        anchor = instant(rule["anchor_at"])
        index = math.floor((now - anchor).total_seconds() / rule["every_seconds"])
        return anchor + timedelta(seconds=index * rule["every_seconds"]) if index >= 0 else None
    return _cron_at(rule, now + timedelta(microseconds=1), False)


def initial_at(rule: dict, now: datetime) -> datetime | None:
    if rule["type"] == "once":
        return instant(rule["at"])
    return next_at(rule, now)
