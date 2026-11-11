"""Contrato HTTP loopback do ingresso de eventos."""
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import socket
import time

from .test_notification_gateway import enable, event
from .test_telegram_gateway_async import lab
from onmyoji_daemon.agent_notification.http import NotificationHttpServer
from onmyoji_daemon.telegram_persistence import GatewayPersistenceError


def request(server, target, value):
    connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    connection.request("POST", target, body=json.dumps(value).encode("utf-8"), headers={"Content-Type": "application/json"})
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_wait_false_returns_202_after_durable_acceptance(lab):
    enable(lab)
    server = NotificationHttpServer(0, lab.gateway)
    assert server.server.server_address[0] == "127.0.0.1" and server.server.max_request_bytes == 1_048_576
    server.start()
    lab.gateway.work.acquire()
    try:
        value = event()
        status, result = request(server, "/v1/events?wait=false", value)
        assert status == 202
        assert result == {"event_id": value["event_id"], "received_at": result["received_at"], "accepted": True}
        assert result["received_at"].endswith("Z") and len(result["received_at"]) == 24
        assert lab.gateway.database.execute("SELECT COUNT(*) FROM notification_receipts WHERE event_id=?", (value["event_id"],)).fetchone() == (1,)
    finally:
        lab.gateway.work.release()
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.item(thread, "Resposta assíncrona"); lab.client.complete(thread)
        server.close()


def test_wait_true_defaults_and_returns_terminal_envelope(lab):
    enable(lab)
    server = NotificationHttpServer(0, lab.gateway)
    server.start()
    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(request, server, "/v1/events", event())
            thread = lab.client.wait_calls("turn/start")[0]["threadId"]
            lab.client.item(thread, "Resposta HTTP"); lab.client.complete(thread)
            status, result = future.result(3)
        assert status == 200
        assert result["status"] == "completed" and result["result"] == result["execution"] == "completed"
        assert result["delivery"] == "confirmed"
    finally:
        server.close()


def test_wait_true_returns_200_when_result_completed_but_delivery_failed(lab):
    from onmyoji_daemon.telegram_delivery import DeliverySuppressed
    enable(lab)
    server = NotificationHttpServer(0, lab.gateway)
    server.start()
    lab.gateway._deliver_terminal = lambda *_args: (_ for _ in ()).throw(DeliverySuppressed("fixture"))
    try:
        with ThreadPoolExecutor() as pool:
            future = pool.submit(request, server, "/v1/events", event())
            thread = lab.client.wait_calls("turn/start")[0]["threadId"]
            lab.client.item(thread, "Resultado sem entrega"); lab.client.complete(thread)
            status, result = future.result(3)
        assert status == 200
        assert result["status"] == "failed" and result["result"] == "completed"
        assert result["delivery"] == "failed" and result["reason"] == "delivery_failed"
    finally:
        server.close()


def test_http_rejections_use_closed_status_set(lab):
    enable(lab)
    server = NotificationHttpServer(0, lab.gateway, max_request_bytes=64)
    server.start()
    try:
        status, result = request(server, "/v1/events?wait=invalid", {})
        assert status == 400 and result["error"]["code"] == "invalid_request"
        status, result = request(server, "/v1/events/query", {})
        assert status == 400 and result["error"]["code"] == "invalid_request"
        status, result = request(server, "/v1/events", {"payload": "x" * 100})
        assert status == 413 and result["error"]["code"] == "request_too_large"
    finally:
        server.close()


def test_http_reports_disabled_and_capacity_without_accepting(lab, monkeypatch):
    server = NotificationHttpServer(0, lab.gateway)
    server.start()
    try:
        status, result = request(server, "/v1/events", event())
        assert status == 503 and result["error"]["code"] == "ingress_disabled"
        enable(lab)
        monkeypatch.setattr(lab.gateway.store, "enqueue", lambda *args, **kwargs: (_ for _ in ()).throw(GatewayPersistenceError("full")))
        status, result = request(server, "/v1/events", event())
        assert status == 429 and result["error"]["code"] == "capacity_exceeded"
    finally:
        server.close()


def test_waiter_limit_rejects_wait_true_before_acceptance_but_not_wait_false(lab):
    enable(lab)
    server = NotificationHttpServer(0, lab.gateway, long_limit=1)
    server.start()
    assert server.server.long_limit.acquire(blocking=False)
    try:
        value = event()
        status, result = request(server, "/v1/events", value)
        assert status == 429 and result["error"]["code"] == "capacity_exceeded"
        assert lab.gateway.database.execute("SELECT COUNT(*) FROM notification_receipts WHERE event_id=?", (value["event_id"],)).fetchone() == (0,)
        async_value = event()
        status, result = request(server, "/v1/events?wait=false", async_value)
        assert status == 202 and result["accepted"]
        thread = lab.client.wait_calls("turn/start")[0]["threadId"]
        lab.client.item(thread, "Resposta assíncrona"); lab.client.complete(thread)
    finally:
        server.server.long_limit.release()
        server.close()


def test_http_disconnect_does_not_cancel_accepted_event(lab):
    enable(lab)
    server = NotificationHttpServer(0, lab.gateway)
    server.start()
    value = event()
    client = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    body = json.dumps(value).encode("utf-8")
    client.sendall(b"POST /v1/events HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    thread = lab.client.wait_calls("turn/start")[0]["threadId"]
    client.close()
    lab.client.item(thread, "Concluído depois da desconexão"); lab.client.complete(thread)
    try:
        assert lab.api.wait_text("Concluído depois da desconexão")
        deadline = time.monotonic() + 3
        while True:
            row = lab.gateway.database.execute("SELECT execution_state,delivery_state FROM notification_receipts WHERE event_id=?", (value["event_id"],)).fetchone()
            if row == ("completed", "confirmed") or time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        assert row == ("completed", "confirmed")
    finally:
        server.close()
