"""Ponte restrita entre MCP e o RPC autenticado do gateway Telegram."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ...rpc import call
from ...supervisor import endpoint


class TelegramMcpError(ValueError):
    """Erro sanitizado de uma operação Telegram solicitada pelo MCP."""


class TelegramMcpService:
    """Reutiliza as regras de destinos e arquivos já aplicadas pelo gateway."""

    def __init__(self, root: Path, rpc: Callable[..., Any] = call, endpoint_reader: Callable[[Path], tuple[str, int, str]] = endpoint):
        self.root, self.rpc, self.endpoint_reader = root.resolve(), rpc, endpoint_reader

    def _call(self, method: str, params: dict[str, Any] | None = None, *, timeout: int = 10) -> Any:
        try:
            host, port, token = self.endpoint_reader(self.root)
            return self.rpc(host, port, token, method, params or {}, timeout=timeout)
        except Exception as error:
            raise TelegramMcpError("Gateway Telegram indisponível ou recusou a operação.") from error

    @staticmethod
    def _channel(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise TelegramMcpError("Informe o identificador de um canal registrado.")
        return value.strip()

    def list_channels(self, query: object = "") -> dict[str, Any]:
        if not isinstance(query, str):
            raise TelegramMcpError("A busca de canais deve ser texto.")
        channels = self._call("telegram.channels.list")
        if not isinstance(channels, list):
            raise TelegramMcpError("O gateway retornou canais em formato inválido.")
        needle = query.casefold().strip()
        return {"channels": [channel for channel in channels if isinstance(channel, dict) and (not needle or needle in str(channel.get("name") or "").casefold())]}

    def remove_channel(self, channel: object, confirm: object) -> dict[str, Any]:
        if confirm is not True:
            raise TelegramMcpError("Confirme a remoção do canal com confirm=true.")
        result = self._call("telegram.channels.remove", {"channel": self._channel(channel)})
        return result if isinstance(result, dict) else {"removed": False}

    def send(self, channel: object, text: object = "", file_path: object = "", kind: object = "document", new_topic: object = "", confirm: object = False) -> dict[str, Any]:
        if confirm is not True:
            raise TelegramMcpError("Confirme o envio externo com confirm=true.")
        if not isinstance(text, str) or not isinstance(file_path, str) or not isinstance(kind, str) or not isinstance(new_topic, str):
            raise TelegramMcpError("Os dados de envio são inválidos.")
        if not text.strip() and not file_path.strip():
            raise TelegramMcpError("Informe texto e/ou arquivo para enviar.")
        params: dict[str, Any] = {"channel": self._channel(channel), "text": text}
        if file_path.strip():
            path = Path(file_path).expanduser()
            if not path.is_absolute() or not path.is_file() or path.is_symlink():
                raise TelegramMcpError("O arquivo deve existir, ser regular e usar caminho absoluto.")
            if kind not in {"document", "photo", "audio", "voice"}:
                raise TelegramMcpError("Tipo de anexo inválido.")
            params.update({"path": str(path.resolve()), "kind": kind})
        if new_topic.strip():
            params["new_topic"] = new_topic.strip()
        result = self._call("telegram.outbound.send", params, timeout=120)
        return result if isinstance(result, dict) else {"sent": True}
