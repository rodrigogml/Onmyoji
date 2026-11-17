"""Contratos do cache nativo Omie, com estado privado e invalidação entre processos."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie import response_cache
from onmyoji_daemon.mcp.omie.response_cache import ResponseCache


@pytest.fixture
def clock(monkeypatch):
    value = [100.0]
    monkeypatch.setattr(response_cache, "time", lambda: value[0])
    return value


def test_cache_is_shared_isolated_and_returns_independent_json_objects(tmp_path, clock):
    cache = ResponseCache(tmp_path, "synthetic-app-secret-key")
    params = {"codigo": "PRIVATE-QUERY-001", "pagina": 1}
    assert cache.generation("projects") == 0
    assert cache.put("projects", "ConsultarProjeto", params, {"codigo": 12, "items": []}, generation=0)

    hit = ResponseCache(tmp_path, "synthetic-app-secret-key").get("projects", "ConsultarProjeto", dict(reversed(list(params.items()))))

    assert hit.data == {"codigo": 12, "items": []}
    assert (hit.stored_at, hit.expires_at, hit.generation) == (100, 161, 0)
    hit.data["items"].append("local mutation")
    assert cache.get("projects", "ConsultarProjeto", params).data["items"] == []
    assert ResponseCache(tmp_path, "another-app").get("projects", "ConsultarProjeto", params) is None
    assert cache.get("projects", "ListarProjetos", params) is None
    assert cache.get("departments", "ConsultarProjeto", params) is None
    state = cache.path.read_bytes()
    assert b"synthetic-app-secret-key" not in state
    assert b"PRIVATE-QUERY-001" not in state
    assert cache.path == tmp_path / "state" / "mcp" / "omie" / "responses.sqlite3"


def test_expiration_and_clock_rollback_do_not_deliver_an_unproven_fresh_response(tmp_path, clock, monkeypatch):
    cache = ResponseCache(tmp_path, "key")
    assert cache.put("projects", "ConsultarProjeto", {}, {}, generation=0)
    clock[0] = 99
    assert cache.get("projects", "ConsultarProjeto", {}) is None
    clock[0] = 160.999
    assert cache.get("projects", "ConsultarProjeto", {}) is not None
    clock[0] = 161
    assert cache.get("projects", "ConsultarProjeto", {}) is None
    clock[0] = 200
    assert cache.put("projects", "ConsultarProjeto", {}, {}, generation=0)
    instants = iter([260.999, 261.0])
    monkeypatch.setattr(response_cache, "time", lambda: next(instants))
    assert cache.get("projects", "ConsultarProjeto", {}) is None


def test_generation_prevents_a_read_in_flight_from_repopulating_invalidated_cache(tmp_path, clock):
    first = ResponseCache(tmp_path, "key")
    second = ResponseCache(tmp_path, "key")
    before = first.generation("account_transactions")
    assert first.put("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}, {"nCodLanc": 12}, generation=before)
    second.invalidate({"account_transactions", "financial_movements"})

    assert first.get("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}) is None
    assert not first.put("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}, {"nCodLanc": 12}, generation=before)
    assert first.generation("account_transactions") == 1
    assert first.generation("financial_movements") == 1
    assert first.generation("projects") == 0
    assert first.put("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}, {"nCodLanc": 12}, generation=1)


def test_snapshot_currency_rejects_concurrent_write_and_changed_generation(tmp_path, clock):
    cache = ResponseCache(tmp_path, "key")
    assert cache.is_current("projects", 0)
    token = cache.begin_write(["projects"])
    assert not cache.is_current("projects", 1)
    cache.end_write(["projects"], token)
    assert not cache.is_current("projects", 0)
    assert cache.is_current("projects", 2)


def test_active_writes_block_cache_and_independent_writes_release_only_their_lease(tmp_path, clock):
    first = ResponseCache(tmp_path, "key")
    second = ResponseCache(tmp_path, "key")
    one = first.begin_write(["account_transactions"])
    two = second.begin_write(["account_transactions"])
    assert first.get("account_transactions", "ConsultaLancCC", {}) is None
    assert not first.put("account_transactions", "ConsultaLancCC", {}, {}, generation=2)
    first.end_write(["account_transactions"], one)
    assert not second.put("account_transactions", "ConsultaLancCC", {}, {}, generation=3)
    second.end_write(["account_transactions"], two)
    assert first.put("account_transactions", "ConsultaLancCC", {}, {}, generation=4)
    first.end_write(["account_transactions"], one)
    assert first.get("account_transactions", "ConsultaLancCC", {}) is None


def test_expired_crashed_write_recovers_but_its_late_end_still_invalidates(tmp_path, clock):
    cache = ResponseCache(tmp_path, "key")
    token = cache.begin_write(["projects"], lease_seconds=10)
    clock[0] = 110
    assert cache.put("projects", "ConsultarProjeto", {}, {}, generation=1)
    cache.end_write(["projects"], token)
    assert cache.generation("projects") == 2
    assert cache.get("projects", "ConsultarProjeto", {}) is None


def test_aliases_preserve_the_effective_http_key_across_response_invalidation(tmp_path, clock):
    cache = ResponseCache(tmp_path, "key")
    by_id, by_integration = {"codigo": 12}, {"codInt": "I-12"}
    actual = cache.request_fingerprint("projects", "ConsultarProjeto", by_integration)
    assert cache.resolve_fingerprint("projects", "ConsultarProjeto", by_id) != actual
    assert cache.register_aliases("projects", "ConsultarProjeto", [by_id, by_integration], fingerprint=actual, generation=0)
    assert cache.resolve_fingerprint("projects", "ConsultarProjeto", by_id) == actual
    cache.invalidate(["projects"])
    assert cache.resolve_fingerprint("projects", "ConsultarProjeto", by_id) == actual
    assert cache.get("projects", "ConsultarProjeto", by_id) is None
    assert not cache.register_aliases("projects", "ConsultarProjeto", [by_id], fingerprint=actual, generation=0)
    token = cache.begin_write(["projects"])
    assert not cache.register_aliases("projects", "ConsultarProjeto", [by_id], fingerprint=actual, generation=2)
    cache.end_write(["projects"], token)
    clock[0] = 400
    assert cache.resolve_fingerprint("projects", "ConsultarProjeto", by_id) == cache.request_fingerprint("projects", "ConsultarProjeto", by_id)


@pytest.mark.parametrize("limit", ["MAX_ENTRIES", "MAX_TOTAL_BYTES", "MAX_RESPONSE_BYTES"])
def test_cache_size_and_entry_limits_do_not_grow_without_bound(tmp_path, clock, monkeypatch, limit):
    cache = ResponseCache(tmp_path, "key")
    monkeypatch.setattr(response_cache, limit, 1 if limit == "MAX_ENTRIES" else 15)
    assert cache.put("projects", "ConsultarProjeto", {"codigo": 1}, {"a": "12345"}, generation=0)
    clock[0] += 1
    kept = cache.put("projects", "ConsultarProjeto", {"codigo": 2}, {"a": "1234567890"}, generation=0)
    if limit == "MAX_RESPONSE_BYTES":
        assert not kept
        assert cache.get("projects", "ConsultarProjeto", {"codigo": 1}) is not None
    elif limit == "MAX_TOTAL_BYTES":
        assert not kept
        assert cache.get("projects", "ConsultarProjeto", {"codigo": 1}) is None
    else:
        assert kept
        assert cache.get("projects", "ConsultarProjeto", {"codigo": 1}) is None


def test_alias_limit_discards_oldest_equivalences(tmp_path, clock, monkeypatch):
    cache = ResponseCache(tmp_path, "key")
    monkeypatch.setattr(response_cache, "MAX_ALIASES", 1)
    one, two = {"codigo": 1}, {"codigo": 2}
    actual = "a" * 64
    assert cache.register_aliases("projects", "ConsultarProjeto", [one], fingerprint=actual, generation=0)
    clock[0] += 1
    assert cache.register_aliases("projects", "ConsultarProjeto", [two], fingerprint=actual, generation=0)
    assert cache.resolve_fingerprint("projects", "ConsultarProjeto", one) != actual
    assert cache.resolve_fingerprint("projects", "ConsultarProjeto", two) == actual


@pytest.mark.parametrize("data", [
    {"faultstring": "Rejected"}, {"cCodStatus": "1"}, {"codigo_status": 1},
    {"cCodStatus": None}, {"codigo_status": True},
])
def test_unsuccessful_responses_are_not_cached(tmp_path, data):
    cache = ResponseCache(tmp_path, "key")
    assert not cache.put("projects", "ConsultarProjeto", {}, data, generation=0)
    assert cache.get("projects", "ConsultarProjeto", {}) is None


@pytest.mark.parametrize("app_key", [None, "", 12])
def test_invalid_app_scope_is_a_sanitized_domain_error(tmp_path, app_key):
    with pytest.raises(OmieError) as error:
        ResponseCache(tmp_path, app_key)
    assert error.value.code == "omie_local_cache_error"
    assert error.value.details == {"retryable": False}


@pytest.mark.parametrize("endpoint,method,params", [
    (None, "Consulta", {}), ("", "Consulta", {}), ("X" * 129, "Consulta", {}),
    ("projects", False, {}), ("projects", "Consulta", None),
    ("projects", "Consulta", {"invalid": float("nan")}), ("projects", "Consulta", {"invalid": {1, 2}}),
])
def test_invalid_request_keys_never_expose_parameters(endpoint, method, params):
    with pytest.raises(OmieError) as error:
        ResponseCache.request_fingerprint(endpoint, method, params)
    assert error.value.code == "omie_local_cache_error"
    assert "invalid" not in str(error.value)


@pytest.mark.parametrize("action", [
    lambda c: c.generation(None),
    lambda c: c.is_current(None, 0), lambda c: c.is_current("projects", -1),
    lambda c: c.put("projects", "Consulta", {}, [], generation=0),
    lambda c: c.put("projects", "Consulta", {}, {"a": float("inf")}, generation=0),
    lambda c: c.put("projects", "Consulta", {}, {}, generation=True),
    lambda c: c.put("projects", "Consulta", {}, {}, generation=-1),
    lambda c: c.put("projects", "Consulta", {}, {}, generation=0, ttl=0),
    lambda c: c.put("projects", "Consulta", {}, {}, generation=0, ttl=3601),
    lambda c: c.put("projects", "Consulta", {}, {}, generation=0, ttl=float("nan")),
    lambda c: c.invalidate("projects"), lambda c: c.invalidate([]), lambda c: c.invalidate([None]),
    lambda c: c.begin_write([], lease_seconds=10), lambda c: c.begin_write(["projects"], lease_seconds=0),
    lambda c: c.end_write([], "a" * 32), lambda c: c.end_write(["projects"], "invalid"),
    lambda c: c.register_aliases("projects", "Consulta", [{}], fingerprint="bad", generation=0),
    lambda c: c.register_aliases("projects", "Consulta", [], fingerprint="a" * 64, generation=0),
    lambda c: c.register_aliases("projects", "Consulta", [{}], fingerprint="a" * 64, generation=0, ttl=60),
    lambda c: c.register_aliases("projects", "Consulta", [{}], fingerprint="a" * 64, generation=True),
])
def test_invalid_cache_operations_fail_closed(tmp_path, action):
    with pytest.raises(OmieError) as error:
        action(ResponseCache(tmp_path, "key"))
    assert error.value.code == "omie_local_cache_error"
    assert error.value.details == {"retryable": False}


@pytest.mark.parametrize("obstruction", ["directory", "corrupt_database", "unsupported_schema"])
def test_unavailable_or_corrupt_database_fails_closed(tmp_path, obstruction):
    cache = ResponseCache(tmp_path, "key")
    if obstruction == "directory":
        (tmp_path / "state").write_text("obstruction", encoding="utf-8")
    else:
        cache.path.parent.mkdir(parents=True)
        if obstruction == "corrupt_database":
            cache.path.write_bytes(b"not a SQLite database")
        else:
            with sqlite3.connect(cache.path) as connection:
                connection.execute("PRAGMA user_version=999")
    with pytest.raises(OmieError) as error:
        cache.get("projects", "Consulta", {})
    assert error.value.code == "omie_local_cache_error"
    assert str(tmp_path) not in str(error.value)


@pytest.mark.parametrize("sql,params", [
    ("UPDATE resources SET generation=?", ("invalid",)),
    ("UPDATE responses SET generation=?", (-1,)),
    ("UPDATE responses SET stored_at=?", ("invalid",)),
    ("UPDATE responses SET expires_at=?", ("invalid",)),
    ("UPDATE responses SET stored_at=?, expires_at=?", (200.0, 190.0)),
    ("UPDATE responses SET data=?", ("not JSON",)),
    ("UPDATE responses SET data=?", ("[]",)),
    ("UPDATE responses SET data=?", ('{"a": NaN}',)),
    ("UPDATE responses SET data=?", ('{"codigo_status":"1"}',)),
])
def test_corrupt_cache_records_are_not_delivered(tmp_path, clock, sql, params):
    cache = ResponseCache(tmp_path, "key")
    cache.invalidate(["projects"])
    assert cache.put("projects", "Consulta", {}, {}, generation=1)
    with sqlite3.connect(cache.path) as connection:
        connection.execute(sql, params)
    with pytest.raises(OmieError) as error:
        cache.get("projects", "Consulta", {})
    assert error.value.code == "omie_local_cache_error"


def test_stale_generation_and_corrupt_alias_are_not_accepted(tmp_path, clock):
    cache = ResponseCache(tmp_path, "key")
    cache.invalidate(["projects"])
    assert cache.put("projects", "Consulta", {}, {}, generation=1)
    with sqlite3.connect(cache.path) as connection:
        connection.execute("UPDATE resources SET generation=2")
    assert cache.get("projects", "Consulta", {}) is None
    cache.register_aliases("projects", "Consulta", [{}], fingerprint="a" * 64, generation=2)
    with sqlite3.connect(cache.path) as connection:
        connection.execute("UPDATE aliases SET fingerprint='corrupt'")
    with pytest.raises(OmieError) as error:
        cache.resolve_fingerprint("projects", "Consulta", {})
    assert error.value.code == "omie_local_cache_error"


def test_an_independent_process_observes_cache_and_invalidates_the_same_generation(tmp_path):
    cache = ResponseCache(tmp_path, "key")
    assert cache.put("projects", "Consulta", {"codigo": 12}, {"codigo": 12}, generation=0)
    source = Path(response_cache.__file__).resolve().parents[3]
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from onmyoji_daemon.mcp.omie.response_cache import ResponseCache
cache = ResponseCache(Path(sys.argv[2]), 'key')
print(json.dumps(cache.get('projects', 'Consulta', {'codigo': 12}).data))
cache.invalidate(['projects'])
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(source), str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"codigo": 12}
    assert cache.generation("projects") == 1
    assert cache.get("projects", "Consulta", {"codigo": 12}) is None
