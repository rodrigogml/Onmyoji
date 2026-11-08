"""Contrato HTTP da revisão com claim, rascunho, aprovação e revogação reais."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket

from aiohttp import ClientSession, TCPConnector
import pytest

from onmyoji_daemon.miniapps import MiniAppsGateway
from onmyoji_daemon.file_review.service import FileReviewService


@pytest.fixture
def review_gateway(tmp_path):
    root = tmp_path / "instance"
    workspace = tmp_path / "workspace"
    root.mkdir()
    workspace.mkdir()
    configs = root / "configs"
    configs.mkdir()
    (configs / "onmyoji-system.toml").write_text("[codex]\nproject_directory = " + json.dumps(str(workspace)) + "\n", encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    gateway = MiniAppsGateway(root, configs / "daemon/services/mini-apps", "test-token", port)
    original = root / "instructions.md"
    proposed = root / "instructions-2026-11-08.proposed.md"
    original.write_bytes("# Instruções\r\n\r\nTexto antigo.\r\n\r\nRemova este parágrafo.\r\n".encode("utf-8"))
    proposed.write_bytes("# Instruções\n\nTexto novo.\n".encode("utf-8"))
    yield gateway, original, proposed
    gateway.stop_http()
    gateway.db.close()


def _create(gateway, original, proposed, **extra):
    return gateway.rpc("mini-apps.file-review.create", {"original_path": str(original), "proposed_path": str(proposed), "confirm": True, **extra})


def test_create_publishes_trusted_template_without_exposing_content_or_changing_auth(review_gateway):
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    assert created["status"] == "editing"
    assert created["claim_url"].startswith(created["publication"]["local_url"] + "?claim=")
    assert created["publication"]["auth_type"] == "claim"
    assert created["publication"]["timeouts"]["single_client"] is True
    assert created["publication"]["timeouts"]["claim_timeout"] == 300
    assert (Path(created["publication"]["root_path"]) / "app.py").is_file()
    assert "Texto antigo" not in json.dumps(created)
    status = gateway.rpc("mini-apps.file-review.status", {"review_id": created["review_id"]})
    assert "claim_url" not in json.dumps(status)
    assert "csrf_token" not in json.dumps(status)
    assert "Texto novo" not in json.dumps(status)


@pytest.mark.parametrize("extra,code", [({"confirm": False}, "confirmation_required"), ({"lifetime": 0}, "invalid_timeout"), ({"claim_timeout": True}, "invalid_timeout"), ({"idle_timeout": "10"}, "invalid_timeout")])
def test_create_rejects_missing_confirmation_and_invalid_policies(review_gateway, extra, code):
    gateway, original, proposed = review_gateway
    with pytest.raises(ValueError) as error:
        _create(gateway, original, proposed, **extra)
    assert error.value.code == code
    assert gateway.list() == []


def test_https_claim_review_partial_apply_and_revocation(review_gateway):
    """A edição manual requer conferência vigente e preserva proposta parcial e CRLF."""
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    gateway.start_http()

    async def verify():
        async with ClientSession(connector=TCPConnector(ssl=False)) as client:
            base = created["publication"]["local_url"]
            async with client.get(base + "api/review") as response:
                assert response.status == 401
            async with client.get(base + "?claim=wrong", allow_redirects=False) as response:
                assert response.status == 401
            async with client.get(created["claim_url"], allow_redirects=False) as response:
                assert response.status == 302
                assert "claim=" not in response.headers["Location"]
            async with client.get(base) as response:
                assert response.status == 200
                assert response.headers["Referrer-Policy"] == "no-referrer"
                assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
                assert "Texto antigo" not in await response.text()
            for resource in ("review.js", "review.css"):
                async with client.get(base + resource) as response:
                    assert response.status == 200
            for resource in ("original.bin", "proposed.bin", "manifest.json", "app.py", "api/unknown"):
                async with client.get(base + resource) as response:
                    assert response.status == 404
            async with client.get(base + "api/review") as response:
                review = await response.json()
            assert review["original"].startswith("# Instruções\n")
            headers = {"X-Onmyoji-Review-CSRF": review["csrf_token"]}
            draft = {"revision": review["revision"], "result": "# Instruções\n\nMinha edição manual.\n"}
            async with client.post(base + "api/draft", json=draft) as response:
                assert response.status == 403
            async with client.post(base + "api/draft", json=draft, headers={**headers, "Origin": "https://evil.example"}) as response:
                assert response.status == 403
                assert (await response.json())["error"]["code"] == "invalid_origin"
            async with client.post(base + "api/draft", json=draft, headers={**headers, "Origin": f"https://localhost:{gateway.http_port}"}) as response:
                assert response.status == 200
                review = await response.json()
            async with client.post(base + "api/draft", json=draft, headers=headers) as response:
                assert response.status == 409
            for chunk in review["chunks"]:
                async with client.post(base + "api/decision", json={"revision": review["revision"], "chunk_id": chunk["id"], "decision": "rejected"}, headers=headers) as response:
                    assert response.status == 200
                    review = await response.json()
            async with client.get(base + "api/preview") as response:
                preview = await response.json()
                assert response.status == 200
                assert preview["result"] == draft["result"]
                assert preview["chunks"]
            async with client.post(base + "api/apply", json={"revision": preview["revision"], "result_hash": "wrong"}, headers=headers) as response:
                assert response.status == 409
            async with client.post(base + "api/apply", json={"revision": preview["revision"], "result_hash": preview["result_hash"]}, headers=headers) as response:
                assert response.status == 200
                receipt = await response.json()
                assert receipt["status"] == "applied"
            assert original.read_bytes() == "# Instruções\r\n\r\nMinha edição manual.\r\n".encode("utf-8")
            assert proposed.is_file()
            assert gateway.inspect(created["app_id"])["state"] == "UNPUBLISHED"
            assert gateway.db.execute("SELECT COUNT(*) FROM sessions WHERE app_id=?", (created["app_id"],)).fetchone()[0] == 0
            async with client.get(base + "api/review") as response:
                assert response.status == 503
            status = gateway.file_review_status(created["review_id"])
            assert status["status"] == "applied"
            assert "csrf_token" not in json.dumps(status)

    asyncio.run(verify())


def test_https_integral_approval_removes_proposal(review_gateway):
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    gateway.start_http()

    async def verify():
        async with ClientSession(connector=TCPConnector(ssl=False)) as client:
            base = created["publication"]["local_url"]
            async with client.get(created["claim_url"]):
                pass
            async with client.get(base + "api/review") as response:
                review = await response.json()
            headers = {"X-Onmyoji-Review-CSRF": review["csrf_token"]}
            for chunk in review["chunks"]:
                async with client.post(base + "api/decision", json={"revision": review["revision"], "chunk_id": chunk["id"], "decision": "accepted"}, headers=headers) as response:
                    assert response.status == 200
                    review = await response.json()
            expected = proposed.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8")
            async with client.post(base + "api/apply", json={"revision": review["revision"], "result_hash": review["result_hash"]}, headers=headers) as response:
                assert response.status == 200
            assert original.read_bytes() == expected
            assert not proposed.exists()

    asyncio.run(verify())


def test_https_validation_cancel_and_external_conflict(review_gateway):
    """Pedidos inválidos não alteram o rascunho; conflito conserva o arquivo externo."""
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    gateway.start_http()

    async def verify():
        async with ClientSession(connector=TCPConnector(ssl=False)) as client:
            base = created["publication"]["local_url"]
            async with client.get(created["claim_url"]):
                pass
            async with client.get(base + "api/review") as response:
                review = await response.json()
            headers = {"X-Onmyoji-Review-CSRF": review["csrf_token"]}
            cases = [
                ("api/draft", {"revision": 0, "result": "text", "original_path": "unbound"}, 400),
                ("api/draft", None, 400),
                ("api/draft", [], 400),
                ("api/apply", {"revision": 0}, 400),
                ("api/decision", {"revision": 0}, 400),
                ("api/cancel", {"confirm": True}, 400),
            ]
            for route, data, expected in cases:
                async with client.post(base + route, data=json.dumps(data), headers={**headers, "Content-Type": "application/json"}) as response:
                    assert response.status == expected
            async with client.post(base + "api/draft", data="{", headers={**headers, "Content-Type": "application/json"}) as response:
                assert response.status == 400
                assert (await response.json())["error"]["code"] == "invalid_json"
            async with client.post(base + "api/draft", data="plain", headers=headers) as response:
                assert response.status == 415
            async with client.post(base + "api/draft", json={}, headers={"X-Onmyoji-Review-CSRF": "á"}) as response:
                assert response.status == 403
            async with client.put(base + "api/review") as response:
                assert response.status == 405
            async with client.head(base + "review.js") as response:
                assert response.status == 200
                assert await response.read() == b""
            for chunk in review["chunks"]:
                async with client.post(base + "api/decision", json={"revision": review["revision"], "chunk_id": chunk["id"], "decision": "rejected"}, headers=headers) as response:
                    review = await response.json()
            async with client.get(base + "api/preview") as response:
                preview = await response.json()
            external = b"Changed in another editor.\n"
            original.write_bytes(external)
            async with client.post(base + "api/apply", json={"revision": preview["revision"], "result_hash": preview["result_hash"]}, headers=headers) as response:
                assert response.status == 409
            assert original.read_bytes() == external
            assert proposed.exists()
            async with client.post(base + "api/cancel", json={}, headers=headers) as response:
                assert response.status == 200
                assert (await response.json())["status"] == "cancelled"
            assert gateway.inspect(created["app_id"])["state"] == "UNPUBLISHED"

    asyncio.run(verify())


def test_rpc_cancel_preserves_files_and_expiry_is_reported(review_gateway):
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    with pytest.raises(ValueError) as error:
        gateway.cancel_file_review({"review_id": created["review_id"]})
    assert error.value.code == "confirmation_required"
    result = gateway.rpc("mini-apps.file-review.cancel", {"review_id": created["review_id"], "confirm": True})
    assert result["status"] == "cancelled"
    assert original.is_file() and proposed.is_file()
    assert gateway.inspect(created["app_id"])["state"] == "UNPUBLISHED"
    next_review = _create(gateway, original, proposed, lifetime=1)
    gateway.db.execute("UPDATE apps SET created_at=0 WHERE id=?", (next_review["app_id"],))
    gateway.db.commit()
    assert gateway.file_review_status(next_review["review_id"])["status"] == "expired"


def test_two_reviews_keep_independent_claim_sessions_in_the_same_browser(review_gateway):
    """Abrir outra revisão não substitui a sessão da primeira Mini App."""
    gateway, original, proposed = review_gateway
    first = _create(gateway, original, proposed)
    second = _create(gateway, original, proposed)
    gateway.start_http()

    async def verify():
        async with ClientSession(connector=TCPConnector(ssl=False)) as client:
            for created in (first, second):
                async with client.get(created["claim_url"], allow_redirects=False) as response:
                    assert response.status == 302
                    cookie = response.cookies["onmyoji_miniapp_session_" + created["app_id"]]
                    assert cookie["path"] == "/a/" + created["app_id"]
                    assert cookie["secure"] and cookie["httponly"] and cookie["samesite"] == "Strict"
            for created in (first, second):
                async with client.get(created["publication"]["local_url"] + "api/review") as response:
                    assert response.status == 200
                    assert (await response.json())["review_id"] == created["review_id"]
            gateway.cancel_file_review({"review_id": first["review_id"], "confirm": True})
            async with client.get(second["publication"]["local_url"] + "api/review") as response:
                assert response.status == 200

    asyncio.run(verify())


def test_http_retry_recovers_applied_result_without_writing_original_again(review_gateway, monkeypatch):
    """Uma falha no recibo não perde a aprovação nem repete a gravação concluída."""
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    gateway.start_http()
    actual_persist = FileReviewService._persist
    interrupted = False

    def persist(service, directory, data):
        nonlocal interrupted
        if data["status"] == "applied" and not interrupted:
            interrupted = True
            raise OSError("receipt interrupted")
        return actual_persist(service, directory, data)

    monkeypatch.setattr(FileReviewService, "_persist", persist)

    async def verify():
        async with ClientSession(connector=TCPConnector(ssl=False)) as client:
            base = created["publication"]["local_url"]
            async with client.get(created["claim_url"]):
                pass
            async with client.get(base + "api/review") as response:
                review = await response.json()
            headers = {"X-Onmyoji-Review-CSRF": review["csrf_token"]}
            for chunk in review["chunks"]:
                async with client.post(base + "api/decision", json={"revision": review["revision"], "chunk_id": chunk["id"], "decision": "accepted"}, headers=headers) as response:
                    review = await response.json()
            approval = {"revision": review["revision"], "result_hash": review["result_hash"]}
            async with client.post(base + "api/apply", json=approval, headers=headers) as response:
                assert response.status == 503
            assert interrupted
            saved_identity = original.stat().st_ino
            assert not proposed.exists()
            async with client.post(base + "api/apply", json=approval, headers=headers) as response:
                assert response.status == 200
                assert (await response.json())["status"] == "applied"
            assert original.stat().st_ino == saved_identity
            assert gateway.inspect(created["app_id"])["state"] == "UNPUBLISHED"

    asyncio.run(verify())


def test_status_revokes_publication_when_completion_preceded_gateway_response(review_gateway):
    """A consulta recupera a revogação de uma conclusão sem resposta do adaptador."""
    gateway, original, proposed = review_gateway
    created = _create(gateway, original, proposed)
    service = gateway._file_reviews()
    review = service.view(created["review_id"])
    for chunk in review["chunks"]:
        review = service.decide(review["review_id"], review["revision"], chunk["id"], "accepted")
    service.apply(review["review_id"], review["revision"], review["result_hash"])
    assert gateway.inspect(created["app_id"])["state"] == "PUBLISHED_UNCLAIMED"
    status = gateway.file_review_status(created["review_id"])
    assert status["status"] == "applied"
    assert status["publication"]["state"] == "UNPUBLISHED"
