"""Contrato versionado do ingresso privado de eventos do agente."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from importlib.resources import files
import secrets
import time
from typing import Any
import uuid

from jsonschema import Draft202012Validator, FormatChecker


class EventContractError(ValueError):
    """Indica rejeição estável antes da persistência ou execução do evento."""

    def __init__(self, code: str, message: str, details: list[dict[str, str]] | None = None):
        super().__init__(message)
        self.code, self.details = code, details or []


@dataclass(frozen=True)
class ResponseChannel:
    """Destino funcional validado para a resposta do evento."""

    type: str
    value: str
    account: str | None = None
    subject: str | None = None

    @property
    def destination_key(self) -> str:
        """Retorna a chave canônica usada para exclusão mútua do destino."""
        return json.dumps([self.type, self.account, self.value], ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class AgentEvent:
    """Evento v1 validado, sem coerção nem identidade adicional."""

    schema_version: int
    event_id: str
    prompt: str
    response_channel: ResponseChannel
    thread_policy: str

    def as_dict(self) -> dict[str, Any]:
        channel: dict[str, Any] = {"type": self.response_channel.type, "value": self.response_channel.value}
        if self.response_channel.account is not None:
            channel["account"] = self.response_channel.account
        if self.response_channel.subject is not None:
            channel["subject"] = self.response_channel.subject
        return {"schema_version": 1, "event_id": self.event_id, "prompt": self.prompt, "response_channel": channel, "thread_policy": self.thread_policy}


_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("uuidv7")
def _is_uuidv7(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        return False
    return parsed.version == 7 and str(parsed) == value.casefold()


class EventContract:
    """Carrega e aplica o schema v1 distribuído com o daemon."""

    def __init__(self) -> None:
        schema = json.loads(files("onmyoji_daemon.agent_notification.schemas").joinpath("event-v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema, format_checker=_FORMAT_CHECKER)

    def parse_json(self, raw: bytes | str) -> AgentEvent:
        """Decodifica JSON UTF-8 e valida integralmente o envelope v1."""
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            value = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise EventContractError("invalid_json", "O corpo deve ser um objeto JSON UTF-8 válido.") from error
        return self.validate(value)

    def validate(self, value: object) -> AgentEvent:
        """Valida tipos, campos fechados e restrições sem coerção."""
        if not isinstance(value, dict):
            raise EventContractError("invalid_json", "O corpo JSON deve ser um objeto.")
        if "schema_version" not in value:
            raise EventContractError("schema_version_required", "schema_version é obrigatório.")
        if type(value.get("schema_version")) is not int or value.get("schema_version") != 1:
            raise EventContractError("unsupported_schema_version", "schema_version deve ser o inteiro 1.")
        errors = sorted(self.validator.iter_errors(value), key=lambda error: list(error.absolute_path))
        if errors:
            details = [{"path": "/" + "/".join(str(part) for part in error.absolute_path), "message": error.message} for error in errors]
            code = "invalid_event_id" if any(list(error.absolute_path) == ["event_id"] for error in errors) else "event_validation_failed"
            raise EventContractError(code, "O evento não corresponde ao schema v1.", details)
        prompt = value["prompt"]
        channel = value["response_channel"]
        if not prompt.strip():
            raise EventContractError("event_validation_failed", "prompt deve conter texto não vazio.", [{"path": "/prompt", "message": "texto vazio"}])
        for field in ("value", "account", "subject"):
            if field in channel and not channel[field].strip():
                raise EventContractError("event_validation_failed", f"response_channel.{field} deve conter texto não vazio.", [{"path": f"/response_channel/{field}", "message": "texto vazio"}])
        response = ResponseChannel(channel["type"], channel["value"], channel.get("account"), channel.get("subject"))
        return AgentEvent(1, value["event_id"], prompt, response, value["thread_policy"])


def received_at(now: datetime | None = None) -> str:
    """Produz timestamp RFC 3339 UTC com precisão fixa de milissegundos."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return current.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_uuid7(timestamp_ms: int | None = None) -> str:
    """Gera UUIDv7 conforme RFC 9562 para ocorrências internas do agendador."""
    milliseconds = int(time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms)
    if not 0 <= milliseconds < 1 << 48:
        raise ValueError("timestamp_ms deve caber em 48 bits.")
    random_a = secrets.randbits(12)
    random_b = secrets.randbits(62)
    integer = (milliseconds << 80) | (7 << 76) | (random_a << 64) | (2 << 62) | random_b
    return str(uuid.UUID(int=integer))
