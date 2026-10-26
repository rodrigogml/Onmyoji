"""Execução segura do cliente Java BISCMD para o BIS2."""
from __future__ import annotations
import datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import tomllib
from typing import Any, Mapping
from ..credentials import CredentialError, KeePassCredentialResolver

COMMANDS = {
    "companies_list": "companiesList", "certificates_list": "certificatesList", "items_list": "itemsList", "item_update": "itemUpdate", "item_price_update": "itemPriceUpdate", "doc_fiscal_repair": "docFiscalRepair", "fix_nfce": "fixNFCe", "doc_fiscal_detail": "docFiscalDetail", "nfce_list": "nfceList", "validate_doc_fiscal": "validateDocFiscal", "nfce_download_xml": "nfceDownloadXML", "nfce_listagem_chaves": "nfceListagemChaves", "nfce_inutilize_number": "nfceInutilizeNumber", "nfce_send_offline": "nfceSendOffline", "export_nfe": "exportNFe", "export_file": "exportFile", "import_file": "importFile",
}
READ_OPERATIONS = {"companies_list", "certificates_list", "items_list", "doc_fiscal_detail", "nfce_list", "nfce_listagem_chaves"}
WRITE_OPERATIONS = set(COMMANDS) - READ_OPERATIONS

class Bis2CmdError(ValueError):
    def __init__(self, code: str, message: str): super().__init__(message); self.code = code

def parse_output(stdout: str) -> dict[str, Any]:
    records, metadata, messages = [], [], []
    for line in stdout.splitlines():
        target, prefix = (records, "BISJSON ") if line.startswith("BISJSON ") else (metadata, "BISMETA ") if line.startswith("BISMETA ") else (None, "")
        if target is not None:
            try: target.append(json.loads(line[len(prefix):]))
            except json.JSONDecodeError: messages.append(line)
        elif line.strip(): messages.append(line)
    result: dict[str, Any] = {"records": records}
    if metadata: result["metadata"] = metadata[-1]
    if messages: result["messages"] = messages
    return result

def _failure(stdout: str, stderr: str) -> str:
    def lines(value: str): return [line.strip() for line in value.splitlines() if line.strip() and not line.lstrip().startswith(("at ", "Caused by:", "<Facade>"))]
    for values in (lines(stderr), lines(stdout)):
        candidates = [line for line in values if "rejei" in line.lower() or "sefaz" in line.lower()]
        if candidates: return candidates[-1].split(": ", 1)[-1]
    for values in (lines(stderr), lines(stdout)):
        if values: return values[-1].split(": ", 1)[-1]
    return "O BIS2CMD retornou erro."

def validate_args(command: str, args: list[str]) -> None:
    if command == "docFiscalDetail" and args and args[0].lower() == "key" and (len(args) != 2 or not args[1].isdigit() or len(args[1]) != 44): raise Bis2CmdError("invalid_request", "doc_fiscal_detail com key exige uma chave de 44 dígitos.")
    if command == "nfceList":
        if len(args) % 2: raise Bis2CmdError("invalid_request", "nfce_list exige pares de parâmetro e valor.")
        allowed = {"companyid", "status", "validationstatus", "validationerrorcode", "start", "end", "limit", "offset"}; values = {}
        for index in range(0, len(args), 2):
            key = args[index].lower()
            if key not in allowed or key in values: raise Bis2CmdError("invalid_request", "Parâmetro inválido ou repetido em nfce_list.")
            values[key] = args[index + 1]
        if not values.get("companyid"): raise Bis2CmdError("invalid_request", "nfce_list exige companyId.")
        for key in ("start", "end"):
            if key in values:
                try: datetime.datetime.fromisoformat(values[key])
                except ValueError as error: raise Bis2CmdError("invalid_request", f"{key} deve usar data e hora ISO-8601.") from error
        if "start" in values and "end" in values and values["end"] < values["start"]: raise Bis2CmdError("invalid_request", "end não pode ser anterior a start.")
    if command == "nfceInutilizeNumber" and not (args and args[0].lower() == "help"):
        _validate_inutilize(args)

def _validate_inutilize(args: list[str]) -> None:
    seen, current, pending, ranges, workers, confirm, index = set(), False, None, 0, False, False, 0
    while index < len(args):
        key = args[index].lower()
        if key == "confirm":
            if confirm: raise Bis2CmdError("invalid_request", "confirm não pode ser repetido.")
            confirm = True; index += 1; continue
        if key == "--workers":
            if workers or index + 1 >= len(args): raise Bis2CmdError("invalid_request", "--workers exige inteiro de 2 a 5.")
            try: value = int(args[index + 1])
            except ValueError: raise Bis2CmdError("invalid_request", "--workers exige inteiro de 2 a 5.") from None
            if not 2 <= value <= 5: raise Bis2CmdError("invalid_request", "--workers exige inteiro de 2 a 5.")
            workers = True; index += 2; continue
        if key in {"companyid", "certificateid"}:
            if key in seen or index + 1 >= len(args): raise Bis2CmdError("invalid_request", f"{key} exige um valor.")
            seen.add(key); index += 2; continue
        if key == "serie" and pending is None and index + 1 < len(args): current = True; index += 2; continue
        if key == "numberstart" and current and pending is None and index + 1 < len(args):
            try: pending = int(args[index + 1])
            except ValueError: raise Bis2CmdError("invalid_request", "numberStart deve ser inteiro.") from None
            index += 2; continue
        if key == "numberend" and pending is not None and index + 1 < len(args):
            try: end = int(args[index + 1])
            except ValueError: raise Bis2CmdError("invalid_request", "numberEnd deve ser inteiro.") from None
            if end < pending: raise Bis2CmdError("invalid_request", "numberEnd não pode ser menor que numberStart.")
            pending, ranges = None, ranges + 1; index += 2; continue
        raise Bis2CmdError("invalid_request", "Parâmetros inválidos para nfce_inutilize_number.")
    if not {"companyid", "certificateid"}.issubset(seen) or not ranges or pending is not None or not confirm: raise Bis2CmdError("invalid_request", "nfce_inutilize_number exige empresa, certificado, faixa completa e confirm.")

class Bis2CmdService:
    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None): self.root = onmyoji_root.resolve(); self.path = self.root / "configs" / "mcp" / "bis2cmd.toml"; self.credentials = credentials or KeePassCredentialResolver(self.root)
    def test_connection(self, profile_name: str) -> dict[str, Any]: self.invoke(profile_name, "companies_list", {"args": []}); return {"profile": profile_name, "authenticated": True}
    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        command = COMMANDS.get(operation)
        if not command: raise Bis2CmdError("unsupported_operation", "Operação BIS2CMD não suportada.")
        if operation in WRITE_OPERATIONS and arguments.get("confirm") is not True: raise Bis2CmdError("confirmation_required", "Esta operação BIS2CMD exige confirm=true.")
        args = arguments.get("args", [])
        if not isinstance(args, list) or not all(isinstance(item, str) for item in args): raise Bis2CmdError("invalid_request", "args deve ser uma lista de strings.")
        validate_args(command, args); profile, defaults = self._profile(profile_name); self._validate_file_paths(profile, operation, args)
        try:
            user = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["username_field"]); password = self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], profile["password_field"])
        except CredentialError as error: raise Bis2CmdError("credential_" + error.code, "Não foi possível obter a credencial BIS2CMD.") from error
        java_args = [defaults["java_path"], *shlex.split(profile.get("java_options", ""), posix=False), "-jar", profile["jar_path"], "-facade", "-" + command, *args]
        environment = os.environ.copy()
        for key in ("BISCMD_HOST", "BISCMD_PORT", "BISCMD_USER", "BISCMD_PASSWORD"): environment.pop(key, None)
        environment.update({"BISCMD_HOST": profile["host"], "BISCMD_PORT": str(profile["port"]), "BISCMD_USER": user, "BISCMD_PASSWORD": password})
        try: completed = subprocess.run(java_args, cwd=profile["working_dir"], env=environment, capture_output=True, check=False, timeout=defaults["timeout_seconds"])
        except subprocess.TimeoutExpired as error: raise Bis2CmdError("timeout", "O BIS2CMD excedeu o tempo configurado.") from error
        except OSError as error: raise Bis2CmdError("execution_error", "Não foi possível iniciar o BIS2CMD.") from error
        finally: del user, password
        def decode(value: bytes):
            decoded = value.decode(defaults["encoding"], errors="replace"); return value.decode("cp1252", errors="replace") if "\ufffd" in decoded else decoded
        stdout, stderr = decode(completed.stdout), decode(completed.stderr)
        if completed.returncode: raise Bis2CmdError("bis2cmd_error", _failure(stdout, stderr))
        return parse_output(stdout)
    def _profile(self, name: str):
        try: document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error: raise Bis2CmdError("config_not_found", "Configuração BIS2CMD MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error: raise Bis2CmdError("invalid_config", "Configuração BIS2CMD MCP inválida.") from error
        profile, defaults = document.get("profiles", {}).get(name), document.get("defaults", {})
        if document.get("schema_version") != 1 or not isinstance(profile, dict) or not isinstance(defaults, dict): raise Bis2CmdError("profile_not_found", "Perfil BIS2CMD não encontrado.")
        if profile.get("enabled") is not True: raise Bis2CmdError("profile_disabled", "Perfil BIS2CMD desabilitado.")
        if not Path(profile["jar_path"]).is_file() or not Path(profile["working_dir"]).is_dir(): raise Bis2CmdError("runtime_not_found", "JAR ou diretório de trabalho BIS2CMD não encontrado.")
        return profile, defaults
    @staticmethod
    def _validate_file_paths(profile: Mapping[str, Any], operation: str, args: list[str]) -> None:
        specification = {
            "import_file": ("inputpath", "readable_roots", True, True),
            "export_file": ("outputpath", "writable_roots", True, True),
            "export_nfe": ("outputpath", "writable_roots", True, True),
            "nfce_download_xml": ("output", "writable_roots", False, False),
        }.get(operation)
        if specification is None or args and args[0].lower() in {"help", "-h"}:
            return
        field, roots_key, required, directory = specification
        value = next((args[index + 1] for index in range(len(args) - 1) if args[index].lower() == field), None)
        if value is None:
            if required:
                raise Bis2CmdError("invalid_request", f"{field} é obrigatório.")
            return
        try:
            path = Path(value).expanduser().resolve(strict=directory)
            confined_path = path if directory else path.parent.resolve(strict=True)
        except OSError as error:
            raise Bis2CmdError("file_not_found", "Diretório local BIS2CMD não encontrado.") from error
        if directory and not path.is_dir():
            raise Bis2CmdError("file_not_found", "Diretório local BIS2CMD não encontrado.")
        for configured in profile.get(roots_key, []):
            try: root = Path(configured).expanduser().resolve(strict=True)
            except OSError: continue
            if root.is_dir() and (confined_path == root or root in confined_path.parents): return
        raise Bis2CmdError("file_denied", "Diretório fora das raízes permitidas do perfil BIS2CMD.")
