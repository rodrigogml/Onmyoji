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

sys.path.insert(0, str(Path(__file__).parent))
from btg import Engine, execute
from client import Client, NoRedirect, load_profile
from contracts import BtgError, bank_date, date, digest, json_exact, money, prepare_item, public
from store import Store


@pytest.fixture
def profile(tmp_path):
    return {"environment": "sandbox", "company_id": "30306294000145", "account_id": "account-1", "account_number": "000050", "branch_code": "50", "workspace": str(tmp_path / "work"), "vault_profile": "vault", "vault_entry_path": "BTG/test", "client_id": "app", "redirect_uri": "http://127.0.0.1:8765/callback", "scopes": "openid brn:btg:empresas:banking:accounts brn:btg:empresas:banking:payments", "max_amount": "1000.00", "timeout_seconds": 1, "max_pages": 2, "pending_interval": 300, "scheduled_interval": 3600, "confirmed_interval": 86400, "confirmation_days": 7, "_config": str(tmp_path / "code" / "configs" / "btg-empresas.toml"), "_profile": "test"}


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
    receipt = restarted.dispatch({"version": 1, "operation": "payments.receipt", "id": row["id"]})
    assert Path(receipt["path"]).read_bytes().startswith(b"%PDF-")
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
        engine.dispatch({"version": 1, "operation": "payments.cancel", "id": row["id"]})
    result = engine.dispatch({"version": 1, "operation": "payments.cancel", "id": row["id"], "preparationHash": row["preparationHash"], "confirm": True})
    assert result["bankStatus"] == "CANCELED" and result["cancellationState"] == "confirmed"


def test_ambiguous_and_incomplete_listing_never_bind_payment(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    engine.submit(row["id"], row["preparationHash"])
    bank.call = lambda *a, **kw: {"data": [bank.payment(), bank.payment()], "_links": {}}
    assert engine.refresh(row["id"])["lastError"] == "payment_unresolved"
    bank.call = lambda *a, **kw: {"data": [bank.payment()], "_links": {"next": {"href": "untrusted"}}}
    assert engine.refresh(row["id"])["lastError"] == "incomplete_listing"
    assert engine.store.get(row["id"])["bankPaymentId"] is None


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


def test_oauth_state_tokens_and_operator_rotation(profile):
    client = Client(profile)
    saved = {}
    client.auth_data = lambda: copy.deepcopy(saved)
    client.save_auth = lambda value: (saved.clear(), saved.update(copy.deepcopy(value)))
    start = client.auth_start()
    assert "prompt=consent" in start["authorizationUrl"]
    assert "resource=30306294000145" in start["authorizationUrl"]
    assert "code_challenge" not in start["authorizationUrl"]
    assert "verifier" not in saved["pending"]
    with pytest.raises(BtgError):
        client.auth_complete("code", "wrong-state")
    state = saved["pending"]["state"]
    client.token_request = lambda *a, **kw: {"binding": client.binding(), "expiresAt": time.time() + 1000, "access_token": "secret", "refresh_token": "refresh", "operatorId": "operator"}
    assert client.auth_complete("code", state)["authenticated"]
    assert "pending" not in saved and client.token()["operatorId"] == "operator"
    saved["tokens"]["binding"] = "production-other"
    with pytest.raises(BtgError):
        client.token()


def test_closed_client_routes_block_redirects_and_arbitrary_queries(profile):
    client = Client(profile)
    client.token = lambda: {"access_token": "secret", "operatorId": "operator"}
    client.http = Mock(return_value=(200, {"data": []}))
    client.call("payments.remote-list", params={"pageNumber": 1, "pageSize": 100})
    url = client.http.call_args.args[0]
    assert url.startswith("https://api.sandbox.empresas.btgpactual.com/")
    with pytest.raises(BtgError):
        client.call("unknown")
    with pytest.raises(BtgError):
        client.call("balances.get", params={"url": "evil"})
    with pytest.raises(BtgError):
        client.call("payments.remote-list", params={"pageNumber": 0, "pageSize": 100})
    with pytest.raises(BtgError):
        client.call("payments.create", expected_operator="other")
    with pytest.raises(BtgError):
        NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://evil")


def test_config_requires_external_workspace_and_cli_errors_are_safe(profile, tmp_path):
    config = Path(profile["_config"])
    config.parent.mkdir(parents=True)
    Path(profile["workspace"]).mkdir()
    (config.parent / "onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(profile["workspace"]), encoding="utf-8")
    def write(value):
        values = {k: v for k, v in value.items() if not k.startswith("_")}
        config.write_text('[profiles.test]\n' + '\n'.join(k + '=' + json.dumps(v) for k, v in values.items()), encoding="utf-8")
    write(profile)
    assert load_profile(config, "test")["company_id"] == profile["company_id"]
    write(dict(profile, workspace=str(config.parent)))
    with pytest.raises(BtgError):
        load_profile(config, "test")
    code, result = execute(config, "test", {"version": 1, "operation": "payments.get"})
    assert code == 2 and "secret" not in json.dumps(result)


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


def test_actual_client_token_exchange_refresh_and_account_binding(profile):
    import base64
    client = Client(profile)
    client.vault = lambda *a, **kw: "client-secret"
    sub = base64.urlsafe_b64encode(json.dumps({"sub": "operator"}).encode()).decode().rstrip("=")
    client.http = Mock(return_value=(200, {"access_token": "access", "refresh_token": "refresh", "id_token": "header." + sub + ".signature", "expires_in": 86400}))
    token = client.token_request({"grant_type": "authorization_code", "code": "test-code"})
    assert token["operatorId"] == "operator" and token["binding"] == client.binding()
    assert client.http.call_args.args[0] == "https://id.sandbox.btgpactual.com/oauth2/token"
    with pytest.raises(BtgError):
        client.token_request({}, dict(token, operatorId="other"))
    client.http.return_value = (200, {"access_token": "access", "expires_in": 1000})
    assert client.token_request({}, token)["refresh_token"] == "refresh"
    client.http.return_value = (200, {})
    with pytest.raises(BtgError):
        client.token_request({})
    client.http.return_value = (200, {"access_token": "access", "refresh_token": "refresh", "id_token": "invalid", "expires_in": 100})
    with pytest.raises(BtgError):
        client.token_request({})
    saved = {"tokens": dict(token, expiresAt=0)}
    client.auth_data = lambda: copy.deepcopy(saved)
    client.save_auth = lambda v: saved.update(v)
    client.token_request = lambda *a, **kw: dict(token, expiresAt=time.time() + 1000)
    assert client.token()["expiresAt"] > time.time()
    client.call = lambda *a, **kw: {"data": [{"accountId": profile["account_id"], "taxId": profile["company_id"], "number": profile["account_number"], "branchCode": "50", "bankCode": 208}]}
    assert client.verify_account()["bankCode"] == 208
    client.call = lambda *a, **kw: {"data": []}
    with pytest.raises(BtgError):
        client.verify_account()


def test_http_handles_pdf_json_errors_timeouts_and_size(profile):
    import io
    import urllib.error
    from contextlib import contextmanager
    client = Client(profile)
    @contextmanager
    def response(raw, status=200):
        stream = io.BytesIO(raw)
        stream.status = status
        yield stream
    client.opener.open = lambda *a, **kw: response(b'{"amount":0.01}')
    assert client.http(client.base, "GET", None, {})[1]["amount"] == Decimal("0.01")
    client.opener.open = lambda *a, **kw: response(b'%PDF-test')
    assert client.http(client.base, "GET", None, {"Accept": "application/pdf"})[1].startswith(b"%PDF")
    client.opener.open = lambda *a, **kw: response(b'')
    assert client.http(client.base, "DELETE", None, {}) == (200, None)
    for raw in [b'not JSON', b'x' * (8 * 1024 * 1024 + 1)]:
        client.opener.open = lambda *a, **kw: response(raw)
        with pytest.raises(BtgError):
            client.http(client.base, "GET", None, {})
    for error in [urllib.error.HTTPError(client.base, 403, "secret-error", {}, None), urllib.error.URLError("secret-detail"), TimeoutError()]:
        client.opener.open = Mock(side_effect=error)
        with pytest.raises(BtgError) as caught:
            client.http(client.base, "GET", None, {})
        assert "secret" not in str(caught.value)
    provider_error = urllib.error.HTTPError(
        client.base,
        404,
        "secret-error",
        {},
        io.BytesIO(b'{"name":"TokenIssueError","message":"[A050305] secret detail"}'),
    )
    client.opener.open = Mock(side_effect=provider_error)
    with pytest.raises(BtgError) as caught:
        client.http(client.base, "GET", None, {})
    assert caught.value.code == "bank_http_error_404_tokenissueerror_a050305"
    assert "secret" not in str(caught.value)
    client.deadline = time.monotonic() - 1
    with pytest.raises(BtgError):
        client.http(client.base, "GET", None, {})


def test_vault_read_write_errors_and_auth_state_validation(profile, monkeypatch):
    from types import SimpleNamespace
    client = Client(profile)
    outputs = []
    def run(command, **kw):
        outputs.append(json.loads(kw["input"]))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "result": {"value": "value"}}))
    monkeypatch.setattr("client.subprocess.run", run)
    assert client.vault("read", "password") == "value"
    client.save_auth({"tokens": {}})
    assert outputs[-1]["operation"] == "edit" and "notes" in outputs[-1]["values"]
    client.vault = lambda *a, **kw: ''
    assert client.auth_data() == {}
    client.vault = lambda *a, **kw: 'not JSON'
    with pytest.raises(BtgError):
        client.auth_data()
    client.vault = lambda *a, **kw: '[]'
    with pytest.raises(BtgError):
        client.auth_data()
    client.vault = lambda *a, **kw: '{"tokens":{}}'
    assert client.auth_data() == {"tokens": {}}


def test_vault_retries_transient_keepass_edit(profile, monkeypatch):
    from types import SimpleNamespace
    client = Client(profile)
    calls = []
    responses = [
        {"ok": False, "error": {"code": "keepass_error", "message": "locked"}},
        {"ok": True, "result": {"edited": True}},
    ]
    def run(*args, **kwargs):
        calls.append(kwargs["input"])
        payload = responses.pop(0)
        return SimpleNamespace(returncode=0 if payload["ok"] else 2, stdout=json.dumps(payload))
    monkeypatch.setattr("client.subprocess.run", run)
    monkeypatch.setattr("client.time.sleep", lambda _: None)
    assert client.vault("edit", value="{}") == {"edited": True}
    assert len(calls) == 2


def test_dispatch_local_queries_and_authorization_are_closed(profile, body):
    engine, bank, row = prepared_engine(profile, body)
    for operation in ["payments.prepare", "payments.submit", "auth.start", "auth.complete"]:
        with pytest.raises(BtgError):
            engine.dispatch({"version": 1, "operation": operation})
    assert engine.dispatch({"version": 1, "operation": "payments.get", "id": row["id"]})["id"] == row["id"]
    assert engine.dispatch({"version": 1, "operation": "payments.list"})["items"][0]["id"] == row["id"]
    assert len(engine.dispatch({"version": 1, "operation": "payments.history", "id": row["id"]})) == 1
    assert engine.dispatch({"version": 1, "operation": "monitor.status"})["duePayments"] == 0
    assert engine.dispatch({"version": 1, "operation": "monitor.check"})["checked"] == []
    assert engine.dispatch({"version": 1, "operation": "accounts.get"})["accountId"] == profile["account_id"]
    engine.client.call = lambda *a, **kw: {"data": []}
    assert engine.dispatch({"version": 1, "operation": "statements.list", "params": {"startDate": "2026-10-01", "endDate": "2026-10-20", "type": "simple"}})["source"] == "bank"
    with pytest.raises(BtgError):
        engine.dispatch({"version": 1, "operation": "raw.sql", "id": row["id"]})
    with pytest.raises(BtgError):
        engine.dispatch({"version": 1, "operation": "payments.list", "url": "bad"})


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


def test_backup_restore_marks_old_preparations_uncertain(profile, body, tmp_path):
    from memory_maintenance import backup, restore
    engine, bank, row = prepared_engine(profile, body)
    engine.store.receipt(row["id"], b"%PDF-test")
    snapshot = backup(engine.store)
    target = restore(engine.store, snapshot, tmp_path / "restored-work", tmp_path / "code")
    restored_profile = dict(profile, workspace=str(tmp_path / "restored-work"))
    restored = Engine(restored_profile, Bank(restored_profile))
    value = restored.store.get(row["id"])
    assert value["submissionState"] == "unknown" and value["nextCheckAt"]
    assert (target / value["receiptPath"]).read_bytes().startswith(b"%PDF")
    assert restored.store.history(row["id"])[-1]["kind"] == "memory.restored"
    with pytest.raises(BtgError):
        restored.submit(row["id"], row["preparationHash"])
    with pytest.raises(BtgError):
        restore(engine.store, snapshot, tmp_path / "restored-work", tmp_path / "code")
    assert bank.calls == []


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
    row = engine.dispatch({"version": 1, "operation": "payments.prepare", "confirm": True, "body": body})
    assert engine.store.list({"id": row["id"], "submissionState": "prepared"})["items"][0]["id"] == row["id"]
    assert engine.dispatch({"version": 1, "operation": "payments.submit", "confirm": True, "id": row["id"], "preparationHash": row["preparationHash"]})["submissionState"] == "accepted"
    assert engine.dispatch({"version": 1, "operation": "payments.refresh", "id": row["id"]})["bankPaymentId"] == "bank-id"
    bank.auth_start = lambda: {"authorizationUrl": "https://id.sandbox.btgpactual.com"}
    bank.auth_complete = lambda *a: {"authenticated": True}
    assert engine.dispatch({"version": 1, "operation": "auth.start", "confirm": True})["authorizationUrl"]
    assert engine.dispatch({"version": 1, "operation": "auth.complete", "confirm": True, "code": "c", "state": "s"})["authenticated"]


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


def test_cli_stdin_contract_and_persistence_errors(profile, body, monkeypatch, capsys):
    import btg
    import io
    monkeypatch.setattr(btg, "load_profile", lambda *a: profile)
    engine = Engine(profile, Bank(profile))
    monkeypatch.setattr(btg, "Engine", lambda *a: engine)
    monkeypatch.setattr(sys, "argv", ["btg.py", "--config", profile["_config"], "--profile", "test"])
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"version":1,"operation":"payments.list"}'))
    assert btg.main() == 0 and json.loads(capsys.readouterr().out)["ok"]
    monkeypatch.setattr(sys, "stdin", io.StringIO('NaN'))
    assert btg.main() == 2 and not json.loads(capsys.readouterr().out)["ok"]
    engine.dispatch = Mock(side_effect=sqlite3.OperationalError("secret DB path"))
    assert btg.execute(Path(profile["_config"]), "test", {})[1]["error"]["code"] == "persistence_unavailable"
    engine.dispatch = Mock(side_effect=ValueError("secret payload"))
    assert btg.execute(Path(profile["_config"]), "test", {})[1]["error"]["code"] == "invalid_data"


def test_setup_validates_profile_without_writing_invalid_candidate(profile, tmp_path):
    config = Path(profile["_config"])
    config.parent.mkdir(parents=True)
    Path(profile["workspace"]).mkdir()
    (config.parent / "onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(profile["workspace"]), encoding="utf-8")
    import importlib.util
    spec = importlib.util.spec_from_file_location("btg_setup_test", Path(__file__).parents[1] / "setupSkill.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = Path(profile["_config"])
    values = {k: v for k, v in profile.items() if not k.startswith("_")}
    assert module.save(path, {"defaults": {}, "profiles": {"test": values}})[0]
    original = path.read_bytes()
    assert not module.save(path, {"defaults": {}, "profiles": {"test": dict(values, environment="invalid")}})[0]
    assert path.read_bytes() == original
