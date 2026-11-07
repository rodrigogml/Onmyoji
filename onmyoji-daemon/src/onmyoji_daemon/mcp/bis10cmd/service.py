"""Execução segura do cliente Java BISCMD para o BIS10."""
from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import subprocess
import tomllib
from typing import Any, Mapping

from ..credentials import CredentialError, KeePassCredentialResolver
from ..artifacts import ArtifactPathError, workspace
from .bills import BILL_OPERATIONS, canonical_identity, expected_resource, money, normalize_request, validate_page, validate_resource
from .exception import Bis10CmdError
from .requests import RequestStore, RequestStoreError, destination_id


READ_OPERATIONS = {
    "ping", "get_session", "list_companies", "select_company", "list_accounts",
    "list_categories", "list_cost_centers", "get_account_statement", "list_account_statements",
    *(BILL_OPERATIONS - {"create_bill_from_code"}),
}
LEGACY_WRITE_ACTIONS = {
    "create_account_statement": "create",
    "update_account_statement": "update",
    "create_transfer": "createTransfer",
    "update_transfer": "updateTransfer",
    "delete_account_statement": "delete",
}
WRITE_ACTIONS = {**LEGACY_WRITE_ACTIONS, "create_bill_from_code": "createFromCode"}
OPERATIONS = READ_OPERATIONS | set(WRITE_ACTIONS)
COMPANY_OPERATIONS = {
    "select_company", "list_accounts", "list_categories", "list_cost_centers",
    "get_account_statement", "list_account_statements", *WRITE_ACTIONS, *BILL_OPERATIONS,
}


def redact(value: str, secrets: list[str]) -> str:
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def validate_statement_page(page: dict[str, Any]) -> None:
    """Recusa páginas incompletas antes que sejam usadas em conciliações."""
    def integer(value: Any, minimum: int = 0) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= minimum

    def money(value: Any) -> bool:
        if not isinstance(value, str):
            return False
        try:
            amount = Decimal(value)
            return amount.is_finite() and amount >= 0
        except InvalidOperation:
            return False

    valid = (integer(page.get("offset")) and integer(page.get("limit"), 1)
             and page["limit"] <= 1000 and isinstance(page.get("hasMore"), bool)
             and isinstance(page.get("items"), list) and len(page["items"]) <= page["limit"])
    if not valid:
        raise Bis10CmdError("invalid_statement_response", "Paginação de lançamentos inválida ou ausente.")
    if page["hasMore"]:
        if (len(page["items"]) != page["limit"] or not integer(page.get("nextOffset"), 1)
                or page["nextOffset"] != page["offset"] + page["limit"]):
            raise Bis10CmdError("invalid_statement_response", "Continuação da consulta de lançamentos inválida.")
    elif "nextOffset" in page:
        raise Bis10CmdError("invalid_statement_response", "Página final não pode indicar continuação.")
    identifiers: set[int] = set()
    for item in page["items"]:
        if (not isinstance(item, dict) or not integer(item.get("id"), 1) or item["id"] in identifiers
                or not integer(item.get("accountId"), 1) or not money(item.get("value"))
                or item.get("operation") not in {"CREDIT", "DEBIT"}
                or item.get("type") not in {"MANUAL", "TRANSFER", "BILLS"}
                or not isinstance(item.get("categories"), list)):
            raise Bis10CmdError("invalid_statement_response", "Lançamento financeiro incompleto ou inválido.")
        identifiers.add(item["id"])
        try:
            if not isinstance(item.get("date"), str):
                raise ValueError()
            datetime.fromisoformat(item["date"])
        except ValueError as error:
            raise Bis10CmdError("invalid_statement_response", "Data do lançamento inválida.") from error
        for category in item["categories"]:
            if (not isinstance(category, dict) or not integer(category.get("id"), 1)
                    or not isinstance(category.get("name"), str) or not money(category.get("value"))):
                raise Bis10CmdError("invalid_statement_response", "Categoria do lançamento inválida.")
        counterpart = item.get("counterpart")
        if counterpart is not None and (not isinstance(counterpart, dict)
                or not integer(counterpart.get("id"), 1) or not integer(counterpart.get("accountId"), 1)
                or counterpart.get("operation") not in {"CREDIT", "DEBIT"} or not money(counterpart.get("value"))):
            raise Bis10CmdError("invalid_statement_response", "Contrapartida da transferência inválida.")


def parse_output(stdout_text: str, stderr_text: str, secrets: list[str]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    messages: list[str] = []
    lookups: list[dict[str, Any]] = []
    for line in stdout_text.splitlines():
        if not line.strip():
            continue
        sanitized = redact(line, secrets)
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict) and type(candidate.get("version")) is int and candidate["version"] == 1 and isinstance(candidate.get("resource"), str) and isinstance(candidate.get("items"), list):
            candidate = sanitize_json(candidate, secrets)
            if candidate["resource"] == "accountStatements":
                validate_statement_page(candidate)
            validate_resource(candidate)
            lookups.append(candidate)
        else:
            messages.append(sanitized)
    stderr = [redact(line, secrets) for line in stderr_text.splitlines() if line.strip()]
    if messages:
        data["messages"] = messages
    if lookups:
        data["lookups"] = lookups
    if stderr:
        data["stderr"] = stderr
    return data


def sanitize_json(value: Any, secrets: list[str]) -> Any:
    """Remove segredos de strings sem invalidar a sintaxe ou os números do JSON."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, dict):
        return {key: sanitize_json(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item, secrets) for item in value]
    return value


class Bis10CmdService:
    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "bis10cmd.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        self.invoke(profile_name, "ping", {})
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            raise Bis10CmdError("invalid_request", "Os parâmetros devem ser um objeto.")
        if operation not in OPERATIONS:
            raise Bis10CmdError("unsupported_operation", "Operação BIS10CMD não suportada.")
        if operation in WRITE_ACTIONS and arguments.get("confirm") is not True:
            raise Bis10CmdError("confirmation_required", "Esta operação BIS10CMD exige confirm=true.")
        company_id = arguments.get("company_id")
        if operation in COMPANY_OPERATIONS and (not isinstance(company_id, int) or isinstance(company_id, bool) or company_id <= 0):
            raise Bis10CmdError("invalid_request", "company_id deve ser um inteiro positivo.")
        if operation in BILL_OPERATIONS:
            try:
                return self._invoke_bill(profile_name, operation, arguments)
            except RequestStoreError as error:
                raise Bis10CmdError(error.code, str(error), error.data) from error
            except ArtifactPathError as error:
                raise Bis10CmdError("workspace_required", "Configure um workspace externo válido para o diário de contas.") from error
        args = arguments.get("args", [])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise Bis10CmdError("invalid_request", "args deve ser uma lista de strings.")
        if operation in {"list_account_statements", *LEGACY_WRITE_ACTIONS} and not args:
            raise Bis10CmdError("invalid_request", "A operação BIS10CMD exige parâmetros em args.")
        self._validate_args(args)
        commands = self._commands(operation, company_id, args, arguments)
        profile, defaults = self._profile(profile_name)
        data = self._execute(profile, defaults, commands)
        if operation in {"get_account_statement", "list_account_statements"}:
            pages = [item for item in data.get("lookups", []) if item["resource"] == "accountStatements"]
            if len(pages) != 1:
                raise Bis10CmdError("statement_query_unavailable", "O cliente não retornou a consulta estruturada de lançamentos. Verifique a versão do BIS10CMD.")
        return data

    def _execute(self, profile: Mapping[str, Any], defaults: Mapping[str, Any],
                 commands: list[tuple[str, list[str]]], *, payload: dict[str, Any] | None = None,
                 mutating: bool = False) -> dict[str, Any]:
        """Executa um processo; falhas após possível escrita preservam resultado incerto."""
        secrets = self._credentials(profile)
        environment = os.environ.copy()
        for key in ("BISCMD_HOST", "BISCMD_PORT", "BISCMD_LOCALE", "BISCMD_JNDI_USER", "BISCMD_JNDI_PASSWORD", "BISCMD_BIS_USER", "BISCMD_BIS_PASSWORD"):
            environment.pop(key, None)
        environment.update({
            "BISCMD_HOST": profile["host"], "BISCMD_PORT": str(profile["port"]), "BISCMD_LOCALE": profile["locale"],
            "BISCMD_JNDI_USER": secrets[0], "BISCMD_JNDI_PASSWORD": secrets[1],
            "BISCMD_BIS_USER": secrets[2], "BISCMD_BIS_PASSWORD": secrets[3],
        })
        invocation = [defaults["java_path"], "-Dfile.encoding=UTF-8", "-jar", profile["jar_path"], "-connect"]
        for name, command_args in commands:
            invocation.extend([name, *command_args])
        try:
            try:
                options: dict[str, Any] = {}
                if payload is not None:
                    options["input"] = json.dumps(payload, ensure_ascii=False, allow_nan=False)
                completed = subprocess.run(invocation, cwd=profile["working_dir"], env=environment, text=True, capture_output=True, check=False, timeout=defaults["timeout_seconds"], encoding="utf-8" if payload is not None else defaults["encoding"], errors="replace", **options)
            except subprocess.TimeoutExpired as error:
                raise Bis10CmdError("timeout", "O BIS10CMD excedeu o tempo configurado.", {"outcome": "unknown" if mutating else "not_submitted"}) from error
            except OSError as error:
                raise Bis10CmdError("execution_error", "Não foi possível iniciar o BIS10CMD.", {"outcome": "not_submitted"}) from error
            try:
                data = parse_output(completed.stdout, completed.stderr, secrets)
            except Bis10CmdError as error:
                if mutating:
                    raise Bis10CmdError(error.code, str(error), {"outcome": "unknown"}) from error
                raise
        finally:
            secrets.clear()
        errors = [page for page in data.get("lookups", []) if page["resource"] == "billError"]
        if errors:
            if len(errors) != 1 or mutating and any(page["resource"] == "bills" for page in data.get("lookups", [])):
                raise Bis10CmdError("invalid_bill_response", "O JAR retornou erros conflitantes.", {"outcome": "unknown" if mutating else "not_submitted"})
            error = errors[0]["items"][0]
            raise Bis10CmdError(error["code"], error["message"], {"outcome": error["outcome"], "details": error})
        if completed.returncode:
            if mutating:
                data["outcome"] = "unknown"
            raise Bis10CmdError("bis10cmd_error", "O BIS10CMD retornou erro.", data)
        return data

    def _bill_call(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], company_id: int,
                   action: str, payload: dict[str, Any] | None = None, *, bill_id: int | None = None) -> dict[str, Any]:
        command = "-people" if action == "people" else "-bill"
        if action == "people":
            command_args = ["list", "stdin"]
        elif action == "get":
            command_args = ["get", "id", str(bill_id)]
        elif action == "capabilities":
            command_args = ["capabilities"]
        else:
            command_args = [action, "stdin"]
        if action == "createFromCode":
            command_args.append("confirm")
        return self._execute(profile, defaults, [("-company", ["id", str(company_id)]), (command, command_args)],
                             payload=payload, mutating=action == "createFromCode")

    def _request_store(self) -> RequestStore:
        work = workspace(self.root)
        if work.is_relative_to(self.root) or self.root.is_relative_to(work):
            raise ArtifactPathError("O workspace do diário deve ser externo ao Onmyōji.")
        return RequestStore(work / ".onmyoji" / "bis10cmd" / "requests.sqlite3")

    def _invoke_bill(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        payload = normalize_request(operation, arguments)
        profile, defaults = self._profile(profile_name, require_runtime=operation not in {"create_bill_from_code", "get_bill_request"})
        company_id = arguments["company_id"]
        if operation in {"create_bill_from_code", "get_bill_request"}:
            store = self._request_store()
            destination = destination_id(profile["host"], profile["port"])
            request_id = arguments["request_id"]
            previous = store.get(destination, company_id, request_id)
            if operation == "get_bill_request":
                if previous is None:
                    raise Bis10CmdError("request_not_found", "Tentativa local não encontrada.")
                return self._request_status(profile, defaults, company_id, previous)
            input_code = payload["code"]
            payload["code"] = canonical_identity(input_code)
            if previous is not None:
                record, _ = store.begin(destination, company_id, request_id, payload, payload["code"])
                if record["state"] == "SUCCEEDED":
                    self._validate_known_result(record)
                    return {**record["result"], "request": self._public_request(record), "replayed": True}
                if record["state"] == "REJECTED":
                    error = record["result"] or {}
                    raise Bis10CmdError(error.get("code", "request_rejected"), error.get("message", "Tentativa rejeitada anteriormente."), {"request": self._public_request(record), "previous_error": error})
                raise Bis10CmdError("outcome_unknown", "A tentativa anterior pode ter sido gravada; consulte get_bill_request.", self._request_status(profile, defaults, company_id, record))
        if operation == "create_bill_from_code":
            self._profile(profile_name)
            # O preflight é somente leitura e impede iniciar uma escrita com JAR antigo.
            try:
                capabilities = self._bill_call(profile, defaults, company_id, "capabilities")
                expected_resource(capabilities, "billCapabilities")
            except Bis10CmdError as error:
                raise Bis10CmdError("bill_client_incompatible", "Não foi possível confirmar o contrato de contas do JAR antes da escrita.", {"cause": error.as_dict(), "outcome": "not_submitted"}) from error
            effective = dict(payload)
            effective["code"] = input_code
            if "entry_date" not in effective:
                effective["entry_date"] = previous["payload"]["entry_date"] if previous is not None else datetime.now().isoformat(timespec="seconds")
            analysis_data = self._bill_call(profile, defaults, company_id, "analyzeCode", effective)
            analysis = expected_resource(analysis_data, "billCodeAnalysis")["items"][0]
            if not analysis["code_valid"]:
                raise Bis10CmdError("invalid_bill_code", "O código informado não é válido.", {"analysis": analysis, "outcome": "not_submitted"})
            if payload["code"] != analysis["barcode"]:
                raise Bis10CmdError("invalid_bill_response", "O código canônico retornado difere do informado.")
            effective["code"] = analysis["barcode"]
            if previous is None and not analysis["ready_to_create"]:
                raise Bis10CmdError("bill_not_ready", "Complete as pendências antes de cadastrar a conta.", {"analysis": analysis, "outcome": "not_submitted"})
            for field in ("original_value", "due_date"):
                if field not in effective and analysis.get(field) is not None:
                    effective[field] = analysis[field]
            record, is_new = store.begin(destination, company_id, request_id, payload, analysis["barcode"], effective_payload=effective)
            if not is_new:
                if record["state"] == "SUCCEEDED":
                    self._validate_known_result(record)
                    return {**record["result"], "request": self._public_request(record), "replayed": True}
                if record["state"] == "REJECTED":
                    error = record["result"] or {}
                    raise Bis10CmdError(error.get("code", "request_rejected"), error.get("message", "Tentativa rejeitada anteriormente."), {"request": self._public_request(record), "previous_error": error})
                raise Bis10CmdError("outcome_unknown", "A tentativa anterior pode ter sido gravada; consulte get_bill_request.", self._request_status(profile, defaults, company_id, record))
            try:
                data = self._bill_call(profile, defaults, company_id, "createFromCode", record["payload"])
                page = expected_resource(data, "bills")
                self._validate_created(page, company_id, record["payload"])
                bill = page["items"][0]
                for field, actual in (("bill_type", bill["bill_type"]), ("bill_payment_type", bill["payments"][0]["payment_type"])):
                    if field in analysis and analysis[field] != actual:
                        raise Bis10CmdError("invalid_bill_response", "O tipo cadastrado difere da análise do código.")
            except Bis10CmdError as error:
                evidence = error.as_dict()
                if (error.data or {}).get("outcome") in {"not_submitted", "rejected"} or error.code.startswith("credential_"):
                    store.rejected(destination, company_id, request_id, evidence)
                    raise
                store.unknown(destination, company_id, request_id, evidence)
                raise Bis10CmdError("outcome_unknown", "Não foi possível confirmar o cadastro; consulte get_bill_request antes de tentar novamente.", {"request_id": request_id, "cause": evidence}) from error
            except BaseException:
                store.unknown(destination, company_id, request_id, {"code": "interrupted", "message": "Execução interrompida após reservar a tentativa."})
                raise
            try:
                record = store.success(destination, company_id, request_id, data)
            except RequestStoreError as error:
                raise Bis10CmdError("outcome_unknown", "O BIS10 retornou o cadastro, mas o diário local não confirmou a gravação do resultado.", {"request_id": request_id, "confirmed_result": data}) from error
            return {**data, "request": self._public_request(record), "replayed": False}
        action = {"analyze_bill_code": "analyzeCode", "get_bill": "get", "list_bills": "list", "list_people": "people"}[operation]
        data = self._bill_call(profile, defaults, company_id, action,
                               None if operation == "get_bill" else payload, bill_id=payload.get("bill_id"))
        resource = "billCodeAnalysis" if operation == "analyze_bill_code" else "people" if operation == "list_people" else "bills"
        page = expected_resource(data, resource)
        if resource == "bills":
            if operation == "list_bills":
                validate_page(page)
            if any(item["company_id"] != company_id for item in page["items"]):
                raise Bis10CmdError("invalid_bill_response", "Consulta retornou empresa diferente da solicitada.")
            if operation == "get_bill" and (len(page["items"]) != 1 or page["items"][0]["id"] != payload["bill_id"]):
                raise Bis10CmdError("bill_not_found", "Conta solicitada não encontrada.")
        return data

    @staticmethod
    def _validate_created(page: dict[str, Any], company_id: int, payload: dict[str, Any]) -> None:
        if len(page["items"]) != 1:
            raise Bis10CmdError("invalid_bill_response", "A inclusão não retornou exatamente uma conta.")
        bill = page["items"][0]
        if (bill["company_id"] != company_id or bill["operation"] != "DEBIT" or bill["state"] != "WAITING"
                or len(bill["payments"]) != 1 or bill.get("pay_date") is not None):
            raise Bis10CmdError("invalid_bill_response", "A inclusão não confirmou uma conta pendente na empresa selecionada.")
        payment = bill["payments"][0]
        numeric_code = payment.get("numeric_code")
        if (payment.get("barcode") != payload["code"] or not isinstance(numeric_code, str)
                or not numeric_code.isascii() or not numeric_code.isdigit() or len(numeric_code) not in {47, 48}
                or payment["payment_state"] != "WAITING" or payment.get("pay_date") is not None):
            raise Bis10CmdError("invalid_bill_response", "A parcela retornada não corresponde ao código e estado solicitados.")
        for field in ("due_date", "original_value", "recipient_person_id"):
            if field in payload and bill.get(field) != payload[field]:
                raise Bis10CmdError("invalid_bill_response", "O resultado difere dos complementos solicitados.", {"field": field})
        if "payee_person_id" in payload and payment.get("payee_person_id") != payload["payee_person_id"]:
            raise Bis10CmdError("invalid_bill_response", "Beneficiário retornado difere do solicitado.")
        if (payment["due_date"] != bill["due_date"] or Decimal(payment["original_value"]) != Decimal(bill["original_value"])
                or Decimal(payment["final_value"]) != Decimal(bill["final_value"])
                or canonical_identity(numeric_code) != payload["code"]):
            raise Bis10CmdError("invalid_bill_response", "Dados da parcela não correspondem ao agregado ou ao código.")
        final_value = Decimal(payment["original_value"])
        for field in ("fine_value", "discount_value", "other_value", "add_value", "fees_value"):
            payment_value = payment.get(field) or "0.00"
            bill_value = bill.get(field) or "0.00"
            if not money(payment_value) or not money(bill_value):
                raise Bis10CmdError("invalid_bill_response", "Componente monetário inválido na resposta.", {"field": field})
            amount = Decimal(payment_value)
            if amount != Decimal(bill_value) or amount != Decimal(payload.get(field, "0.00")):
                raise Bis10CmdError("invalid_bill_response", "Componente monetário difere da solicitação.", {"field": field})
            final_value += -amount if field == "discount_value" else amount
        if final_value != Decimal(payment["final_value"]):
            raise Bis10CmdError("invalid_bill_response", "Valor final da parcela não corresponde aos componentes monetários.")
        categories = payment["categories"]
        total = Decimal("0.00")
        for category in categories:
            value = Decimal(category["value"])
            total += value if category["operation"] == "EXPENSE" else -value
            centers = category["cost_centers"]
            if (not centers or len({center["cost_center_id"] for center in centers}) != len(centers)
                    or any(Decimal(center["ratio"]) <= 0 or center.get("enabled") is False for center in centers)
                    or sum(Decimal(center["ratio"]) for center in centers) != 100):
                raise Bis10CmdError("invalid_bill_response", "O rateio retornado não compõe 100% com centros válidos.")
        if not categories or total != final_value:
            raise Bis10CmdError("invalid_bill_response", "A classificação retornada não corresponde ao total da parcela.")
        if "categories" in payload:
            def signature(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
                return sorted((item["category_id"], Decimal(item["value"]),
                               tuple(sorted((center["cost_center_id"], Decimal(center["ratio"])) for center in item["cost_centers"]))) for item in items)
            if signature(categories) != signature(payload["categories"]):
                raise Bis10CmdError("invalid_bill_response", "A classificação retornada difere das categorias e rateios enviados.")

    @staticmethod
    def _public_request(record: dict[str, Any]) -> dict[str, Any]:
        return {key: record[key] for key in ("request_id", "company_id", "canonical_code", "state", "created_at", "updated_at")}

    @classmethod
    def _validate_known_result(cls, record: dict[str, Any]) -> None:
        if not isinstance(record.get("result"), dict):
            raise Bis10CmdError("invalid_bill_response", "Resultado local confirmado está incompleto; não reenvie a escrita.")
        page = expected_resource(record["result"], "bills")
        validate_resource(page)
        cls._validate_created(page, record["company_id"], record["payload"])

    def _request_status(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], company_id: int,
                        record: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"request": self._public_request(record), "result": record["result"]}
        if record["state"] == "SUCCEEDED":
            self._validate_known_result(record)
        if record["state"] in {"SENDING", "UNKNOWN"}:
            try:
                data = self._bill_call(profile, defaults, company_id, "list", {"code": record["canonical_code"], "limit": 1000})
                page = expected_resource(data, "bills")
                validate_page(page)
                if any(item["company_id"] != company_id for item in page["items"]):
                    raise Bis10CmdError("invalid_bill_response", "Reconciliação retornou outra empresa.")
                result["reconciliation"] = {"candidates": page["items"], "has_more": page["has_more"], "attribution": "unconfirmed", "automatic_resend": False}
            except Bis10CmdError as error:
                result["reconciliation_error"] = error.as_dict()
        return result

    @staticmethod
    def _validate_args(args: list[str]) -> None:
        for value in args:
            normalized = value.casefold()
            if normalized == "confirm" or normalized.startswith("-") or "biscmd_" in normalized or normalized.startswith("biscmd."):
                raise Bis10CmdError("invalid_request", "Confirmação, outros comandos, propriedades Java, variáveis BISCMD e credenciais não podem ser informados em args.")

    @staticmethod
    def _commands(operation: str, company_id: int | None, args: list[str], arguments: Mapping[str, Any]) -> list[tuple[str, list[str]]]:
        commands: list[tuple[str, list[str]]] = []
        if operation in COMPANY_OPERATIONS:
            commands.append(("-company", ["id", str(company_id)]))
        if operation == "ping": commands.append(("-ping", []))
        elif operation == "get_session": commands.append(("-session", []))
        elif operation == "list_companies": commands.append(("-companies", []))
        elif operation == "select_company": pass
        elif operation == "list_accounts": commands.append(("-accounts", args))
        elif operation == "list_categories":
            category_args = list(args)
            for field, native in (("bill_type", "billType"), ("bill_payment_type", "billPaymentType")):
                if field in arguments:
                    value = arguments[field]
                    if not isinstance(value, str) or not value or value.startswith("-"):
                        raise Bis10CmdError("invalid_request", "Tipo de conta ou parcela inválido.")
                    if any(item.casefold() == native.casefold() for item in args[::2]):
                        raise Bis10CmdError("invalid_request", "Filtro de tipo repetido em args e no contrato tipado.")
                    category_args.extend([native, value])
            commands.append(("-categories", category_args))
        elif operation == "list_cost_centers": commands.append(("-costCenters", args))
        elif operation == "get_account_statement":
            statement_id = arguments.get("statement_id")
            if not isinstance(statement_id, int) or isinstance(statement_id, bool) or statement_id <= 0:
                raise Bis10CmdError("invalid_request", "statement_id deve ser um inteiro positivo.")
            commands.append(("-accountStatement", ["get", "id", str(statement_id)]))
        elif operation == "list_account_statements": commands.append(("-accountStatement", ["list", *args]))
        else: commands.append(("-accountStatement", [WRITE_ACTIONS[operation], *args, "confirm"]))
        return commands

    def _credentials(self, profile: Mapping[str, Any]) -> list[str]:
        try:
            return [
                self.credentials.resolve(profile["jndi_credential_profile"], profile["jndi_credential_entry_path"], profile["jndi_username_field"]),
                self.credentials.resolve(profile["jndi_credential_profile"], profile["jndi_credential_entry_path"], profile["jndi_password_field"]),
                self.credentials.resolve(profile["bis_credential_profile"], profile["bis_credential_entry_path"], profile["bis_username_field"]),
                self.credentials.resolve(profile["bis_credential_profile"], profile["bis_credential_entry_path"], profile["bis_password_field"]),
            ]
        except CredentialError as error:
            raise Bis10CmdError("credential_" + error.code, "Não foi possível obter as credenciais BIS10CMD.") from error

    def _profile(self, name: str, *, require_runtime: bool = True) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise Bis10CmdError("config_not_found", "Configuração BIS10CMD MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise Bis10CmdError("invalid_config", "Configuração BIS10CMD MCP inválida.") from error
        profile, defaults = document.get("profiles", {}).get(name), document.get("defaults", {})
        if document.get("schema_version") != 1 or not isinstance(profile, dict) or not isinstance(defaults, dict):
            raise Bis10CmdError("profile_not_found", "Perfil BIS10CMD não encontrado.")
        if profile.get("enabled") is not True:
            raise Bis10CmdError("profile_disabled", "Perfil BIS10CMD desabilitado.")
        working_dir = Path(profile["working_dir"])
        if require_runtime and (not Path(profile["jar_path"]).is_file() or not working_dir.is_dir() or not (working_dir / "libs").is_dir()):
            raise Bis10CmdError("runtime_not_found", "JAR, diretório de trabalho ou bibliotecas do BIS10CMD não encontrados.")
        return profile, defaults
