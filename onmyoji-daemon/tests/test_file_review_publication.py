"""Revisão humana pelo MCP autenticado e pelo proxy HTTPS compartilhado."""
from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading

from aiohttp import ClientSession, CookieJar, TCPConnector

from onmyoji_daemon.gateway.service import PublicGateway
from onmyoji_daemon.mcp.file_review.service import FileReviewMcpService
from onmyoji_daemon.miniapps import MiniAppsGateway
from onmyoji_daemon.rpc import RpcServer


def test_review_claim_draft_preview_partial_application_and_revocation_through_https_proxy(tmp_path):
    """A origem pública é aceita mesmo quando o proxy reescreve o Host interno.

    Os dois trechos HTTPS validam suas CAs locais. A publicação representa o
    listener compartilhado sem configurar DNS nem chamar Cloudflare real.
    """
    root, workspace = tmp_path / "instance", tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    config = root / "configs/onmyoji-system.toml"
    config.parent.mkdir()
    config.write_text("[codex]\nproject_directory=" + json.dumps(str(workspace)), encoding="utf-8")
    original, proposed = root / "guide.md", root / "guide-2026-11-08.proposed.md"
    original.write_bytes("# Revisão\r\n\r\nTexto original.\r\n".encode("utf-8"))
    proposed.write_bytes("# Revisão\n\nTexto proposto.\n".encode("utf-8"))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        mini_port = int(probe.getsockname()[1])
    mini = MiniAppsGateway(root, root / "configs/daemon/services/mini-apps", "mini-test-token", mini_port)
    gateway = PublicGateway(root, root / "configs/daemon/services/gateway", "gateway-test-token", http_port=0)
    stopped = threading.Event()
    rpc = RpcServer("127.0.0.1", 0, "mini-test-token", mini.rpc)
    worker = threading.Thread(target=rpc.serve_forever, args=(stopped,), daemon=True)
    worker.start()
    mcp = FileReviewMcpService(root, endpoint_reader=lambda _root: ("127.0.0.1", rpc.port, "mini-test-token"))
    public_origin = "https://reviews.example.test"
    try:
        mini.start_http()
        gateway.start_http()
        gateway.rpc("gateway.publication.context", {"public_origin": public_origin, "tunnel": {"configured": True}})
        registered = gateway.rpc("gateway.publication.sync", {"owner": "mini-apps", "generation": "review-test", "listeners": mini.rpc("mini-apps.publication.describe", {})["listeners"]})
        mini.rpc("mini-apps.publication.update", {"public_origin": public_origin, "public_ready": True, "gateway_ready": True, "listeners": registered["listeners"], "tunnel": {"running": True}})
        created = mcp.invoke("create", {"original_path": str(original), "proposed_path": str(proposed), "confirm": True})
        assert created["publication"]["public_available"]
        assert created["claim_url"].startswith(public_origin + "/a/")
        proxy_origin = gateway.status()["http_url"]
        base = created["publication"]["public_url"].replace(public_origin, proxy_origin)

        async def verify():
            context = ssl.create_default_context(cafile=str(gateway.data_dir / "tls/ca.pem"))
            async with ClientSession(connector=TCPConnector(ssl=context), cookie_jar=CookieJar(unsafe=True)) as client:
                client.headers["Host"] = "reviews.example.test"
                async with client.get(base + "api/review") as response:
                    assert response.status == 401
                async with client.get(created["claim_url"].replace(public_origin, proxy_origin), allow_redirects=False) as response:
                    assert response.status == 302
                    assert response.headers["Location"] == "/a/" + created["app_id"] + "/"
                    assert "claim=" not in response.headers["Location"]
                    assert response.cookies["onmyoji_miniapp_session_" + created["app_id"]]["secure"]
                async with client.get(base) as response:
                    assert response.status == 200
                    assert response.headers["Cache-Control"] == "no-store"
                    assert response.headers["Referrer-Policy"] == "no-referrer"
                async with client.get(base + "review.js") as response:
                    assert response.status == 200
                async with client.get(base + "api/review") as response:
                    assert response.status == 200
                    review = await response.json()
                headers = {"Origin": public_origin, "X-Onmyoji-Review-CSRF": review["csrf_token"]}
                result = "# Revisão\n\nTexto ajustado pelo usuário.\n"
                async with client.post(base + "api/draft", headers=headers, json={"revision": review["revision"], "result": result}) as response:
                    assert response.status == 200
                    review = await response.json()
                for chunk in review["chunks"]:
                    async with client.post(base + "api/decision", headers=headers, json={"revision": review["revision"], "chunk_id": chunk["id"], "decision": "rejected"}) as response:
                        assert response.status == 200
                        review = await response.json()
                async with client.get(base + "api/preview") as response:
                    assert response.status == 200
                    preview = await response.json()
                    assert preview["result"] == result
                async with client.post(base + "api/apply", headers=headers, json={"revision": preview["revision"], "result_hash": preview["result_hash"]}) as response:
                    assert response.status == 200
                    receipt = await response.json()
                    assert receipt["status"] == "applied" and receipt["approval"] == "partial"
                    assert not receipt["proposed_removed"]
                async with client.get(base + "api/review") as response:
                    assert response.status == 503
                assert original.read_bytes() == result.replace("\n", "\r\n").encode("utf-8")
                assert proposed.exists()

        asyncio.run(verify())
        status = mcp.invoke("status", {"review_id": created["review_id"]})
        assert status["status"] == "applied"
        assert status["publication"]["state"] == "UNPUBLISHED"
        assert "claim_url" not in json.dumps(status) and "csrf_token" not in json.dumps(status)
    finally:
        stopped.set()
        worker.join(2)
        rpc.close()
        gateway.close()
        mini.stop_http()
        mini.db.close()
