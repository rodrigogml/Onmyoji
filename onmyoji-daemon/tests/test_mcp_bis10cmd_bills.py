"""Integração local MCP/BISCMD simulada, sem conexão EJB ou dados operacionais."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import Mock

import pytest

from onmyoji_daemon.mcp.bis10cmd import Bis10CmdError, Bis10CmdService
from onmyoji_daemon.mcp.bis10cmd.requests import RequestStore, destination_id
from onmyoji_daemon.mcp.configuration import McpConfiguration


BARCODE = "0" + "1" * 43
NUMERIC_CODE = "0" + "1" * 46


class Credentials:
    def __init__(self):
        self.calls = []

    def resolve(self, profile, entry, field):
        self.calls.append((profile, entry, field))
        return f"test-{profile}-{field}"


@pytest.fixture
def configured(tmp_path):
    """Configuração e diário pertencem a diretórios irmãos isolados da suíte."""
    root, work = tmp_path / "instance", tmp_path / "work"
    root.mkdir()
    work.mkdir()
    runtime = root / "runtime"
    runtime.mkdir()
    jar = runtime / "BISCMD.jar"
    jar.write_bytes(b"test")
    (runtime / "libs").mkdir()
    config = McpConfiguration(root)
    document = config.bis10cmd()
    document["profiles"]["bis"] = {
        "enabled": True, "description": "Instância de teste.", "jar_path": str(jar),
        "working_dir": str(runtime), "host": "localhost", "port": 8080, "locale": "pt-BR",
        "jndi_credential_profile": "jndi", "jndi_credential_entry_path": "JNDI",
        "jndi_username_field": "username", "jndi_password_field": "password",
        "bis_credential_profile": "bis", "bis_credential_entry_path": "BIS10",
        "bis_username_field": "username", "bis_password_field": "password",
    }
    config.save_bis10cmd(document)
    system = root / "configs" / "onmyoji-system.toml"
    system.write_text("[codex]\nproject_directory = " + json.dumps(str(work)) + "\n", encoding="utf-8")
    return Bis10CmdService(root, Credentials()), work


def envelope(resource, items, **extra):
    return {"version": 1, "resource": resource, "items": items, **extra}


def analysis(*, ready=True):
    return envelope("billCodeAnalysis", [{
        "code_valid": True, "supported": True, "ready_to_create": ready,
        "barcode": BARCODE, "numeric_code": NUMERIC_CODE,
        "pending_fields": [] if ready else [{"field": "categories"}],
        "warnings": [], "duplicates": [],
    }])


def bill():
    return {
        "id": 101, "company_id": 2, "operation": "DEBIT", "bill_type": "MANUAL",
        "state": "WAITING", "entry_date": "2026-11-07T11:00:00", "due_date": "2026-12-10",
        "pay_date": None, "original_value": "125.00", "final_value": "125.00", "recipient_person_id": 10,
        "payments": [{
            "id": 201, "bill_id": 101, "payment_type": "BOLETO", "payment_state": "WAITING",
            "barcode": BARCODE, "numeric_code": NUMERIC_CODE, "due_date": "2026-12-10", "pay_date": None,
            "original_value": "125.00", "final_value": "125.00", "payee_person_id": 11,
            "categories": [{"category_id": 7, "operation": "EXPENSE", "value": "125.00",
                            "cost_centers": [{"cost_center_id": 9, "ratio": "100.00"}]}],
        }],
    }


def create_arguments(**changes):
    return {
        "company_id": 2, "code": BARCODE, "request_id": "request-1", "confirm": True,
        "due_date": "2026-12-10", "original_value": "125", "recipient_person_id": 10,
        "payee_person_id": 11,
        "categories": [{"category_id": 7, "value": "125", "cost_centers": [{"cost_center_id": 9, "ratio": "100"}]}],
        **changes,
    }


class Java:
    """Simula somente o limite do subprocesso e mantém o protocolo real do serviço."""
    def __init__(self, monkeypatch, *, create=None, analyze=None, listing=None, capabilities=None):
        self.calls = []
        self.create = create
        self.analyze = analyze
        self.listing = listing
        self.capabilities = capabilities
        monkeypatch.setattr("onmyoji_daemon.mcp.bis10cmd.service.subprocess.run", self.run)

    def run(self, command, **options):
        native = "-bill" if "-bill" in command else "-people"
        action = command[command.index(native) + 1]
        payload = json.loads(options["input"]) if "input" in options else None
        self.calls.append({"command": command, "options": options, "action": action, "payload": payload})
        if action == "capabilities":
            response = self.capabilities if self.capabilities is not None else envelope("billCapabilities", [{
                "api_version": 1, "actions": ["analyzeCode", "createFromCode", "get", "list"],
            }])
        elif action == "analyzeCode":
            response = self.analyze if self.analyze is not None else analysis()
        elif action == "createFromCode":
            if isinstance(self.create, BaseException):
                raise self.create
            response = self.create if self.create is not None else envelope("bills", [bill()])
        elif action == "list" and native == "-people":
            response = envelope("people", [{"id": 10, "display_name": "Fornecedor"}], offset=0, limit=100, has_more=False)
        elif action == "list":
            response = self.listing if self.listing is not None else envelope("bills", [bill()], offset=0, limit=1000, has_more=False)
        else:
            response = envelope("bills", [bill()])
        return Mock(returncode=0, stdout=json.dumps(response) if isinstance(response, dict) else response, stderr="")


def test_analyze_normalizes_code_and_sends_utf8_stdin_without_credentials(configured, monkeypatch):
    service, work = configured
    java = Java(monkeypatch)
    result = service.invoke("bis", "analyze_bill_code", {"company_id": 2, "code": NUMERIC_CODE[:20] + ". " + NUMERIC_CODE[20:], "notes": "Água"})
    assert result["lookups"][0]["items"][0]["barcode"] == BARCODE
    call = java.calls[0]
    assert call["action"] == "analyzeCode"
    assert call["payload"] == {"code": NUMERIC_CODE, "notes": "Água"}
    assert call["command"][-6:] == ["-company", "id", "2", "-bill", "analyzeCode", "stdin"]
    assert call["options"]["encoding"] == "utf-8"
    assert "Água" in call["options"]["input"]
    assert "test-jndi-password" not in str(call["command"])
    assert call["options"]["env"]["BISCMD_JNDI_PASSWORD"] == "test-jndi-password"
    assert not (work / ".onmyoji").exists()


def test_create_preflights_and_persists_only_one_creation_with_replay(configured, monkeypatch):
    service, work = configured
    java = Java(monkeypatch)
    arguments = create_arguments(code=NUMERIC_CODE)
    first = service.invoke("bis", "create_bill_from_code", arguments)
    second = Bis10CmdService(service.root, Credentials()).invoke("bis", "create_bill_from_code", arguments)
    assert [call["action"] for call in java.calls[:3]] == ["capabilities", "analyzeCode", "createFromCode"]
    assert sum(call["action"] == "createFromCode" for call in java.calls) == 1
    sent = next(call for call in java.calls if call["action"] == "createFromCode")
    assert sent["command"][-3:] == ["createFromCode", "stdin", "confirm"]
    assert sent["payload"]["code"] == BARCODE
    assert sent["payload"]["original_value"] == "125.00"
    assert sent["payload"]["categories"][0]["cost_centers"][0]["ratio"] == "100.00"
    assert "entry_date" in sent["payload"]
    assert "request_id" not in sent["payload"] and "confirm" not in sent["payload"]
    assert first["request"]["state"] == "SUCCEEDED" and first["replayed"] is False
    assert second["replayed"] is True and second["lookups"] == first["lookups"]
    journal = work / ".onmyoji" / "bis10cmd" / "requests.sqlite3"
    record = RequestStore(journal).get(destination_id("localhost", 8080), 2, "request-1")
    assert record["payload"] == sent["payload"]
    assert "test-jndi-password" not in json.dumps(record)


def test_known_result_replays_even_when_subprocess_is_unavailable(configured, monkeypatch):
    service, _ = configured
    Java(monkeypatch)
    first = service.invoke("bis", "create_bill_from_code", create_arguments())
    unavailable = Mock(side_effect=OSError("offline"))
    monkeypatch.setattr("onmyoji_daemon.mcp.bis10cmd.service.subprocess.run", unavailable)
    replay = service.invoke("bis", "create_bill_from_code", create_arguments())
    assert replay["lookups"] == first["lookups"] and replay["replayed"] is True
    unavailable.assert_not_called()


def test_profiles_for_same_destination_share_request_identity(configured, monkeypatch):
    service, _ = configured
    config = McpConfiguration(service.root)
    document = config.bis10cmd()
    document["profiles"]["alias"] = {**document["profiles"]["bis"], "host": "LOCALHOST."}
    config.save_bis10cmd(document)
    java = Java(monkeypatch)
    first = service.invoke("bis", "create_bill_from_code", create_arguments())
    replay = service.invoke("alias", "create_bill_from_code", create_arguments())
    assert replay["replayed"] is True and replay["lookups"] == first["lookups"]
    assert sum(call["action"] == "createFromCode" for call in java.calls) == 1


def test_same_request_with_changed_content_conflicts_before_any_new_subprocess(configured, monkeypatch):
    service, _ = configured
    java = Java(monkeypatch)
    service.invoke("bis", "create_bill_from_code", create_arguments())
    java.calls.clear()
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments(original_value="126"))
    assert caught.value.code == "request_conflict" and not java.calls


def test_classification_response_order_does_not_change_financial_meaning(configured, monkeypatch):
    service, _ = configured
    categories = [
        {"category_id": 7, "value": "100.00", "cost_centers": [
            {"cost_center_id": 9, "ratio": "60.00"}, {"cost_center_id": 10, "ratio": "40.00"}]},
        {"category_id": 8, "value": "25.00", "cost_centers": [{"cost_center_id": 11, "ratio": "100.00"}]},
    ]
    returned = bill()
    returned["payments"][0]["categories"] = [
        {**categories[1], "operation": "EXPENSE"},
        {**categories[0], "operation": "EXPENSE", "cost_centers": list(reversed(categories[0]["cost_centers"]))},
    ]
    Java(monkeypatch, create=envelope("bills", [returned]))
    result = service.invoke("bis", "create_bill_from_code", create_arguments(categories=categories))
    assert result["request"]["state"] == "SUCCEEDED"


def test_valid_server_classification_can_complete_an_omitted_draft(configured, monkeypatch):
    service, _ = configured
    Java(monkeypatch)
    arguments = create_arguments()
    del arguments["categories"]
    result = service.invoke("bis", "create_bill_from_code", arguments)
    assert result["request"]["state"] == "SUCCEEDED"
    assert result["lookups"][0]["items"][0]["payments"][0]["categories"]


@pytest.mark.parametrize("capabilities", ["legacy help", envelope("billCapabilities", [{"api_version": 1, "actions": ["get"]}])])
def test_incompatible_jar_stops_before_reservation_or_write(configured, monkeypatch, capabilities):
    service, work = configured
    java = Java(monkeypatch, capabilities=capabilities)
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code in {"bill_query_unavailable", "bill_client_incompatible"}
    assert [call["action"] for call in java.calls] == ["capabilities"]
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    assert store.get(destination_id("localhost", 8080), 2, "request-1") is None


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("java", 1), "not JSON", envelope("bills", [])])
def test_uncertain_creation_persists_and_blocks_same_code_under_another_key(configured, monkeypatch, failure):
    service, work = configured
    java = Java(monkeypatch, create=failure)
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code == "outcome_unknown"
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    assert store.get(destination_id("localhost", 8080), 2, "request-1")["state"] == "UNKNOWN"
    reopened = Bis10CmdService(service.root, Credentials())
    with pytest.raises(Bis10CmdError) as caught:
        reopened.invoke("bis", "create_bill_from_code", create_arguments(request_id="another-key"))
    assert caught.value.code == "bill_code_pending"
    assert sum(call["action"] == "createFromCode" for call in java.calls) == 1
    with pytest.raises(Bis10CmdError) as caught:
        reopened.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code == "outcome_unknown"
    assert sum(call["action"] == "createFromCode" for call in java.calls) == 1


def test_get_request_reconciles_read_only_without_attributing_external_candidate(configured, monkeypatch):
    service, work = configured
    java = Java(monkeypatch, create=subprocess.TimeoutExpired("java", 1))
    with pytest.raises(Bis10CmdError):
        service.invoke("bis", "create_bill_from_code", create_arguments())
    java.calls.clear()
    status = service.invoke("bis", "get_bill_request", {"company_id": 2, "request_id": "request-1"})
    assert [call["action"] for call in java.calls] == ["list"]
    assert java.calls[0]["payload"] == {"code": BARCODE, "limit": 1000}
    assert status["request"]["state"] == "UNKNOWN"
    assert status["reconciliation"] == {"candidates": [bill()], "has_more": False, "attribution": "unconfirmed", "automatic_resend": False}
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    assert store.get(destination_id("localhost", 8080), 2, "request-1")["state"] == "UNKNOWN"


@pytest.mark.parametrize("outcome", ["not_submitted", "rejected"])
def test_explicit_nonwriting_failure_is_rejected_and_replayed(configured, monkeypatch, outcome):
    service, work = configured
    error = envelope("billError", [{"code": "invalid_due_date", "message": "Vencimento inválido.", "outcome": outcome}])
    java = Java(monkeypatch, create=error)
    for _ in range(2):
        with pytest.raises(Bis10CmdError) as caught:
            service.invoke("bis", "create_bill_from_code", create_arguments())
        assert caught.value.code == "invalid_due_date"
    assert sum(call["action"] == "createFromCode" for call in java.calls) == 1
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    assert store.get(destination_id("localhost", 8080), 2, "request-1")["state"] == "REJECTED"


@pytest.mark.parametrize("mutation", [
    "company", "state", "payment_state", "barcode", "missing_barcode", "paid", "person", "due_date", "id",
    "payment_due_date", "payment_original_value", "aggregate_total", "empty_categories",
    "category_value", "category_id", "center_ratio", "center_id", "invalid_state_type", "invalid_category_operation_type",
])
def test_create_never_confirms_mismatched_or_incomplete_response(configured, monkeypatch, mutation):
    service, work = configured
    returned = bill()
    if mutation == "company": returned["company_id"] = 3
    elif mutation == "state": returned["state"] = "PAYED"
    elif mutation == "payment_state": returned["payments"][0]["payment_state"] = "PAYED"
    elif mutation == "barcode": returned["payments"][0]["barcode"] = "2" * 44
    elif mutation == "missing_barcode": del returned["payments"][0]["barcode"]
    elif mutation == "paid": returned["pay_date"] = "2026-11-07"
    elif mutation == "person": returned["recipient_person_id"] = 99
    elif mutation == "due_date": returned["due_date"] = "2026-12-11"
    elif mutation == "id": returned["id"] = True
    elif mutation == "payment_due_date": returned["payments"][0]["due_date"] = "2026-12-11"
    elif mutation == "payment_original_value": returned["payments"][0]["original_value"] = "126.00"
    elif mutation == "aggregate_total": returned["final_value"] = "126.00"
    elif mutation == "empty_categories": returned["payments"][0]["categories"] = []
    elif mutation == "category_value": returned["payments"][0]["categories"][0]["value"] = "1.00"
    elif mutation == "category_id": returned["payments"][0]["categories"][0]["category_id"] = 8
    elif mutation == "center_ratio": returned["payments"][0]["categories"][0]["cost_centers"][0]["ratio"] = "99.00"
    elif mutation == "center_id": returned["payments"][0]["categories"][0]["cost_centers"][0]["cost_center_id"] = 99
    elif mutation == "invalid_state_type": returned["state"] = []
    elif mutation == "invalid_category_operation_type": returned["payments"][0]["categories"][0]["operation"] = []
    Java(monkeypatch, create=envelope("bills", [returned]))
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code == "outcome_unknown"
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    assert store.get(destination_id("localhost", 8080), 2, "request-1")["state"] == "UNKNOWN"


@pytest.mark.parametrize("company", [None, True, 0, -1, "2"])
def test_company_and_confirmation_are_validated_before_credentials(configured, monkeypatch, company):
    service, _ = configured
    java = Java(monkeypatch)
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments(company_id=company))
    assert caught.value.code == "invalid_request"
    assert not java.calls and not service.credentials.calls


def test_confirmation_is_required_before_credentials_or_journal(configured, monkeypatch):
    service, work = configured
    java = Java(monkeypatch)
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments(confirm=False))
    assert caught.value.code == "confirmation_required"
    assert not java.calls and not service.credentials.calls and not (work / ".onmyoji").exists()


def test_not_ready_analysis_does_not_reserve(configured, monkeypatch):
    service, work = configured
    java = Java(monkeypatch, analyze=analysis(ready=False))
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code == "bill_not_ready"
    assert [call["action"] for call in java.calls] == ["capabilities", "analyzeCode"]
    assert RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3").get(destination_id("localhost", 8080), 2, "request-1") is None


def test_lookup_commands_and_selected_company(configured, monkeypatch):
    service, _ = configured
    java = Java(monkeypatch)
    assert service.invoke("bis", "get_bill", {"company_id": 2, "bill_id": 101})["lookups"][0]["items"][0]["id"] == 101
    assert java.calls[-1]["command"][-7:] == ["-company", "id", "2", "-bill", "get", "id", "101"]
    result = service.invoke("bis", "list_people", {"company_id": 2, "name": "Fornecedor"})
    assert result["lookups"][0]["items"][0]["display_name"] == "Fornecedor"
    assert java.calls[-1]["command"][-6:] == ["-company", "id", "2", "-people", "list", "stdin"]


def test_reconciliation_without_pagination_is_reported_as_functional_error(configured, monkeypatch):
    service, _ = configured
    Java(monkeypatch, create=subprocess.TimeoutExpired("java", 1), listing=envelope("bills", [bill()]))
    with pytest.raises(Bis10CmdError):
        service.invoke("bis", "create_bill_from_code", create_arguments())
    result = service.invoke("bis", "get_bill_request", {"company_id": 2, "request_id": "request-1"})
    assert result["request"]["state"] == "UNKNOWN"
    assert result["reconciliation_error"]["code"] == "invalid_bill_response"


def test_reconciliation_never_exposes_candidates_from_another_company(configured, monkeypatch):
    service, _ = configured
    candidate = bill()
    candidate["company_id"] = 3
    Java(monkeypatch, create=subprocess.TimeoutExpired("java", 1),
         listing=envelope("bills", [candidate], offset=0, limit=1000, has_more=False))
    with pytest.raises(Bis10CmdError):
        service.invoke("bis", "create_bill_from_code", create_arguments())
    result = service.invoke("bis", "get_bill_request", {"company_id": 2, "request_id": "request-1"})
    assert result["reconciliation_error"]["code"] == "invalid_bill_response"
    assert "reconciliation" not in result


def test_workspace_inside_instance_is_refused_before_reservation(configured, monkeypatch):
    service, _ = configured
    internal = service.root / "internal-work"
    internal.mkdir()
    (service.root / "configs" / "onmyoji-system.toml").write_text(
        "[codex]\nproject_directory = " + json.dumps(str(internal)) + "\n", encoding="utf-8")
    java = Java(monkeypatch)
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code == "workspace_required"
    assert not java.calls and not (internal / ".onmyoji").exists()


def test_rejected_error_and_journal_do_not_leak_resolved_credentials(configured, monkeypatch):
    service, work = configured
    Java(monkeypatch, create=envelope("billError", [{
        "code": "rejected", "message": "test-bis-password / test-jndi-password", "outcome": "not_submitted",
        "details": {"field": "notes", "value": "test-bis-username"},
    }]))
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert str(caught.value) == "[REDACTED] / [REDACTED]"
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    record = store.get(destination_id("localhost", 8080), 2, "request-1")
    serialized = json.dumps({"error": caught.value.as_dict(), "record": record})
    for secret in ("test-bis-password", "test-jndi-password", "test-bis-username"):
        assert secret not in serialized


def test_conflicting_success_and_rejection_envelopes_never_release_code(configured, monkeypatch):
    service, work = configured
    stdout = json.dumps(envelope("bills", [bill()])) + "\n" + json.dumps(envelope("billError", [{
        "code": "invalid_due_date", "message": "Rejeitado.", "outcome": "rejected",
    }]))
    Java(monkeypatch, create=stdout)
    with pytest.raises(Bis10CmdError) as caught:
        service.invoke("bis", "create_bill_from_code", create_arguments())
    assert caught.value.code == "outcome_unknown"
    store = RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")
    assert store.get(destination_id("localhost", 8080), 2, "request-1")["state"] == "UNKNOWN"


def test_local_confirmed_request_survives_missing_java_artifact(configured, monkeypatch):
    service, _ = configured
    java = Java(monkeypatch)
    first = service.invoke("bis", "create_bill_from_code", create_arguments())
    jar = service.root / "runtime" / "BISCMD.jar"
    jar.unlink()
    java.calls.clear()
    replay = service.invoke("bis", "create_bill_from_code", create_arguments())
    status = service.invoke("bis", "get_bill_request", {"company_id": 2, "request_id": "request-1"})
    assert replay["replayed"] is True and replay["lookups"] == first["lookups"]
    assert status["request"]["state"] == "SUCCEEDED"
    assert not java.calls
