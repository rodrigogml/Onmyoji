"""Schema distribuível e identidade UUIDv7, inclusive no Python 3.11."""
from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
import secrets
import time
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from ..rpc import RpcError


def uuid7() -> str:
    milliseconds = time.time_ns() // 1_000_000
    value = (milliseconds << 80) | (7 << 76) | (secrets.randbits(12) << 64) | (2 << 62) | secrets.randbits(62)
    return str(UUID(int=value))


def is_uuid7(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = UUID(value)
        return parsed.version == 7 and str(parsed) == value.lower()
    except ValueError:
        return False


CHECKER = FormatChecker()
CHECKER.checks("uuid7")(is_uuid7)
SCHEMA = json.loads(files(__package__).joinpath("schemas/event-v1.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=CHECKER)


def validate_event(value: object) -> dict:
    if isinstance(value, dict) and type(value.get("schema_version")) is not int:
        raise RpcError("Versão deve ser um inteiro.", "invalid_event", {"path": "/schema_version", "rule": "type"})
    errors = sorted(VALIDATOR.iter_errors(value), key=lambda error: tuple(str(part) for part in error.absolute_path))
    if errors:
        error = errors[0]
        parts = list(error.absolute_path)
        if error.validator == "required":
            parts.append(next(field for field in error.validator_value if field not in error.instance))
        path = "/" + "/".join(str(part) for part in parts)
        # jsonschema.message pode incluir valores do prompt: não o expor.
        messages = {"required": "Campo obrigatório ausente.", "type": "Tipo de campo inválido.", "additionalProperties": "Campos adicionais não são permitidos.", "const": "Versão não suportada.", "enum": "Valor de campo não suportado.", "pattern": "Informe texto não vazio.", "format": "UUIDv7 inválido.", "not": "Campos incompatíveis com o canal.", "allOf": "Canal de resposta inválido."}
        raise RpcError(messages.get(error.validator, "Envelope de evento inválido."), "invalid_event", {"path": path, "rule": error.validator})
    return deepcopy(value)
