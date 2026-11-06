"""Ponte restrita entre MCP e o RPC autenticado do gateway Telegram."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ...rpc import call
from ...supervisor import endpoint
from ...telegram_location import OMITTED, LocationValidationError, normalize_location


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

    def send_location(self, channel: object, latitude: object, longitude: object, horizontal_accuracy: object = OMITTED, new_topic: object = "", confirm: object = False, reply_context: object = OMITTED) -> dict[str, Any]:
        """Envia um ponto fixo autorizado e só confirma uma entrega com recibo válido.

        Dados inválidos ou confirmação ausente lançam ``TelegramMcpError`` antes
        do RPC. Falhas após iniciar o RPC são indeterminadas e não autorizam
        repetição automática, inclusive quando um tópico pode ter sido criado.
        """
        if confirm is not True:
            raise TelegramMcpError("Confirme o envio externo com confirm=true.")
        reply = reply_context is not OMITTED
        if reply:
            if channel is not None or new_topic != "":
                raise TelegramMcpError("O contexto de resposta não aceita canal ou novo tópico.")
            if not isinstance(reply_context, str) or not reply_context.isascii() or not 1 <= len(reply_context) <= 128:
                raise TelegramMcpError("Contexto de resposta inválido ou expirado.")
            destination = None
        else:
            destination = self._channel(channel)
        if not isinstance(new_topic, str) or len(new_topic.strip()) > 128:
            raise TelegramMcpError("O nome do tópico deve ser texto de até 128 caracteres.")
        try:
            location = normalize_location(latitude, longitude, horizontal_accuracy)
        except LocationValidationError as error:
            raise TelegramMcpError(str(error)) from error
        params = {**({"reply_context": reply_context} if reply else {"channel": destination}), **{key: value for key, value in location.items() if key != "kind"}}
        if new_topic.strip():
            params["new_topic"] = new_topic.strip()
        try:
            host, port, token = self.endpoint_reader(self.root)
        except Exception as error:
            raise TelegramMcpError("Gateway Telegram indisponível; a localização não foi enviada.") from error
        try:
            result = self.rpc(host, port, token, "telegram.reply.send_location" if reply else "telegram.outbound.send_location", params, timeout=120)
        except Exception as error:
            raise TelegramMcpError("Não foi possível confirmar a entrega da localização ou a criação de tópico. Não repita automaticamente.") from error
        if isinstance(result, dict) and result.get("sent") is False:
            messages = {
                "argument_validation": "O gateway recusou os dados da localização ou o destino cadastrado.",
                "authorization": "O contexto de resposta não está autorizado ou expirou.",
                "capability": "Envio de mídia pelo agente está desabilitado.",
                "gateway": "Gateway Telegram indisponível; a localização não foi enviada.",
                "topic_creation": "Telegram não confirmou a criação do tópico; ele pode ter sido criado, mas a localização não foi enviada. Não repita automaticamente.",
                "telegram_api": "Não foi possível confirmar a entrega da localização. Não repita automaticamente.",
                "receipt_validation": "Telegram não retornou um recibo válido; a entrega da localização é indeterminada. Não repita automaticamente.",
            }
            stage = result.get("stage")
            message = messages.get(stage, "Não foi possível confirmar a entrega da localização. Não repita automaticamente.") if isinstance(stage, str) else "Não foi possível confirmar a entrega da localização. Não repita automaticamente."
            if result.get("topic_created") is True:
                message += " O novo tópico já foi criado."
            raise TelegramMcpError(message)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(result, dict) or result.get("sent") is not True or not isinstance(message_id, int) or isinstance(message_id, bool) or message_id <= 0 or (not reply and (not isinstance(result.get("channel"), str) or not result["channel"] or not isinstance(result.get("topic_created"), bool))):
            raise TelegramMcpError("O gateway retornou um recibo inválido; a entrega da localização é indeterminada. Não repita automaticamente.")
        receipt = {key: result[key] for key in (("sent", "message_id") if reply else ("sent", "channel", "message_id", "topic_created"))}
        if result.get("warning"):
            receipt["warning"] = "Telegram confirmou o envio, mas não foi possível preservar integralmente o contexto local."
        return receipt
