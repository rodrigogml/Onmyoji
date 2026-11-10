"""Contratos de repetição e exclusão local das inclusões de contas BIS10."""
from __future__ import annotations

import multiprocessing
from pathlib import Path
import sqlite3

import pytest

from onmyoji_daemon.mcp.bis10cmd.requests import RequestStore, RequestStoreError, destination_id


DESTINATION = destination_id("bis10.example", 8080)
CODE = "0" + "1" * 43
PAYLOAD = {"code": CODE, "original_value": "125.00", "categories": [{"id": 7, "value": "125.00"}]}


def _claim(path, request_id, barrier, queue):
    """Cada processo abre sua conexão antes de disputar a mesma reserva."""
    try:
        store = RequestStore(Path(path))
        barrier.wait(timeout=15)
        record, is_new = store.begin(DESTINATION, 1, request_id, PAYLOAD, CODE)
        queue.put(("ok", record["request_id"], is_new))
    except RequestStoreError as error:
        queue.put((error.code, request_id, False))
    except BaseException as error:
        queue.put(("unexpected", repr(error), False))


def test_begin_freezes_effective_defaults_and_nested_values(tmp_path):
    """Replay usa conteúdo normalizado e os padrões efetivos da primeira chamada."""
    path = tmp_path / "work" / "requests.sqlite3"
    store = RequestStore(path)
    payload = {"code": CODE, "categories": [{"value": "125.00", "id": 7}]}
    effective = {**payload, "entry_date": "2026-11-07T11:00:00"}
    first, is_new = store.begin(DESTINATION, 1, "request-1", payload, CODE, effective_payload=effective)
    assert is_new and first["state"] == "SENDING"
    payload["categories"][0]["value"] = "999.00"
    first["payload"]["categories"][0]["id"] = 99
    reordered = {"categories": [{"id": 7, "value": "125.00"}], "code": CODE}
    replay, is_new = RequestStore(path).begin(DESTINATION, 1, "request-1", reordered, CODE,
                                            effective_payload={**reordered, "entry_date": "2027-01-01T12:00:00"})
    assert not is_new
    assert replay["payload"] == {**reordered, "entry_date": "2026-11-07T11:00:00"}
    assert replay["payload_hash"] == first["payload_hash"]
    assert replay["created_at"] == first["created_at"]


@pytest.mark.parametrize("change", ["payload", "code"])
def test_request_id_cannot_be_rebound(tmp_path, change):
    store = RequestStore(tmp_path / "requests.sqlite3")
    original, _ = store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    payload = {**PAYLOAD, "original_value": "126.00"} if change == "payload" else PAYLOAD
    code = "2" * 44 if change == "code" else CODE
    with pytest.raises(RequestStoreError) as caught:
        store.begin(DESTINATION, 1, "request-1", payload, code)
    assert caught.value.code == "request_conflict"
    assert store.get(DESTINATION, 1, "request-1") == original


@pytest.mark.parametrize("state", ["SENDING", "UNKNOWN"])
def test_pending_code_cannot_be_bypassed_with_new_key_or_restart(tmp_path, state):
    path = tmp_path / "requests.sqlite3"
    store = RequestStore(path)
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    if state == "UNKNOWN":
        store.unknown(DESTINATION, 1, "request-1", {"code": "timeout"})
    # Uma data antiga não constitui lease: o bloqueio persiste sem TTL.
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE bill_requests SET updated_at='2000-01-01T00:00:00+00:00'")
    reopened = RequestStore(path)
    replay, is_new = reopened.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    assert not is_new and replay["state"] == state
    with pytest.raises(RequestStoreError) as caught:
        reopened.begin(DESTINATION, 1, "new-key", PAYLOAD, CODE)
    assert caught.value.code == "bill_code_pending"
    assert caught.value.data == {"request_id": "request-1", "state": state}
    assert reopened.get(DESTINATION, 1, "new-key") is None


def test_pending_code_scope_is_destination_and_company(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3")
    assert store.begin(DESTINATION, 1, "same-key", PAYLOAD, CODE)[1]
    assert store.begin(DESTINATION, 2, "same-key", PAYLOAD, CODE)[1]
    other = destination_id("bis10.example", 8180)
    assert store.begin(other, 1, "same-key", PAYLOAD, CODE)[1]
    assert store.begin(DESTINATION, 1, "other-code", {"code": "2" * 44}, "2" * 44)[1]


@pytest.mark.parametrize("state", ["SUCCEEDED", "REJECTED"])
def test_terminal_results_replay_without_claiming_again(tmp_path, state):
    path = tmp_path / "requests.sqlite3"
    store = RequestStore(path)
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    result = {"bill_id": 12, "payment_ids": [34]} if state == "SUCCEEDED" else {"code": "validation_error", "field": "due_date"}
    method = store.success if state == "SUCCEEDED" else store.rejected
    final = method(DESTINATION, 1, "request-1", result)
    assert final["state"] == state and final["result"] == result
    assert method(DESTINATION, 1, "request-1", result) == final
    result["mutated"] = True
    replay, is_new = RequestStore(path).begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    assert not is_new and "mutated" not in replay["result"]
    # Código reutilizável pode ter nova solicitação depois de resultado conhecido.
    assert store.begin(DESTINATION, 1, "request-2", PAYLOAD, CODE)[1]
    with pytest.raises(RequestStoreError) as caught:
        store.unknown(DESTINATION, 1, "request-1", {"code": "late_timeout"})
    assert caught.value.code == "request_state_conflict"


def test_unknown_requires_unambiguous_success_to_release_code(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3")
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    uncertain = store.unknown(DESTINATION, 1, "request-1", {"code": "incomplete_response"})
    assert store.unknown(DESTINATION, 1, "request-1") == uncertain
    with pytest.raises(RequestStoreError) as caught:
        store.rejected(DESTINATION, 1, "request-1", {"code": "not_found_yet"})
    assert caught.value.code == "request_state_conflict"
    assert store.get(DESTINATION, 1, "request-1")["state"] == "UNKNOWN"
    confirmed = store.success(DESTINATION, 1, "request-1", {"bill_id": 12, "payment_ids": [34]})
    assert confirmed["state"] == "SUCCEEDED"
    assert store.begin(DESTINATION, 1, "request-2", PAYLOAD, CODE)[1]
    with pytest.raises(RequestStoreError) as caught:
        store.success(DESTINATION, 1, "request-1", {"bill_id": 99})
    assert caught.value.code == "request_state_conflict"


@pytest.mark.parametrize("request_ids,expected", [
    (("same", "same"), ["ok", "ok"]),
    (("first", "second"), ["bill_code_pending", "ok"]),
])
def test_reservations_are_exclusive_across_processes(tmp_path, request_ids, expected):
    """A exclusão depende da transação SQLite, não de locks internos do Python."""
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    path = tmp_path / "requests.sqlite3"
    workers = [context.Process(target=_claim, args=(str(path), identifier, barrier, queue)) for identifier in request_ids]
    for worker in workers:
        worker.start()
    try:
        outcomes = [queue.get(timeout=30) for _ in workers]
        assert sorted(outcome[0] for outcome in outcomes) == expected
        assert sum(outcome[2] for outcome in outcomes) == 1
    finally:
        for worker in workers:
            worker.join(timeout=10)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=10)
        queue.close()
    assert all(worker.exitcode == 0 for worker in workers)


@pytest.mark.parametrize("payload,code", [
    (None, CODE), ({"amount": float("nan")}, CODE), ({"amount": float("inf")}, CODE),
    ({1: "wrong key"}, CODE), ({"category": object()}, CODE), ({"categories": (1, 2)}, CODE),
    ({}, None), ({}, "1" * 43), ({}, "1" * 45), ({}, "١" * 44), ({}, "A" * 44),
])
def test_invalid_payload_or_code_does_not_reserve(tmp_path, payload, code):
    store = RequestStore(tmp_path / "requests.sqlite3")
    with pytest.raises(RequestStoreError) as caught:
        store.begin(DESTINATION, 1, "request-1", payload, code)
    assert caught.value.code == "invalid_request"
    assert store.get(DESTINATION, 1, "request-1") is None


@pytest.mark.parametrize("destination,company,request_id", [
    (None, 1, "key"), ("host:8080", 1, "key"), ("g" * 64, 1, "key"),
    (DESTINATION, None, "key"), (DESTINATION, True, "key"), (DESTINATION, 0, "key"),
    (DESTINATION, -1, "key"), (DESTINATION, 1, None), (DESTINATION, 1, ""),
    (DESTINATION, 1, " "), (DESTINATION, 1, "key\nvalue"), (DESTINATION, 1, "x" * 129),
])
def test_invalid_scope_is_rejected(tmp_path, destination, company, request_id):
    store = RequestStore(tmp_path / "requests.sqlite3")
    with pytest.raises(RequestStoreError) as caught:
        store.get(destination, company, request_id)
    assert caught.value.code == "invalid_request"


def test_missing_request_and_invalid_results(tmp_path):
    store = RequestStore(tmp_path / "requests.sqlite3")
    assert store.get(DESTINATION, 1, "missing") is None
    with pytest.raises(RequestStoreError) as caught:
        store.unknown(DESTINATION, 1, "missing")
    assert caught.value.code == "request_not_found"
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    for value in (None, [], {"value": float("nan")}):
        with pytest.raises(RequestStoreError) as caught:
            store.success(DESTINATION, 1, "request-1", value)
        assert caught.value.code == "invalid_request"
    assert store.get(DESTINATION, 1, "request-1")["state"] == "SENDING"


def test_destination_normalizes_presentation_without_resolving_dns():
    assert DESTINATION == destination_id(" BIS10.EXAMPLE. ", 8080)
    assert destination_id("[2001:0db8::1]", 8080) == destination_id("2001:db8::1", 8080)
    assert DESTINATION != destination_id("other-alias.example", 8080)
    assert DESTINATION != destination_id("bis10.example", 8180)
    assert "bis10" not in DESTINATION


@pytest.mark.parametrize("host,port", [(None, 8080), ("", 8080), ("..", 8080), ("a b", 8080),
                                        ("http://host", 8080), ("user@host", 8080),
                                        ("host", 0), ("host", 65536), ("host", True), ("host", "8080")])
def test_invalid_destinations_are_rejected(host, port):
    with pytest.raises(RequestStoreError) as caught:
        destination_id(host, port)
    assert caught.value.code == "invalid_request"


def test_storage_failures_and_future_schema_fail_closed(tmp_path):
    with pytest.raises(RequestStoreError) as caught:
        RequestStore(Path("relative.sqlite3"))
    assert caught.value.code == "invalid_storage"
    path = tmp_path / "requests.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=2")
    with pytest.raises(RequestStoreError) as caught:
        RequestStore(path)
    assert caught.value.code == "request_schema_version"
    with pytest.raises(RequestStoreError) as caught:
        RequestStore(tmp_path)
    assert caught.value.code == "request_storage_error"


@pytest.mark.parametrize("field,value", [("payload", "broken JSON"), ("payload", "[]"),
                                         ("result", "broken JSON"), ("result", "[]")])
def test_damaged_record_fails_closed(tmp_path, field, value):
    path = tmp_path / "requests.sqlite3"
    store = RequestStore(path)
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE bill_requests SET " + field + "=?", (value,))
    with pytest.raises(RequestStoreError) as caught:
        store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    assert caught.value.code == "request_storage_error"
    with pytest.raises(RequestStoreError) as caught:
        store.begin(DESTINATION, 1, "request-2", PAYLOAD, CODE)
    assert caught.value.code == "bill_code_pending"


def test_database_constraint_preserves_pending_code_exclusion(tmp_path):
    """Mesmo uma inserção fora de begin encontra a exclusão do índice SQLite."""
    path = tmp_path / "requests.sqlite3"
    store = RequestStore(path)
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    with sqlite3.connect(path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("""INSERT INTO bill_requests
                SELECT destination,company_id,'other-key',canonical_code,payload_hash,payload,
                       state,result,created_at,updated_at FROM bill_requests""")
    assert store.get(DESTINATION, 1, "other-key") is None


def test_workspace_parent_must_be_a_directory(tmp_path):
    blocker = tmp_path / "work"
    blocker.write_text("occupied", encoding="utf-8")
    with pytest.raises(RequestStoreError) as caught:
        RequestStore(blocker / "requests.sqlite3")
    assert caught.value.code == "request_storage_error"


def test_unavailable_schema_does_not_authorize_a_new_attempt(tmp_path):
    path = tmp_path / "requests.sqlite3"
    store = RequestStore(path)
    store.begin(DESTINATION, 1, "request-1", PAYLOAD, CODE)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE bill_requests RENAME TO inaccessible")
    with pytest.raises(RequestStoreError) as caught:
        store.begin(DESTINATION, 1, "new-key", PAYLOAD, CODE)
    assert caught.value.code == "request_storage_error"
