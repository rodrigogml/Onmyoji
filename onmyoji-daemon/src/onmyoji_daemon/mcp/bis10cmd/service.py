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


READ_OPERATIONS = {
    "ping", "get_session", "list_companies", "select_company", "list_accounts",
    "list_categories", "list_cost_centers", "get_account_statement", "list_account_statements",
}
WRITE_ACTIONS = {
    "create_account_statement": "create",
    "update_account_statement": "update",
    "create_transfer": "createTransfer",
    "update_transfer": "updateTransfer",
    "delete_account_statement": "delete",
}
OPERATIONS = READ_OPERATIONS | set(WRITE_ACTIONS)
COMPANY_OPERATIONS = {
    "select_company", "list_accounts", "list_categories", "list_cost_centers",
    "get_account_statement", "list_account_statements", *WRITE_ACTIONS,
}


class Bis10CmdError(ValueError):
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.data = data


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
            candidate = json.loads(sanitized)
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict) and candidate.get("version") == 1 and isinstance(candidate.get("resource"), str) and isinstance(candidate.get("items"), list):
            if candidate["resource"] == "accountStatements":
                validate_statement_page(candidate)
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


class Bis10CmdService:
    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve()
        self.path = self.root / "configs" / "mcp" / "bis10cmd.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        self.invoke(profile_name, "ping", {})
        return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in OPERATIONS:
            raise Bis10CmdError("unsupported_operation", "Operação BIS10CMD não suportada.")
        if operation in WRITE_ACTIONS and arguments.get("confirm") is not True:
            raise Bis10CmdError("confirmation_required", "Esta operação BIS10CMD exige confirm=true.")
        company_id = arguments.get("company_id")
        if operation in COMPANY_OPERATIONS and (not isinstance(company_id, int) or isinstance(company_id, bool) or company_id <= 0):
            raise Bis10CmdError("invalid_request", "company_id deve ser um inteiro positivo.")
        args = arguments.get("args", [])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise Bis10CmdError("invalid_request", "args deve ser uma lista de strings.")
        if operation in {"list_account_statements", *WRITE_ACTIONS} and not args:
            raise Bis10CmdError("invalid_request", "A operação BIS10CMD exige parâmetros em args.")
        self._validate_args(args)
        commands = self._commands(operation, company_id, args, arguments)
        profile, defaults = self._profile(profile_name)
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
                completed = subprocess.run(invocation, cwd=profile["working_dir"], env=environment, text=True, capture_output=True, check=False, timeout=defaults["timeout_seconds"], encoding=defaults["encoding"], errors="replace")
            except subprocess.TimeoutExpired as error:
                raise Bis10CmdError("timeout", "O BIS10CMD excedeu o tempo configurado.") from error
            except OSError as error:
                raise Bis10CmdError("execution_error", "Não foi possível iniciar o BIS10CMD.") from error
            data = parse_output(completed.stdout, completed.stderr, secrets)
        finally:
            secrets.clear()
        if completed.returncode:
            raise Bis10CmdError("bis10cmd_error", "O BIS10CMD retornou erro.", data)
        if operation in {"get_account_statement", "list_account_statements"}:
            pages = [item for item in data.get("lookups", []) if item["resource"] == "accountStatements"]
            if len(pages) != 1:
                raise Bis10CmdError("statement_query_unavailable", "O cliente não retornou a consulta estruturada de lançamentos. Verifique a versão do BIS10CMD.")
        return data

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
        elif operation == "list_categories": commands.append(("-categories", args))
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

    def _profile(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
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
        if not Path(profile["jar_path"]).is_file() or not working_dir.is_dir() or not (working_dir / "libs").is_dir():
            raise Bis10CmdError("runtime_not_found", "JAR, diretório de trabalho ou bibliotecas do BIS10CMD não encontrados.")
        return profile, defaults
