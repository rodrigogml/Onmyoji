#!/usr/bin/env python3
"""JSON wrapper for the BISCMD EJB/JNDI command-line client."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any
import tomllib


class BIS2CMDError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_config(path: str, profile_name: str) -> dict[str, dict[str, Any]]:
    try: document=tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc: raise BIS2CMDError("config_not_found", "Perfil BISCMD não encontrado.") from exc
    except (OSError,tomllib.TOMLDecodeError) as exc: raise BIS2CMDError("invalid_config", "Perfil TOML inválido.") from exc
    defaults,profile=document.get("defaults",{}),document.get("profiles",{}).get(profile_name)
    if not isinstance(defaults,dict) or not isinstance(profile,dict):raise BIS2CMDError("invalid_config","Perfil solicitado não encontrado.")
    data={**defaults,**profile}; biscmd={k:data.get(k,"") for k in ("jar_path","working_dir","java_path","host","port","java_options")};auth={k:data.get(k,"") for k in ("vault_profile","vault_entry_path","username_field","password_field")};execution={k:data.get(k,"") for k in ("timeout_seconds","encoding")}
    if not all(str(biscmd[k]).strip() for k in ("jar_path","host","port")) or not str(auth["vault_profile"]).strip() or not str(auth["vault_entry_path"]).strip():raise BIS2CMDError("invalid_config","Perfil BISCMD ou KeePass incompleto.")
    if not Path(str(biscmd["jar_path"])).is_file():
        raise BIS2CMDError("jar_not_found", "O BISCMD configurado não foi encontrado.")
    return {"biscmd":biscmd,"auth":auth,"execution":execution}


def read_secret(config: dict[str,dict[str,Any]], field: str) -> str:
    auth = config["auth"]
    request = {"operation":"read","path":auth["vault_entry_path"],"field":field,"auth":{"mode":"configured"}}
    try:
        script=Path(__file__).resolve().parents[2]/"keepass-vault"/"scripts"/"keepass_vault.py";vault=Path(__file__).resolve().parents[3]/"configs"/"keepass.toml"
        result = subprocess.run([sys.executable,str(script),"--config",str(vault),"--profile",str(auth["vault_profile"])],
                                input=json.dumps(request, ensure_ascii=True), text=True,
                                capture_output=True, check=False, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BIS2CMDError("credential_provider_error", "Não foi possível consultar o KeePassVault.") from exc
    try:
        response = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BIS2CMDError("credential_provider_error", "Resposta inválida do KeePassVault.") from exc
    if result.returncode or not response.get("ok"):
        raise BIS2CMDError("credential_provider_error", "O KeePassVault recusou a leitura da credencial.")
    value = response.get("result", {}).get("value")
    if not isinstance(value, str) or not value:
        raise BIS2CMDError("credential_provider_error", "Campo de credencial vazio ou inválido.")
    return value


def parse_output(stdout: str) -> dict[str, Any]:
    records: list[Any] = []
    metadata: list[Any] = []
    messages: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("BISJSON "):
            try:
                records.append(json.loads(line[8:]))
            except json.JSONDecodeError:
                messages.append(line)
        elif line.startswith("BISMETA "):
            try:
                metadata.append(json.loads(line[8:]))
            except json.JSONDecodeError:
                messages.append(line)
        elif line.strip():
            messages.append(line)
    data: dict[str, Any] = {"records": records}
    if metadata:
        data["metadata"] = metadata[-1]
    if messages:
        data["messages"] = messages
    return data


def run(config: dict[str,dict[str,Any]], request: dict[str, Any]) -> dict[str, Any]:
    command = request.get("command")
    args = request.get("args", [])
    if not isinstance(command, str) or not command.strip():
        raise BIS2CMDError("invalid_request", "command é obrigatório.")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise BIS2CMDError("invalid_request", "args deve ser uma lista de strings.")
    if any(secret in command.lower() for secret in ("password", "credential", "biscmd_password")):
        raise BIS2CMDError("invalid_request", "Credenciais não podem ser argumentos do BISCMD.")
    cfg = config["biscmd"]
    auth = config["auth"]
    user = read_secret(config, str(auth.get("username_field", "username")))
    password = read_secret(config, str(auth.get("password_field", "password")))
    java = str(cfg.get("java_path", "java")).strip()
    java_args = [java]
    for key in ("java_options",):
        if str(cfg.get(key, "")).strip(): java_args += shlex.split(str(cfg[key]), posix=False)
    java_args += ["-jar", cfg["jar_path"]]
    normalized = command.upper()
    if not normalized.startswith("-"):
        normalized = "-" + normalized
    command_args = java_args + ["-facade", normalized] + args
    environment = os.environ.copy()
    environment.update({"BISCMD_HOST": str(cfg["host"]), "BISCMD_PORT": str(cfg["port"]),
                        "BISCMD_USER": user, "BISCMD_PASSWORD": password})
    cwd = str(cfg.get("working_dir") or Path(str(cfg["jar_path"])).parent)
    try:
        completed = subprocess.run(command_args, cwd=cwd, env=environment, text=True,
                                   capture_output=True, check=False,
                                   timeout=int(config["execution"].get("timeout_seconds", 180)),
                                   encoding=str(config["execution"].get("encoding", "utf-8")),
                                   errors="replace")
    except subprocess.TimeoutExpired as exc:
        raise BIS2CMDError("timeout", "O BISCMD excedeu o tempo configurado.") from exc
    except OSError as exc:
        raise BIS2CMDError("execution_error", "Não foi possível iniciar o BISCMD.") from exc
    if completed.returncode:
        raise BIS2CMDError("biscmd_error", "O BISCMD retornou erro.")
    return parse_output(completed.stdout)


def main() -> int:
    try:
        if len(sys.argv) != 5 or sys.argv[1] != "--config" or sys.argv[3] != "--profile":
            raise BIS2CMDError("usage", "Uso: bis2cmd.py --config PERFIL.toml --profile NOME")
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or request.get("version") != 1:
            raise BIS2CMDError("unsupported_version", "A versão do protocolo deve ser 1.")
        config = load_config(sys.argv[2],sys.argv[4])
        data = run(config, request)
        response = {"version": 1, "ok": True, "command": request.get("command"), "data": data}
    except BIS2CMDError as exc:
        response = {"version": 1, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
    except (json.JSONDecodeError, TypeError):
        response = {"version": 1, "ok": False, "error": {"code": "invalid_json", "message": "Entrada JSON inválida."}}
    print(json.dumps(response, ensure_ascii=True))
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
