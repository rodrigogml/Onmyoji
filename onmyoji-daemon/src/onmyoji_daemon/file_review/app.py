"""Adaptador HTTP do modelo de revisão, protegido pela sessão Mini Apps."""
from __future__ import annotations

import asyncio
import hmac
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..miniapps import HttpRequest, HttpResponse
from .exception import FileReviewError
from .service import FileReviewService


ASSETS = Path(__file__).parent / "assets"
MAX_JSON_BYTES = 8 * 1024 * 1024
SECURITY_HEADERS = [
    ("Cache-Control", "no-store"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Content-Type-Options", "nosniff"),
    ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"),
]


def _origin(value: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


class FileReviewApp:
    """Entrega somente a revisão vinculada e os assets confiáveis da base.

    O Gateway autentica o claim antes de chamar este adaptador. Mutações também
    exigem JSON e o token CSRF da revisão; origem divergente recebe
    ``invalid_origin`` (403), e token ausente ou incorreto, ``invalid_csrf``
    (403). Caminhos de arquivos nunca são aceitos do navegador. Aplicação ou
    cancelamento concluídos revogam a publicação após entregar o recibo.
    """

    def __init__(self, root: Path, workspace: Path, state_dir: Path, review_id: str, allowed_origins: list[str]):
        self.service = FileReviewService(root, workspace, state_dir)
        self.review_id = review_id
        self.allowed_origins = {_origin(value) for value in allowed_origins} - {None}

    async def handle(self, request: HttpRequest) -> HttpResponse:
        """Processa o contrato HTTP sem bloquear o loop do Gateway em operações de disco."""
        return await asyncio.to_thread(self._handle, request)

    def _json(self, value: Any, status: int = 200, close: bool = False) -> HttpResponse:
        response = HttpResponse.json(value, status)
        response.headers.extend(SECURITY_HEADERS)
        response.close_publication = close
        return response

    def _payload(self, request: HttpRequest) -> dict[str, Any]:
        headers = {}
        for key, value in request.headers:
            headers.setdefault(key.lower(), []).append(value)
        origins = headers.get("origin", [])
        if len(origins) > 1 or (origins and _origin(origins[0]) not in self.allowed_origins):
            raise FileReviewError("invalid_origin", "A origem desta solicitação não é permitida.", 403)
        tokens = headers.get("x-onmyoji-review-csrf", [])
        current = self.service.view(self.review_id)
        if current["status"] not in {"editing", "conflict"} and not (request.path == "/api/apply" and current["status"] == "applied"):
            raise FileReviewError("review_closed", "Esta revisão já foi encerrada.", 410)
        if len(tokens) != 1 or not tokens[0].isascii() or not hmac.compare_digest(tokens[0], current["csrf_token"]):
            raise FileReviewError("invalid_csrf", "A confirmação da sessão é inválida. Reabra a revisão.", 403)
        if request.content_type != "application/json":
            raise FileReviewError("invalid_content_type", "Envie os dados como application/json.", 415)
        if request.body_file:
            with request.open_body() as stream:
                body = stream.read(MAX_JSON_BYTES + 1)
        else:
            body = request.body
        if len(body) > MAX_JSON_BYTES:
            raise FileReviewError("request_too_large", "O pedido excede o limite da revisão.", 413)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as error:
            raise FileReviewError("invalid_json", "Os dados da solicitação são inválidos.") from error
        if not isinstance(payload, dict):
            raise FileReviewError("invalid_json", "Os dados da solicitação devem ser um objeto JSON.")
        return payload

    def _review(self) -> dict[str, Any]:
        review = self.service.view(self.review_id)
        if review["status"] not in {"editing", "conflict"}:
            raise FileReviewError("review_closed", "Esta revisão já foi encerrada.", 410)
        return review

    @staticmethod
    def _fields(payload: dict[str, Any], required: set[str], optional: set[str] | None = None) -> None:
        if not required <= payload.keys() or payload.keys() - required - (optional or set()):
            raise FileReviewError("invalid_arguments", "A solicitação contém campos ausentes ou não permitidos.")

    def _handle(self, request: HttpRequest) -> HttpResponse:
        try:
            if not request.authenticated_identity.authenticated:
                raise FileReviewError("unauthorized", "A sessão da revisão não está autenticada.", 401)
            if request.method in {"GET", "HEAD"}:
                static = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/index.html": ("index.html", "text/html; charset=utf-8"),
                    "/review.js": ("review.js", "text/javascript; charset=utf-8"),
                    "/review.css": ("review.css", "text/css; charset=utf-8"),
                }.get(request.path)
                if static:
                    filename, content_type = static
                    body = b"" if request.method == "HEAD" else (ASSETS / filename).read_bytes()
                    return HttpResponse(200, [("Content-Type", content_type), *SECURITY_HEADERS], body)
                if request.method == "GET" and request.path == "/api/review":
                    return self._json(self._review())
                if request.method == "GET" and request.path == "/api/preview":
                    from .service import build_chunks

                    review = self._review()
                    return self._json({"revision": review["revision"], "result_hash": review["result_hash"], "chunks": build_chunks(review["original"], review["result"]), "result": review["result"], "status": review["status"]})
                return self._json({"error": {"code": "not_found", "message": "Recurso não encontrado."}}, 404)
            if request.method != "POST":
                return self._json({"error": {"code": "method_not_allowed", "message": "Método não permitido."}}, 405)
            if request.path not in {"/api/draft", "/api/decision", "/api/apply", "/api/cancel"}:
                return self._json({"error": {"code": "not_found", "message": "Recurso não encontrado."}}, 404)
            payload = self._payload(request)
            if request.path == "/api/draft":
                self._fields(payload, {"revision", "result"})
                return self._json(self.service.save(self.review_id, payload["revision"], payload["result"]))
            if request.path == "/api/decision":
                self._fields(payload, {"revision", "chunk_id", "decision"})
                return self._json(self.service.decide(self.review_id, payload["revision"], payload["chunk_id"], payload["decision"]))
            if request.path == "/api/apply":
                self._fields(payload, {"revision", "result_hash"}, {"discard_remaining"})
                return self._json(self.service.apply(self.review_id, payload["revision"], payload["result_hash"], payload.get("discard_remaining", False)), close=True)
            self._fields(payload, set())
            return self._json(self.service.cancel(self.review_id), close=True)
        except FileReviewError as error:
            return self._json({"error": {"code": error.code, "message": str(error)}}, error.status)
        except OSError:
            return self._json({"error": {"code": "storage_unavailable", "message": "Não foi possível acessar os arquivos da revisão. Preserve seu texto e tente novamente."}}, 503)
