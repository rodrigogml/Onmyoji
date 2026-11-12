"""Link de teste efêmero no listener HTTPS real, sem MiniApps ou Cloudflare real."""
from __future__ import annotations

import asyncio
import json
import ssl
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

from aiohttp import ClientSession
import pytest
from yarl import URL

from onmyoji_daemon.gateway.component import publication_test as test_module
from onmyoji_daemon.gateway.component.publication_test import PublicationTest
from onmyoji_daemon.gateway.exception import GatewayError
from onmyoji_daemon.gateway.service import PublicGateway
from onmyoji_daemon import cli
from onmyoji_daemon.supervisor import Supervisor


@pytest.fixture
def gateway(tmp_path):
    instance = PublicGateway(tmp_path, tmp_path / "configs" / "daemon" / "services" / "gateway", "private-rpc", 0)
    instance.start_http()
    instance.context({"public_origin": "https://gateway.example.com", "tunnel": dict.fromkeys(("configured", "provisioned", "running", "connected", "ingress_ready"), True)})
    yield instance
    instance.close()


def start(gateway):
    return gateway.rpc("gateway.publication-test.start", {"confirm": True})


def status(gateway, attempt):
    return gateway.rpc("gateway.publication-test.status", {"test_id": attempt["test_id"]})


def request(gateway, path, method="GET"):
    """Simula localmente a chegada ao Gateway; não valida um domínio público."""
    async def exercise():
        context = ssl.create_default_context(cafile=gateway.status()["ca_file"])
        async with ClientSession() as session:
            async with session.request(method, URL(gateway.status()["http_url"] + path, encoded=True), ssl=context) as response:
                return response.status, await response.text(), dict(response.headers)
    return asyncio.run(exercise())


def test_exact_get_confirms_correct_gateway_and_consumes_nonce(gateway, caplog):
    before = gateway.registry.list()
    attempt = start(gateway)
    assert attempt["state"] == "waiting" and 1 <= attempt["expires_in_seconds"] <= 120
    assert len(attempt["test_id"]) == 32
    path = urlsplit(attempt["url"]).path
    assert len(path.removeprefix(PublicationTest.PATH_PREFIX)) >= 43
    code, body, headers = request(gateway, path)
    assert code == 200
    assert "Acesso confirmado" in body and "listener HTTPS do Gateway" in body
    assert "no-store" in headers["Cache-Control"]
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "default-src 'none'" in headers["Content-Security-Policy"]
    assert status(gateway, attempt)["state"] == "confirmed"
    assert "url" not in status(gateway, attempt)
    assert path not in body and path not in caplog.text
    assert "private-rpc" not in body
    assert request(gateway, path)[0] == 404
    assert gateway.registry.list() == before == []


@pytest.mark.parametrize("suffix,method", [("/extra", "GET"), ("?extra=1", "GET"), ("/", "GET"), ("", "HEAD"), ("", "POST"), ("", "OPTIONS"), ("", "TRACE")])
def test_wrong_path_or_method_cannot_confirm(gateway, suffix, method):
    attempt = start(gateway)
    assert request(gateway, urlsplit(attempt["url"]).path + suffix, method)[0] == 404
    assert status(gateway, attempt)["state"] == "waiting"


def test_wrong_nonce_encoded_path_and_unrelated_page_cannot_confirm(gateway):
    attempt = start(gateway)
    path = urlsplit(attempt["url"]).path
    for wrong in (PublicationTest.PATH_PREFIX + "wrong", path[:-1], path.replace("/internal/", "/%69nternal/"), "/a/any-page", "/internal/publication-test", "/"):
        assert request(gateway, wrong)[0] in {400, 404}
        assert status(gateway, attempt)["state"] == "waiting"


def test_cancel_is_idempotent_and_does_not_erase_confirmation(gateway):
    attempt = start(gateway)
    params = {"test_id": attempt["test_id"]}
    assert gateway.rpc("gateway.publication-test.cancel", params)["state"] == "cancelled"
    assert gateway.rpc("gateway.publication-test.cancel", params)["state"] == "cancelled"
    assert request(gateway, urlsplit(attempt["url"]).path)[0] == 404
    attempt = start(gateway)
    assert request(gateway, urlsplit(attempt["url"]).path)[0] == 200
    assert gateway.rpc("gateway.publication-test.cancel", {"test_id": attempt["test_id"]})["state"] == "confirmed"


def test_new_attempt_invalidates_old_url_and_old_timer_cannot_expire_new_one(gateway):
    previous = start(gateway)
    current = start(gateway)
    assert previous["url"] != current["url"]
    assert previous["test_id"] != current["test_id"]
    gateway.publication_test._expire(previous["test_id"])
    assert request(gateway, urlsplit(previous["url"]).path)[0] == 404
    assert status(gateway, current)["state"] == "waiting"
    with pytest.raises(GatewayError, match="Tentativa não encontrada"):
        status(gateway, previous)


def test_request_to_another_listener_does_not_confirm_the_attempt(gateway, tmp_path):
    attempt = start(gateway)
    other = PublicGateway(tmp_path, tmp_path / "configs" / "other-gateway", "other-private", 0)
    other.start_http()
    try:
        path = urlsplit(attempt["url"]).path
        assert request(other, path)[0] == 404
        assert status(gateway, attempt)["state"] == "waiting"
        assert request(gateway, path)[0] == 200
        assert status(gateway, attempt)["state"] == "confirmed"
    finally:
        other.close()


def test_deadline_is_checked_at_request_time_without_polling(gateway, monkeypatch):
    attempt = start(gateway)
    current_time = test_module.time.monotonic()
    monkeypatch.setattr(test_module, "time", SimpleNamespace(monotonic=lambda: current_time + 121))
    assert request(gateway, urlsplit(attempt["url"]).path)[0] == 404
    assert status(gateway, attempt) == {"test_id": attempt["test_id"], "state": "expired", "expires_in_seconds": 0}


def test_attempt_expires_automatically_even_when_setup_disappears(gateway, monkeypatch):
    monkeypatch.setattr(PublicationTest, "TTL_SECONDS", 0.02)
    attempt = start(gateway)
    timer = gateway.publication_test.timer
    timer.join(2)
    assert not timer.is_alive()
    assert gateway.publication_test.attempt.state == "expired"
    assert request(gateway, urlsplit(attempt["url"]).path)[0] == 404


@pytest.mark.parametrize("identifier", [None, "", "wrong", "á", [], 1])
def test_unknown_or_invalid_identifier_cannot_read_or_cancel(gateway, identifier):
    attempt = start(gateway)
    for action in ("status", "cancel"):
        with pytest.raises(GatewayError) as error:
            gateway.rpc("gateway.publication-test." + action, {"test_id": identifier})
        assert error.value.code == "test_not_found"
    assert status(gateway, attempt)["state"] == "waiting"


def test_missing_attempt_is_not_found(gateway):
    with pytest.raises(GatewayError) as error:
        gateway.rpc("gateway.publication-test.status", {"test_id": "a" * 32})
    assert error.value.code == "test_not_found"


def test_start_requires_confirmation(gateway):
    with pytest.raises(GatewayError) as error:
        gateway.rpc("gateway.publication-test.start", {})
    assert error.value.code == "confirmation_required"


@pytest.mark.parametrize("field", ["configured", "provisioned", "running", "connected", "ingress_ready"])
def test_start_explains_incomplete_publication(gateway, field):
    gateway.context({"public_origin": "https://gateway.example.com", "tunnel": {**gateway.tunnel, field: False}})
    with pytest.raises(GatewayError) as error:
        start(gateway)
    assert error.value.code == "publication_unavailable"
    assert "inicie Gateway e Tunnel" in str(error.value)


def test_origin_change_or_shutdown_invalidates_attempt(gateway):
    attempt = start(gateway)
    gateway.context({"public_origin": "https://other.example.com", "tunnel": gateway.tunnel})
    assert status(gateway, attempt)["state"] == "cancelled"
    assert request(gateway, urlsplit(attempt["url"]).path)[0] == 404
    attempt = start(gateway)
    gateway.stop_http()
    assert status(gateway, attempt)["state"] == "cancelled"
    with pytest.raises(GatewayError) as error:
        start(gateway)
    assert error.value.code == "publication_unavailable"


def test_no_public_origin_blocks_test_and_nonces_are_not_persisted(gateway):
    attempt = start(gateway)
    gateway.context({"public_origin": None, "tunnel": gateway.tunnel})
    with pytest.raises(GatewayError) as error:
        start(gateway)
    assert error.value.code == "publication_unavailable"
    for path in gateway.data_dir.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert attempt["test_id"].encode() not in data
            assert attempt["url"].encode() not in data


def test_cli_reaches_test_contract_through_supervisor_rpc(gateway, monkeypatch, capsys):
    """Exercita CLI/RPC/HTTPS local; nenhum processo ou Tunnel operacional é iniciado."""
    supervisor = Supervisor(gateway.root)
    supervisor.services["gateway"].process = SimpleNamespace(poll=lambda: None)

    def forward(name, method, params, **_kwargs):
        assert name == "gateway"
        return gateway.rpc(method, params)

    monkeypatch.setattr(supervisor, "_call_service", forward)
    monkeypatch.setattr(cli, "endpoint", lambda _root: ("127.0.0.1", supervisor.rpc.port, supervisor.token))
    stopped = threading.Event()
    worker = threading.Thread(target=supervisor.rpc.serve_forever, args=(stopped,), daemon=True)
    worker.start()
    command = ["--onmyoji-root", str(gateway.root), "gateway", "publication-test"]
    try:
        assert cli.main([*command, "start", "--confirm"]) == 0
        attempt = json.loads(capsys.readouterr().out)
        assert cli.main([*command, "status", attempt["test_id"]]) == 0
        assert json.loads(capsys.readouterr().out)["state"] == "waiting"
        assert request(gateway, urlsplit(attempt["url"]).path)[0] == 200
        assert cli.main([*command, "status", attempt["test_id"]]) == 0
        assert json.loads(capsys.readouterr().out)["state"] == "confirmed"
    finally:
        stopped.set()
        worker.join(2)
        supervisor.rpc.close()
