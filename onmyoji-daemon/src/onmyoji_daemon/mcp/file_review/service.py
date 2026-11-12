"""Ponte autenticada para o serviço de revisão hospedado em Mini Apps."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Callable

from ...rpc import RpcError, call
from ...supervisor import endpoint
from .exception import FileReviewMcpError


class FileReviewMcpService:
    """Abre, consulta ou cancela revisões; a aprovação pertence à interface humana."""

    def __init__(self, root: Path, rpc: Callable[..., Any] = call, endpoint_reader: Callable[[Path], tuple[str, int, str]] = endpoint):
        self.root = root.resolve()
        self.rpc = rpc
        self.endpoint_reader = endpoint_reader

    def invoke(self, action: str, params: dict[str, Any]) -> Any:
        """Encaminha uma operação fechada e preserva códigos de domínio válidos.

        Lança ``FileReviewMcpError`` com ``invalid_request`` para ações fora do
        contrato, ``daemon_unavailable`` em falhas de transporte ou o código
        sanitizado da rejeição RPC. Mensagens remotas não são repassadas.
        """
        if action not in {"create", "status", "cancel"}:
            raise FileReviewMcpError("Operação de revisão de arquivos inválida.")
        try:
            host, port, token = self.endpoint_reader(self.root)
            return self.rpc(host, port, token, "mini-apps.file-review." + action, params, timeout=30 if action == "create" else 5)
        except RpcError as error:
            code = error.code if isinstance(error.code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", error.code) else "daemon_rejected"
            raise FileReviewMcpError("Revisão de arquivos indisponível ou operação recusada pelo daemon.", code) from error
        except Exception as error:
            raise FileReviewMcpError("Revisão de arquivos indisponível ou operação recusada pelo daemon.", "daemon_unavailable") from error
