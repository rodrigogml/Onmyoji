#!/usr/bin/env python3
"""JSON wrapper multiplataforma para KeePassXC CLI."""
from __future__ import annotations

import argparse
import ctypes
import getpass
import hashlib
import io
import json
import os
import subprocess
import tempfile
import sys
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

READ_OPERATIONS = {"list", "list.totp", "read", "attachment.export"}
ALL_OPERATIONS = READ_OPERATIONS | {"add", "edit", "delete", "copy", "attachment.import", "attachment.delete"}


class VaultError(Exception):
    def __init__(self, code: str, message: str): self.code, self.message = code, message


def fail(code: str, message: str) -> None:
    raise VaultError(code, message)


def load_settings(path: Path, profile_name: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    try: data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: fail("config_not_found", "Arquivo de configuração não encontrado.")
    except tomllib.TOMLDecodeError: fail("invalid_config", "Arquivo de configuração TOML inválido.")
    if data.get("schema_version") != 1: fail("unsupported_config", "schema_version não suportada.")
    profile = data.get("profiles", {}).get(profile_name)
    if not isinstance(profile, dict): fail("profile_not_found", "Perfil não encontrado.")
    vault = data.get("vaults", {}).get(profile.get("vault"))
    if not isinstance(vault, dict): fail("vault_not_found", "Vault do perfil não encontrado.")
    return data, profile, vault


def normalize(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def within(value: str, roots: list[str]) -> bool:
    if not roots: return True
    value = normalize(value)
    return any(value == normalize(root) or value.startswith(normalize(root) + "/") for root in roots)


def replace_encrypted_database(source: Path, destination: Path) -> None:
    """Atomically replace a KDBX while retaining the destination's Windows ACL."""
    if os.name != "nt":
        os.replace(source, destination)
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    replace.restype = wintypes.BOOL
    if not replace(str(destination), str(source), None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


def check_request(request: dict[str, Any], profile: dict[str, Any]) -> str:
    operation = request.get("operation")
    if operation not in ALL_OPERATIONS: fail("unsupported_operation", "Operação não suportada.")
    allowed = profile.get("allowed_operations", [])
    if operation not in allowed: fail("operation_denied", "Operação não permitida para o perfil.")
    if profile.get("access", "read_only") != "read_write" and operation not in READ_OPERATIONS: fail("write_denied", "Perfil não permite alterações.")
    for key in ("path", "source_path", "destination_path"):
        if key in request and not within(str(request[key]), profile.get("allowed_entry_roots", [])):
            fail("entry_denied", "Caminho da entrada não permitido para o perfil.")
    if operation in {"delete", "attachment.import", "attachment.delete"} and request.get("confirm") is not True:
        fail("confirmation_required", "A operação requer confirm: true.")
    if operation.startswith("attachment.") and "file_path" in request:
        file_path = Path(str(request["file_path"])).resolve()
        roots = [Path(root).resolve() for root in profile.get("allowed_attachment_roots", [])]
        if roots and not any(file_path == root or root in file_path.parents for root in roots):
            fail("attachment_path_denied", "Caminho do anexo não permitido para o perfil.")
    return operation


def windows_credential(target: str) -> str:
    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_byte * 8), ("CredentialBlobSize", ctypes.c_ulong), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", ctypes.c_ulong), ("AttributeCount", ctypes.c_ulong), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]
    ptr = ctypes.POINTER(CREDENTIAL)()
    if not ctypes.windll.advapi32.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        fail("auth_unavailable", "Credencial do Windows não encontrada.")
    try:
        raw = ctypes.string_at(ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize)
        return raw.decode("utf-16-le").rstrip("\x00")
    finally: ctypes.windll.advapi32.CredFree(ptr)


def password_for(request: dict[str, Any], profile: dict[str, Any]) -> tuple[str, str | None]:
    auth = request.get("auth", {})
    if not isinstance(auth, dict): fail("invalid_request", "auth deve ser um objeto.")
    platform = "windows" if os.name == "nt" else "linux"
    configured = profile.get("auth", {}).get(platform, {})
    mode = auth.get("mode", "configured")
    allowed = profile.get("auth", {}).get("allowed_modes", ["configured"])
    if mode not in allowed: fail("auth_denied", "Modo de autenticação não permitido para o perfil.")
    key_file = auth.get("key_file")
    if key_file is not None and not Path(key_file).is_file(): fail("key_file_not_found", "Arquivo de chave não encontrado.")
    if mode == "stdin":
        password = auth.get("password")
        if not isinstance(password, str): fail("auth_required", "auth.password é obrigatório para modo stdin.")
        return password, key_file
    if mode == "prompt": return getpass.getpass("Senha mestra KeePass: "), key_file
    if mode == "windows_credential_manager": return windows_credential(str(auth.get("target", ""))), key_file
    selected = configured.get("mode")
    if selected == "windows_credential_manager" and os.name == "nt": return windows_credential(str(configured.get("target", ""))), key_file
    if selected == "command":
        command = configured.get("command")
        if not isinstance(command, list) or not all(isinstance(item, str) for item in command): fail("invalid_config", "Comando de autenticação inválido.")
        try: result = subprocess.run(command, text=True, capture_output=True, timeout=15, check=True)
        except (OSError, subprocess.SubprocessError): fail("auth_unavailable", "Não foi possível obter a senha pelo provedor configurado.")
        password = result.stdout.rstrip("\r\n")
        if not password: fail("auth_unavailable", "O provedor configurado não retornou senha.")
        return password, key_file
    fail("auth_unavailable", "Autenticação configurada indisponível nesta plataforma.")


class KeePass:
    def __init__(self, vault: dict[str, Any], password: str, key_file: str | None, timeout: int):
        database = vault.get("database", {}).get("windows" if os.name == "nt" else "linux")
        if not database: fail("invalid_config", "Caminho do cofre não configurado para esta plataforma.")
        self.database, self.password, self.key_file = str(database), password, key_file
        self.cli = [str(x) for x in vault.get("cli_command", ["keepassxc-cli"])]
        self.timeout = timeout

    def run(self, args: list[str], extra_input: str = "") -> str:
        command = self.cli + (["--key-file", self.key_file] if self.key_file else []) + [self.database if item == "__DATABASE__" else item for item in args]
        try: result = subprocess.run(command, input=self.password + "\n" + extra_input, text=True, capture_output=True, timeout=self.timeout)
        except FileNotFoundError: fail("cli_not_found", "keepassxc-cli não encontrado.")
        except subprocess.TimeoutExpired: fail("timeout", "Tempo limite ao acessar o cofre.")
        if result.returncode: fail("keepass_error", "KeePassXC CLI recusou a operação.")
        return result.stdout.rstrip("\r\n")

    def list(self, path: str = "") -> list[str]:
        args = ["ls", "-q", "-R", "-f", "__DATABASE__"] + ([path] if path else [])
        output = self.run(args)
        return [line for line in output.splitlines() if line]

    def list_totp(self, path: str = "") -> list[str]:
        xml = self.run(["export", "-q", "--format", "xml", "__DATABASE__"])
        try: root = ET.fromstring(xml)
        except ET.ParseError: fail("keepass_error", "Exportação XML inválida do KeePassXC.")
        entries: list[str] = []
        def walk(group: ET.Element, prefix: str) -> None:
            name = group.findtext("Name", "")
            current = "/".join(part for part in (prefix, name) if part)
            for entry in group.findall("Entry"):
                title = next((s.findtext("Value", "") for s in entry.findall("String") if s.findtext("Key") == "Title"), "")
                has_totp = any(s.findtext("Key") == "otp" and bool(s.findtext("Value")) for s in entry.findall("String"))
                entry_path = "/".join(part for part in (current, title) if part)
                if has_totp and (not path or within(entry_path, [path])): entries.append(entry_path)
            for child in group.findall("Group"): walk(child, current)
        for group in root.findall(".//Root/Group"): walk(group, "")
        return entries

    def show(self, path: str, field: str) -> str:
        if field == "otp":
            return self.totp_entry(path)
        if field == "totp": return self.run(["show", "-q", "--totp", "__DATABASE__", path])
        attrs = {"title": "Title", "username": "Username", "password": "Password", "url": "URL", "notes": "Notes"}
        if field not in attrs: fail("invalid_request", "Campo inválido para read.")
        args = ["show", "-q"] + (["--show-protected"] if field == "password" else []) + ["-a", attrs[field], "__DATABASE__", path]
        return self.run(args)

    def add(self, path: str, data: dict[str, Any]) -> None:
        args = ["add", "-q", "__DATABASE__", path]
        if "title" in data: fail("unsupported_operation", "add usa o último componente de entry.path como título.")
        for key, flag in (("username", "--username"), ("url", "--url"), ("notes", "--notes")):
            if key in data: args += [flag, str(data[key])]
        if "password" in data: args.append("--password-prompt"); self.run(args, str(data["password"]) + "\n")
        else: self.run(args)

    def edit(self, path: str, data: dict[str, Any]) -> None:
        if "totp" in data:
            if set(data) != {"totp"}:
                fail("invalid_request", "A alteração de TOTP deve ser enviada separadamente dos demais campos.")
            self.totp_entry(path, data["totp"])
            return
        args = ["edit", "-q", "__DATABASE__", path]
        for key, flag in (("title", "--title"), ("username", "--username"), ("url", "--url"), ("notes", "--notes")):
            if key in data: args += [flag, str(data[key])]
        if "password" in data: args.append("--password-prompt"); self.run(args, str(data["password"]) + "\n")
        else: self.run(args)

    def totp_entry(self, path: str, uri: str | None = None, validate_only: bool = False) -> str:
        """Read or atomically save a protected OTP field in the encrypted KDBX.

        Reject duplicate/missing entries and concurrent changes. No secret is
        written to subprocess arguments or unencrypted staging files.
        """
        if uri is not None:
            if not isinstance(uri, str): fail("invalid_request", "totp deve ser uma URI otpauth válida.")
            parsed = urlparse(uri)
            query = parse_qs(parsed.query)
            if parsed.scheme != "otpauth" or parsed.netloc != "totp" or not query.get("secret"):
                fail("invalid_request", "totp deve ser uma URI otpauth válida.")
        try:
            from pykeepass import PyKeePass
        except ImportError: fail("dependency_missing", "A edição de TOTP requer pykeepass.")
        temporary = None
        stage = "load"
        try:
            database = Path(self.database)
            original = database.read_bytes()
            original_hash = hashlib.sha256(original).digest()
            keepass = PyKeePass(io.BytesIO(original), password=self.password, keyfile=self.key_file)
            entries = [entry for entry in keepass.entries if normalize("/".join(entry.path)) == normalize(path)]
            if len(entries) != 1: fail("entry_not_unique", "Entrada ausente ou duplicada; o cofre não foi alterado.")
            entry = entries[0]
            if uri is None or validate_only: return entry.otp or ""
            # PyKeePass reserves otp and does not expose a public protection setter.
            entry._set_string_field("otp", uri, True)
            stage = "serialize"
            encrypted = io.BytesIO()
            keepass.save(encrypted)
            # Validate that the new KDBX opens before replacing the original.
            verified = PyKeePass(io.BytesIO(encrypted.getvalue()), password=self.password, keyfile=self.key_file)
            checked = [e for e in verified.entries if normalize("/".join(e.path)) == normalize(path)]
            if len(checked) != 1 or checked[0].otp != uri: fail("totp_save_failed", "Falha ao validar TOTP salvo.")
            stage = "staging"
            with tempfile.NamedTemporaryFile(dir=database.parent, prefix=database.name + ".", suffix=".kdbx", delete=False) as target:
                temporary = Path(target.name)
                target.write(encrypted.getvalue())
                target.flush()
                os.fsync(target.fileno())
            if hashlib.sha256(database.read_bytes()).digest() != original_hash:
                fail("vault_changed", "O cofre mudou durante a operação; repita após sincronizá-lo.")
            stage = "replace"
            # Preserve metadata/permissions on the encrypted replacement.
            import shutil
            shutil.copystat(database, temporary)
            for attempt in range(6):
                try:
                    replace_encrypted_database(temporary, database)
                    break
                except PermissionError as exc:
                    if getattr(exc, "winerror", None) != 32 or attempt == 5:
                        raise
                    # A sync client may briefly hold the new encrypted file.
                    import time
                    time.sleep(0.2)
            temporary = None
            return ""
        except VaultError: raise
        except PermissionError as exc:
            fail("vault_write_denied_" + stage + "_" + str(getattr(exc, "winerror", 0)),
                 "O cofre não permite substituição; verifique permissões e editores abertos.")
        except Exception: fail("totp_save_failed_" + stage, "Não foi possível acessar ou salvar o TOTP no cofre.")
        finally:
            if temporary is not None: temporary.unlink(missing_ok=True)

    def attachment(self, operation: str, request: dict[str, Any]) -> Any:
        path, name = str(request["path"]), str(request["name"])
        if operation == "attachment.export": self.run(["attachment-export", "-q", "__DATABASE__", path, name, str(request["file_path"])]); return {"exported": True}
        if operation == "attachment.import": self.run(["attachment-import", "-q", "__DATABASE__", path, str(request["file_path"]), name]); return {"imported": True}
        self.run(["attachment-rm", "-q", "__DATABASE__", path, name]); return {"deleted": True}


def compatible_request(request: dict[str, Any]) -> dict[str, Any]:
    """Aceita o formato v1 existente e o formato abreviado documentado nesta base."""
    normalized = dict(request)
    def entry(value: Any) -> str:
        if isinstance(value, str) and value.strip(): return value.strip()
        if isinstance(value, dict) and isinstance(value.get("path"), str) and value["path"].strip(): return value["path"].strip()
        fail("invalid_entry", "entry.path é obrigatório.")
    operation = normalized.get("operation")
    if operation == "copy":
        normalized["source_path"] = entry(normalized.get("source", normalized.get("source_path")))
        normalized["destination_path"] = entry(normalized.get("destination", normalized.get("destination_path")))
    elif operation != "list" and operation != "list.totp":
        normalized["path"] = entry(normalized.get("entry", normalized.get("path")))
    if "fields" in normalized and "values" not in normalized: normalized["values"] = normalized["fields"]
    if operation == "attachment.export" and "file_path" not in normalized: normalized["file_path"] = normalized.get("destination")
    if operation == "attachment.import" and "file_path" not in normalized: normalized["file_path"] = normalized.get("source")
    if "attachment" in normalized and "name" not in normalized: normalized["name"] = normalized["attachment"]
    return normalized


def execute(request: dict[str, Any], profile: dict[str, Any], vault: dict[str, Any], defaults: dict[str, Any]) -> Any:
    request = compatible_request(request)
    operation = check_request(request, profile)
    password, key_file = password_for(request, profile)
    keepass = KeePass(vault, password, key_file, int(defaults.get("timeout_seconds", 30)))
    if operation == "list": return {"entries": [{"path": path, "uuid": None, "has_totp": None} for path in keepass.list(str(request.get("path", "")))]}
    if operation == "list.totp": return {"entries": [{"path": path, "uuid": None, "has_totp": True} for path in keepass.list_totp(str(request.get("path", "")))]}
    if operation == "read":
        field, path = str(request["field"]), str(request["path"])
        return {"entry": path, "field": field, "value": keepass.show(path, field)}
    if operation in {"add", "edit"}:
        values = request.get("values", {})
        if operation == "edit" and ("totp" in values or request.get("validate_only") is True):
            if request.get("confirm") is not True: fail("confirmation_required", "Alterar TOTP requer confirm: true.")
            if request.get("validate_only") is True:
                keepass.totp_entry(str(request["path"]), validate_only=True)
                return {"entry": str(request["path"]), "validated": True}
        getattr(keepass, operation)(str(request["path"]), request.get("values", {})); return {"entry": str(request["path"]), "operation": operation, "saved": True}
    if operation == "delete": keepass.run(["rm", "-q", "__DATABASE__", str(request["path"])]); return {"entry": str(request["path"]), "operation": operation, "deleted": True}
    if operation == "copy":
        field, source, destination = str(request["field"]), str(request["source_path"]), str(request["destination_path"])
        keepass.edit(destination, {field: keepass.show(source, field)}); return {"source": source, "destination": destination, "field": field, "copied": True}
    result = keepass.attachment(operation, request)
    return {"entry": str(request["path"]), "attachment": str(request["name"]), **result}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True, type=Path); parser.add_argument("--profile", required=True); args = parser.parse_args()
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict): fail("invalid_request", "A requisição deve ser um objeto JSON.")
        settings, profile, vault = load_settings(args.config, args.profile)
        print(json.dumps({"ok": True, "result": execute(request, profile, vault, settings.get("defaults", {}))}, ensure_ascii=False))
        return 0
    except VaultError as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": error.message}}, ensure_ascii=False)); return 2
    except (KeyError, TypeError, ValueError):
        print(json.dumps({"ok": False, "error": {"code": "invalid_request", "message": "Requisição inválida."}}, ensure_ascii=False)); return 2


if __name__ == "__main__": raise SystemExit(main())
