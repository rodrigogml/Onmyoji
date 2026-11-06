"""Contrato de pontos geográficos fixos compartilhado pelo gateway Telegram.

Coordenadas e metadados de venue são conteúdo definido pelo remetente. A
normalização não comprova presença, consulta mapas nem acompanha deslocamentos.
"""
from __future__ import annotations

import math
from typing import Any


OMITTED = object()
_LIVE_FIELDS = frozenset({"live_period", "heading", "proximity_alert_radius"})
_UNSUPPORTED_MESSAGE = "Localização ao vivo não é suportada. Envie um ponto fixo."


class LocationValidationError(ValueError):
    """Indica um ponto incompleto, malformado ou fora das faixas permitidas."""


class UnsupportedLocationError(LocationValidationError):
    """Rejeita localização ao vivo sem convertê-la silenciosamente em ponto fixo."""


def _number(value: object, field: str, minimum: int, maximum: int) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LocationValidationError(f"{field} deve ser um número finito.")
    if isinstance(value, float) and not math.isfinite(value):
        raise LocationValidationError(f"{field} deve ser um número finito.")
    if not minimum <= value <= maximum:
        raise LocationValidationError(f"{field} deve estar entre {minimum} e {maximum}.")
    return value


def normalize_location(latitude: object, longitude: object, horizontal_accuracy: object = OMITTED) -> dict[str, Any]:
    """Valida um ponto fixo sem arredondar ou inventar precisão.

    Latitude e longitude são números finitos nas faixas geográficas inclusivas.
    Precisão, quando fornecida, é o raio de incerteza entre 0 e 1500 metros;
    ausência difere de zero. Booleanos, texto e ``None`` não são números do
    contrato. Dados inválidos levantam ``LocationValidationError`` sem incluir
    os valores recebidos na mensagem de erro.
    """
    point = {
        "kind": "static",
        "latitude": _number(latitude, "latitude", -90, 90),
        "longitude": _number(longitude, "longitude", -180, 180),
    }
    if horizontal_accuracy is not OMITTED:
        point["horizontal_accuracy"] = _number(horizontal_accuracy, "horizontal_accuracy", 0, 1500)
    return point


def _point(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LocationValidationError("A localização deve ser um objeto com latitude e longitude.")
    if _LIVE_FIELDS.intersection(value):
        raise UnsupportedLocationError(_UNSUPPORTED_MESSAGE)
    return normalize_location(value.get("latitude", OMITTED), value.get("longitude", OMITTED), value.get("horizontal_accuracy", OMITTED))


def _venue_metadata(value: dict[str, Any]) -> dict[str, str]:
    return {key: value[key] for key in ("title", "address") if isinstance(value.get(key), str)}


def parse_message_location(message: dict[str, Any]) -> dict[str, Any] | None:
    """Extrai um ponto Telegram, priorizando venue para evitar duplicidade.

    Retorna ``None`` quando a mensagem não contém localização. Venue conserva
    somente título/endereço textuais disponíveis como dados do remetente.
    Estrutura ou coordenadas inválidas levantam ``LocationValidationError``;
    qualquer campo de localização ao vivo levanta ``UnsupportedLocationError``.
    Campos desconhecidos e identificadores de provedores não são propagados.
    """
    if not isinstance(message, dict):
        raise LocationValidationError("A mensagem deve ser um objeto.")
    if "venue" in message:
        venue = message["venue"]
        if not isinstance(venue, dict):
            raise LocationValidationError("O local recebido deve ser um objeto.")
        point = _point(venue.get("location"))
        metadata = _venue_metadata(venue)
        if metadata:
            point["venue"] = metadata
        return point
    if "location" in message:
        return _point(message["location"])
    return None


def normalize_stored_location(value: object) -> dict[str, Any]:
    """Revalida o ponto normalizado lido do estado privado ou de um envelope.

    Exige ``kind=static`` e reconstrói somente os campos aceitos. Venue deve ser
    um objeto quando presente, com metadados textuais opcionais. Levanta
    ``LocationValidationError`` para conteúdo inválido e
    ``UnsupportedLocationError`` se houver campos live. O chamador decide como
    descartar o conteúdo inválido sem prejudicar a retomada da conversa.
    """
    if not isinstance(value, dict) or value.get("kind") != "static":
        raise LocationValidationError("A localização persistida deve representar um ponto fixo.")
    point = _point(value)
    if "venue" in value:
        if not isinstance(value["venue"], dict):
            raise LocationValidationError("Os metadados do local devem ser um objeto.")
        metadata = _venue_metadata(value["venue"])
        if metadata:
            point["venue"] = metadata
    return point
