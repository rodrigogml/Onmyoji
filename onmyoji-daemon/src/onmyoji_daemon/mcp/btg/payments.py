"""Motor transacional BTG para pagamentos e acompanhamento via MCP."""
from __future__ import annotations

import datetime as dt
import json
import random
import time
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

from .client import Client, context
from .contracts import BtgError, bank_company_id, bank_date, digest, future, money, now, prepare_item, public, require, text
from .store import Store


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
    """Prepara e rastreia pagamentos; a reserva transacional antecede cada envio.

    Uma iniciação aceita não comprova execução. Resultados incertos somente
    podem ser recuperados por consultas; não há repetição automática de envio.
    """
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
                # Listagens podem conter pagamentos sem tags; eles não podem
                # ser associados com segurança ao registro local e devem ser
                # ignorados durante a recuperação.
                if not isinstance(tags, dict):
                    continue
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
        previous_deadline = self.client.deadline
        self.client.deadline = min(previous_deadline, time.monotonic() + 120) if previous_deadline is not None else time.monotonic() + 120
        try:
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
        finally:
            self.client.deadline = previous_deadline
        return {"checked": checked, "busy": busy, "hasMore": rows["hasMore"] or len(checked) + len(busy) < len(rows["items"]), "checkedAt": now()}

    def invoke(self, operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Aplica o contrato MCP sem permitir operação bancária ou SQL arbitrários."""
        require(isinstance(arguments, dict), "invalid_request", "Argumentos devem ser objeto.")
        if operation in {"prepare_payment", "submit_payment", "cancel_payment"}:
            require(arguments.get("confirm") is True, "confirmation_required", "Obtenha autorização explícita do usuário e informe confirm=true.")
        if operation == "prepare_payment":
            return self.prepare(arguments.get("body"))
        if operation == "list_payments":
            filters = arguments.get("filters", {})
            require(isinstance(filters, dict), "invalid_filter", "filters deve ser objeto.")
            result = self.store.list(filters)
            result["items"] = [payment_view(row) for row in result["items"]]
            return {**result, "source": "local-memory"}
        if operation == "check_pending_payments":
            return self.monitor_check()
        if operation == "get_monitor_status":
            result = self.store.list({"dueOnly": True, "limit": 500})
            return {"configuredEnabled": self.profile.get("monitor_enabled", False), "duePayments": len(result["items"]), "hasMore": result["hasMore"], "source": "local-memory", "serviceLiveness": "consult daemon status"}
        require(operation in {"get_payment", "get_payment_history", "submit_payment", "refresh_payment", "cancel_payment", "get_payment_receipt"}, "unsupported_operation", "Operação de pagamento não registrada.")
        identifier = text(arguments.get("payment_id"), "ID local", 100)
        if operation == "get_payment":
            return payment_view(self.store.get(identifier))
        if operation == "get_payment_history":
            rows = self.store.history(identifier)
            return {"source": "local-memory", "events": [{**row, "evidence": public(json.loads(row["evidence"], parse_float=Decimal))} for row in rows]}
        if operation in {"submit_payment", "cancel_payment"}:
            revision = text(arguments.get("preparation_hash"), "Hash da preparação autorizada", 64)
            return self.submit(identifier, revision) if operation == "submit_payment" else self.cancel(identifier, revision)
        if operation == "refresh_payment":
            return self.refresh(identifier)
        row = self.store.get(identifier)
        require(row["bankPaymentId"] is not None and row["bankStatus"] == "CONFIRMED", "receipt_unavailable", "Atualize e confirme o pagamento antes de obter comprovante.")
        content = self.client.call("payments.receipt", payment_id=row["bankPaymentId"])
        require(isinstance(content, bytes), "invalid_receipt", "Comprovante inválido.")
        return {"path": self.store.receipt(identifier, content), "source": "bank", "observedAt": now()}
