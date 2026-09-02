#!/usr/bin/env python3
"""JSON wrapper for the BIS10 BISCMD client."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any


class BIS10CMDError(Exception):
    def __init__(self, code: str, message: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.data = code, data


def load_config(path: str, profile_name: str) -> dict[str, dict[str, Any]]:
    try:
        document = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BIS10CMDError("config_not_found", "Perfil BIS10CMD não encontrado.") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BIS10CMDError("invalid_config", "Perfil TOML inválido.") from exc
    defaults, profile = document.get("defaults", {}), document.get("profiles", {}).get(profile_name)
    if not isinstance(defaults, dict) or not isinstance(profile, dict):
        raise BIS10CMDError("invalid_config", "Perfil solicitado não encontrado.")
    data = {**defaults, **profile}
    client = {key: data.get(key, "") for key in ("jar_path", "working_dir", "java_path", "host", "port", "locale")}
    jndi = {key.removeprefix("jndi_"): data.get(key, "") for key in ("jndi_vault_profile", "jndi_vault_entry_path", "jndi_username_field", "jndi_password_field")}
    bis = {key.removeprefix("bis_"): data.get(key, "") for key in ("bis_vault_profile", "bis_vault_entry_path", "bis_username_field", "bis_password_field")}
    execution = {key: data.get(key, "") for key in ("timeout_seconds", "encoding")}
    required = ("jar_path", "working_dir", "java_path", "host", "port", "locale")
    if not all(str(client[key]).strip() for key in required) or not all(str(value).strip() for group in (jndi, bis) for value in group.values()):
        raise BIS10CMDError("invalid_config", "Perfil BIS10CMD ou referências KeePass incompletos.")
    if not Path(str(client["jar_path"])).is_file():
        raise BIS10CMDError("jar_not_found", "O JAR BIS10CMD configurado não foi encontrado.")
    if not Path(str(client["working_dir"])).is_dir() or not (Path(str(client["working_dir"])) / "libs").is_dir():
        raise BIS10CMDError("runtime_not_found", "O diretório de trabalho ou as bibliotecas libs do BIS10CMD não foram encontrados.")
    return {"client": client, "jndi": jndi, "bis": bis, "execution": execution}


def read_secret(section: dict[str, Any], field: str) -> str:
    request = {"operation": "read", "path": section["vault_entry_path"], "field": field, "auth": {"mode": "configured"}}
    script = Path(__file__).resolve().parents[2] / "keepass-vault" / "scripts" / "keepass_vault.py"
    vault = Path(__file__).resolve().parents[3] / "configs" / "keepass.toml"
    try:
        result = subprocess.run([sys.executable, str(script), "--config", str(vault), "--profile", str(section["vault_profile"])], input=json.dumps(request), text=True, capture_output=True, check=False, timeout=30)
        response = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError) as exc:
        raise BIS10CMDError("credential_provider_error", "Não foi possível consultar o KeePassVault.") from exc
    value = response.get("result", {}).get("value") if not result.returncode and response.get("ok") else None
    if not isinstance(value, str) or not value:
        raise BIS10CMDError("credential_provider_error", "O KeePassVault recusou ou não encontrou a credencial solicitada.")
    return value


COMMANDS = {"help": "-h", "facade": "-facade", "login": "-login", "connect": "-connect", "ping": "-ping", "session": "-session", "accountstatement": "-accountStatement"}
SESSION_COMMANDS = {"ping", "session", "accountstatement"}


def normalize_commands(request: dict[str, Any]) -> list[tuple[str, list[str]]]:
    raw = request.get("commands")
    if not isinstance(raw, list) or not raw:
        raise BIS10CMDError("invalid_request", "commands deve ser uma lista não vazia.")
    commands: list[tuple[str, list[str]]] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("args", []), list) or not all(isinstance(value, str) for value in item.get("args", [])):
            raise BIS10CMDError("invalid_request", "Cada comando exige name e args como lista de strings.")
        name = item["name"].casefold()
        if name not in COMMANDS:
            raise BIS10CMDError("unsupported_command", f"Comando BIS10CMD não permitido: {item['name']}.")
        args = item.get("args", [])
        if any(value.casefold().startswith("-d") or "biscmd_" in value.casefold() or value.casefold().startswith("biscmd.") for value in args):
            raise BIS10CMDError("invalid_request", "Propriedades Java, variáveis BISCMD e credenciais não podem ser argumentos.")
        commands.append((name, args))
    for index, (name, _) in enumerate(commands):
        if name not in SESSION_COMMANDS:
            continue
        prior = [item[0] for item in commands[:index]]
        connected = "connect" in prior or ("facade" in prior and "login" in prior and prior.index("facade") < prior.index("login"))
        if not connected:
            commands.insert(index, ("connect", []))
        break
    return commands


def redact(value: str, secrets: list[str]) -> str:
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def output(completed: Any, secrets: list[str]) -> dict[str, list[str]]:
    data: dict[str, list[str]] = {}
    stdout = [redact(line, secrets) for line in completed.stdout.splitlines() if line.strip()]
    stderr = [redact(line, secrets) for line in completed.stderr.splitlines() if line.strip()]
    if stdout:
        data["messages"] = stdout
    if stderr:
        data["stderr"] = stderr
    return data


def run(config: dict[str, dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
    commands = normalize_commands(request)
    client = config["client"]
    remote = any(name != "help" for name, _ in commands)
    secrets: list[str] = []
    environment = os.environ.copy()
    environment.update({"BISCMD_HOST": str(client["host"]), "BISCMD_PORT": str(client["port"]), "BISCMD_LOCALE": str(client["locale"])})
    if remote:
        jndi_user = read_secret(config["jndi"], str(config["jndi"]["username_field"]))
        jndi_password = read_secret(config["jndi"], str(config["jndi"]["password_field"]))
        bis_user = read_secret(config["bis"], str(config["bis"]["username_field"]))
        bis_password = read_secret(config["bis"], str(config["bis"]["password_field"]))
        secrets = [jndi_user, jndi_password, bis_user, bis_password]
        environment.update({"BISCMD_JNDI_USER": jndi_user, "BISCMD_JNDI_PASSWORD": jndi_password, "BISCMD_BIS_USER": bis_user, "BISCMD_BIS_PASSWORD": bis_password})
    invocation = [str(client["java_path"]), "-jar", str(client["jar_path"])]
    for name, args in commands:
        invocation.extend([COMMANDS[name], *args])
    try:
        completed = subprocess.run(invocation, cwd=str(client["working_dir"]), env=environment, text=True, capture_output=True, check=False, timeout=int(config["execution"].get("timeout_seconds", 180)), encoding=str(config["execution"].get("encoding", "utf-8")), errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise BIS10CMDError("timeout", "O BIS10CMD excedeu o tempo configurado.") from exc
    except OSError as exc:
        raise BIS10CMDError("execution_error", "Não foi possível iniciar o BIS10CMD.") from exc
    data = output(completed, secrets)
    if completed.returncode:
        raise BIS10CMDError("bis10cmd_error", "O BIS10CMD retornou erro.", data)
    return data


def main() -> int:
    try:
        if len(sys.argv) != 5 or sys.argv[1] != "--config" or sys.argv[3] != "--profile":
            raise BIS10CMDError("usage", "Uso: bis10cmd.py --config PERFIL.toml --profile NOME")
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or request.get("version") != 1:
            raise BIS10CMDError("unsupported_version", "A versão do protocolo deve ser 1.")
        data = run(load_config(sys.argv[2], sys.argv[4]), request)
        response = {"version": 1, "ok": True, "data": data}
    except BIS10CMDError as exc:
        response = {"version": 1, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        if exc.data:
            response["data"] = exc.data
    except (json.JSONDecodeError, TypeError):
        response = {"version": 1, "ok": False, "error": {"code": "invalid_json", "message": "Entrada JSON inválida."}}
    print(json.dumps(response, ensure_ascii=True))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
