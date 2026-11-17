"""Coordenação de consumo Omie entre instâncias e processos independentes."""
from __future__ import annotations

from hashlib import sha256
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import errno
import sqlite3
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace

import pytest

from onmyoji_daemon.mcp.omie import traffic
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.traffic import OmieTrafficGate


def test_posix_lock_backend_reserves_and_releases_the_file(tmp_path, monkeypatch):
    """O backend POSIX usa exclusão não bloqueante e libera o mesmo descritor."""
    calls = []
    backend = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=lambda descriptor, mode: calls.append((descriptor, mode)))
    monkeypatch.setitem(sys.modules, "fcntl", backend)
    monkeypatch.setattr(traffic, "os", SimpleNamespace(name="posix"))

    with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
        assert len(calls) == 1

    assert calls[0][0] == calls[1][0]
    assert [mode for _, mode in calls] == [backend.LOCK_EX | backend.LOCK_NB, backend.LOCK_UN]


@pytest.fixture
def clock(monkeypatch):
    now, sleeps = [10000.0], []
    monkeypatch.setattr(traffic, "time", lambda: now[0])
    monkeypatch.setattr(traffic, "monotonic", lambda: now[0])

    def advance(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(traffic, "sleep", advance)
    return now, sleeps


def test_interval_and_method_window_are_shared_by_independent_instances(tmp_path, clock):
    now, sleeps = clock
    first, second = OmieTrafficGate(), OmieTrafficGate()
    first.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0.5, method_limit=2)
    second.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0.5, method_limit=2)
    assert now[0] == 10000.5

    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0.5, method_limit=2)

    assert now[0] == 10060.0
    assert sleeps and all(0 < delay <= 1 for delay in sleeps)


def test_installation_limit_includes_other_methods_and_application_keys(tmp_path, clock):
    now, _ = clock
    OmieTrafficGate().await_slot(tmp_path, "key-a", "ConsultarProjeto", interval=0, root_limit=2)
    OmieTrafficGate().await_slot(tmp_path, "key-b", "ListarClientes", interval=0, root_limit=2)

    OmieTrafficGate().await_slot(tmp_path, "key-c", "ConsultarContaPagar", interval=0, root_limit=2)

    assert now[0] == 10060.0


def test_optional_trial_minute_limit_groups_methods_but_isolates_applications(tmp_path, clock):
    now, _ = clock
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "key-a", "Primeiro", interval=0, app_limit=2)
    gate.await_slot(tmp_path, "key-a", "Segundo", interval=0, app_limit=2)
    gate.await_slot(tmp_path, "key-b", "Primeiro", interval=0, app_limit=2)
    assert now[0] == 10000.0

    OmieTrafficGate().await_slot(tmp_path, "key-a", "Terceiro", interval=0, app_limit=2)

    assert now[0] == 10060.0


def test_daily_limit_has_no_midnight_reset_and_uses_compact_shared_buckets(tmp_path, clock):
    now, sleeps = clock
    now[0] = 3599.0
    OmieTrafficGate().await_slot(tmp_path, "key-a", "ConsultarProjeto", interval=0, day_limit=1)
    now[0] = 86400.0

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "key-a", "ConsultarContaPagar", interval=0, day_limit=1)

    assert error.value.code == "omie_rate_limited"
    assert error.value.details["reason"] == "application_daily_limit"
    assert error.value.details["retry_after_seconds"] == 3600
    assert sleeps == []
    OmieTrafficGate().await_slot(tmp_path, "key-b", "ConsultarProjeto", interval=0, day_limit=1)
    now[0] = 90000.0
    OmieTrafficGate().await_slot(tmp_path, "key-a", "ConsultarProjeto", interval=0, day_limit=1)
    with sqlite3.connect(tmp_path / "state/mcp/omie/traffic.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM daily").fetchone()[0] == 2


def test_identical_read_waits_from_completion_even_after_new_gate_instance(tmp_path, clock):
    now, sleeps = clock
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0, request_fingerprint="record-12", request_timeout=30)
    now[0] += 5.0
    gate.complete(tmp_path, "synthetic-key", "ConsultarProjeto", "record-12")
    gate.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0, request_fingerprint="record-34")
    assert now[0] == 10005.0

    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0, request_fingerprint="record-12")

    assert now[0] == 10066.0
    assert len(sleeps) == 61
    assert all(delay == 1 for delay in sleeps)


def test_crashed_read_preserves_timeout_plus_repetition_window(tmp_path, clock):
    now, sleeps = clock
    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0,
                               request_fingerprint="record-12", request_timeout=30)
    now[0] += 10

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0,
                                   request_fingerprint="record-12", request_timeout=30)

    assert error.value.code == "omie_rate_limited"
    assert error.value.details["retry_after_seconds"] == 81
    assert error.value.details["reason"] == "identical_request_interval"
    assert sleeps == []


def test_crashed_request_keeps_method_lease_for_other_parameters(tmp_path, clock):
    now, sleeps = clock
    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0,
                               request_fingerprint="record-12", request_timeout=30)
    now[0] += 10
    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ListarClientes", interval=0)
    assert now[0] == 10010

    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0,
                               request_fingerprint="record-34", request_timeout=30)

    assert now[0] == 10030
    assert len(sleeps) == 20


def test_successful_write_completion_releases_method_without_a_read_fingerprint(tmp_path, clock):
    now, sleeps = clock
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "synthetic-key", "AlterarLancCC", interval=0, request_timeout=30)
    now[0] += 5
    gate.complete(tmp_path, "synthetic-key", "AlterarLancCC", None)

    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "AlterarLancCC", interval=0, request_timeout=30)

    assert now[0] == 10005 and sleeps == []


def test_none_completion_does_not_create_state(tmp_path):
    OmieTrafficGate().complete(tmp_path, "synthetic-key", "ConsultarProjeto", None)
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("wait_seconds", [-1, None, True, float("inf"), 3601])
def test_invalid_serialization_budget_is_rejected_before_creating_state(tmp_path, wait_seconds):
    with pytest.raises(OmieError) as error:
        with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto", wait_seconds=wait_seconds):
            pytest.fail("A trava não pode aceitar um prazo inválido.")
    assert error.value.code == "invalid_config"
    assert not (tmp_path / "state").exists()


def test_custom_file_lock_budget_uses_the_requested_deadline(tmp_path, monkeypatch):
    def unavailable(*_args):
        raise OSError(errno.EACCES, "synthetic contention")

    monkeypatch.setattr("msvcrt.locking" if os.name == "nt" else "fcntl.flock", unavailable)
    ticks = iter([0.0, 0.0, 100.0])
    sleeps = []
    monkeypatch.setattr(traffic, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(traffic, "sleep", sleeps.append)

    with pytest.raises(OmieError) as error:
        with OmieTrafficGate().serialize(tmp_path, "synthetic-key", "ConsultarProjeto", wait_seconds=100):
            pytest.fail("O arquivo continua bloqueado.")

    assert error.value.code == "omie_local_busy"
    assert sleeps == [0.1]


def test_same_process_thread_wait_also_has_a_budget(tmp_path):
    gate, errors = OmieTrafficGate(), []

    def contender():
        try:
            with gate.serialize(tmp_path, "synthetic-key", "ConsultarProjeto", wait_seconds=0.05):
                errors.append("unexpected admission")
        except OmieError as error:
            errors.append(error.code)

    with gate.serialize(tmp_path, "synthetic-key", "ConsultarProjeto"):
        worker = Thread(target=contender)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert errors == ["omie_local_busy"]


def test_long_interval_exceeds_wait_budget_without_reserving_another_request(tmp_path, clock):
    now, sleeps = clock
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=120)

    assert error.value.code == "omie_rate_limited"
    assert error.value.details == {"retryable": False, "retry_after_seconds": 120, "reason": "method_interval"}
    assert now[0] == 10000.0 and sleeps == []
    with sqlite3.connect(tmp_path / "state/mcp/omie/traffic.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_largest_existing_restriction_wins_without_shortening_other_limits(tmp_path, clock):
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0, method_limit=1,
                    root_limit=1, app_limit=1, day_limit=1, request_fingerprint="record-12", request_timeout=30)

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=100000,
                                    method_limit=1, root_limit=1, app_limit=1, day_limit=1,
                                    request_fingerprint="record-12")

    assert error.value.code == "omie_rate_limited"
    assert error.value.details["reason"] == "method_interval"
    assert error.value.details["retry_after_seconds"] == 100000


def test_invalid_persisted_daily_counter_fails_closed(tmp_path, clock):
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)
    with sqlite3.connect(tmp_path / "state/mcp/omie/traffic.sqlite3") as connection:
        connection.execute("UPDATE daily SET count=-1")

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0, day_limit=100)

    assert error.value.code == "omie_local_state_error"


def test_wait_budget_uses_monotonic_clock_when_wall_clock_stalls(tmp_path, monkeypatch):
    monotonic_now, sleeps = [0.0], []
    monkeypatch.setattr(traffic, "time", lambda: 10000.0)
    monkeypatch.setattr(traffic, "monotonic", lambda: monotonic_now[0])

    def advance(seconds):
        sleeps.append(seconds)
        monotonic_now[0] += seconds

    monkeypatch.setattr(traffic, "sleep", advance)
    gate = OmieTrafficGate()
    gate.pause(tmp_path, "synthetic-key", "ConsultarProjeto", 2)

    with pytest.raises(OmieError) as error:
        gate.await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)

    assert error.value.code == "omie_cooldown"
    assert monotonic_now[0] <= 65
    assert sleeps and all(delay <= 1 for delay in sleeps)


def test_short_remote_pause_waits_but_long_cooldown_returns_immediately(tmp_path, clock):
    now, sleeps = clock
    gate = OmieTrafficGate()
    gate.pause(tmp_path, "synthetic-key", "ConsultarProjeto", 61)
    OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)
    assert now[0] == 10061.0 and len(sleeps) == 61
    gate.pause(tmp_path, "synthetic-key", "ConsultarProjeto", 1800)
    gate.pause(tmp_path, "synthetic-key", "ConsultarProjeto", 2)
    sleeps.clear()

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)

    assert error.value.code == "omie_cooldown"
    assert error.value.details["retry_after_seconds"] == 1800
    assert sleeps == []


def test_legacy_cooldowns_are_imported_without_shortening_or_deleting_them(tmp_path, clock):
    legacy = tmp_path / "state/mcp/omie/cooldowns.json"
    legacy.parent.mkdir(parents=True)
    key = sha256(b"synthetic-key\0ConsultarProjeto").hexdigest()
    content = json.dumps({key: 11800.0, "expired": 9000.0})
    legacy.write_text(content, encoding="utf-8")

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto")

    assert error.value.code == "omie_cooldown"
    assert error.value.details["retry_after_seconds"] == 1800
    assert legacy.read_text(encoding="utf-8") == content
    with sqlite3.connect(legacy.parent / "traffic.sqlite3") as connection:
        assert connection.execute("SELECT method, until FROM cooldowns").fetchall() == [(key, 11800.0)]


@pytest.mark.parametrize("content", ["sensitive invalid JSON", "[]", '{"hash": "sensitive"}', '{"hash": NaN}'])
def test_invalid_legacy_cooldowns_fail_closed(tmp_path, content):
    legacy = tmp_path / "state/mcp/omie/cooldowns.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(content, encoding="utf-8")

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)

    assert error.value.code == "omie_local_state_error"
    assert "sensitive" not in str(error.value.as_dict())


@pytest.mark.parametrize("failure", ["corrupt_database", "future_schema", "invalid_timestamp", "blocked_directory"])
def test_unavailable_or_invalid_shared_state_never_falls_back_to_local_counters(tmp_path, failure, clock):
    database = tmp_path / "state/mcp/omie/traffic.sqlite3"
    if failure == "blocked_directory":
        (tmp_path / "state").write_text("sensitive obstruction", encoding="utf-8")
    elif failure == "corrupt_database":
        database.parent.mkdir(parents=True)
        database.write_bytes(b"sensitive not a sqlite database")
    else:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)
        with sqlite3.connect(database) as connection:
            if failure == "future_schema":
                connection.execute("PRAGMA user_version=2")
            else:
                connection.execute("UPDATE last_calls SET sent='sensitive'")

    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", interval=0)

    assert error.value.code == "omie_local_state_error"
    assert error.value.details == {"retryable": False}
    assert "sensitive" not in str(error.value.as_dict())


@pytest.mark.parametrize("arguments", [
    {"interval": -1}, {"interval": None}, {"interval": float("nan")},
    {"method_limit": 0}, {"root_limit": True}, {"app_limit": 0}, {"day_limit": -1},
    {"request_fingerprint": ""}, {"request_fingerprint": 123},
    {"request_timeout": -1}, {"request_timeout": None}, {"request_timeout": True},
    {"request_timeout": float("nan")}, {"request_timeout": 3501},
])
def test_invalid_limits_do_not_create_shared_state(tmp_path, arguments):
    with pytest.raises(OmieError) as error:
        OmieTrafficGate().await_slot(tmp_path, "synthetic-key", "ConsultarProjeto", **arguments)
    assert error.value.code == "invalid_config"
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("seconds", [None, True, float("inf")])
def test_invalid_pause_duration_is_rejected(tmp_path, seconds):
    with pytest.raises(OmieError) as error:
        OmieTrafficGate().pause(tmp_path, "synthetic-key", "ConsultarProjeto", seconds)
    assert error.value.code == "invalid_config"


@pytest.mark.parametrize("fingerprint", ["", 123, False])
def test_invalid_completion_fingerprint_is_rejected(tmp_path, fingerprint):
    with pytest.raises(OmieError) as error:
        OmieTrafficGate().complete(tmp_path, "synthetic-key", "ConsultarProjeto", fingerprint)
    assert error.value.code == "invalid_config"
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("headers,expected", [
    (None, None), ({}, None), ({"Retry-After": "12.5"}, 12.5), ({"Retry-After": "-3"}, 0),
    ({"Retry-After": "nan"}, None), ({"Retry-After": "inf"}, None),
    ({"Retry-After": "invalid"}, None), ({"Retry-After": []}, None),
    ({"Retry-After": "Tue, 17 Nov 2026 12:00:00 GMT"}, 30),
    ({"Retry-After": "Tue, 17 Nov 2026 12:00:00"}, 30),
])
def test_retry_after_supports_numeric_and_http_dates_without_trusting_invalid_values(monkeypatch, headers, expected):
    class ClockDate(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 17, 11, 59, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(traffic, "datetime", ClockDate)

    assert traffic.retry_after_seconds(headers) == expected


def test_shared_database_contains_no_credential_or_request_payload(tmp_path, clock):
    gate = OmieTrafficGate()
    gate.await_slot(tmp_path, "PRIVATE-APP-KEY", "ConsultarProjeto", interval=0, request_fingerprint="PRIVATE-REQUEST")
    gate.complete(tmp_path, "PRIVATE-APP-KEY", "ConsultarProjeto", "PRIVATE-REQUEST")
    gate.pause(tmp_path, "PRIVATE-APP-KEY", "ConsultarProjeto", 1800)

    data = (tmp_path / "state/mcp/omie/traffic.sqlite3").read_bytes()
    assert b"PRIVATE-APP-KEY" not in data and b"PRIVATE-REQUEST" not in data


def test_daily_reservations_are_atomic_between_independent_processes(tmp_path):
    """Três CLIs concorrentes compartilham a mesma última vaga da aplicação."""
    source = str(Path(traffic.__file__).resolve().parents[3])
    script = """
import json
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from onmyoji_daemon.mcp.omie.traffic import OmieTrafficGate
from onmyoji_daemon.mcp.omie.exception import OmieError
try:
    OmieTrafficGate().await_slot(Path(sys.argv[2]), "synthetic-key", "ConsultarProjeto", interval=0, day_limit=1)
    print(json.dumps({"result": "admitted"}))
except OmieError as error:
    print(json.dumps({"result": error.code, "reason": error.details.get("reason")}))
"""
    processes = [subprocess.Popen(
        [sys.executable, "-B", "-c", script, source, str(tmp_path)], cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) for _ in range(3)]
    results = []
    try:
        for process in processes:
            output, errors = process.communicate(timeout=15)
            assert process.returncode == 0, errors
            results.append(json.loads(output))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    assert [item["result"] for item in results].count("admitted") == 1
    assert [item["result"] for item in results].count("omie_rate_limited") == 2
    assert all(item.get("reason") == "application_daily_limit" for item in results if item["result"] != "admitted")


def test_concurrent_process_pauses_preserve_all_methods(tmp_path):
    source = str(Path(traffic.__file__).resolve().parents[3])
    script = """
from pathlib import Path
import sys
sys.path.insert(0, sys.argv[1])
from onmyoji_daemon.mcp.omie.traffic import OmieTrafficGate
OmieTrafficGate().pause(Path(sys.argv[2]), "synthetic-key", sys.argv[3], 1800)
"""
    processes = [subprocess.Popen(
        [sys.executable, "-B", "-c", script, source, str(tmp_path), method], cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    ) for method in ("ConsultarProjeto", "ListarClientes")]
    try:
        for process in processes:
            _, errors = process.communicate(timeout=15)
            assert process.returncode == 0, errors
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
    for method in ("ConsultarProjeto", "ListarClientes"):
        with pytest.raises(OmieError) as error:
            OmieTrafficGate().await_slot(tmp_path, "synthetic-key", method)
        assert error.value.code == "omie_cooldown"
