"""Proxy transparente por namespace, sem autenticação de usuários finais."""
from __future__ import annotations

import asyncio
import re
import ssl
from urllib.parse import unquote

from aiohttp import ClientConnectionError, ClientError, ClientSession, ClientTimeout, DummyCookieJar, TraceConfig, WSServerHandshakeError, WSMsgType, web
from multidict import CIMultiDict
from yarl import URL

from ..exception import GatewayError
from ..repository import ListenerRegistry, validate_prefix

MAX_BODY_BYTES = 64 * 1024 * 1024
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"}
UNTRUSTED_HEADERS = {"forwarded", "cf-connecting-ip", "cf-connecting-ipv6", "cf-pseudo-ipv4", "true-client-ip", "x-real-ip", "x-onmyoji-token"}


def route_prefix(raw_target: str) -> str | None:
    """Valida sem reescrever o path; query strings permanecem opacas.

    Rejeita ambiguidades de segmento, escapes inválidos, separadores codificados
    e traversal inclusive após uma segunda decodificação. A URL aprovada é
    encaminhada em sua representação original.
    """
    path = raw_target.split("?", 1)[0]
    if not path.startswith("/") or path.startswith("//") or "\\" in path or "#" in path or re.search(r"%(?![0-9a-fA-F]{2})", path):
        raise web.HTTPBadRequest(text="Invalid path")
    decoded = path
    for _ in range(3):
        if re.search(r"%(?:2f|5c)", decoded, re.IGNORECASE):
            raise web.HTTPBadRequest(text="Encoded path separator")
        try:
            candidate = unquote(decoded, encoding="utf-8", errors="strict")
        except UnicodeError:
            raise web.HTTPBadRequest(text="Invalid path encoding")
        if "\\" in candidate or any(ord(char) < 32 or ord(char) == 127 for char in candidate) or "//" in candidate or any(segment in {".", ".."} for segment in candidate.split("/")):
            raise web.HTTPBadRequest(text="Invalid path")
        if candidate == decoded:
            break
        decoded = candidate
    if re.search(r"%(?:25|2e|2f|5c|0[0-9a-f]|1[0-9a-f]|7f)", decoded, re.IGNORECASE):
        raise web.HTTPBadRequest(text="Ambiguous path encoding")
    first = path[1:].split("/", 1)[0]
    if not first or first == "internal":
        return None
    try:
        prefix = validate_prefix(first)
    except GatewayError:
        raise web.HTTPBadRequest(text="Invalid namespace")
    return prefix


def forwarded_headers(headers, *, request: bool = False, host: str | None = None) -> CIMultiDict:
    """Conserva duplicatas e remove cabeçalhos de conexão e contexto não confiável."""
    connection_names = {part.strip().lower() for value in headers.getall("Connection", []) for part in value.split(",")}
    excluded = HOP_HEADERS | connection_names
    if request:
        excluded |= UNTRUSTED_HEADERS | {"host", "content-length"}
    result = CIMultiDict()
    for key, value in headers.items():
        lower = key.lower()
        if lower not in excluded and not (request and lower.startswith("x-forwarded-")):
            result.add(key, value)
    if host is not None:
        result["Host"] = host
    return result


class HttpProxy:
    """Encaminha apenas destinos declarados; não segue redirects nem guarda cookies."""

    def __init__(self, registry: ListenerRegistry, origin_reader, local_authority_reader=lambda: "localhost"):
        self.registry, self.origin_reader = registry, origin_reader
        self.local_authority_reader = local_authority_reader
        self.session: ClientSession | None = None

    async def start(self, _app=None) -> None:
        trace = TraceConfig()
        async def reject_redirect(_session, _context, params):
            # ws_connect follows redirects internally without exposing an option
            # to disable them. Stop before it can disclose headers to a new URL.
            response = params.response
            error = WSServerHandshakeError(response.request_info, response.history, status=response.status, headers=CIMultiDict(response.headers))
            response.close()
            raise error
        trace.on_request_redirect.append(reject_redirect)
        self.session = ClientSession(cookie_jar=DummyCookieJar(), auto_decompress=False, timeout=ClientTimeout(total=None, connect=5, sock_connect=5, sock_read=200), trust_env=False, skip_auto_headers={"Accept-Encoding", "User-Agent", "Content-Type"}, trace_configs=[trace])

    async def close(self, _app=None) -> None:
        if self.session:
            await self.session.close()
            self.session = None

    @staticmethod
    def _ssl(listener: dict):
        if URL(listener["target"]).scheme == "https":
            return ssl.create_default_context(cafile=listener.get("ca_file"))
        return True

    async def handle(self, request: web.Request) -> web.StreamResponse:
        if request.method in {"CONNECT", "TRACE"}:
            raise web.HTTPMethodNotAllowed(request.method, ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        prefix = route_prefix(request.raw_path)
        if prefix is None:
            raise web.HTTPNotFound()
        try:
            listener = self.registry.inspect(prefix, include_removed=False)
        except GatewayError as error:
            if error.code == "listener_not_found":
                raise web.HTTPNotFound()
            raise
        if not listener["enabled"]:
            raise web.HTTPNotFound()
        if not listener["ready"]:
            raise web.HTTPServiceUnavailable(text="Listener unavailable")
        if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=request.content_length)
        origin = self.origin_reader()
        host = URL(origin).raw_authority if origin else self.local_authority_reader()
        if not host or any(char in host for char in "\r\n/@\\"):
            raise web.HTTPBadRequest(text="Invalid host")
        headers = forwarded_headers(request.headers, request=True, host=host)
        target = URL(listener["target"] + request.raw_path, encoded=True)
        assert self.session is not None
        try:
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return await self._websocket(request, target, headers, listener)
            return await self._http(request, target, headers, listener)
        except web.HTTPException:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            raise web.HTTPGatewayTimeout(text="Listener timeout")
        except (ClientConnectionError, ClientError, OSError, ValueError):
            raise web.HTTPServiceUnavailable(text="Listener unavailable")

    async def _http(self, request: web.Request, target: URL, headers, listener: dict) -> web.StreamResponse:
        exceeded = False
        discard = False
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=2)
        def stop_forwarding(_writer=None):
            nonlocal discard
            discard = True
            while not queue.empty():
                queue.get_nowait()

        async def read_body():
            nonlocal exceeded
            received = 0
            try:
                while chunk := await asyncio.wait_for(request.content.read(64 * 1024), timeout=200):
                    received += len(chunk)
                    if received > MAX_BODY_BYTES:
                        exceeded = True
                        raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=received)
                    if not discard:
                        await queue.put(chunk)
            finally:
                if not discard:
                    await queue.put(None)

        async def body():
            # The writer may be cancelled while suspended outside this generator,
            # so its completion callback also releases a backpressured reader.
            asyncio.current_task().add_done_callback(stop_forwarding)
            while (chunk := await queue.get()) is not None:
                yield chunk
            await asyncio.shield(reader)

        # Only an actual incoming payload supplies a body; GET/HEAD requests keep
        # their original semantics instead of acquiring a chunked empty entity.
        reader = asyncio.create_task(read_body()) if request.can_read_body else None
        payload = body() if reader is not None else None
        response = None
        outgoing = None
        try:
            response = await self.session.request(request.method, target, headers=headers, data=payload, allow_redirects=False, ssl=self._ssl(listener))
            if reader is not None:
                # An upstream may send final headers before consuming its body.
                # Finish counting the incoming upload before committing headers,
                # even when aiohttp cancels the upstream's payload writer.
                await reader
            if exceeded:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=MAX_BODY_BYTES + 1)
            outgoing = web.StreamResponse(status=response.status, reason=response.reason, headers=forwarded_headers(response.headers))
            await outgoing.prepare(request)
            async for chunk in response.content.iter_chunked(64 * 1024):
                await outgoing.write(chunk)
            await outgoing.write_eof()
            return outgoing
        except Exception:
            if outgoing is not None and outgoing.prepared:
                request.transport.close() if request.transport else None
                return outgoing
            if exceeded:
                raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY_BYTES, actual_size=MAX_BODY_BYTES + 1)
            raise
        finally:
            stop_forwarding()
            if reader is not None:
                if not reader.done():
                    reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
            if response is not None:
                response.close()

    async def _websocket(self, request: web.Request, target: URL, headers, listener: dict) -> web.StreamResponse:
        # aiohttp generates handshake headers; forwarding the client's key would
        # create a second, mismatched handshake at the backend.
        for key in list(headers):
            if key.lower().startswith("sec-websocket-"):
                headers.popall(key, None)
        protocols = tuple(value.strip() for value in request.headers.get("Sec-WebSocket-Protocol", "").split(",") if value.strip())
        try:
            backend = await self.session.ws_connect(target, headers=headers, protocols=protocols, ssl=self._ssl(listener), autoclose=False, autoping=True, max_msg_size=MAX_BODY_BYTES)
        except WSServerHandshakeError as error:
            response_headers = forwarded_headers(error.headers or CIMultiDict())
            response_headers.popall("Content-Length", None)
            return web.Response(status=error.status, headers=response_headers)
        socket = web.WebSocketResponse(protocols=(backend.protocol,) if backend.protocol else (), autoclose=False, autoping=True, max_msg_size=MAX_BODY_BYTES)
        try:
            for key, value in forwarded_headers(backend._response.headers).items():
                if not key.lower().startswith("sec-websocket-"):
                    socket.headers.add(key, value)
            await socket.prepare(request)
            async def relay(source, destination):
                async for message in source:
                    if message.type == WSMsgType.TEXT:
                        await destination.send_str(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await destination.send_bytes(message.data)
                    elif message.type == WSMsgType.ERROR:
                        break
                await destination.close(code=source.close_code or 1000)
            tasks = [asyncio.create_task(relay(socket, backend)), asyncio.create_task(relay(backend, socket))]
            try:
                _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
        finally:
            await backend.close()
            await socket.close()
        return socket
