"""Cenários contratuais sem conexão a contas reais ou movimentação bancária."""
from __future__ import annotations
import concurrent.futures
import copy
import json
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest
from jsonschema import Draft202012Validator

from onmyoji_daemon.mcp.btg.payments import Engine
from onmyoji_daemon.mcp.btg.contracts import BtgError, bank_date, date, digest, json_exact, money, prepare_item, public
from onmyoji_daemon.mcp.btg.store import Store
from onmyoji_daemon.mcp.btg.service import BtgService
from onmyoji_daemon.mcp.btg.tools import BtgToolRegistry
from onmyoji_daemon.mcp.btg.payment_tools import preparation_schema


@pytest.fixture
def profile(tmp_path):
    return {"environment": "sandbox", "company_id": "30306294000145", "account_id": "account-1", "account_number": "000050", "branch_code": "50", "workspace": str(tmp_path / "work"), "vault_profile": "vault", "vault_entry_path": "BTG/test", "client_id": "app", "redirect_uri": "http://127.0.0.1:8765/callback", "scopes": "openid empresas.btgpactual.com/accounts.readonly brn:btg:empresas:banking:payments", "max_amount": "1000.00", "timeout_seconds": 1, "max_pages": 2, "pending_interval": 300, "scheduled_interval": 3600, "confirmed_interval": 86400, "confirmation_days": 7, "_config": str(tmp_path / "code" / "configs" / "btg-empresas.toml"), "_profile": "test"}


@pytest.fixture
def body():
    return {"type": "PIX_KEY", "amount": "12.34", "paymentDate": "2026-10-20", "description": "Fornecedor", "detail": {"key": {"value": "fornecedor@example.com"}, "creditParty": {"name": "Fornecedor", "taxId": "12345678901"}}}


class Bank:
    def __init__(self, profile):
        self.profile, self.calls, self.created, self.fail, self.deadline = profile, [], None, None, None
        self.status = "CREATED"

    def verify_account(self):
        return {"accountId": self.profile["account_id"]}

    def token(self):
        return {"operatorId": "operator"}

    def payment(self):
        item = self.created["items"][0]
        return {"paymentId": "bank-id", "status": self.status, "type": "PIX", "amount": item["amount"], "tags": item["tags"], "debitParty": {"taxId": self.profile["company_id"], "number": self.profile["account_number"], "branchCode": self.profile["branch_code"]}, "paymentDate": "2026-10-21T00:00:00.000Z"}

    def call(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "payments.create":
            self.created = kwargs["body"]
            if self.fail:
                raise self.fail
            return {"batchId": "batch", "contractGuid": "contract", "operationNeedsApproval": True}
        if operation == "payments.remote-list":
            return {"data": [self.payment()], "_links": {}}
        if operation == "payments.remote-get":
            if self.fail:
                raise self.fail
            return self.payment()
        if operation.startswith("payments.cancel-"):
            self.status = "CANCELED"
            return None
        if operation == "payments.receipt":
            return b"%PDF-1.7\nfake-test-pdf"
        raise AssertionError(operation)


def prepared_engine(profile, body):
    bank = Bank(profile)
    engine = Engine(profile, bank)
    payment = engine.prepare(body)
    return engine, bank, payment


def test_payment_lifecycle_and_restart_preserve_evidence(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    submitted = engine.submit(row["id"], row["preparationHash"])
    assert submitted["submissionState"] == "accepted" and submitted["approvalState"] == "pending"
    refreshed = engine.refresh(row["id"])
    assert refreshed["bankPaymentId"] == "bank-id" and refreshed["approvalState"] == "unknown"
    assert refreshed["bankPaymentDate"] is None
    bank.status = "CONFIRMED"
    restarted = Engine(profile, bank)
    confirmed = restarted.refresh(row["id"])
    assert confirmed["approvalState"] == "evidenced" and confirmed["confirmationObservedAt"]
    assert confirmed["requestedPaymentDate"] == "2026-10-20" and confirmed["bankPaymentDate"] == "2026-10-21"
    assert confirmed["dueDate"] is None and confirmed["nextCheckAt"]
    receipt = restarted.invoke("get_payment_receipt", {"payment_id": row["id"]})
    assert receipt["receipt_id"] == row["id"] and receipt["available"] is True
    assert "path" not in receipt
    assert Path(restarted.store.get(row["id"])["receiptPath"]).name.endswith(".pdf")
    bank.status = "REVERTED"
    reversed_row = restarted.refresh(row["id"])
    assert reversed_row["bankStatus"] == "REVERTED" and reversed_row["nextCheckAt"] is None
    assert len(restarted.store.history(row["id"])) >= 7
    with pytest.raises(BtgError, match="já enviada"):
        restarted.submit(row["id"], row["preparationHash"])
    assert len([op for op, _ in bank.calls if op == "payments.create"]) == 1


def test_timeout_after_bank_acceptance_recovers_without_new_send(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    bank.fail = BtgError("bank_unavailable", "timeout")
    assert engine.submit(row["id"], row["preparationHash"])["submissionState"] == "unknown"
    assert engine.refresh(row["id"])["bankPaymentId"] == "bank-id"
    assert len([op for op, _ in bank.calls if op == "payments.create"]) == 1


def test_bank_rejection_is_preserved_and_not_retried(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    bank.fail = BtgError("bank_http_error", "forbidden", 403)
    result = engine.submit(row["id"], row["preparationHash"])
    assert result["submissionState"] == "rejected" and result["nextCheckAt"] is None
    with pytest.raises(BtgError):
        engine.submit(row["id"], row["preparationHash"])


def test_failed_check_does_not_change_bank_status(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    original = engine.refresh(row["id"])
    bank.fail = BtgError("bank_http_error", "revoked", 401)
    updated = engine.refresh(row["id"])
    assert updated["bankStatus"] == original["bankStatus"]
    assert updated["lastSuccessfulCheckAt"] == original["lastSuccessfulCheckAt"]
    assert updated["failures"] == 1 and updated["lastError"] == "bank_http_error"


def test_concurrent_reservation_only_allows_one_send(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    def reserve():
        try:
            return engine.store.reserve(row["id"], row["preparationHash"], "operator")[1]
        except BtgError:
            return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert bank.calls == []


def test_interrupted_attempt_and_immutable_history(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.store.reserve(row["id"], row["preparationHash"], "operator")
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET leaseUntil='2000-01-01' WHERE id=?", (row["id"],))
    bank.created = {"items": [json.loads(engine.store.get(row["id"])["item"], parse_float=Decimal)]}
    assert engine.refresh(row["id"])["submissionState"] == "accepted"
    assert any(e["kind"] == "interrupted_submission" for e in engine.store.history(row["id"]))
    with pytest.raises(sqlite3.IntegrityError):
        with engine.store.connection() as con:
            con.execute("DELETE FROM paymentEvent")


def test_context_isolation_and_changed_preparation(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    with pytest.raises(BtgError):
        engine.submit(row["id"], "wrong-hash")
    assert bank.calls == []
    other = dict(profile, company_id="11111111000111")
    with pytest.raises(BtgError):
        Engine(other, Bank(other)).store.get(row["id"])
    production = dict(profile, environment="production")
    assert Engine(production, Bank(production)).store.list({})["items"] == []


@pytest.mark.parametrize("state", ["SCHEDULED", "CREATED"])
def test_cancel_requires_confirmation_and_bank_confirmation(profile, body, state):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.status = state
    with pytest.raises(BtgError):
        engine.invoke("cancel_payment", {"payment_id": row["id"]})
    result = engine.invoke("cancel_payment", {"payment_id": row["id"], "preparation_hash": row["preparationHash"], "confirm": True})
    assert result["bankStatus"] == "CANCELED" and result["cancellationState"] == "confirmed"


def test_ambiguous_and_incomplete_listing_never_bind_payment(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.call = lambda *a, **kw: {"data": [bank.payment(), bank.payment()], "_links": {}}
    assert engine.refresh(row["id"])["lastError"] == "payment_unresolved"
    bank.call = lambda *a, **kw: {"data": [bank.payment()], "_links": {"next": {"href": "untrusted"}}}
    assert engine.refresh(row["id"])["lastError"] == "incomplete_listing"
    assert engine.store.get(row["id"])["bankPaymentId"] is None


def test_listing_ignores_unreferenced_items_without_tags(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    unrelated = bank.payment()
    unrelated.pop("tags", None)
    bank.call = lambda *a, **kw: {"data": [unrelated], "_links": {}}
    assert engine.refresh(row["id"])["lastError"] == "payment_unresolved"


def test_wrong_account_or_amount_cannot_be_associated(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    value = bank.payment()
    value["debitParty"]["number"] = "other"
    bank.call = lambda *a, **kw: {"data": [value], "_links": {}}
    assert engine.refresh(row["id"])["lastError"] == "context_mismatch"
    value["debitParty"]["number"] = profile["account_number"]
    value["amount"] = Decimal("1.00")
    assert engine.refresh(row["id"])["lastError"] == "amount_mismatch"


def test_monitor_is_read_only_and_unknown_states_are_preserved(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.status = "NEW_BANK_STATUS"
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET nextCheckAt='2000-01-01' WHERE id=?", (row["id"],))
    before = len(bank.calls)
    assert len(engine.monitor_check()["checked"]) == 1
    assert all(op.startswith("payments.remote-") for op, _ in bank.calls[before:])
    assert engine.store.get(row["id"])["bankStatus"] == "NEW_BANK_STATUS"


@pytest.mark.parametrize("value", [None, True, 1.23, "NaN", "Infinity", "-1", "0", "1.001", "1000000000000", "abc"])
def test_invalid_amounts(value):
    with pytest.raises(BtgError):
        money(value)


def test_money_and_dates_are_exact():
    assert money("0.01") == Decimal("0.01")
    assert money(0, positive=False) == 0
    assert json_exact({"amount": money("123456789.12")}) == '{"amount":123456789.12}'
    assert public({"a": [Decimal("0.10")]}) == {"a": ["0.10"]}
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    assert bank_date(None) is None and bank_date("2026-10-21T00:00:00Z") == "2026-10-21"
    for value in [None, "2026-02-30", "2026-1-01"]:
        with pytest.raises(BtgError):
            date(value)


@pytest.mark.parametrize("change", [{"type": "PIX_REVERSAL"}, {"items": []}, {"amount": "1000.01"}, {"detail": {"key": {"value": "a"}}}, {"paymentDate": "invalid"}, {"detail": {"creditParty": {"name": "F", "taxId": "123"}, "key": {"value": "a"}}}])
def test_invalid_preparations(profile, body, change):
    with pytest.raises(BtgError):
        prepare_item(dict(body, **change), profile, "id")


@pytest.mark.parametrize("kind", ["TED", "PIX_MANUAL", "PIX_QR_CODE", "DARF", "BANKSLIP", "UTILITIES"])
def test_all_supported_modalities(profile, body, kind):
    value = dict(body, type=kind)
    if kind in {"TED", "PIX_MANUAL"}:
        value["detail"] = {"creditParty": {"name": "F", "taxId": "12345678901", "account": {"type": "CC", "number": "001", "branch": "1", "bankCode": "208"}}}
    elif kind == "PIX_QR_CODE":
        value["detail"] = {"emv": "000201-test", "creditParty": body["detail"]["creditParty"]}
    elif kind == "DARF":
        value["detail"] = {"taxPayer": {"id": profile["company_id"], "name": "Empresa"}, "expireDate": "2026-10-20", "baselinePeriodDate": "2026-09-01", "principalAmount": "12.00", "fineAmount": "0.34", "treasuryRevenueCode": "0211"}
    else:
        value["detail"] = {"digitableLine": "1" * 47} if kind == "BANKSLIP" else {"barcode": "8" * 44}
    Draft202012Validator(preparation_schema()).validate(value)
    item, metadata = prepare_item(value, profile, "local-id")
    assert item["type"] == kind and item["amount"] == Decimal("12.34")
    assert item["tags"]["externalId"] == "local-id"


def test_receipt_validation_and_local_filters(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    with pytest.raises(BtgError):
        engine.store.receipt(row["id"], b"not PDF")
    result = engine.store.list({"createdFrom": "2000-01-01", "limit": 1})
    assert result["items"][0]["id"] == row["id"]
    assert engine.store.list({"dueFrom": "2026-01-01"})["items"] == []
    with pytest.raises(BtgError):
        engine.store.list({"rawSql": "select"})


def test_barcode_preparation_revalidates_beneficiary_before_submit(profile, body):
    bank = Bank(profile)
    engine = Engine(profile, bank)
    code = "1" * 47
    value = dict(body, type="BANKSLIP", detail={"digitableLine": code}, dueDate="2026-10-20")
    bank_row = {"digitableLine": code, "amount": Decimal("12.34"), "dueDate": "2026-10-20", "payee": {"socialName": "Fornecedor", "taxId": "12345678901"}}
    original_call = bank.call
    bank.call = lambda op, **kw: {"data": [bank_row]} if op == "barcodes.get" else original_call(op, **kw)
    prepared = engine.prepare(value)
    assert prepared["dueDate"] == "2026-10-20"
    bank_row["payee"]["taxId"] = "11111111111"
    with pytest.raises(BtgError):
        engine.submit(prepared["id"], prepared["preparationHash"])
    assert bank.created is None
    bank_row["amount"] = Decimal("1.00")
    with pytest.raises(BtgError):
        engine.prepare(value)


def test_exact_money_nested_darf_reference_and_missing_metadata(profile, body):
    assert json_exact([Decimal("1.23"), "São Paulo", True, None]) == '[1.23,"São Paulo",true,null]'
    value = dict(body, type="DARF", detail={"taxPayer": {"id": profile["company_id"], "name": "Empresa"}, "expireDate": "2026-10-20", "baselinePeriodDate": "2026-09-01", "principalAmount": "12.34", "treasuryRevenueCode": "0211", "referenceNumber": "123"}, document="DARF-123", reference="contas-pagar-1")
    item, metadata = prepare_item(value, profile, "id")
    assert item["detail"]["referenceNumber"] == "123" and metadata["document"] == "DARF-123"


def test_preparation_integrity_and_changed_profile_limit_block_send(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    profile["max_amount"] = "1.00"
    with pytest.raises(BtgError):
        engine.submit(row["id"], row["preparationHash"])
    profile["max_amount"] = "1000.00"
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET item=replace(item,'12.34','10.00') WHERE id=?", (row["id"],))
    with pytest.raises(BtgError):
        engine.submit(row["id"], row["preparationHash"])
    assert bank.created is None


def test_interrupted_cancellation_and_civil_date_filters(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.status = "SCHEDULED"
    engine.refresh(row["id"])
    engine.store.reserve(row["id"], row["preparationHash"], "operator", "cancel")
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET leaseUntil='2000-01-01',createdAt='2026-10-15T01:00:00+00:00' WHERE id=?", (row["id"],))
    assert engine.refresh(row["id"])["cancellationState"] == "unknown"
    assert engine.store.list({"createdFrom": "2026-10-14", "createdTo": "2026-10-14"})["items"][0]["id"] == row["id"]
    assert engine.store.list({"createdFrom": "2026-10-15", "createdTo": "2026-10-15"})["items"] == []
    assert engine.store.list({"createdFrom": "2026-10-14T22:00:00-03:00"})["items"]
    with pytest.raises(BtgError):
        engine.store.list({"createdFrom": "no-date"})


def test_monitor_skips_busy_item_without_sending(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.store.reserve(row["id"], row["preparationHash"], "operator")
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET nextCheckAt='2000-01-01' WHERE id=?", (row["id"],))
    assert engine.monitor_check()["checked"] == []
    assert bank.calls == []


def test_exact_local_filters_and_dispatched_bank_lifecycle(profile, body):
    bank = Bank(profile)
    engine = Engine(profile, bank)
    row = engine.invoke("prepare_payment", {"confirm": True, "body": body})
    assert engine.store.list({"id": row["id"], "submissionState": "prepared"})["items"][0]["id"] == row["id"]
    assert engine.invoke("submit_payment", {"confirm": True, "payment_id": row["id"], "preparation_hash": row["preparationHash"]})["submissionState"] == "accepted"
    assert engine.invoke("refresh_payment", {"payment_id": row["id"]})["bankPaymentId"] == "bank-id"


def test_confirmation_window_and_old_pending_operations(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    engine.refresh(row["id"])
    bank.status = "CONFIRMED"
    engine.refresh(row["id"])
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET confirmationObservedAt='2000-01-01T00:00:00+00:00',submittedAt='2000-01-01T00:00:00+00:00' WHERE id=?", (row["id"],))
    assert engine.refresh(row["id"])["nextCheckAt"] is None
    bank.status = "ADJOURNED"
    refreshed = engine.refresh(row["id"])
    import datetime as dt
    assert dt.datetime.fromisoformat(refreshed["nextCheckAt"]) > dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=23)


def test_processed_does_not_prove_human_approval(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.status = "PROCESSED"
    result = engine.refresh(row["id"])
    assert result["approvalState"] == "unknown"
    assert result["approvalObservedAt"] is None and result["confirmationObservedAt"] is None


def test_cancellation_http_failure_does_not_claim_cancelled(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.status = "SCHEDULED"
    original = bank.call
    def call(op, **kw):
        if op.startswith("payments.cancel-"):
            raise BtgError("bank_http_error", "rejected", 403)
        return original(op, **kw)
    bank.call = call
    result = engine.cancel(row["id"], row["preparationHash"])
    assert result["bankStatus"] == "SCHEDULED" and result["cancellationState"] == "rejected"


def test_cancel_never_uses_stale_bank_state(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.status = "SCHEDULED"
    engine.refresh(row["id"])
    bank.fail = BtgError("bank_unavailable", "timeout")
    with pytest.raises(BtgError):
        engine.cancel(row["id"], row["preparationHash"])
    assert not any(op.startswith("payments.cancel-") for op, _ in bank.calls)


def test_mcp_registry_payment_lifecycle_and_sanitized_history(profile, body, monkeypatch):
    bank = Bank(profile)
    config = Mock()
    config.enabled_btg_profiles.return_value = [{"name": "company", "description": "Contexto da conta"}]
    service = BtgService(Path(profile["workspace"]))
    service.client = lambda _name: bank
    monkeypatch.setattr("onmyoji_daemon.mcp.btg.tools.BtgService", lambda _root: service)
    registry = BtgToolRegistry(config)
    row = registry.call("btg.prepare_payment", {"body": body, "confirm": True})
    assert row["item"]["amount"] == "12.34" and bank.calls == []
    assert not {"idempotencyKey", "operatorId", "leaseOwner"} & set(row)
    identifier, revision = row["id"], row["preparationHash"]
    listed = registry.call("btg.list_payments", {"filters": {"limit": 1}})
    assert listed["source"] == "local-memory" and listed["items"][0]["id"] == identifier
    assert registry.call("btg.get_payment", {"payment_id": identifier})["id"] == identifier
    assert registry.call("btg.submit_payment", {"payment_id": identifier, "preparation_hash": revision, "confirm": True})["submissionState"] == "accepted"
    bank.status = "CONFIRMED"
    assert registry.call("btg.refresh_payment", {"payment_id": identifier})["bankStatus"] == "CONFIRMED"
    history = registry.call("btg.get_payment_history", {"payment_id": identifier})
    assert history["source"] == "local-memory" and isinstance(history["events"][0]["evidence"], dict)
    assert "operatorId" not in json.dumps(history) and "idempotencyKey" not in json.dumps(history)
    receipt = registry.call("btg.get_payment_receipt", {"payment_id": identifier})
    assert receipt == {"receipt_id": identifier, "available": True, "source": "bank", "observedAt": receipt["observedAt"]}
    assert "path" not in receipt
    assert registry.call("btg.get_monitor_status", {})["source"] == "local-memory"
    assert registry.call("btg.check_pending_payments", {})["checked"] == []


@pytest.mark.parametrize("operation", ["prepare_payment", "submit_payment", "cancel_payment"])
@pytest.mark.parametrize("confirmation", [None, False, 1, "true"])
def test_engine_rejects_missing_explicit_confirmation_without_store_or_bank(profile, operation, confirmation):
    bank = Bank(profile)
    engine = Engine(profile, bank)
    with pytest.raises(BtgError) as error:
        engine.invoke(operation, {"confirm": confirmation})
    assert error.value.code == "confirmation_required"
    assert bank.calls == [] and engine._store is None


@pytest.mark.parametrize("exception,code", [(sqlite3.OperationalError("private DB path"), "persistence_unavailable"), (OSError("private OS detail"), "persistence_unavailable"), (ValueError("private payload"), "invalid_data")])
def test_service_payment_errors_are_sanitized(profile, exception, code, monkeypatch):
    service = BtgService(Path(profile["workspace"]))
    service.client = lambda _name: Bank(profile)
    monkeypatch.setattr(Engine, "invoke", Mock(side_effect=exception))
    with pytest.raises(BtgError) as error:
        service.invoke("company", "list_payments", {})
    assert error.value.code == code and "private" not in str(error.value)


def test_unknown_tool_arguments_and_filters_do_not_send_payments(profile, body, monkeypatch):
    config = Mock()
    config.enabled_btg_profiles.return_value = [{"name": "company", "description": "Contexto"}]
    service = Mock()
    monkeypatch.setattr("onmyoji_daemon.mcp.btg.tools.BtgService", lambda _root: service)
    with pytest.raises(ValueError):
        BtgToolRegistry(config).call("btg.submit_payment", {"confirm": True, "url": "https://invalid.example"})
    service.invoke.assert_not_called()
    engine = Engine(profile, Bank(profile))
    for filters in [None, {"sql": "SELECT 1"}, {"dueOnly": "false"}, {"limit": True}, {"offset": -1}]:
        with pytest.raises(BtgError):
            engine.invoke("list_payments", {"filters": filters})


def test_monitor_deadline_is_restored_on_unexpected_error(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    with engine.store.connection() as con:
        con.execute("UPDATE payment SET nextCheckAt='2000-01-01' WHERE id=?", (row["id"],))
    deadline = time.monotonic() + 1000
    bank.deadline = deadline
    engine.refresh = Mock(side_effect=BtgError("monitor_error", "Falha sanitizada"))
    with pytest.raises(BtgError):
        engine.monitor_check()
    assert bank.deadline == deadline
