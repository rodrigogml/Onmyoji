"""Publicação HTTP compartilhada dos serviços locais do Onmyōji."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .service import PublicGateway

__all__ = ["PublicGateway"]


def __getattr__(name: str) -> Any:
    """Carrega o serviço HTTP somente quando sua API pública é solicitada."""
    if name != "PublicGateway":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from .service import PublicGateway

    globals()[name] = PublicGateway
    return PublicGateway
