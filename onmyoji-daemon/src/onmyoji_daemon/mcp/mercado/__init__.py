"""Integração MCP com Mercado Livre e Mercado Pago."""

from .configuration import MODULES, PROVIDERS
from .exception import MercadoError

__all__ = ["MODULES", "PROVIDERS", "MercadoError"]
