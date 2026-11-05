"""Definições MCP para envios externos autorizados pelo usuário."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .service import TelegramMcpError, TelegramMcpService


class TelegramToolRegistry:
    """Publica ferramentas conforme a habilitação do serviço Telegram no daemon."""

    def __init__(self, root: Path): self.root = root.resolve()

    def available(self) -> bool:
        """Consulta o estado do ciclo de vida; estado ausente ou inválido não publica ferramentas."""
        path = self.root / "configs" / "daemon" / "services.json"
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        service = values.get("telegram") if isinstance(values, dict) else None
        return isinstance(service, dict) and bool(service.get("enabled", False))

    def definitions(self) -> list[dict[str, Any]]:
        if not self.available(): return []
        return [
            {"name": "telegram.list_channels", "description": "Lista destinos Telegram já registrados pelo owner. Use antes de qualquer envio quando o destino não for inequívoco.", "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False}},
            {"name": "telegram.send", "description": "Envia texto e/ou arquivo a UM destino Telegram já registrado. Exige autorização explícita do usuário e confirm=true; nunca escolha destino ambíguo.", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "text": {"type": "string"}, "file_path": {"type": "string", "description": "Caminho absoluto de arquivo regular dentro do workspace."}, "kind": {"type": "string", "enum": ["document", "photo", "audio", "voice"]}, "new_topic": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["channel", "confirm"], "additionalProperties": False}},
            {"name": "telegram.remove_channel", "description": "Remove um destino Telegram registrado somente por pedido explícito do owner. Exige confirm=true.", "input_schema": {"type": "object", "properties": {"channel": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": ["channel", "confirm"], "additionalProperties": False}},
        ]

    def developer_instructions(self) -> str | None:
        """Publica o contrato de descoberta e envio quando o módulo está ativo."""
        if not self.available():
            return None
        resource = Path(__file__).resolve().parents[4] / "resources" / "instructions" / "telegram-mcp.md"
        return resource.read_text(encoding="utf-8").strip()

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.available(): raise TelegramMcpError("A integração Telegram não está habilitada.")
        service = TelegramMcpService(self.root)
        if name == "telegram.list_channels": return service.list_channels(arguments.get("query", ""))
        if name == "telegram.send": return service.send(arguments.get("channel"), arguments.get("text", ""), arguments.get("file_path", ""), arguments.get("kind", "document"), arguments.get("new_topic", ""), arguments.get("confirm"))
        if name == "telegram.remove_channel": return service.remove_channel(arguments.get("channel"), arguments.get("confirm"))
        raise TelegramMcpError("Ferramenta Telegram indisponível.")
