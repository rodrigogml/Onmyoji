"""Cobertura da base de conexão da integração Omie MCP."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

from onmyoji_daemon.mcp.omie import OmieError, OmieService


class Credentials:
    def resolve(self, _profile: str, _entry: str, field: str) -> str:
        return "private-app-key" if field == "username" else "private-app-secret"


class Response:
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps({"pagina": 1, "total_de_paginas": 1, "departamentos": []}).encode("utf-8")


def write_config(root: Path) -> None:
    path = root / "configs" / "mcp" / "omie.toml"
    path.parent.mkdir(parents=True)
    path.write_text('''schema_version = 1

[defaults]
timeout_seconds = 10
max_retries = 0

[profiles.example]
enabled = true
description = "ERP de teste"
credential_profile = "vault"
credential_entry_path = "APIs/Omie/example"
app_key_field = "username"
app_secret_field = "password"
''', encoding="utf-8")


def test_connection_uses_two_vault_fields_without_exposing_them(tmp_path: Path, monkeypatch) -> None:
    write_config(tmp_path)
    requests = []

    def open_request(request, timeout):
        requests.append((request, timeout))
        return Response()

    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", open_request)
    assert OmieService(tmp_path, Credentials()).test_connection("example") == {"profile": "example", "authenticated": True}
    payload = json.loads(requests[0][0].data)
    assert payload["call"] == "ListarDepartamentos"
    assert payload["param"] == [{"pagina": 1, "registros_por_pagina": 1}]
    assert "private-app-key" not in requests[0][0].full_url


@pytest.mark.parametrize("arguments", [
    {"params": {"codigo": 1}}, {"body": {"codigo": "FAT"}},
    {"params": None}, {"params": []}, {"body": False},
])
def test_invalid_arguments_fail_before_reading_credentials(tmp_path, arguments):
    class UnavailableCredentials:
        def resolve(self, *_args): pytest.fail("O cofre não deve ser acessado")
    service = OmieService(tmp_path, UnavailableCredentials())
    with pytest.raises(OmieError) as error:
        service.invoke("example", "document_types_get", arguments)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize("status", ["1", 2, None, True, []])
def test_api_processing_failure_is_sanitized(tmp_path, monkeypatch, status):
    write_config(tmp_path)
    class Failure(Response):
        def read(self): return json.dumps({"cCodStatus": status, "cDesStatus": "sensitive"}).encode()
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", lambda *_args, **_kwargs: Failure())
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials()).invoke("example", "projects_get", {"params": {"codInt": "P1"}})
    assert error.value.code == "omie_api_error"
    assert "sensitive" not in str(error.value)


@pytest.mark.parametrize("payload", [b"sensitive invalid json", b"\xff\xfe", b"null", b"[]"])
def test_invalid_response_is_sanitized_without_retry(tmp_path, monkeypatch, payload):
    write_config(tmp_path)
    class Failure(Response):
        def read(self): return payload
    calls = []
    def request(*_args, **_kwargs):
        calls.append(True)
        return Failure()
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials()).invoke("example", "projects_get", {"params": {"codInt": "P1"}})
    assert error.value.code == "invalid_response"
    assert "sensitive" not in str(error.value)
    assert len(calls) == 1


def test_customer_inclusion_timeout_is_never_retried(tmp_path, monkeypatch):
    write_config(tmp_path)
    calls = []
    class EmptyPage(Response):
        def read(self): return json.dumps({"pagina": 1, "total_de_paginas": 0, "total_de_registros": 0, "clientes_cadastro": []}).encode()
    def request(http_request, **_kwargs):
        calls.append(json.loads(http_request.data)["call"])
        if calls[-1] == "ListarClientes": return EmptyPage()
        raise TimeoutError("sensitive")
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    service = OmieService(tmp_path, Credentials())
    with pytest.raises(OmieError) as error:
        service.invoke("example", "customers_create", {"confirm": True, "body": {"cnpj_cpf": "01234567890", "razao_social": "Nome"}})
    assert error.value.code == "network_error"
    assert calls == ["ListarClientes", "IncluirCliente"]


def test_network_retry_remains_available_for_reads(tmp_path, monkeypatch):
    write_config(tmp_path)
    calls = []
    def request(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1: raise URLError("sensitive")
        return Response()
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.time.sleep", lambda _seconds: None)
    service = OmieService(tmp_path, Credentials())
    assert service._request({"credential_profile": "vault", "credential_entry_path": "APIs/Omie", "app_key_field": "username", "app_secret_field": "password"}, {"max_retries": 1}, "departments", "ListarDepartamentos", {})["pagina"] == 1
    assert len(calls) == 2


def test_http_failure_does_not_expose_response_body(tmp_path, monkeypatch):
    write_config(tmp_path)
    def request(*_args, **_kwargs): raise HTTPError("https://app.omie.com.br/", 403, "sensitive", {}, None)
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials()).invoke("example", "projects_get", {"params": {"codInt": "P1"}})
    assert error.value.code == "omie_http_error"
    assert "sensitive" not in str(error.value)


def test_http_500_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def request(*_args, **_kwargs):
        calls.append(True)
        raise HTTPError("https://app.omie.com.br/", 500, "sensitive", {}, None)
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials())._request({"credential_profile": "vault", "credential_entry_path": "APIs/Omie", "app_key_field": "username", "app_secret_field": "password"}, {"max_retries": 2}, "departments", "ListarDepartamentos", {})
    assert error.value.code == "omie_http_error"
    assert error.value.details == {"http_status": 500, "retryable": False}
    assert len(calls) == 1


def test_http_425_stops_without_retry_or_sleep_and_preserves_diagnostic(tmp_path, monkeypatch):
    calls = []
    def request(*_args, **_kwargs):
        calls.append(True)
        body = BytesIO(b'{"code": "blocked", "description": "API bloqueada temporariamente"}')
        raise HTTPError("https://app.omie.com.br/", 425, "sensitive", {}, body)
    def sleep(_seconds):
        pytest.fail("HTTP 425 não deve aguardar para repetir a chamada")
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.time.sleep", sleep)
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials())._request({"credential_profile": "vault", "credential_entry_path": "APIs/Omie", "app_key_field": "username", "app_secret_field": "password"}, {"max_retries": 2}, "account_transactions", "ListarLancCC", {})
    assert error.value.code == "omie_http_error"
    assert error.value.details == {"http_status": 425, "retryable": False, "remote_code": "blocked", "remote_description": "API bloqueada temporariamente"}
    assert "bloqueio temporário" in str(error.value)
    assert "sensitive" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_transient_http_errors_remain_retryable_for_reads(tmp_path, monkeypatch, status):
    calls = []
    def request(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1: raise HTTPError("https://app.omie.com.br/", status, "temporary", {}, None)
        return Response()
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.time.sleep", lambda _seconds: None)
    result = OmieService(tmp_path, Credentials())._request({"credential_profile": "vault", "credential_entry_path": "APIs/Omie", "app_key_field": "username", "app_secret_field": "password"}, {"max_retries": 1}, "departments", "ListarDepartamentos", {})
    assert result["pagina"] == 1 and len(calls) == 2


def test_http_failure_exposes_sanitized_omie_diagnostic(tmp_path, monkeypatch):
    write_config(tmp_path)
    class Body:
        def read(self): return b'{"code": 123, "description": "Filtro de data invalido"}'
        def close(self): pass
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPError("https://app.omie.com.br/", 500, "error", {}, Body())))
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials()).invoke("example", "projects_get", {"params": {"codInt": "P1"}})
    assert error.value.details["remote_code"] == 123
    assert error.value.details["remote_description"] == "Filtro de data invalido"


def test_http_425_persists_cooldown_across_service_instances(tmp_path, monkeypatch):
    calls = []

    def request(*_args, **_kwargs):
        calls.append(True)
        raise HTTPError("https://app.omie.com.br/", 425, "blocked", {}, None)

    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    profile = {"credential_profile": "vault", "credential_entry_path": "APIs/Omie",
               "app_key_field": "username", "app_secret_field": "password"}
    with pytest.raises(OmieError) as first:
        OmieService(tmp_path, Credentials())._request(profile, {"max_retries": 0}, "account_transactions", "ListarLancCC", {})
    assert first.value.details["http_status"] == 425
    assert (tmp_path / "state" / "mcp" / "omie" / "cooldowns.json").exists()

    with pytest.raises(OmieError) as second:
        OmieService(tmp_path, Credentials())._request(profile, {"max_retries": 0}, "account_transactions", "ListarLancCC", {})
    assert second.value.code == "omie_cooldown"
    assert second.value.details["retry_after_seconds"] > 0
    assert calls == [True]


def test_http_429_long_retry_after_does_not_loop_or_block_tool(tmp_path, monkeypatch):
    calls = []

    def request(*_args, **_kwargs):
        calls.append(True)
        raise HTTPError("https://app.omie.com.br/", 429, "busy", {"Retry-After": "65"}, None)

    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.urlopen", request)
    monkeypatch.setattr("onmyoji_daemon.mcp.omie.service.time.sleep", lambda _seconds: pytest.fail("Não aguardar 65 segundos."))
    profile = {"credential_profile": "vault", "credential_entry_path": "APIs/Omie",
               "app_key_field": "username", "app_secret_field": "password"}
    with pytest.raises(OmieError) as error:
        OmieService(tmp_path, Credentials())._request(profile, {"max_retries": 2}, "departments", "ListarDepartamentos", {})
    assert error.value.code == "omie_http_error"
    assert error.value.details["retry_after_seconds"] >= 65
    assert calls == [True]


def test_rate_limit_prevents_bursts_and_enforces_method_window(tmp_path, monkeypatch):
    import onmyoji_daemon.mcp.omie.traffic as traffic

    clock = [100.0]
    sleeps = []
    monkeypatch.setattr(traffic, "monotonic", lambda: clock[0])
    monkeypatch.setattr(traffic, "time", lambda: 10000 + clock[0])
    def advance(delay):
        sleeps.append(delay)
        clock[0] += delay
    monkeypatch.setattr(traffic, "sleep", advance)
    gate = traffic.OmieTrafficGate()
    gate.await_slot(tmp_path, "test-app-key", "ListarLancCC", interval=0.5, method_limit=2, root_limit=3)
    gate.await_slot(tmp_path, "test-app-key", "ListarLancCC", interval=0.5, method_limit=2, root_limit=3)
    assert clock[0] >= 100.5
    gate.await_slot(tmp_path, "test-app-key", "ListarLancCC", interval=0.5, method_limit=2, root_limit=3)
    assert clock[0] >= 160.0
    assert sleeps
