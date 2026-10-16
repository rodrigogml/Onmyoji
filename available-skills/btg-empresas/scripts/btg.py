#!/usr/bin/env python3
"""Wrapper JSON v1 BTG Empresas, com preparação persistida e monitor de leitura."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sqlite3
import sys
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from client import Client, context, load_profile
from contracts import BtgError, bank_company_id, bank_date, date, digest, future, money, now, prepare_item, public, require, text
from store import Store


READS = {"accounts.get", "balances.get", "statements.list", "dda.list", "barcodes.get", "payments.remote-list"}


def payment_view(row: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in row.items() if key not in {"context", "leaseOwner", "leaseUntil", "idempotencyKey", "operatorId", "cancellationKey"}}
    result["item"] = public(json.loads(row["item"], parse_float=Decimal))
    result["metadata"] = json.loads(row["metadata"])
    result["source"] = "local-memory"
    result["approvalLimitation"] = "A API consultada não fornece aprovador ou instante de cada assinatura."
    result["attentionReasons"] = []
    if row["lastError"]:
        result["attentionReasons"].append("check-or-submission-error")
    if row["submissionState"] in {"sending", "unknown"}:
        result["attentionReasons"].append("submission-unresolved")
    if row["submissionState"] == "accepted" and row["approvalState"] in {"pending", "unknown"} and row["bankStatus"] not in {"CANCELED", "FAILED", "REVERTED"}:
        result["attentionReasons"].append("approval-pending-or-not-verifiable")
    # Data civil atual de São Paulo (UTC-3), sem depender de tzdata no Windows.
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=-3))).date().isoformat()
    if row["requestedPaymentDate"] < today and row["bankStatus"] not in {"CONFIRMED", "CANCELED", "FAILED", "REVERTED"}:
        result["attentionReasons"].append("requested-payment-date-passed")
    return result


class Engine:
    def __init__(self, profile: dict[str, Any], client: Client | None = None):
        self.profile, self.client = profile, client or Client(profile)
        self._store = None

    @property
    def store(self) -> Store:
        require(bool(self.profile["account_id"]), "account_setup_required", "Conclua a conexão da conta pelo setup BTG antes de operar.")
        if self._store is None:
            self._store = Store(Path(self.profile["workspace"]), context(self.profile), self.profile["_profile"])
        return self._store

    def check_barcode(self, item: dict[str, Any]) -> dict[str, Any]:
        code = next(iter(item["detail"].values()))
        response = self.client.call("barcodes.get", params={"codes": code})
        require(isinstance(response, dict) and isinstance(response.get("data"), list), "invalid_bank_response", "Consulta de código incompleta.")
        rows = [row for row in response["data"] if isinstance(row, dict) and code in {row.get("digitableLine"), row.get("barcode")}]
        require(len(rows) == 1, "barcode_unresolved", "Código não identificado de forma única no BTG.")
        row = rows[0]
        require(money(row.get("amount")) == item["amount"], "amount_mismatch", "Valor consultado no BTG diverge do valor solicitado; prepare novamente.")
        payee = row.get("payee")
        require(isinstance(payee, dict) and isinstance(payee.get("taxId"), str) and (payee.get("socialName") or payee.get("fantasyName")), "beneficiary_unavailable", "Beneficiário não informado pelo BTG.")
        return {"name": payee.get("socialName") or payee.get("fantasyName"), "taxId": payee["taxId"], "dueDate": bank_date(row.get("dueDate"))}

    def prepare(self, body: dict[str, Any]) -> dict[str, Any]:
        identifier = str(uuid.uuid4())
        item, metadata = prepare_item(body, self.profile, identifier)
        if item["type"] in {"BANKSLIP", "UTILITIES"}:
            payee = self.check_barcode(item)
            if metadata.get("dueDate"):
                require(metadata["dueDate"] == payee["dueDate"], "due_date_mismatch", "Vencimento diverge da consulta BTG.")
            metadata.update(beneficiary=payee, dueDate=payee["dueDate"])
        else:
            metadata["beneficiary"] = item["detail"].get("creditParty") or item["detail"].get("taxPayer")
        return payment_view(self.store.prepare(identifier, item, metadata))

    def submit(self, identifier: str, revision: str) -> dict[str, Any]:
        before = self.store.get(identifier)
        require(before["submissionState"] == "prepared", "already_submitted", "Preparação já enviada; consulte antes de qualquer nova operação.")
        require(revision == before["preparationHash"], "preparation_changed", "Hash da preparação diverge da autorização.")
        item = json.loads(before["item"], parse_float=Decimal)
        require(digest({"context": self.store.context, "item": item, "metadata": json.loads(before["metadata"])}) == revision, "preparation_corrupted", "Conteúdo preparado diverge do hash; envio bloqueado.")
        require(item["amount"] <= money(self.profile.get("max_amount", "1000000.00")), "amount_limit", "Valor excede o limite atual do perfil.")
        self.client.verify_account()
        if item["type"] in {"BANKSLIP", "UTILITIES"}:
            beneficiary = self.check_barcode(item)
            require(beneficiary == json.loads(before["metadata"])["beneficiary"], "beneficiary_changed", "Beneficiário/vencimento mudou; prepare novamente.")
        operator = self.client.token()["operatorId"]
        row, attempt = self.store.reserve(identifier, revision, operator)
        try:
            result = self.client.call("payments.create", body={"items": [item]}, key=row["idempotencyKey"], expected_operator=operator)
            require(isinstance(result, dict) and type(result.get("operationNeedsApproval")) is bool and isinstance(result.get("batchId"), str) and bool(result["batchId"]), "invalid_bank_response", "Iniciação sem identificadores suficientes; envio precisa de recuperação.")
            evidence = {key: result[key] for key in ("operationNeedsApproval", "batchId", "contractGuid") if key in result}
            result_state = "accepted"
        except BtgError as error:
            result_state = "rejected" if error.status in {400, 401, 403, 404, 422} else "unknown"
            evidence = {"errorCode": error.code, "httpStatus": error.status}
        return payment_view(self.store.finish(identifier, attempt, result_state, evidence))

    def remote_match(self, row: dict[str, Any]) -> dict[str, Any]:
        if row["bankPaymentId"]:
            value = self.client.call("payments.remote-get", payment_id=row["bankPaymentId"])
            require(isinstance(value, dict) and value.get("paymentId") == row["bankPaymentId"], "invalid_bank_response", "ID bancário divergente.")
            self.validate_match(row, value)
            return value
        matches = []
        for page in range(1, self.profile["max_pages"] + 1):
            params = {"pageNumber": page, "pageSize": 100}
            if row["bankBatchId"]:
                params["batchId"] = row["bankBatchId"]
            result = self.client.call("payments.remote-list", params=params)
            require(isinstance(result, dict) and isinstance(result.get("data"), list), "invalid_bank_response", "Listagem bancária incompleta.")
            for value in result["data"]:
                require(isinstance(value, dict), "invalid_bank_response", "Item bancário inválido.")
                tags = value.get("tags")
                require(isinstance(tags, dict), "invalid_bank_response", "Item bancário sem referência estruturada.")
                if tags.get("externalId") == row["id"]:
                    self.validate_match(row, value)
                    matches.append(value)
            require(isinstance(result.get("_links"), dict), "incomplete_listing", "Listagem não informou continuidade.")
            if not result["_links"].get("next"):
                require(len(matches) == 1, "payment_unresolved", "Pagamento não encontrado ou ambíguo; não reenviar.")
                return matches[0]
        raise BtgError("incomplete_listing", "Limite de páginas atingido; não é seguro concluir ausência de pagamento.")

    def validate_match(self, row: dict[str, Any], value: dict[str, Any]) -> None:
        debit = value.get("debitParty")
        require(isinstance(debit, dict) and debit.get("taxId") == bank_company_id(self.profile) and debit.get("number") == self.profile["account_number"] and debit.get("branchCode") == self.profile["branch_code"], "context_mismatch", "Pagamento bancário pertence a outra empresa/conta.")
        require(money(value.get("amount")) * 100 == row["amountCents"], "amount_mismatch", "Valor bancário diverge do rastreamento.")
        item = json.loads(row["item"])
        require(value.get("type") == item["type"] or value.get("type") == "PIX" and item["type"].startswith("PIX_"), "type_mismatch", "Modalidade bancária diverge do rastreamento.")
        require(isinstance(value.get("paymentId"), str) and bool(value["paymentId"]) and isinstance(value.get("status"), str), "invalid_bank_response", "Resposta bancária sem ID ou status.")

    def refresh(self, identifier: str) -> dict[str, Any]:
        row, owner = self.store.claim(identifier)
        previous_deadline = self.client.deadline
        if previous_deadline is None:
            self.client.deadline = time.monotonic() + 90
        try:
            value = self.remote_match(row)
            status = value["status"]
            interval = self.profile["scheduled_interval"] if status == "SCHEDULED" else self.profile["pending_interval"]
            elapsed = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(row["submittedAt"])
            if elapsed > dt.timedelta(days=self.profile.get("max_pending_days", 30)):
                interval = max(interval, 86400)
            fields = {"bankPaymentId": value["paymentId"], "bankStatus": status, "submissionState": "accepted", "nextCheckAt": future(interval)}
            evidence = {"paymentId": value["paymentId"], "status": status, "amount": value["amount"], "paymentDate": value.get("paymentDate"), "approvalEvidence": "not-exposed"}
            if status == "CONFIRMED":
                fields.update(approvalState="evidenced", approvalObservedAt=row["approvalObservedAt"] or now())
                evidence["approvalEvidence"] = "execution-observed"
            if status == "CONFIRMED":
                stamp = row["confirmationObservedAt"] or now()
                fields.update(confirmationObservedAt=stamp, bankPaymentDate=bank_date(value.get("paymentDate")))
                age = dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(stamp)
                fields["nextCheckAt"] = future(self.profile["confirmed_interval"]) if age < dt.timedelta(days=self.profile["confirmation_days"]) else None
            elif status in {"CANCELED", "FAILED", "REVERTED"}:
                fields["nextCheckAt"] = None
            if status == "CANCELED" and row["cancellationState"]:
                fields["cancellationState"] = "confirmed"
            if status not in {"CONFIRMED", "PROCESSED", "SCHEDULED", "CANCELED", "FAILED", "REVERTED", "CREATED", "ADJOURNED", "RETAINED", "VALIDATED", "INVALIDATED"}:
                evidence["unknownStatus"] = True
            if row["approvalState"] == "pending" and status in {"CREATED", "VALIDATED", "SCHEDULED", "PROCESSED"}:
                fields["approvalState"] = "unknown"
                evidence["approvalEvidence"] = "pending-not-verifiable-by-query"
            updated = self.store.update_check(identifier, owner, fields, evidence)
        except BtgError as error:
            interval = min(3600, self.profile["pending_interval"] * 2 ** min(row["failures"], 4)) + random.randint(0, 30)
            updated = self.store.update_check(identifier, owner, {"nextCheckAt": future(interval)}, {"errorCode": error.code, "httpStatus": error.status}, error.code)
        finally:
            self.client.deadline = previous_deadline
        return payment_view(updated)

    def cancel(self, identifier: str, revision: str) -> dict[str, Any]:
        recent = self.refresh(identifier)
        require(recent["lastError"] is None, "cancel_state_unavailable", "Não foi possível verificar situação recente; cancelamento não enviado.")
        operator = self.client.token()["operatorId"]
        row, attempt = self.store.reserve(identifier, revision, operator, "cancel")
        op = "payments.cancel-scheduled" if row["bankStatus"] == "SCHEDULED" else "payments.cancel-pending"
        try:
            self.client.call(op, payment_id=row["bankPaymentId"], key=row["cancellationKey"], expected_operator=operator)
            result, evidence = "requested", {"bankPaymentId": row["bankPaymentId"]}
        except BtgError as error:
            result, evidence = ("rejected" if error.status in {400, 401, 403, 404, 422} else "unknown"), {"errorCode": error.code, "httpStatus": error.status}
        self.store.finish(identifier, attempt, result, evidence)
        return self.refresh(identifier)

    def monitor_check(self) -> dict[str, Any]:
        rows = self.store.list({"dueOnly": True, "limit": 100})
        checked, busy = [], []
        self.client.deadline = time.monotonic() + 120
        for row in rows["items"]:
            if time.monotonic() >= self.client.deadline:
                break
            try:
                value = self.refresh(row["id"])
                checked.append({"id": value["id"], "bankStatus": value["bankStatus"], "lastError": value["lastError"]})
            except BtgError as error:
                if error.code != "payment_busy":
                    raise
                busy.append(row["id"])
        self.client.deadline = None
        return {"checked": checked, "busy": busy, "hasMore": rows["hasMore"] or len(checked) + len(busy) < len(rows["items"]), "checkedAt": now()}

    def dispatch(self, request: dict[str, Any]) -> Any:
        require(isinstance(request, dict) and type(request.get("version")) is int and request["version"] == 1, "invalid_request", "Requisição JSON v1 obrigatória.")
        require(set(request) <= {"version", "operation", "body", "params", "id", "preparationHash", "confirm", "code", "state"}, "invalid_request", "Campo de requisição desconhecido.")
        operation = request.get("operation")
        require(isinstance(operation, str), "invalid_request", "Informe operation.")
        params = request.get("params", {})
        require(isinstance(params, dict), "invalid_request", "params deve ser objeto.")
        if operation in {"payments.prepare", "payments.submit", "payments.cancel", "auth.start", "auth.complete", "auth.cancel"}:
            require(request.get("confirm") is True, "confirmation_required", "Operação exige autorização explícita e confirm:true.")
        if operation == "auth.start":
            if self.profile.get("oauth_mode") == "remote":
                return self.client.remote_oauth("start", confirm=True)
            return self.client.auth_start()
        if operation == "auth.complete":
            require(self.profile.get("oauth_mode") != "remote", "automatic_callback_required", "No modo remoto, conclua pelo callback do Gateway.")
            return self.client.auth_complete(request.get("code"), request.get("state"))
        if operation == "auth.callback-info":
            return self.client.remote_oauth("info")
        if operation == "auth.status":
            return self.client.remote_oauth("status", session_id=text(params.get("session_id"), "Sessão OAuth", 100)) if params.get("session_id") else self.client.auth_status()
        if operation == "auth.cancel":
            return self.client.remote_oauth("cancel", session_id=text(params.get("session_id"), "Sessão OAuth", 100), confirm=True)
        require(bool(self.profile["account_id"]), "account_setup_required", "Conclua a conexão da conta pelo setup BTG antes de operar.")
        if operation in READS:
            if operation == "accounts.get":
                return self.client.verify_account()
            if operation == "statements.list":
                date(params.get("startDate")); date(params.get("endDate"))
                require(params["startDate"] <= params["endDate"], "invalid_query", "Intervalo de extrato invertido.")
            result = self.client.call(operation, params=params)
            return {"source": "bank", "observedAt": now(), "response": public(result), "pagination": "explicit; never infer completion without _links"}
        if operation == "payments.prepare":
            return self.prepare(request.get("body"))
        if operation == "payments.list":
            result = self.store.list(params)
            result["items"] = [payment_view(row) for row in result["items"]]
            return result
        if operation == "monitor.check":
            return self.monitor_check()
        if operation == "monitor.status":
            return {"configuredEnabled": self.profile.get("monitor_enabled", False), "duePayments": len(self.store.list({"dueOnly": True, "limit": 500})["items"]), "source": "local-memory", "serviceLiveness": "consult daemon status"}
        identifier = text(request.get("id"), "ID local", 100)
        if operation == "payments.get":
            return payment_view(self.store.get(identifier))
        if operation == "payments.history":
            return self.store.history(identifier)
        if operation == "payments.submit":
            return self.submit(identifier, request.get("preparationHash"))
        if operation == "payments.refresh":
            return self.refresh(identifier)
        if operation == "payments.cancel":
            return self.cancel(identifier, request.get("preparationHash"))
        if operation == "payments.receipt":
            row = self.store.get(identifier)
            require(row["bankPaymentId"] is not None and row["bankStatus"] == "CONFIRMED", "receipt_unavailable", "Atualize e confirme o pagamento antes de obter comprovante.")
            content = self.client.call("payments.receipt", payment_id=row["bankPaymentId"])
            require(isinstance(content, bytes), "invalid_receipt", "Comprovante inválido.")
            return {"path": self.store.receipt(identifier, content)}
        raise BtgError("unsupported_operation", "Operação não registrada.")


def execute(config: Path, profile_name: str, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    try:
        result = Engine(load_profile(config, profile_name)).dispatch(request)
        return 0, {"ok": True, "data": public(result)}
    except BtgError as error:
        return 2, {"ok": False, "error": {"code": error.code, "message": str(error), "httpStatus": error.status}}
    except (sqlite3.Error, OSError):
        return 2, {"ok": False, "error": {"code": "persistence_unavailable", "message": "Memória indisponível; não repita escritas bancárias sem recuperação."}}
    except (ValueError, TypeError, KeyError, AttributeError):
        return 2, {"ok": False, "error": {"code": "invalid_data", "message": "Dados ou resposta inválidos; consulte a memória antes de repetir um envio."}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raw = sys.stdin.read(1048577)
        require(len(raw) <= 1048576, "invalid_request", "Requisição excede o limite.")
        request = json.loads(raw, parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        code, payload = execute(args.config, args.profile, request)
    except (ValueError, BtgError):
        code, payload = 2, {"ok": False, "error": {"code": "invalid_request", "message": "Entrada JSON inválida."}}
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
