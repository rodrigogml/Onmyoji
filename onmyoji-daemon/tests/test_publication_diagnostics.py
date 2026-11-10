"""Diagnóstico de publicação antes do setup e em instalações parciais."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from onmyoji_daemon import cli, publication_diagnostics as diagnostics
from onmyoji_daemon.mcp.credentials import CredentialError


def write_tunnel(root: Path, body: str) -> Path:
    path = root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "tunnel.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("schema_version = 1\n" + body, encoding="utf-8")
    return path


def by_label(checks):
    return {check["label"]: check for check in checks}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "runtime_python", lambda _root: tmp_path / "absent-python")
    monkeypatch.setattr(diagnostics.shutil, "which", lambda _value: None)
    monkeypatch.setattr(diagnostics, "endpoint", lambda _root: (_ for _ in ()).throw(FileNotFoundError()))


def test_empty_instance_reports_all_steps_without_creating_files(tmp_path):
    progress = []
    checks = by_label(diagnostics.diagnose(tmp_path, progress.append))

    assert list(tmp_path.iterdir()) == []
    assert len(progress) >= 5
    for label in ("Runtime Python", "Daemon e registro dos serviços", "Cloudflare Account ID", "Cloudflare Zone ID", "Hostname público", "Credencial Cloudflare no KeePass", "Executável cloudflared", "Listener HTTPS do Gateway", "Conexão Cloudflare"):
        assert checks[label]["state"] == "pending"
        assert checks[label]["action"]
    assert checks["Mini Apps (opcional)"]["state"] == "ok"


def test_invalid_tunnel_toml_does_not_hide_other_diagnostics(tmp_path):
    path = write_tunnel(tmp_path, 'hostname = "unterminated\n')
    before = {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}

    checks = by_label(diagnostics.diagnose(tmp_path))

    assert checks["Arquivo de configuração do Tunnel"]["state"] == "error"
    assert str(path) in checks["Arquivo de configuração do Tunnel"]["detail"]
    assert checks["Executável cloudflared"]["state"] == "pending"
    assert checks["Conexão Cloudflare"]["state"] == "pending"
    assert before == {item: item.read_bytes() for item in tmp_path.rglob("*") if item.is_file()}


def test_binary_metadata_alone_is_not_a_configured_or_provisioned_tunnel(tmp_path, monkeypatch):
    binary = tmp_path / "cloudflared.exe"; binary.write_bytes(b"test")
    write_tunnel(tmp_path, 'cloudflared_executable = ' + json.dumps(str(binary)) + '\nmanaged_version = "2026.1.1"\n')
    monkeypatch.setattr(diagnostics.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "cloudflared version 2026.1.1", ""))

    checks = by_label(diagnostics.diagnose(tmp_path))

    assert checks["Executável cloudflared"]["state"] == "ok"
    assert checks["Cloudflare Account ID"]["state"] == "pending"
    assert checks["Credencial Cloudflare no KeePass"]["state"] == "pending"
    assert checks["Tunnel, ingress e DNS provisionados"]["state"] == "pending"


def test_old_daemon_is_explained_without_starting_services(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(diagnostics, "endpoint", lambda _root: ("127.0.0.1", 1, "private-rpc-token"))
    monkeypatch.setattr(diagnostics, "call", lambda *_args, **_kwargs: calls.append(_args[3]) or [{"name": "telegram", "state": "running"}])

    checks = by_label(diagnostics.diagnose(tmp_path))

    assert calls == ["list-services"]
    assert "Reinicie" in checks["Daemon e registro dos serviços"]["action"]
    assert "gateway" in checks["Daemon e registro dos serviços"]["detail"]
    assert "private-rpc-token" not in json.dumps(checks)


@pytest.mark.parametrize("available", [True, False])
def test_credential_presence_is_checked_without_obtaining_secret(tmp_path, monkeypatch, available):
    write_tunnel(tmp_path, 'credential_profile = "main"\ncredential_entry_path = "Akuma/Serviço/Cloudflare:API"\n')
    calls = []

    class Resolver:
        def __init__(self, _root): pass
        def password_exists(self, profile, entry): calls.append((profile, entry)); return available
        def resolve(self, *_args): raise AssertionError("O diagnóstico não deve obter o token")

    monkeypatch.setattr(diagnostics, "KeePassCredentialResolver", Resolver)
    check = by_label(diagnostics.diagnose(tmp_path))["Credencial Cloudflare no KeePass"]

    assert calls == [("main", "Akuma/Serviço/Cloudflare:API")]
    assert check["state"] == ("ok" if available else "error")


def test_credential_failure_is_actionable_and_does_not_expose_error_values(tmp_path, monkeypatch):
    write_tunnel(tmp_path, 'credential_profile = "main"\ncredential_entry_path = "entry"\n')

    class Resolver:
        def __init__(self, _root): pass
        def password_exists(self, *_args): raise CredentialError("entry_denied", "unexpected-secret-value")

    monkeypatch.setattr(diagnostics, "KeePassCredentialResolver", Resolver)
    checks = by_label(diagnostics.diagnose(tmp_path))
    assert checks["Credencial Cloudflare no KeePass"]["state"] == "error"
    assert "raízes autorizadas" in checks["Credencial Cloudflare no KeePass"]["detail"]
    assert "unexpected-secret-value" not in json.dumps(checks)
    assert "Executável cloudflared" in checks


def test_binary_probe_timeout_does_not_abort_diagnostic(tmp_path, monkeypatch):
    binary = tmp_path / "cloudflared.exe"; binary.write_bytes(b"test")
    write_tunnel(tmp_path, "cloudflared_executable = " + json.dumps(str(binary)) + "\n")
    monkeypatch.setattr(diagnostics.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("cloudflared", 10)))

    checks = by_label(diagnostics.diagnose(tmp_path))
    assert checks["Executável cloudflared"]["state"] == "error"
    assert checks["Executável cloudflared"]["action"]
    assert "Conexão Cloudflare" in checks


def test_reachable_services_report_connection_and_optional_miniapps_independently(tmp_path, monkeypatch):
    write_tunnel(tmp_path, 'account_id = "account"\nzone_id = "zone"\nhostname = "akuma.example.com"\n')
    replies = {
        "list-services": [{"name": name, "state": "running"} for name in ("gateway", "cloudflare-tunnel", "mini-apps")],
        "gateway.status": {"ready": True, "http_url": "https://localhost:39390"},
        "cloudflare-tunnel.status": {"running": True, "connected": True, "ingress_ready": True},
        "mini-apps.diagnose": {"local_ready": False},
    }
    monkeypatch.setattr(diagnostics, "endpoint", lambda _root: ("127.0.0.1", 1, "private-rpc-token"))
    monkeypatch.setattr(diagnostics, "call", lambda *_args, **_kwargs: replies[_args[3]])

    checks = by_label(diagnostics.diagnose(tmp_path))
    assert checks["Listener HTTPS do Gateway"]["state"] == "ok"
    assert checks["Conexão Cloudflare"]["state"] == "ok"
    assert checks["Mini Apps (opcional)"]["state"] == "pending"


def test_cli_diagnostic_runs_without_required_daemon_endpoint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "endpoint", lambda _root: (_ for _ in ()).throw(AssertionError("Não exigir endpoint")))

    assert cli.main(["--onmyoji-root", str(tmp_path), "publication-diagnose"]) == 0
    output = capsys.readouterr()
    checks = json.loads(output.out)
    assert checks and any(item["state"] == "pending" for item in checks)
    assert "Verificando" in output.err
    assert list(tmp_path.iterdir()) == []


def write_operations(root: Path, operations: dict) -> Path:
    path = root / "configs" / "daemon" / "services" / "cloudflare-tunnel" / "operations.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "operations": operations}), encoding="utf-8")
    return path


@pytest.mark.parametrize("code, expected", [
    ("dns_conflict", "reutilização"),
    ("ingress_conflict", "origem remota"),
    ("cloudflare_http_error", "API Token administrativo"),
    ("cloudflare_rejected", "permissões"),
    ("cloudflare_unavailable", "Internet"),
    ("operation_interrupted", "estado remoto"),
    ("credential_missing", "entrada KeePass"),
    ("invalid_preview", "prévia válida"),
    ("stale_preview", "diferenças atualizadas"),
    ("sensitive_remote_config", "campos sensíveis"),
    ("unsupported_remote_tunnel", "gerenciado remotamente"),
    ("recovery_verification_failed", "backup"),
    ("unknown_error", "corrija o erro"),
])
def test_failed_operation_reports_stage_code_and_action_without_writing(tmp_path, code, expected):
    path = write_operations(tmp_path, {"abc": {
        "operation": "provision", "created_at": 10, "state": "failed",
        "progress": "verificando hostname e DNS", "error_code": code,
        "error": "Não foi possível aplicar a publicação.",
    }})
    before = path.read_bytes()

    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]

    assert check["state"] == "error"
    assert "verificando hostname e DNS" in check["detail"]
    assert code in check["detail"]
    assert "Não foi possível aplicar a publicação." in check["detail"]
    assert expected in check["action"]
    assert before == path.read_bytes()


def test_binary_install_success_does_not_hide_unresolved_publication_error(tmp_path):
    write_operations(tmp_path, {
        "provision": {"operation": "provision", "created_at": 10, "state": "failed", "error_code": "dns_conflict"},
        "install": {"operation": "install-cloudflared", "created_at": 20, "state": "succeeded"},
    })
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    assert check["state"] == "error"
    assert "dns_conflict" in check["detail"]


def test_later_recovery_success_replaces_previous_provision_error(tmp_path):
    write_operations(tmp_path, {
        "old": {"operation": "provision", "created_at": 10, "state": "failed", "error_code": "dns_conflict"},
        "new": {"operation": "adopt-publication", "created_at": 20, "state": "succeeded"},
    })
    checks = by_label(diagnostics.diagnose(tmp_path))
    assert checks["Última operação Cloudflare"]["state"] == "ok"
    assert "dns_conflict" not in checks["Última operação Cloudflare"]["detail"]
    assert checks["Conexão Cloudflare"]["state"] == "pending"


@pytest.mark.parametrize("operation", ["provision", "adopt-publication"])
def test_successful_inspection_does_not_hide_unresolved_remote_write_error(tmp_path, operation):
    write_operations(tmp_path, {
        "write": {"operation": operation, "created_at": 10, "state": "failed", "error_code": "ingress_conflict"},
        "inspect": {"operation": "inspect-publication", "created_at": 20, "state": "succeeded"},
    })
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    assert check["state"] == "error"
    assert operation in check["detail"]
    assert "ingress_conflict" in check["detail"]


def test_failed_inspection_after_provision_failure_reports_new_permission_error(tmp_path):
    write_operations(tmp_path, {
        "write": {"operation": "provision", "created_at": 10, "state": "failed", "error_code": "dns_conflict"},
        "inspect": {"operation": "inspect-publication", "created_at": 20, "state": "failed", "error_code": "cloudflare_http_error", "error": "HTTP 403"},
    })
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    assert check["state"] == "error"
    assert "inspect-publication" in check["detail"]
    assert "403" in check["detail"]
    assert "permissões" in check["action"]
    assert "dns_conflict" not in check["detail"]


def test_successful_inspection_clears_previous_inspection_error_but_not_provision_error(tmp_path):
    write_operations(tmp_path, {
        "write": {"operation": "provision", "created_at": 10, "state": "failed", "error_code": "dns_conflict"},
        "old-inspect": {"operation": "inspect-publication", "created_at": 20, "state": "failed", "error_code": "cloudflare_http_error"},
        "new-inspect": {"operation": "inspect-publication", "created_at": 30, "state": "succeeded"},
    })
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    assert check["state"] == "error"
    assert "dns_conflict" in check["detail"]
    assert "cloudflare_http_error" not in check["detail"]


def test_successful_rollback_replaces_old_error_without_claiming_current_origin_ready(tmp_path):
    write_operations(tmp_path, {
        "adopt": {"operation": "adopt-publication", "created_at": 10, "state": "failed", "error_code": "operation_failed"},
        "rollback": {"operation": "rollback-publication", "created_at": 20, "state": "succeeded"},
        "inspect": {"operation": "inspect-publication", "created_at": 30, "state": "succeeded"},
    })
    checks = by_label(diagnostics.diagnose(tmp_path))
    check = checks["Última operação Cloudflare"]
    assert check["state"] == "ok"
    assert "restaurada" in check["detail"]
    assert "não confirma" in check["detail"]
    assert checks["Conexão Cloudflare"]["state"] == "pending"


def test_failed_inspection_is_reported_even_without_previous_publication_operation(tmp_path):
    write_operations(tmp_path, {
        "inspect": {"operation": "inspect-publication", "created_at": 10, "state": "failed", "error_code": "cloudflare_http_error"},
        "install": {"operation": "install-cloudflared", "created_at": 20, "state": "succeeded"},
    })
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    assert check["state"] == "error"
    assert "inspect-publication" in check["detail"]


@pytest.mark.parametrize("state, expected", [("queued", "pending"), ("running", "pending"), ("interrupted", "error")])
def test_incomplete_operation_never_reports_readiness(tmp_path, state, expected):
    write_operations(tmp_path, {"op": {"operation": "provision", "created_at": 10, "state": state, "progress": "consulta remota", "error_code": "operation_interrupted"}})
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    assert check["state"] == expected
    assert "consulta remota" in check["detail"]


@pytest.mark.parametrize("body", ["{invalid", "[]", '{"schema_version":1,"operations":[]}', '{"schema_version":1,"operations":{"x":{}}}'])
def test_invalid_operation_history_is_actionable_and_other_checks_continue(tmp_path, body):
    path = write_operations(tmp_path, {})
    path.write_text(body, encoding="utf-8")
    checks = by_label(diagnostics.diagnose(tmp_path))
    assert checks["Última operação Cloudflare"]["state"] == "error"
    assert "operations.json" in checks["Última operação Cloudflare"]["action"]
    assert "Conexão Cloudflare" in checks
    assert path.read_text(encoding="utf-8") == body


def test_operation_error_is_sanitized_before_presentation(tmp_path):
    write_operations(tmp_path, {"op": {
        "operation": "provision", "created_at": 10, "state": "failed",
        "progress": "token=stage-secret", "error_code": "cloudflare_http_error",
        "error": "HTTP 403 token=api-secret https://api.example.test/?secret=hidden-value",
    }})
    check = by_label(diagnostics.diagnose(tmp_path))["Última operação Cloudflare"]
    detail = json.dumps(check)
    assert "403" in detail
    assert "stage-secret" not in detail
    assert "api-secret" not in detail
    assert "hidden-value" not in detail
