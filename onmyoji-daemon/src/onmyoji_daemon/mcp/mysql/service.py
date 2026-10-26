"""Cliente MySQL nativo com credenciais resolvidas pelo vault MCP."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tomllib
from typing import Any, Mapping

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS = {"query", "execute", "script", "client", "ping"}
WRITE_OPERATIONS = {"execute", "script", "client"}


class MySQLError(ValueError):
    def __init__(self, code: str, message: str): super().__init__(message); self.code = code


class MySQLService:
    """Executa o cliente configurado sem colocar usuário ou senha em argv."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve(); self.path = self.root / "configs" / "mcp" / "mysql.toml"; self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        self.invoke(profile_name, "ping", {}); return {"profile": profile_name, "authenticated": True}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in OPERATIONS: raise MySQLError("unsupported_operation", "Operação MySQL não suportada.")
        if operation in WRITE_OPERATIONS and arguments.get("confirm") is not True: raise MySQLError("confirmation_required", "Operações amplas ou de escrita exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        try:
            username = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["username_field"])
            password = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["password_field"])
        except CredentialError as error: raise MySQLError("credential_" + error.code, "Não foi possível obter as credenciais MySQL.") from error
        command = [defaults["executable"], "--batch", "--raw", "-h", profile["host"], "-u", username, "-P", str(defaults["port"]), f"--connect-timeout={defaults['connect_timeout']}"]
        if profile.get("socket"): command += ["-S", profile["socket"]]
        if profile.get("database"): command.append(profile["database"])
        input_data = None
        if operation in {"query", "execute"}:
            sql = arguments.get("sql")
            if not isinstance(sql, str) or not sql.strip(): raise MySQLError("invalid_request", "sql é obrigatório.")
            command += ["-e", sql]
        elif operation == "script":
            source = self._script(profile, arguments.get("path")); input_data = source.read_text(encoding="utf-8-sig")
        elif operation == "client":
            if profile.get("allow_client_commands") is not True: raise MySQLError("client_commands_disabled", "Comandos livres do cliente estão desabilitados no perfil.")
            args = arguments.get("args", [])
            if not isinstance(args, list) or not all(isinstance(item, str) for item in args): raise MySQLError("invalid_request", "args deve ser uma lista de strings.")
            command += args
        else: command += ["-e", "SELECT 1"]
        environment = os.environ.copy(); environment.pop("MYSQL_TEST_LOGIN_FILE", None); environment["MYSQL_PWD"] = password
        try:
            completed = subprocess.run(command, input=input_data, text=True, encoding="utf-8", errors="replace", capture_output=True, env=environment, check=False, timeout=defaults["timeout_seconds"])
        except subprocess.TimeoutExpired as error: raise MySQLError("timeout", "O comando MySQL excedeu o tempo configurado.") from error
        except OSError as error: raise MySQLError("executable_error", "Não foi possível iniciar o cliente MySQL.") from error
        finally: del username, password
        if completed.returncode: raise MySQLError("mysql_error", "O cliente MySQL retornou erro.")
        output = completed.stdout or ""
        if operation == "query":
            lines = [line.split("\t") for line in output.splitlines() if line]; columns = lines[0] if lines else []
            rows = [dict(zip(columns, values)) for values in lines[1:]]
            return {"columns": columns, "rows": rows, "row_count": len(rows)}
        return {"stdout": output, "stderr": completed.stderr or ""}

    def _profile(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try: document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error: raise MySQLError("config_not_found", "Configuração MySQL MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error: raise MySQLError("invalid_config", "Configuração MySQL MCP inválida.") from error
        profile, defaults = document.get("profiles", {}).get(name), document.get("defaults", {})
        if document.get("schema_version") != 1 or not isinstance(profile, dict) or not isinstance(defaults, dict): raise MySQLError("profile_not_found", "Perfil MySQL não encontrado.")
        if profile.get("enabled") is not True: raise MySQLError("profile_disabled", "Perfil MySQL está desabilitado.")
        return profile, defaults

    @staticmethod
    def _script(profile: Mapping[str, Any], value: Any) -> Path:
        if not isinstance(value, str) or not value.strip(): raise MySQLError("invalid_request", "path deve indicar um script SQL.")
        try: source = Path(value).expanduser().resolve(strict=True)
        except OSError as error: raise MySQLError("script_not_found", "Script SQL não encontrado.") from error
        for configured in profile.get("readable_roots", []):
            try: root = Path(configured).expanduser().resolve(strict=True)
            except OSError: continue
            if root.is_dir() and source.is_file() and (source == root or root in source.parents): return source
        raise MySQLError("file_denied", "O script está fora das raízes permitidas do perfil MySQL.")
