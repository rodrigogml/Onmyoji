"""Integração do serviço Omie com cache e consumo compartilhados, sem cofre ou API reais."""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError

import pytest

from onmyoji_daemon.mcp.omie import service, traffic, response_cache
from onmyoji_daemon.mcp.omie.exception import OmieError
from onmyoji_daemon.mcp.omie.finance import OmieFinanceFacade
from onmyoji_daemon.mcp.omie.response_cache import ResponseCache
from onmyoji_daemon.mcp.omie.service import OmieService
from onmyoji_daemon.mcp.omie.traffic import OmieTrafficGate


PROFILE = {"credential_profile": "synthetic-vault", "credential_entry_path": "synthetic-entry",
           "app_key_field": "username", "app_secret_field": "password"}
DEFAULTS = {"max_retries": 0, "min_request_interval_seconds": 0}
APP_KEY = "synthetic-shared-app-key"


class Credentials:
    def resolve(self, _profile, _entry, field):
        return APP_KEY if field == "username" else "synthetic-shared-app-secret"


class Response:
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps(self.data).encode("utf-8")


class Clock:
    def __init__(self):
        self.value = 1000000.0
        self.sleeps = []

    def now(self): return self.value

    def sleep(self, delay):
        assert 0 < delay <= 1
        self.sleeps.append(delay)
        self.value += delay


@pytest.fixture
def clock(monkeypatch):
    value = Clock()
    monkeypatch.setattr(traffic, "time", value.now)
    monkeypatch.setattr(traffic, "monotonic", value.now)
    monkeypatch.setattr(traffic, "sleep", value.sleep)
    monkeypatch.setattr(response_cache, "time", value.now)
    monkeypatch.setattr(service.time, "time", value.now)
    monkeypatch.setattr(service, "TRAFFIC_GATE", OmieTrafficGate())
    return value


def install_http(monkeypatch, clock, handler):
    calls = []

    def request(value, **_kwargs):
        payload = json.loads(value.data)
        calls.append((payload["call"], deepcopy(payload["param"][0]), clock.value))
        return Response(handler(payload["call"], payload["param"][0]))

    monkeypatch.setattr(service, "urlopen", request)
    return calls


def invoke(root, method="ConsultaLancCC", params=None, endpoint="account_transactions", **options):
    instance = OmieService(root, Credentials())
    data = instance._request(PROFILE, DEFAULTS, endpoint, method, params if params is not None else {"nCodLanc": 12}, **options)
    return data, instance.cache_metadata()


def test_independent_services_share_a_successful_response_and_expose_its_age(tmp_path, monkeypatch, clock):
    calls = install_http(monkeypatch, clock, lambda *_args: {"nCodLanc": 12, "cCodIntLanc": "I-12", "departamentos": []})

    first, first_metadata = invoke(tmp_path)
    clock.value += 5
    second, second_metadata = invoke(tmp_path)

    assert first == second
    assert len(calls) == 1
    assert first_metadata["reutilizado"] is False
    assert second_metadata["reutilizado"] is True
    assert first_metadata["consultado_em"] == second_metadata["consultado_em"]
    assert first_metadata["valido_ate"] == second_metadata["valido_ate"]
    assert not clock.sleeps


def test_valid_cache_remains_usable_during_a_remote_cooldown_without_http(tmp_path, monkeypatch, clock):
    calls = install_http(monkeypatch, clock, lambda *_args: {"nCodLanc": 12})
    original, _ = invoke(tmp_path)
    OmieTrafficGate().pause(tmp_path, APP_KEY, "ConsultaLancCC", 1800)

    result, metadata = invoke(tmp_path)

    assert result == original
    assert metadata["reutilizado"] is True
    assert len(calls) == 1
    assert not clock.sleeps
    with pytest.raises(OmieError) as error:
        invoke(tmp_path, params={"nCodLanc": 34})
    assert error.value.code == "omie_cooldown"
    assert len(calls) == 1


def test_expired_cache_performs_a_new_read_after_the_shared_window(tmp_path, monkeypatch, clock):
    calls = install_http(monkeypatch, clock, lambda *_args: {"nCodLanc": 12, "version": len(calls)})
    before, _ = invoke(tmp_path)
    clock.value += 61

    after, metadata = invoke(tmp_path)

    assert before["version"] == 1
    assert after["version"] == 2
    assert metadata["reutilizado"] is False
    assert [call[2] for call in calls] == [1000000, 1000061]
    assert not clock.sleeps


def test_forced_refresh_does_not_bypass_the_identical_request_interval(tmp_path, monkeypatch, clock):
    calls = install_http(monkeypatch, clock, lambda *_args: {"nCodLanc": 12})
    invoke(tmp_path)

    _, metadata = invoke(tmp_path, fresh=True)

    assert metadata["reutilizado"] is False
    assert [call[2] for call in calls] == [1000000, 1000061]
    assert sum(clock.sleeps) == 61


@pytest.mark.parametrize("status", [429, 500, 503])
def test_remote_failures_are_not_retried_rapidly_and_the_next_call_waits(tmp_path, monkeypatch, clock, status):
    def handler(*_args):
        if len(calls) == 1:
            raise HTTPError("https://synthetic.invalid/", status, "rejected", {}, BytesIO(b'{"description":"Synthetic rejection"}'))
        return {"nCodLanc": 12}

    calls = install_http(monkeypatch, clock, handler)
    with pytest.raises(OmieError) as error:
        instance = OmieService(tmp_path, Credentials())
        instance._request(PROFILE, {**DEFAULTS, "max_retries": 2}, "account_transactions", "ConsultaLancCC", {"nCodLanc": 12})

    assert error.value.code == "omie_http_error"
    assert error.value.details["retry_after_seconds"] == 61
    assert len(calls) == 1
    invoke(tmp_path)
    assert [call[2] for call in calls] == [1000000, 1000061]
    assert sum(clock.sleeps) == 61


def test_transport_retry_waits_the_same_shared_window_before_resending(tmp_path, monkeypatch, clock):
    def handler(*_args):
        if len(calls) == 1:
            raise URLError("synthetic transport failure")
        return {"nCodLanc": 12}

    calls = install_http(monkeypatch, clock, handler)
    instance = OmieService(tmp_path, Credentials())

    result = instance._request(PROFILE, {**DEFAULTS, "max_retries": 1}, "account_transactions", "ConsultaLancCC", {"nCodLanc": 12})

    assert result == {"nCodLanc": 12}
    assert [call[2] for call in calls] == [1000000, 1000061]
    assert sum(clock.sleeps) == 61


def test_uncertain_write_invalidates_previous_reads_without_repeating_the_write(tmp_path, monkeypatch, clock):
    def handler(method, _params):
        if method == "AlterarLancCC":
            raise TimeoutError("synthetic timeout after submission")
        return {"nCodLanc": 12}

    calls = install_http(monkeypatch, clock, handler)
    invoke(tmp_path)
    instance = OmieService(tmp_path, Credentials())
    monkeypatch.setattr(instance, "_profile", lambda _name: (PROFILE, DEFAULTS))

    with pytest.raises(OmieError) as error:
        instance.invoke_financial("synthetic", "account_transactions_update", {"body": {"id": 12, "note": "Requested correction"}}, writes=True)

    assert error.value.code == "outcome_unknown"
    assert [call[0] for call in calls] == ["ConsultaLancCC", "AlterarLancCC"]
    assert ResponseCache(tmp_path, APP_KEY).get("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}) is None
    invoke(tmp_path)
    assert [call[2] for call in calls] == [1000000, 1000000, 1000061]


def test_a_read_overlapping_a_write_cannot_publish_or_confirm_the_old_snapshot(tmp_path, monkeypatch, clock):
    def handler(method, _params):
        if method == "ConsultaLancCC":
            invoke(tmp_path, "AlterarLancCC", {"nCodLanc": 12, "detalhes": {"cObs": "Concurrent change"}})
            return {"nCodLanc": 12, "departamentos": [{"cCodDep": "D1", "nPerDep": 100}]}
        return {"cCodStatus": "0"}

    calls = install_http(monkeypatch, clock, handler)
    with pytest.raises(OmieError) as error:
        invoke(tmp_path)

    assert error.value.code == "omie_read_invalidated"
    assert [call[0] for call in calls] == ["ConsultaLancCC", "AlterarLancCC"]
    assert ResponseCache(tmp_path, APP_KEY).get("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}) is None


def test_old_cache_does_not_confirm_allocation_after_a_write_that_did_not_persist(tmp_path, monkeypatch, clock):
    departments = [[{"cCodDep": "D1", "nPerDep": 100}]]

    def handler(method, _params):
        if method == "AlterarLancCC":
            departments[0] = []
            return {"cCodStatus": "0", "nCodLanc": 12}
        return {"nCodLanc": 12, "departamentos": deepcopy(departments[0])}

    calls = install_http(monkeypatch, clock, handler)
    invoke(tmp_path)
    monkeypatch.setattr(OmieService, "_profile", lambda _self, _name: (PROFILE, DEFAULTS))
    monkeypatch.setattr(service.KeePassCredentialResolver, "resolve", Credentials.resolve)

    class Configuration:
        root = tmp_path

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration()).call("omie.financeiro.lancamentos_conta.alterar", {
            "confirmar": True, "dados": {"id": 12, "departamentos": [{"codigo": "D1", "percentual": 100}]},
        }, "synthetic")

    assert error.value.code == "allocation_not_persisted"
    assert error.value.details["escrita_processada"] is True
    assert [call[0] for call in calls] == ["ConsultaLancCC", "AlterarLancCC", "ConsultaLancCC"]
    assert [call[2] for call in calls] == [1000000, 1000000, 1000061]


def test_a_concurrent_write_makes_allocation_confirmation_inconclusive(tmp_path, monkeypatch, clock):
    def handler(method, _params):
        if method == "ConsultaLancCC":
            invoke(tmp_path, "AlterarLancCC", {"nCodLanc": 12, "detalhes": {"cObs": "Another correction"}})
            return {"nCodLanc": 12, "departamentos": [{"cCodDep": "D1", "nPerDep": 100}]}
        return {"nCodLanc": 12, "cCodStatus": "0"}

    calls = install_http(monkeypatch, clock, handler)
    monkeypatch.setattr(OmieService, "_profile", lambda _self, _name: (PROFILE, DEFAULTS))
    monkeypatch.setattr(service.KeePassCredentialResolver, "resolve", Credentials.resolve)

    class Configuration:
        root = tmp_path

    with pytest.raises(OmieError) as error:
        OmieFinanceFacade(Configuration()).call("omie.financeiro.lancamentos_conta.alterar", {
            "confirmar": True, "dados": {"id": 12, "departamentos": [{"codigo": "D1", "percentual": 100}]},
        }, "synthetic")

    assert error.value.code == "write_verification_failed"
    assert error.value.details["cause_code"] == "omie_read_invalidated"
    assert error.value.details["escrita_processada"] is True
    assert [call[0] for call in calls] == ["AlterarLancCC", "ConsultaLancCC", "AlterarLancCC"]
    assert ResponseCache(tmp_path, APP_KEY).get("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}) is None


def test_response_too_large_to_cache_remains_valid_but_the_next_request_waits(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(response_cache, "MAX_RESPONSE_BYTES", 1)
    calls = install_http(monkeypatch, clock, lambda *_args: {"nCodLanc": 12, "data": "large synthetic response"})

    result, metadata = invoke(tmp_path)

    assert result["nCodLanc"] == 12
    assert metadata["reutilizado"] is False
    assert metadata["consultado_em"] == metadata["valido_ate"]
    assert ResponseCache(tmp_path, APP_KEY).get("account_transactions", "ConsultaLancCC", {"nCodLanc": 12}) is None
    invoke(tmp_path)
    assert [call[2] for call in calls] == [1000000, 1000061]


@pytest.mark.parametrize("initial_query", [{"nCodLanc": 12}, {"cCodIntLanc": "I-12"}])
def test_process_write_invalidates_dependencies_and_readback_respects_id_alias_window(tmp_path, monkeypatch, clock, initial_query):
    version = ["before"]
    calls = install_http(monkeypatch, clock, lambda method, _params: {
        "nCodLanc": 12, "cCodIntLanc": "I-12", "version": version[0],
    } if method == "ConsultaLancCC" else {"version": version[0]})
    invoke(tmp_path, params=initial_query)
    invoke(tmp_path, "ListarMovimentos", {"nPagina": 1}, "financial_movements")
    invoke(tmp_path, "ConsultarContaPagar", {"codigo_lancamento_omie": 34}, "payables")
    source = Path(service.__file__).resolve().parents[3]
    script = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from onmyoji_daemon.mcp.omie import service, traffic, response_cache
now = [float(sys.argv[3])]
traffic.time = lambda: now[0]
traffic.monotonic = lambda: now[0]
traffic.sleep = lambda delay: now.__setitem__(0, now[0] + delay)
response_cache.time = lambda: now[0]
service.time.time = lambda: now[0]
class Credentials:
    def resolve(self, _profile, _entry, field):
        return 'synthetic-shared-app-key' if field == 'username' else 'synthetic-secret'
class Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return b'{"cCodStatus":"0"}'
service.urlopen = lambda *_args, **_kwargs: Response()
profile = {'credential_profile':'synthetic', 'credential_entry_path':'synthetic', 'app_key_field':'username', 'app_secret_field':'password'}
result = service.OmieService(Path(sys.argv[2]), Credentials())._request(profile, {'max_retries':0, 'min_request_interval_seconds':0}, 'account_transactions', 'AlterarLancCC', {'nCodLanc':12, 'detalhes':{'cObs':'Correction'}})
print(json.dumps(result))
"""
    process = subprocess.run([sys.executable, "-B", "-c", script, str(source), str(tmp_path), str(clock.value)],
                             cwd=tmp_path, capture_output=True, text=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"cCodStatus": "0"}
    cache = ResponseCache(tmp_path, APP_KEY)
    assert cache.get("financial_movements", "ListarMovimentos", {"nPagina": 1}) is None
    assert cache.get("payables", "ConsultarContaPagar", {"codigo_lancamento_omie": 34}) is None
    version[0] = "after"

    result, metadata = invoke(tmp_path)

    assert result["version"] == "after"
    assert metadata["reutilizado"] is False
    assert calls[-1][2] == 1000061
    assert sum(clock.sleeps) == 61


def test_simultaneous_independent_processes_make_one_http_call_and_share_metadata(tmp_path):
    source = Path(service.__file__).resolve().parents[3]
    script = """
import json, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from onmyoji_daemon.mcp.omie import service
root = Path(sys.argv[2])
(root / ('ready-' + sys.argv[3])).touch()
while not (root / 'start').exists(): time.sleep(0.01)
class Credentials:
    def resolve(self, _profile, _entry, field):
        return 'synthetic-shared-app-key' if field == 'username' else 'synthetic-secret'
class Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return b'{"nCodLanc":12}'
def urlopen(*_args, **_kwargs):
    with (root / 'http-calls').open('a', encoding='utf-8') as output: output.write('HTTP\\n')
    time.sleep(0.1)
    return Response()
service.urlopen = urlopen
profile = {'credential_profile':'synthetic', 'credential_entry_path':'synthetic', 'app_key_field':'username', 'app_secret_field':'password'}
instance = service.OmieService(root, Credentials())
result = instance._request(profile, {'max_retries':0, 'min_request_interval_seconds':0}, 'account_transactions', 'ConsultaLancCC', {'nCodLanc':12})
print(json.dumps({'data':result, 'cache':instance.cache_metadata()}))
"""
    processes = []
    try:
        for number in range(2):
            processes.append(subprocess.Popen([sys.executable, "-B", "-c", script, str(source), str(tmp_path), str(number)],
                                              cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        deadline = time.monotonic() + 10
        while not all((tmp_path / f"ready-{number}").exists() for number in range(2)):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        (tmp_path / "start").touch()
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)

    assert (tmp_path / "http-calls").read_text(encoding="utf-8").splitlines() == ["HTTP"]
    assert all(result["data"] == {"nCodLanc": 12} for result in results)
    assert sorted(result["cache"]["reutilizado"] for result in results) == [False, True]
    assert results[0]["cache"]["consultado_em"] == results[1]["cache"]["consultado_em"]
