"""Transparência HTTP, isolamento e TLS do Gateway compartilhado."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import gzip
import ssl

from aiohttp import ClientPayloadError, ClientSession, CookieJar, WSServerHandshakeError, web
from multidict import CIMultiDict
import pytest
from yarl import URL

from onmyoji_daemon.gateway.component import proxy as proxy_module
from onmyoji_daemon.gateway.component.proxy import forwarded_headers, route_prefix
from onmyoji_daemon.gateway.component.tls import server_context
from onmyoji_daemon.gateway.exception import GatewayError
from onmyoji_daemon.gateway.service import PublicGateway


@pytest.fixture
def gateway(tmp_path):
    instance = PublicGateway(tmp_path, tmp_path / "configs" / "daemon" / "services" / "gateway", "private-token", 0)
    instance.start_http()
    instance.context({"public_origin": "https://gateway.example.com", "tunnel": {"configured": True, "running": False}})
    yield instance
    instance.close()


def gateway_ssl(gateway):
    return ssl.create_default_context(cafile=gateway.status()["ca_file"])


@asynccontextmanager
async def backend(gateway, handler, *, tls=False):
    application = web.Application(client_max_size=128 * 1024 * 1024)
    application.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(application, access_log=None, auto_decompress=False)
    await runner.setup()
    tls_dir = gateway.root / "configs" / "backend" / "tls"
    context = server_context(tls_dir) if tls else None
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=context)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    endpoint = {"target": f"{'https' if tls else 'http'}://127.0.0.1:{port}", "ready": True}
    if tls:
        endpoint["ca_file"] = str(tls_dir / "ca.pem")
    try:
        yield endpoint
    finally:
        await runner.cleanup()


def publish(gateway, endpoint, prefixes=("a", "oauth"), owner="mini-apps"):
    return gateway.rpc("gateway.publication.sync", {"owner": owner, "generation": "generation-1", "listeners": [{"path_prefix": prefix, **endpoint} for prefix in prefixes]})


@pytest.mark.parametrize("path,prefix", [("/", None), ("/internal/v1/status", None), ("/a", "a"), ("/a/", "a"), ("/application/x", "application"), ("/oauth/mcp/email/callback?code=a%2Fb&state=%252e%252e", "oauth"), ("/a/caf%C3%A9", "a")])
def test_route_selects_exact_segment_and_does_not_validate_query(path, prefix):
    assert route_prefix(path) == prefix


@pytest.mark.parametrize("path", ["a/x", "//a/x", "/a//x", "/a/../internal", "/a/%2e%2e/x", "/a/%252e%252e/x", "/a/%25252f/x", "/a/%2f/x", "/a/%5c/x", "/a/\\x", "/%61/x", "/A/x", "/a/%", "/a/%GG", "/a/%ff", "/a/%00", "/a/x#fragment", "/internal//x", "/internal/%2e%2e/a", "/a/%25252500", "/a/%2525251f", "/a/%2525257f"])
def test_route_rejects_ambiguous_paths(path):
    with pytest.raises(web.HTTPBadRequest):
        route_prefix(path)


def test_headers_preserve_auth_duplicates_and_remove_untrusted_context():
    headers = CIMultiDict([("Connection", "X-Remove, keep-alive"), ("X-Remove", "drop"), ("Authorization", "Bearer business"), ("Cookie", "session=business"), ("X-Onmyoji-Callback-Secret", "callback"), ("X-Onmyoji-Token", "private-rpc"), ("Forwarded", "for=spoofed"), ("X-Forwarded-For", "spoofed"), ("CF-Connecting-IP", "spoofed"), ("Host", "evil.example"), ("Content-Length", "42"), ("Set-Cookie", "first=1"), ("Set-Cookie", "second=2")])
    result = forwarded_headers(headers, request=True, host="gateway.example.com")
    assert result["Authorization"] == "Bearer business"
    assert result["Cookie"] == "session=business"
    assert result["X-Onmyoji-Callback-Secret"] == "callback"
    assert result.getall("Set-Cookie") == ["first=1", "second=2"]
    assert result["Host"] == "gateway.example.com"
    for removed in ("Connection", "X-Remove", "X-Onmyoji-Token", "Forwarded", "X-Forwarded-For", "CF-Connecting-IP", "Content-Length"):
        assert removed not in result


def test_gateway_preserves_paths_queries_auth_body_and_tls(gateway):
    async def exercise():
        async def handler(request):
            return web.json_response({"raw_path": request.raw_path, "authorization": request.headers.get("Authorization"), "callback_secret": request.headers.get("X-Onmyoji-Callback-Secret"), "cookie": request.headers.get("Cookie"), "host": request.host, "body": await request.text(), "forwarded": request.headers.get("Forwarded"), "rpc_token": request.headers.get("X-Onmyoji-Token")})
        async with backend(gateway, handler, tls=True) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                url = URL(gateway.status()["http_url"] + "/oauth/mcp/email/callback?code=a%2Fb&state=%252f", encoded=True)
                async with session.post(url, data=b"payload", ssl=gateway_ssl(gateway), headers={"Authorization": "Bearer own-token", "X-Onmyoji-Callback-Secret": "own-secret", "Cookie": "session=own-cookie", "Forwarded": "for=spoofed", "Host": "evil.example", "X-Onmyoji-Token": "private-rpc"}) as response:
                    assert response.status == 200
                    assert await response.json() == {"raw_path": "/oauth/mcp/email/callback?code=a%2Fb&state=%252f", "authorization": "Bearer own-token", "callback_secret": "own-secret", "cookie": "session=own-cookie", "host": "gateway.example.com", "body": "payload", "forwarded": None, "rpc_token": None}
    asyncio.run(exercise())


def test_gateway_preserves_redirects_multiple_cookies_and_client_isolation(gateway):
    async def exercise():
        async def handler(request):
            if request.path.endswith("/claim"):
                return web.Response(status=303, headers=CIMultiDict([("Location", "/a/result"), ("Set-Cookie", "session=first; Path=/; Secure; HttpOnly"), ("Set-Cookie", "second=second; Path=/; Secure")]))
            return web.Response(text=request.headers.get("Cookie", "no-cookie"))
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession(cookie_jar=CookieJar()) as first, ClientSession(cookie_jar=CookieJar()) as second:
                origin = gateway.status()["http_url"]
                async with first.get(origin + "/a/claim", ssl=gateway_ssl(gateway), allow_redirects=False) as response:
                    assert response.status == 303
                    assert response.headers["Location"] == "/a/result"
                    assert len(response.headers.getall("Set-Cookie")) == 2
                async with first.get(origin + "/a/result", ssl=gateway_ssl(gateway)) as response:
                    cookies = await response.text()
                    assert "session=first" in cookies and "second=second" in cookies
                async with second.get(origin + "/a/result", ssl=gateway_ssl(gateway)) as response:
                    assert await response.text() == "no-cookie"
    asyncio.run(exercise())


def test_gateway_preserves_gzip_and_streamed_bytes(gateway):
    async def exercise():
        compressed = gzip.compress(b"content" * 1024)
        async def handler(request):
            if request.path.endswith("/gzip"):
                return web.Response(body=compressed, headers={"Content-Encoding": "gzip"})
            response = web.StreamResponse(headers={"Content-Type": "text/plain"})
            await response.prepare(request)
            for chunk in (b"first", b"second", b"third"):
                await response.write(chunk)
            await response.write_eof()
            return response
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession(auto_decompress=False) as session:
                origin = gateway.status()["http_url"]
                async with session.get(origin + "/a/gzip", ssl=gateway_ssl(gateway)) as response:
                    assert response.headers["Content-Encoding"] == "gzip"
                    assert int(response.headers["Content-Length"]) == len(compressed)
                    assert await response.read() == compressed
                async with session.get(origin + "/a/stream", ssl=gateway_ssl(gateway)) as response:
                    assert await response.read() == b"firstsecondthird"
    asyncio.run(exercise())


def test_gateway_distinguishes_missing_disabled_and_unavailable_routes(gateway):
    async def exercise():
        async def handler(request):
            return web.Response(text="local")
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint, prefixes=("app",))
            async with ClientSession() as session:
                origin, context = gateway.status()["http_url"], gateway_ssl(gateway)
                for path in ("/", "/application", "/internal/v1/status", "/unknown"):
                    async with session.get(origin + path, ssl=context) as response:
                        assert response.status == 404
                async with session.get(origin + "/app", ssl=context) as response:
                    assert response.status == 200
                gateway.registry.change("disable", "app")
                async with session.get(origin + "/app", ssl=context) as response:
                    assert response.status == 404
                gateway.registry.change("enable", "app")
                gateway.registry.unavailable("mini-apps")
                async with session.get(origin + "/app", ssl=context) as response:
                    assert response.status == 503
                gateway.registry.change("remove", "app", confirm=True)
                async with session.get(origin + "/app", ssl=context) as response:
                    assert response.status == 404
    asyncio.run(exercise())


def test_gateway_relays_authenticated_websocket_and_backend_rejection(gateway):
    async def exercise():
        async def handler(request):
            if request.headers.get("Authorization") != "Bearer own-token":
                return web.Response(status=401, headers={"WWW-Authenticate": "Bearer"})
            socket = web.WebSocketResponse(protocols=("echo",))
            await socket.prepare(request)
            async for message in socket:
                if isinstance(message.data, str):
                    await socket.send_str("echo:" + message.data)
                elif isinstance(message.data, bytes):
                    await socket.send_bytes(message.data)
            return socket
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                url, context = gateway.status()["http_url"] + "/a/socket", gateway_ssl(gateway)
                with pytest.raises(WSServerHandshakeError) as failure:
                    await session.ws_connect(url, ssl=context)
                assert failure.value.status == 401
                assert failure.value.headers["WWW-Authenticate"] == "Bearer"
                async with session.ws_connect(url, ssl=context, headers={"Authorization": "Bearer own-token"}, protocols=("echo",)) as socket:
                    assert socket.protocol == "echo"
                    await socket.send_str("hello")
                    assert (await socket.receive()).data == "echo:hello"
                    await socket.send_bytes(b"binary")
                    assert (await socket.receive()).data == b"binary"
    asyncio.run(exercise())


def test_gateway_does_not_follow_websocket_redirect_or_disclose_client_headers(gateway):
    async def exercise():
        redirected = []
        async def destination(request):
            redirected.append(dict(request.headers))
            return web.Response(status=403)
        async with backend(gateway, destination) as external:
            location = external["target"] + "/unexpected"
            async def redirect(request):
                return web.Response(status=302, headers=CIMultiDict([("Location", location), ("Set-Cookie", "first=1"), ("Set-Cookie", "second=2")]))
            async with backend(gateway, redirect) as endpoint:
                publish(gateway, endpoint)
                headers = {"Upgrade": "websocket", "Connection": "Upgrade", "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Cookie": "session=private", "Authorization": "Bearer own-token"}
                async with ClientSession() as session:
                    async with session.get(gateway.status()["http_url"] + "/a/socket", ssl=gateway_ssl(gateway), headers=headers, allow_redirects=False) as response:
                        assert response.status == 302
                        assert response.headers["Location"] == location
                        assert response.headers.getall("Set-Cookie") == ["first=1", "second=2"]
                assert redirected == []
    asyncio.run(exercise())


def test_gateway_closes_partial_response_when_backend_fails_after_headers(gateway):
    async def exercise():
        async def handler(request):
            response = web.StreamResponse(headers={"Content-Length": "100"})
            await response.prepare(request)
            await response.write(b"partial")
            request.transport.close()
            return response
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                async with session.get(gateway.status()["http_url"] + "/a/stream", ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 200
                    with pytest.raises(ClientPayloadError):
                        await response.read()
    asyncio.run(exercise())


def test_gateway_counts_chunked_upload_and_reports_oversize(gateway, monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_BODY_BYTES", 1024)
    async def exercise():
        async def handler(request):
            return web.Response(body=await request.read())
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                async def chunks():
                    yield b"a" * 800
                    yield b"b" * 800
                async with session.post(gateway.status()["http_url"] + "/a/upload", data=chunks(), ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 413
                async with session.post(gateway.status()["http_url"] + "/a/upload", data=b"x" * 1025, ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 413
    asyncio.run(exercise())


def test_gateway_counts_chunked_upload_when_backend_responds_early(gateway, monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_BODY_BYTES", 1024)
    async def exercise():
        async def handler(request):
            return web.Response(text="early response")
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                async def chunks():
                    yield b"a" * 800
                    await asyncio.sleep(0.1)
                    yield b"b" * 800
                async with session.post(gateway.status()["http_url"] + "/a/upload", data=chunks(), ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 413
    asyncio.run(exercise())


def test_gateway_finishes_valid_upload_after_early_streaming_response_headers(gateway):
    async def exercise():
        async def handler(request):
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(await request.read())
            await response.write_eof()
            return response
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                async def chunks():
                    yield b"first"
                    await asyncio.sleep(0.05)
                    yield b"second"
                async with asyncio.timeout(2):
                    async with session.post(gateway.status()["http_url"] + "/a/upload", data=chunks(), ssl=gateway_ssl(gateway)) as response:
                        assert response.status == 200
                        assert await response.read() == b"firstsecond"
    asyncio.run(exercise())


def test_gateway_reports_backend_transport_failure_and_recovers(gateway):
    async def exercise():
        async def handler(request):
            return web.Response(text="available")
        async with backend(gateway, handler) as endpoint:
            publish(gateway, endpoint)
            async with ClientSession() as session:
                async with session.get(gateway.status()["http_url"] + "/a", ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 200
        async with ClientSession() as session:
            async with session.get(gateway.status()["http_url"] + "/a", ssl=gateway_ssl(gateway)) as response:
                assert response.status == 503
        async with backend(gateway, handler) as replacement:
            publish(gateway, replacement)
            async with ClientSession() as session:
                async with session.get(gateway.status()["http_url"] + "/a", ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 200
    asyncio.run(exercise())


def test_gateway_checks_backend_certificate_before_forwarding(gateway):
    async def exercise():
        async def handler(request):
            return web.Response(text="trusted")
        async with backend(gateway, handler, tls=True) as endpoint:
            publish(gateway, {**endpoint, "ca_file": gateway.status()["ca_file"]})
            async with ClientSession() as session:
                origin, context = gateway.status()["http_url"], gateway_ssl(gateway)
                async with session.get(origin + "/a", ssl=context) as response:
                    assert response.status == 503
                publish(gateway, endpoint)
                async with session.get(origin + "/a", ssl=context) as response:
                    assert response.status == 200
                    assert await response.text() == "trusted"
    asyncio.run(exercise())


def test_two_consumers_coexist_and_removing_one_does_not_affect_the_other(gateway):
    async def exercise():
        async def mini(request):
            return web.Response(text="mini:" + request.headers.get("Cookie", ""))
        async def reikon(request):
            if request.headers.get("Authorization") != "Bearer reikon-token":
                return web.Response(status=401)
            return web.Response(text="reikon")
        async with backend(gateway, mini) as first, backend(gateway, reikon) as second:
            publish(gateway, first, prefixes=("a",))
            publish(gateway, second, prefixes=("reikon",), owner="reikon")
            async with ClientSession() as session:
                origin, context = gateway.status()["http_url"], gateway_ssl(gateway)
                async with session.get(origin + "/a/app", ssl=context, headers={"Cookie": "mini-session=own"}) as response:
                    assert await response.text() == "mini:mini-session=own"
                async with session.get(origin + "/reikon/api", ssl=context) as response:
                    assert response.status == 401
                async with session.get(origin + "/reikon/api", ssl=context, headers={"Authorization": "Bearer reikon-token"}) as response:
                    assert response.status == 200
                gateway.registry.change("remove", "a", "mini-apps", True)
                async with session.get(origin + "/reikon/api", ssl=context, headers={"Authorization": "Bearer reikon-token"}) as response:
                    assert response.status == 200 and await response.text() == "reikon"
    asyncio.run(exercise())


def test_http_timeout_does_not_impose_a_websocket_idle_deadline(tmp_path, monkeypatch):
    real_timeout = proxy_module.ClientTimeout
    monkeypatch.setattr(proxy_module, "ClientTimeout", lambda **values: real_timeout(**{**values, "sock_read": 0.05}))
    instance = PublicGateway(tmp_path, tmp_path / "configs" / "gateway", "private-token", 0)
    instance.start_http()
    async def exercise():
        async def handler(request):
            if request.path.endswith("/slow"):
                await asyncio.sleep(0.15)
                return web.Response(text="late")
            socket = web.WebSocketResponse()
            await socket.prepare(request)
            async for message in socket:
                await socket.send_str(str(message.data))
            return socket
        async with backend(instance, handler) as endpoint:
            publish(instance, endpoint)
            async with ClientSession() as session:
                origin, context = instance.status()["http_url"], gateway_ssl(instance)
                async with session.get(origin + "/a/slow", ssl=context) as response:
                    assert response.status == 504
                async with session.ws_connect(origin + "/a/socket", ssl=context) as socket:
                    await asyncio.sleep(0.15)
                    await socket.send_str("still-open")
                    assert (await socket.receive()).data == "still-open"
    try:
        asyncio.run(exercise())
    finally:
        instance.close()


def test_gateway_configuration_and_context_validate_without_leaking_fields(tmp_path):
    instance = PublicGateway(tmp_path, tmp_path / "configs" / "gateway", "private-token", 0)
    try:
        with pytest.raises(GatewayError) as failure:
            instance.configure({"http_port": 39390})
        assert failure.value.code == "confirmation_required"
        assert instance.configure({"http_port": 39392, "confirm": True}) == {"http_port": 39392, "restart_required": True}
        assert instance.config_path.name == "config.toml"
        assert "39392" in instance.config_path.read_text(encoding="utf-8")
        for bad in ("http://example.com", "https://user:secret@example.com", "https://example.com/path", "https://example.com:444", "https://example.com\n", " https://example.com", {}, 1):
            with pytest.raises(GatewayError):
                instance.context({"public_origin": bad})
        result = instance.context({"public_origin": "https://gateway.example.com/", "tunnel": {"running": True, "api_token": "secret", "credential": {"password": "secret"}}})
        assert result["public_origin"] == "https://gateway.example.com"
        assert result["tunnel"] == {"running": True}
    finally:
        instance.close()


def test_https_publication_requires_valid_explicit_ca_before_changing_routes(gateway, tmp_path):
    publish(gateway, {"target": "http://localhost:8100", "ready": True}, prefixes=("a",))
    invalid_ca = tmp_path / "configs" / "not-a-ca.pem"
    invalid_ca.write_text("not a certificate", encoding="utf-8")
    for target, ca in (("https://localhost:8101", None), ("HTTPS://LOCALHOST:8101", None), ("https://localhost:8101", str(invalid_ca))):
        with pytest.raises(GatewayError) as failure:
            publish(gateway, {"target": target, "ready": True, "ca_file": ca}, prefixes=("a", "oauth"))
        assert failure.value.code == "invalid_ca"
        assert gateway.registry.inspect("a")["target"] == "http://localhost:8100"
        assert [item["path_prefix"] for item in gateway.registry.list()] == ["a"]


def test_gateway_uses_explicit_ca_for_case_insensitive_https_scheme(gateway):
    async def exercise():
        async def handler(request):
            return web.Response(text="trusted")
        async with backend(gateway, handler, tls=True) as endpoint:
            publish(gateway, {**endpoint, "target": endpoint["target"].replace("https:", "HTTPS:")})
            async with ClientSession() as session:
                async with session.get(gateway.status()["http_url"] + "/a", ssl=gateway_ssl(gateway)) as response:
                    assert response.status == 200
                    assert await response.text() == "trusted"
    asyncio.run(exercise())
