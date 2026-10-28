"""Definições MCP para envios externos autorizados pelo usuário."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .service import TelegramMcpError, TelegramMcpService


class TelegramToolRegistry:
    """Publica o canal somente quando o gateway Telegram está habilitado."""

    def __init__(self, root: Path): self.root = root.resolve()

    def available(self) -> bool:
        path = self.root / "shikigami" / "daemon" / "telegram.toml"
        try:
            return bool(tomllib.loads(path.read_text(encoding="utf-8")).get("enabled") is True)
        except (OSError, tomllib.TOMLDecodeError):
            return False

    def definitions(self) -> list[dict[str, Any]]:
        if not self.available(): return []
        return [
            {"name": "telegram.list_channels", "description": "Lista destinos Telegram já registrados pelo owner. Use antes de qualquer envio quando o destino não for inequívoco.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False}},
            {"name": "telegram.send", "description": "Envia texto e/ou arquivo a UM destino Telegram já registrado. Exige autorização explícita do usuário e confirm=true; nunca escolha destino ambíguo.", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}, "file_path": {"type": "string", "description": "Caminho absoluto de arquivo regular dentro do workspace."}, "kind": {"type": "string", "enum": ["document", "photo", "audio", "voice"]}, "new_topic": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["channel", "confirm"], "additionalProperties": False}},
            {"name": "telegram.remove_channel", "description": "Remove um destino Telegram registrado somente por pedido explícito do owner. Exige confirm=true.", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["channel", "confirm"], "additionalProperties": False}},
        ]

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.available(): raise TelegramMcpError("A integração Telegram não está habilitada.")
        service = TelegramMcpService(self.root)
        if name == "telegram.list_channels": return service.list_channels(arguments.get("query", ""))
        if name == "telegram.send": return service.send(arguments.get("channel"), arguments.get("text", ""), arguments.get("file_path", ""), arguments.get("kind", "document"), arguments.get("new_topic", ""), arguments.get("confirm"))
        if name == "telegram.remove_channel": return service.remove_channel(arguments.get("channel"), arguments.get("confirm"))
        raise TelegramMcpError("Ferramenta Telegram indisponível.")
