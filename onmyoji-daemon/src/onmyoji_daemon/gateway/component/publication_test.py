"""Tentativa efêmera de chegada ao listener HTTPS pelo link apresentado no setup."""
from __future__ import annotations

from dataclasses import dataclass
import math
import secrets
import threading
import time

from aiohttp import web

from ..exception import GatewayError


@dataclass
class _Attempt:
    identifier: str
    path: str | None
    deadline: float
    state: str = "waiting"


class PublicationTest:
    """Mantém uma tentativa em memória, sem rotas ou registros duráveis.

    O identificador administrativo é independente do nonce público. Somente um
    GET com o request-target exato confirma a chegada ao listener; headers de
    proxy não são tratados como prova da origem. A tentativa expira mesmo sem
    consultas do setup e uma nova tentativa invalida qualquer link anterior.
    """

    TTL_SECONDS = 120
    PATH_PREFIX = "/internal/publication-test/"

    def __init__(self):
        self.lock = threading.RLock()
        self.attempt: _Attempt | None = None
        self.timer: threading.Timer | None = None

    def _finish(self, state: str) -> None:
        if self.attempt is not None and self.attempt.state == "waiting":
            self.attempt.state, self.attempt.path = state, None
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None

    def _expire(self, identifier: str) -> None:
        with self.lock:
            if self.attempt is not None and self.attempt.identifier == identifier:
                self._finish("expired")

    def start(self, origin: str) -> dict:
        """Cria um link por 120 segundos e substitui a tentativa anterior."""
        with self.lock:
            self._finish("cancelled")
            path = self.PATH_PREFIX + secrets.token_urlsafe(32)
            attempt = _Attempt(secrets.token_hex(16), path, time.monotonic() + self.TTL_SECONDS)
            self.attempt = attempt
            self.timer = threading.Timer(self.TTL_SECONDS, self._expire, (attempt.identifier,))
            self.timer.daemon = True
            self.timer.start()
            return {**self._status(), "url": origin + path}

    def _status(self) -> dict:
        attempt = self.attempt
        assert attempt is not None
        remaining = max(0.0, attempt.deadline - time.monotonic())
        if remaining == 0 and attempt.state == "waiting":
            self._finish("expired")
        return {"test_id": attempt.identifier, "state": attempt.state,
                "expires_in_seconds": math.ceil(remaining) if attempt.state == "waiting" else 0}

    def status(self, identifier: str) -> dict:
        """Consulta sem retornar o nonce; test_not_found indica sessão inválida."""
        with self.lock:
            if self.attempt is None or not isinstance(identifier, str) or not identifier.isascii() or not secrets.compare_digest(self.attempt.identifier, identifier):
                raise GatewayError("test_not_found", "Tentativa não encontrada; crie um novo teste no setup.")
            return self._status()

    def cancel(self, identifier: str) -> dict:
        """Invalida a tentativa pendente; preserva um resultado já confirmado.

        Lança ``test_not_found`` quando o identificador não é da sessão atual.
        """
        with self.lock:
            self.status(identifier)
            self._finish("cancelled")
            return self._status()

    async def handle(self, request: web.Request) -> web.Response:
        """Confirma somente GET exato e vigente; demais acessos recebem 404.

        A resposta não incorpora URL, nonce, headers ou dados do cliente.
        Cancelamento, confirmação e expiração removem imediatamente o path.
        """
        with self.lock:
            attempt = self.attempt
            if attempt is not None:
                self._status()
            if (attempt is None or attempt.state != "waiting" or request.method != "GET"
                    or request.raw_path != attempt.path):
                raise web.HTTPNotFound(text="Teste inexistente, encerrado ou link incorreto. Volte ao setup para criar outro.",
                                       headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
            self._finish("confirmed")
        return web.Response(text="""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Acesso ao Gateway confirmado</title>
<h1>Acesso confirmado</h1>
<p>A requisição deste link temporário chegou ao listener HTTPS do Gateway desta instância.</p>
<p>Volte ao setup para consultar o resultado. Este link já foi encerrado.</p></html>""",
                            content_type="text/html", headers={"Cache-Control": "no-store, max-age=0",
                                "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                                "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"})

    def close(self) -> None:
        """Invalida a sessão e cancela seu temporizador ao encerrar o listener."""
        with self.lock:
            self._finish("cancelled")
