"""Materializa somente os agendamentos reservados da checagem de e-mail."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

from ...config_publication import publish_instance_file
from .contracts import EmailError, require


MANAGED_BY = "onmyoji.email-monitor"
SCHEDULE_PREFIX = "onmyoji-email-"


def interval_seconds(value: object) -> int:
    require(isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,6}[MH]", value) is not None,
            "invalid_interval", "Intervalo deve ser um inteiro seguido de M ou H.")
    amount = int(value[:-1])
    seconds = amount * (60 if value[-1] == "M" else 3600)
    require(seconds <= 31_536_000, "invalid_interval", "Intervalo não pode exceder um ano.")
    return seconds


def schedule_id(account: str) -> str:
    return SCHEDULE_PREFIX + account


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def prepare_monitoring(previous: dict, candidate: dict, now: datetime | None = None) -> dict:
    """Normaliza a âncora sem mudar a ativação durante simples edições."""
    result = deepcopy(candidate)
    stamp = _timestamp(now or datetime.now(timezone.utc))
    old_accounts = previous.get("accounts", {}) if isinstance(previous, dict) else {}
    for name, account in result.get("accounts", {}).items():
        monitoring = account.get("monitoring")
        if not isinstance(monitoring, dict):
            continue
        old = old_accounts.get(name, {}).get("monitoring", {})
        if monitoring.get("enabled"):
            if old.get("enabled") and old.get("activated_at"):
                monitoring["activated_at"] = old["activated_at"]
            else:
                monitoring["activated_at"] = stamp
        else:
            monitoring.pop("activated_at", None)
    return result


def _owned(path: Path, account: str) -> bool:
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return False
    action = value.get("action", {})
    return value.get("managed_by") == MANAGED_BY and action.get("type") == "email_check" and action.get("account") == account


def _active(document: dict) -> dict[str, dict]:
    return {name: account["monitoring"] for name, account in document.get("accounts", {}).items()
            if isinstance(account.get("monitoring"), dict) and account["monitoring"].get("enabled")}


def preflight_email_schedules(root: Path, document: dict) -> None:
    directory = root / "configs" / "daemon" / "services" / "task-scheduler" / "schedules"
    for account in _active(document):
        path = directory / (schedule_id(account) + ".toml")
        if path.exists() and not _owned(path, account):
            raise EmailError("schedule_collision", f"O agendamento reservado de {account} já existe e não é gerenciado pelo Onmyōji.")


def _render(account: str, monitoring: dict) -> str:
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    return "\n".join((
        "schema_version = 1",
        "enabled = true",
        f"description = {quote('Checagem automática da conta de e-mail ' + account)}",
        f"created_at = {quote(monitoring['activated_at'])}",
        f"managed_by = {quote(MANAGED_BY)}",
        "",
        "[time]",
        'type = "interval"',
        f"anchor_at = {quote(monitoring['activated_at'])}",
        f"every_seconds = {interval_seconds(monitoring['interval'])}",
        "",
        "[action]",
        'type = "email_check"',
        f"account = {quote(account)}",
        "timeout_seconds = 300",
        "",
    ))


def reconcile_email_schedules(root: Path, previous: dict, current: dict) -> None:
    directory = root / "configs" / "daemon" / "services" / "task-scheduler" / "schedules"
    directory.mkdir(parents=True, exist_ok=True)
    preflight_email_schedules(root, current)
    active = _active(current)
    old_names = set(previous.get("accounts", {})) if isinstance(previous, dict) else set()
    for account, monitoring in active.items():
        target = directory / (schedule_id(account) + ".toml")
        staging = root / "tmp" / "email-schedules"
        staging.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=staging, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(_render(account, monitoring))
            tomllib.loads(Path(temporary).read_text(encoding="utf-8"))
            publish_instance_file(root, Path(temporary), target)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise
    for account in old_names - set(active):
        path = directory / (schedule_id(account) + ".toml")
        if path.exists() and _owned(path, account):
            path.unlink()
