from __future__ import annotations

from pathlib import Path
import asyncio
import socket

from aiohttp import ClientSession, TCPConnector
from onmyoji_daemon.miniapps import MiniAppsGateway
from onmyoji_daemon.miniapps_pki import ensure_material, server_context


def configured_gateway(tmp_path: Path) -> tuple[MiniAppsGateway, Path]:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    configs = tmp_path / "configs"; configs.mkdir()
    (configs / "onmyoji-system.toml").write_text("[codex]\nproject_directory = " + repr(str(workspace)) + "\n", encoding="utf-8")
    return MiniAppsGateway(tmp_path, tmp_path / "state", "token", 39391), workspace


def test_create_claim_publication_in_workspace(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    app = workspace / "report"; app.mkdir(); (app / "app.py").write_text("async def create_app(): return None\n", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "claim"}})
    assert created["state"] == "PUBLISHED_UNCLAIMED"
    assert created["claim_url"].startswith(created["url"] + "?claim=")
    assert gateway.inspect(created["id"])["auth_type"] == "claim"


def test_rejects_root_outside_workspace(tmp_path):
    gateway, _workspace = configured_gateway(tmp_path)
    app = tmp_path / "outside"; app.mkdir(); (app / "app.py").write_text("", encoding="utf-8")
    try: gateway.create({"root_path": str(app), "entrypoint": "app.py"})
    except ValueError as error: assert "workspace" in str(error)
    else: raise AssertionError("root outside workspace was accepted")


def test_rejects_entrypoint_with_syntax_error(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    app = workspace / "broken"; app.mkdir(); (app / "app.py").write_text("def broken(:\n", encoding="utf-8")
    try: gateway.create({"root_path": str(app), "entrypoint": "app.py"})
    except ValueError as error: assert "carregado" in str(error)
    else: raise AssertionError("broken entrypoint was accepted")


def test_unpublish_and_delete_preserve_registry_tombstone(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    app = workspace / "report"; app.mkdir(); (app / "app.py").write_text("", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "basic", "username": "u", "password": "p"}})
    assert gateway.unpublish(created["id"])["state"] == "UNPUBLISHED"
    assert gateway.delete(created["id"])["state"] == "DELETED"
    assert app.is_dir()


def test_basic_password_is_salted_scrypt(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    app = workspace / "report"; app.mkdir(); (app / "app.py").write_text("", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "basic", "username": "u", "password": "p"}})
    item = gateway._app(created["id"])
    assert item["auth"]["password_hash"].startswith("scrypt$")
    assert item["auth"]["password_hash"] != "p"


def test_callback_secret_is_returned_once_and_not_exposed_by_inspect(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    app = workspace / "callback"; app.mkdir(); (app / "app.py").write_text("", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "claim", "public_callbacks": [{"path": "/oauth/callback", "method": "GET"}]}})
    assert created["callback_secret"]
    assert "callback_secret" not in gateway.inspect(created["id"])


def test_events_are_limited_and_redact_secrets(tmp_path):
    gateway, _workspace = configured_gateway(tmp_path)
    gateway._event("app.error", "app", "failure", "token=leak password=bad")
    event = gateway.events("app", 1)[0]
    assert event["details"] == "token=[redacted] password=[redacted]"


def test_local_tls_material_is_created_with_server_context(tmp_path):
    ca, cert, key = ensure_material(tmp_path / "tls")
    assert ca.is_file() and cert.is_file() and key.is_file()
    assert server_context(tmp_path / "tls").minimum_version.name == "TLSv1_2"


def test_https_gateway_claims_session_and_forwards_internal_path(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = int(probe.getsockname()[1])
    gateway.http_port = port
    gateway.tunnel.local_url = f"https://localhost:{port}"
    app = workspace / "report"; app.mkdir()
    (app / "app.py").write_text("""
from onmyoji_daemon.miniapps import HttpResponse
class App:
    async def handle(self, request):
        return HttpResponse.json({"path": request.path, "session": request.authenticated_identity.authenticated})
async def create_app(): return App()
""", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py"})
    gateway.start_http()
    async def verify():
        connector = TCPConnector(ssl=False)
        async with ClientSession(connector=connector) as session:
            claim_url = created["claim_url"].replace("/?claim=", "/api/items?claim=")
            async with session.get(claim_url, allow_redirects=False) as claimed:
                assert claimed.status == 302
                location = claimed.headers["Location"]
                assert "claim=" not in location
            async with session.get(f"https://localhost:{port}" + location) as response:
                assert response.status == 200
                assert await response.json() == {"path": "/api/items", "session": True}
    try: asyncio.run(verify())
    finally: gateway.stop_http()


def test_https_gateway_forwards_authenticated_websocket(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = int(probe.getsockname()[1])
    gateway.http_port = port
    app = workspace / "socket"; app.mkdir()
    (app / "app.py").write_text("""
class App:
    async def handle(self, request): raise RuntimeError("HTTP not expected")
    async def websocket(self, socket):
        message = await socket.receive()
        await socket.send("echo:" + str(message))
async def create_app(): return App()
""", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py"})
    gateway.start_http()
    async def verify():
        connector = TCPConnector(ssl=False)
        async with ClientSession(connector=connector) as session:
            claim_url = created["claim_url"].replace("/?claim=", "/socket?claim=")
            async with session.get(claim_url, allow_redirects=False): pass
            async with session.ws_connect(f"wss://localhost:{port}/a/{created['id']}/socket") as socket_client:
                await socket_client.send_str("hello")
                assert (await socket_client.receive()).data == "echo:hello"
    try: asyncio.run(verify())
    finally: gateway.stop_http()


def test_gateway_accepts_synchronous_handle(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = int(probe.getsockname()[1])
    gateway.http_port = port
    app = workspace / "sync"; app.mkdir()
    (app / "app.py").write_text("""
from onmyoji_daemon.miniapps import HttpResponse
class App:
    def handle(self, request): return HttpResponse(body="sync")
def create_app(): return App()
""", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "basic", "username": "u", "password": "p"}})
    gateway.start_http()
    async def verify():
        connector = TCPConnector(ssl=False)
        async with ClientSession(connector=connector) as session:
            async with session.get(f"https://localhost:{port}/a/{created['id']}/", headers={"Authorization": "Basic dTpw"}) as response:
                assert response.status == 200 and await response.text() == "sync"
    try: asyncio.run(verify())
    finally: gateway.stop_http()


def test_declared_callback_accepts_its_one_time_secret(tmp_path):
    gateway, workspace = configured_gateway(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = int(probe.getsockname()[1])
    gateway.http_port = port
    app = workspace / "oauth"; app.mkdir()
    (app / "app.py").write_text("""
from onmyoji_daemon.miniapps import HttpResponse
class App:
    async def handle(self, request): return HttpResponse(body=request.authenticated_identity.auth_type)
def create_app(): return App()
""", encoding="utf-8")
    created = gateway.create({"root_path": str(app), "entrypoint": "app.py", "auth": {"type": "claim", "public_callbacks": [{"path": "/oauth/callback", "method": "GET"}]}})
    gateway.start_http()
    async def verify():
        connector = TCPConnector(ssl=False)
        async with ClientSession(connector=connector) as session:
            async with session.get(f"https://localhost:{port}/a/{created['id']}/oauth/callback", headers={"X-Onmyoji-Callback-Secret": created["callback_secret"]}) as response:
                assert response.status == 200 and await response.text() == "public_callback"
            async with session.get(f"https://localhost:{port}/a/{created['id']}/other") as response:
                assert response.status == 401
    try: asyncio.run(verify())
    finally: gateway.stop_http()
