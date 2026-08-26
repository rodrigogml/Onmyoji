#!/usr/bin/env python3
"""Secure JSON wrapper for configured MySQL profiles."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import tomllib


class MySQLError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_config(path: str, profile_name: str) -> dict[str, dict[str, Any]]:
    try: document = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc: raise MySQLError("config_not_found", "Perfil TOML não encontrado.") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc: raise MySQLError("invalid_config", "Não foi possível ler o perfil TOML.") from exc
    defaults, profile = document.get("defaults", {}), document.get("profiles", {}).get(profile_name)
    if not isinstance(defaults, dict) or not isinstance(profile, dict): raise MySQLError("invalid_config", "Perfil solicitado não encontrado.")
    values = {**defaults, **profile}
    mysql = {key: values.get(key, "") for key in ("executable", "host", "port", "socket", "database", "user")}
    auth = {key: values.get(key, "") for key in ("vault_profile", "vault_entry_path", "username_field", "password_field")}
    execution = {key: values.get(key, "") for key in ("timeout_seconds", "allow_client_commands")}
    if not str(mysql["executable"]).strip() or not str(mysql["host"]).strip() or not str(auth["vault_profile"]).strip() or not str(auth["vault_entry_path"]).strip():
        raise MySQLError("invalid_config", "Perfil MySQL ou KeePass incompleto.")
    if not str(mysql["user"]).strip() and not str(auth["username_field"]).strip():
        raise MySQLError("invalid_config", "Informe mysql.user ou auth.username_field.")
    return {"mysql": mysql, "auth": auth, "execution": execution}


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _safe_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_safe_json(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def get_secret(config: dict[str, dict[str, Any]], field: str) -> str:
    request = {"operation": "read", "path": config["auth"]["vault_entry_path"], "field": field, "auth": {"mode": "configured"}}
    vault_script = Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"
    vault_config = Path(__file__).resolve().parents[3] / "configs" / "keepass.toml"
    try:
        result = subprocess.run([sys.executable, str(vault_script), "--config", str(vault_config), "--profile", str(config["auth"]["vault_profile"])],
                                input=json.dumps(request, ensure_ascii=True), text=True,
                                capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MySQLError("credential_provider_error", "Não foi possível consultar o provedor de credenciais.") from exc
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MySQLError("credential_provider_error", "Resposta inválida do provedor de credenciais.") from exc
    if result.returncode or not payload.get("ok"):
        raise MySQLError("credential_provider_error", "O provedor de credenciais recusou a operação.")
    value = payload.get("result", {}).get("value")
    if not isinstance(value, str) or not value:
        raise MySQLError("credential_provider_error", "O provedor não retornou uma senha válida.")
    return value


def get_credentials(config: dict[str, dict[str, Any]]) -> tuple[str, str]:
    username_field = str(config["auth"].get("username_field", "")).strip()
    username = str(config["mysql"].get("user", "")).strip()
    if username_field:
        username = get_secret(config, username_field)
    if not username:
        raise MySQLError("invalid_config", "Informe mysql.user ou auth.username_field.")
    return username, get_secret(config, str(config["auth"].get("password_field", "password")))


def base_command(config: dict[str, dict[str, Any]], username: str, password: str) -> tuple[list[str], dict[str, str]]:
    section = config["mysql"]
    command = [section["executable"], "--batch", "--raw", "-h", section["host"],
               "-u", username]
    if str(section.get("port", "")).strip():
        command += ["-P", str(section["port"])]
    if str(section.get("socket", "")).strip():
        command += ["-S", section["socket"]]
    if str(section.get("database", "")).strip():
        command.append(section["database"])
    env = os.environ.copy()
    env["MYSQL_PWD"] = password
    return command, env


def run(config: dict[str, dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
    operation = request.get("operation")
    if operation not in {"query", "execute", "script", "client", "ping"}:
        raise MySQLError("unsupported_operation", "Operação MySQL não suportada.")
    username, password = get_credentials(config)
    command, env = base_command(config, username, password)
    if operation in {"query", "execute"}:
        sql = request.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise MySQLError("invalid_request", "sql é obrigatório.")
        command += ["-e", sql]
    elif operation == "script":
        script = request.get("path")
        if not isinstance(script, str) or not Path(script).is_file():
            raise MySQLError("invalid_request", "path deve apontar para um arquivo SQL existente.")
        command += ["--batch"]
        input_data = Path(script).read_text(encoding="utf-8")
    elif operation == "client":
        if config["execution"].get("allow_client_commands") is not True:
            raise MySQLError("client_commands_disabled", "Comandos do cliente estão desabilitados no perfil.")
        args = request.get("args", [])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
            raise MySQLError("invalid_request", "args deve ser uma lista de strings.")
        command += args
    else:
        command += ["-e", "SELECT 1"]
    try:
        completed = subprocess.run(command, input=locals().get("input_data"), text=True,
                                   capture_output=True, env=env, check=False,
                                   timeout=int(config["execution"].get("timeout_seconds", 120)))
    except subprocess.TimeoutExpired as exc:
        raise MySQLError("timeout", "O comando MySQL excedeu o tempo configurado.") from exc
    except OSError as exc:
        raise MySQLError("executable_error", "Não foi possível iniciar o cliente MySQL.") from exc
    if completed.returncode:
        raise MySQLError("mysql_error", "O cliente MySQL retornou erro.")
    output = completed.stdout or ""
    if operation == "query":
        lines = [line.split("\t") for line in output.splitlines() if line]
        rows = []
        columns = lines[0] if lines else []
        for values in lines[1:]:
            rows.append(dict(zip(columns, values)))
        return {"columns": columns, "rows": rows, "row_count": len(rows)}
    return {"stdout": output, "stderr": completed.stderr or ""}


def main() -> int:
    try:
        if len(sys.argv) != 5 or sys.argv[1] != "--config" or sys.argv[3] != "--profile":
            raise MySQLError("usage", "Uso: mysql.py --config PERFIL.toml --profile NOME")
        config = load_config(sys.argv[2], sys.argv[4])
        request = json.load(sys.stdin)
        if request.get("version") != 1:
            raise MySQLError("unsupported_version", "A versão do protocolo deve ser 1.")
        data = run(config, request)
        response = {"version": 1, "ok": True, "operation": request.get("operation"), "data": _safe_json(data)}
    except MySQLError as exc:
        response = {"version": 1, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (json.JSONDecodeError, TypeError):
        response = {"version": 1, "ok": False, "error": {"code": "invalid_json", "message": "Entrada JSON inválida."}}
    print(json.dumps(response, ensure_ascii=True))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
