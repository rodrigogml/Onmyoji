"""Compatibilidade de URLs e callbacks do consumidor do Gateway compartilhado."""
import asyncio
import json
import socket
import ssl
import threading
from concurrent.futures import ThreadPoolExecutor

from aiohttp import ClientSession, TCPConnector
import pytest

from onmyoji_daemon.gateway.service import PublicGateway
from onmyoji_daemon.miniapps import MiniAppsGateway
from onmyoji_daemon.rpc import RpcServer, call
from onmyoji_daemon.mcp.oauth_broker import BTG_INTEGRATION, EMAIL_INTEGRATION, OAuthError


def test_publication_snapshot_preserves_configured_url_when_tunnel_stops(tmp_path):
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    config = root / "configs/onmyoji-system.toml"
    config.parent.mkdir()
    config.write_text("[codex]\nproject_directory=" + json.dumps(str(workspace)), encoding="utf-8")
    app = workspace / "app"
    app.mkdir()
    (app / "app.py").write_text("def create_app(): return None", encoding="utf-8")
    mini = MiniAppsGateway(root, root / "configs/daemon/services/mini-apps", "private", 39391)
    try:
        mini.rpc("mini-apps.publication.update", {"public_origin": "https://gateway.example.com", "public_ready": False, "gateway_ready": True, "listeners": [{"path_prefix": "a", "enabled": True, "ready": True}], "tunnel": {"running": False}})
        publication = mini.create({"root_path": str(app), "entrypoint": "app.py"})
        assert publication["public_url"] == "https://gateway.example.com/a/" + publication["id"] + "/"
        assert publication["local_url"].startswith("https://localhost:39391/a/")
        assert not publication["public_available"]
        with pytest.raises(OAuthError):
            mini._oauth_origin()
        for method in ("status", "configure", "start", "stop", "restart", "provision"):
            with pytest.raises(ValueError, match="unknown"):
                mini.rpc("mini-apps.tunnel." + method, {})
    finally:
        mini.db.close()


@pytest.mark.parametrize("integration,callback", [(EMAIL_INTEGRATION, "/oauth/mcp/email/callback"), (BTG_INTEGRATION, "/oauth/mcp/btg/callback")])
def test_oauth_callbacks_and_redirect_keep_original_paths_through_proxy(tmp_path, integration, callback):
    root = tmp_path / "instance"
    root.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        mini_port = probe.getsockname()[1]
    mini = MiniAppsGateway(root, root / "configs/daemon/services/mini-apps", "mini-token", mini_port)
    gateway = PublicGateway(root, root / "configs/daemon/services/gateway", "gateway-token", http_port=0)
    calls = []
    oauth_started, oauth_finished, rpc_stopped = threading.Event(), threading.Event(), threading.Event()
    rpc_server = RpcServer("127.0.0.1", 0, "mini-token", mini.rpc)
    rpc_worker = threading.Thread(target=rpc_server.serve_forever, args=(rpc_stopped,), daemon=True)
    rpc_worker.start()
    executor = ThreadPoolExecutor(max_workers=1)

    class Broker:
        def start(self, integration, profile, confirm):
            assert profile == "test" and confirm is True
            assert mini._oauth_origin() == "https://gateway.example.com"
            oauth_started.set()
            assert oauth_finished.wait(5), "O callback público foi bloqueado pelo RPC OAuth."
            return {"session_id": "session-id"}
        def callback(self, integration, query):
            assert oauth_started.is_set() and not oauth_finished.is_set()
            calls.append((integration, query))
            oauth_finished.set()
            return "session-id"
        def result(self, session_id):
            assert session_id == "session-id"
            return "Autorização concluída."

    mini.oauth_broker = Broker()
    try:
        mini.start_http()
        gateway.start_http()
        gateway.rpc("gateway.publication.context", {"public_origin": "https://gateway.example.com", "tunnel": {"configured": True}})
        listeners = mini.rpc("mini-apps.publication.describe", {})["listeners"]
        result = gateway.rpc("gateway.publication.sync", {"owner": "mini-apps", "generation": "one", "listeners": listeners})
        mini.rpc("mini-apps.publication.update", {"public_origin": "https://gateway.example.com", "public_ready": True, "gateway_ready": True, "listeners": result["listeners"], "tunnel": {"running": True}})
        pending = executor.submit(call, "127.0.0.1", rpc_server.port, "mini-token", "mini-apps.oauth.start", {"integration": integration, "profile": "test", "confirm": True}, 10)
        assert oauth_started.wait(2)

        async def verify():
            context = ssl.create_default_context(cafile=str(gateway.data_dir / "tls/ca.pem"))
            async with ClientSession(connector=TCPConnector(ssl=context)) as session:
                async with session.get(gateway.status()["http_url"] + callback + "?code=example&state=one&state=two", headers={"Host": "gateway.example.com"}, allow_redirects=False) as response:
                    assert response.status == 303
                    assert response.headers["Location"] == "/oauth/result/session-id"
                async with session.get(gateway.status()["http_url"] + "/oauth/result/session-id", headers={"Host": "gateway.example.com"}) as response:
                    assert response.status == 200
                    assert "Autorização concluída" in await response.text()
                async with session.get(gateway.status()["http_url"] + "/internal/v1/tunnel", headers={"Host": "gateway.example.com"}) as response:
                    assert response.status == 404

        asyncio.run(verify())
        assert pending.result(timeout=2) == {"session_id": "session-id"}
        assert calls == [(integration, {"code": ["example"], "state": ["one", "two"]})]
    finally:
        oauth_finished.set()
        executor.shutdown(wait=True)
        rpc_stopped.set()
        rpc_worker.join(2)
        rpc_server.close()
        gateway.close()
        mini.stop_http()
        mini.db.close()
