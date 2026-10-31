"""Validações e erros públicos da integração de e-mail."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


class EmailError(ValueError):
    """Erro sanitizado; valores de respostas remotas nunca são mensagens públicas."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise EmailError(code, message)


def text(value: Any, label: str, maximum: int = 1000) -> str:
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum and all(ord(c) >= 32 and ord(c) != 127 for c in value), "invalid_argument", f"{label} inválido.")
    return value


def identifier(value: Any) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None, "invalid_account", "Nome da conta inválido.")
    return value


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
