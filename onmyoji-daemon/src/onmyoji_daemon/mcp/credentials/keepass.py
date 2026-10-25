"""Acessa segredos de integrações MCP por perfis locais do KeePassXC.

O resolvedor lê somente ``configs/mcp/credentials.toml`` e nunca persiste,
registra nem devolve segredos ao chamador. A senha mestra é obtida do provedor
de credenciais configurado para o sistema operacional e é usada apenas como
entrada padrão do ``keepassxc-cli``. A escrita é restrita ao setup local.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import tomllib
from typing import Any
import xml.etree.ElementTree as ET


class CredentialError(ValueError):
    """Erro sanitizado de resolução de credencial, sem dados sensíveis."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def mcp_credentials_path(onmyoji_root: Path) -> Path:
    """Retorna o único local da configuração privada de credenciais MCP."""
    return onmyoji_root.resolve() / "configs" / "mcp" / "credentials.toml"


def _platform_name() -> str:
    return "windows" if os.name == "nt" else "linux"


def _normalize_entry_path(value: str) -> str:
    return value.replace("\\", "/").strip("/")


def _is_allowed_entry(entry_path: str, roots: list[str]) -> bool:
    if not roots:
        return True
    entry = _normalize_entry_path(entry_path)
    return any(entry == _normalize_entry_path(root) or entry.startswith(_normalize_entry_path(root) + "/") for root in roots)


def _require_string(value: Any, code: str, message: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CredentialError(code, message)
    return value.strip()


class KeePassCredentialResolver:
    """Resolve segredos de perfis MCP sem expor o cofre à superfície MCP.

    Cada perfil limita as raízes de entradas que os módulos podem consultar. A
    classe aceita somente o modo de autenticação configurado: modos interativos
    e senhas recebidas por stdin não pertencem a um processo MCP por stdio.
    """

    def __init__(self, onmyoji_root: Path):
        self.root = onmyoji_root.resolve()
        self.path = mcp_credentials_path(self.root)

    def resolve(self, profile_name: str, entry_path: str, field: str = "password") -> str:
        """Retorna um campo secreto permitido de uma entrada KeePass.

        Raises:
            CredentialError: Quando a configuração, o perfil, o provedor local
                ou o KeePassXC não puderem atender à consulta.
        """
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        requested_entry = _require_string(entry_path, "invalid_entry", "Entrada de credencial inválida.")
        if field not in {"password", "notes", "totp"}:
            raise CredentialError("invalid_field", "Campo de credencial não permitido.")

        defaults, profile, vault = self._settings(profile_id)
        roots = profile.get("allowed_entry_roots", [])
        if not isinstance(roots, list) or not all(isinstance(item, str) and item.strip() for item in roots):
            raise CredentialError("invalid_config", "Raízes permitidas do perfil são inválidas.")
        if not _is_allowed_entry(requested_entry, roots):
            raise CredentialError("entry_denied", "Entrada de credencial não permitida para este perfil.")

        password = self._master_password(profile)
        try:
            return self._read_secret(vault, defaults, requested_entry, field, password)
        finally:
            # Reduz a vida da referência; Python não garante limpeza de memória.
            del password

    def list_totp(self, profile_name: str) -> list[str]:
        """Lista somente os caminhos permitidos que possuem TOTP configurado."""
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        defaults, profile, vault = self._settings(profile_id)
        roots = profile.get("allowed_entry_roots", [])
        if not isinstance(roots, list) or not all(isinstance(item, str) and item.strip() for item in roots):
            raise CredentialError("invalid_config", "Raízes permitidas do perfil são inválidas.")
        password = self._master_password(profile)
        try:
            return [entry for entry in self._list_totp(vault, defaults, password) if _is_allowed_entry(entry, roots)]
        finally:
            del password

    def test_profile_access(self, profile_name: str) -> None:
        """Confirma que o perfil abre seu KDBX sem consultar uma entrada secreta."""
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        defaults, profile, vault = self._settings(profile_id)
        password = self._master_password(profile)
        try:
            self._check_access(vault, defaults, password)
        finally:
            del password

    def entry_exists(self, profile_name: str, entry_path: str) -> bool:
        """Informa se uma entrada permitida já existe no cofre.

        A verificação lista apenas os caminhos. Ela não lê nem devolve campos
        da entrada, para que o setup consiga decidir se deve reutilizar ou
        substituir uma credencial sem revelar seu conteúdo.
        """
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        requested_entry = _require_string(entry_path, "invalid_entry", "Entrada de credencial inválida.")
        defaults, profile, vault = self._settings(profile_id)
        self._require_allowed_entry(profile, requested_entry)
        password = self._master_password(profile)
        try:
            return self._entry_exists(vault, defaults, requested_entry, password)
        finally:
            del password

    def password_exists(self, profile_name: str, entry_path: str) -> bool:
        """Informa se uma entrada possui uma senha protegida não vazia.

        O valor é lido apenas para validar sua presença e é descartado antes
        de retornar. Assim o configurador pode oferecer reutilização somente
        quando há de fato uma chave salva.
        """
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        requested_entry = _require_string(entry_path, "invalid_entry", "Entrada de credencial inválida.")
        defaults, profile, vault = self._settings(profile_id)
        self._require_allowed_entry(profile, requested_entry)
        password = self._master_password(profile)
        try:
            try:
                value = self._read_secret(vault, defaults, requested_entry, "password", password)
            except CredentialError as error:
                if error.code == "secret_not_found":
                    return False
                raise
            del value
            return True
        finally:
            del password

    def save_password(self, profile_name: str, entry_path: str, password_value: str) -> bool:
        """Cria ou substitui a senha protegida de uma entrada permitida.

        Returns:
            ``True`` quando a entrada foi criada; ``False`` quando uma entrada
            existente recebeu a nova senha.

        Raises:
            CredentialError: Quando não for possível ler ou gravar o cofre.
        """
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        requested_entry = _require_string(entry_path, "invalid_entry", "Entrada de credencial inválida.")
        secret = _require_string(password_value, "invalid_secret", "A chave de autenticação é obrigatória.")
        defaults, profile, vault = self._settings(profile_id)
        self._require_allowed_entry(profile, requested_entry)
        password = self._master_password(profile)
        try:
            exists = self._entry_exists(vault, defaults, requested_entry, password)
            if exists:
                self._edit_password(vault, defaults, requested_entry, password, secret)
            else:
                self._ensure_parent_groups(vault, defaults, requested_entry, password)
                self._add_password(vault, defaults, requested_entry, password, secret)
            return not exists
        finally:
            del password
            del secret

    def _settings(self, profile_name: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        try:
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CredentialError("config_not_found", "Configuração de credenciais MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise CredentialError("invalid_config", "Configuração de credenciais MCP inválida.") from error
        if data.get("schema_version") != 1:
            raise CredentialError("unsupported_config", "Versão da configuração de credenciais não suportada.")
        profile = data.get("profiles", {}).get(profile_name)
        if not isinstance(profile, dict):
            raise CredentialError("profile_not_found", "Perfil de credencial não encontrado.")
        vault_name = profile.get("vault")
        vault = data.get("vaults", {}).get(vault_name)
        if not isinstance(vault, dict):
            raise CredentialError("vault_not_found", "Cofre do perfil de credencial não encontrado.")
        defaults = data.get("defaults", {})
        if not isinstance(defaults, dict):
            raise CredentialError("invalid_config", "Configuração padrão de credenciais inválida.")
        return defaults, profile, vault

    @staticmethod
    def _require_allowed_entry(profile: dict[str, Any], entry_path: str) -> None:
        roots = profile.get("allowed_entry_roots", [])
        if not isinstance(roots, list) or not all(isinstance(item, str) and item.strip() for item in roots):
            raise CredentialError("invalid_config", "Raízes permitidas do perfil são inválidas.")
        if not _is_allowed_entry(entry_path, roots):
            raise CredentialError("entry_denied", "Entrada de credencial não permitida para este perfil.")

    def _master_password(self, profile: dict[str, Any]) -> str:
        auth = profile.get("auth", {})
        platform = _platform_name()
        configured = auth.get(platform, {}) if isinstance(auth, dict) else {}
        if not isinstance(configured, dict):
            raise CredentialError("invalid_config", "Autenticação do perfil de credencial inválida.")
        mode = configured.get("mode")
        if mode == "windows_credential_manager" and platform == "windows":
            return self._windows_credential(_require_string(configured.get("target"), "invalid_config", "Destino da credencial Windows não configurado."))
        if mode == "command" and platform == "linux":
            command = configured.get("command")
            if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
                raise CredentialError("invalid_config", "Comando de credencial Linux inválido.")
            try:
                result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=15, check=True)
            except (OSError, subprocess.SubprocessError) as error:
                raise CredentialError("auth_unavailable", "Não foi possível obter a senha no provedor do sistema operacional.") from error
            password = result.stdout.rstrip("\r\n")
            if not password:
                raise CredentialError("auth_unavailable", "O provedor do sistema operacional não retornou uma senha.")
            return password
        raise CredentialError("auth_unavailable", "Autenticação configurada indisponível nesta plataforma.")

    @staticmethod
    def _windows_credential(target: str) -> str:
        class Credential(ctypes.Structure):
            _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_byte * 8), ("CredentialBlobSize", ctypes.c_ulong), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", ctypes.c_ulong), ("AttributeCount", ctypes.c_ulong), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]

        pointer = ctypes.POINTER(Credential)()
        if not ctypes.windll.advapi32.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            raise CredentialError("auth_unavailable", "Credencial do Windows não encontrada.")
        try:
            value = ctypes.string_at(pointer.contents.CredentialBlob, pointer.contents.CredentialBlobSize).decode("utf-16-le").rstrip("\x00")
            if not value:
                raise CredentialError("auth_unavailable", "Credencial do Windows está vazia.")
            return value
        finally:
            ctypes.windll.advapi32.CredFree(pointer)

    @staticmethod
    def _read_secret(vault: dict[str, Any], defaults: dict[str, Any], entry_path: str, field: str, password: str) -> str:
        platform = _platform_name()
        database = vault.get("database", {})
        if not isinstance(database, dict):
            raise CredentialError("invalid_config", "Banco de dados do cofre inválido.")
        database_path = _require_string(database.get(platform), "invalid_config", "Caminho do cofre não configurado para esta plataforma.")
        command = vault.get("cli_command", ["keepassxc-cli"])
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise CredentialError("invalid_config", "Comando KeePassXC inválido.")
        try:
            timeout = float(defaults.get("timeout_seconds", 30))
        except (TypeError, ValueError) as error:
            raise CredentialError("invalid_config", "Timeout do cofre inválido.") from error
        if timeout <= 0:
            raise CredentialError("invalid_config", "Timeout do cofre inválido.")
        arguments = [*command, "show", "-q"]
        if field == "totp":
            arguments += ["--totp", database_path, entry_path]
        else:
            attribute = "Password" if field == "password" else "Notes"
            arguments += ["--show-protected", "-a", attribute, database_path, entry_path]
        try:
            result = subprocess.run(arguments, input=password + "\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
        except FileNotFoundError as error:
            raise CredentialError("cli_not_found", "keepassxc-cli não encontrado.") from error
        except subprocess.TimeoutExpired as error:
            raise CredentialError("timeout", "Tempo limite ao acessar o cofre.") from error
        except OSError as error:
            raise CredentialError("vault_unavailable", "Não foi possível iniciar o KeePassXC.") from error
        if result.returncode:
            raise CredentialError("keepass_error", "KeePassXC recusou a consulta de credencial.")
        value = result.stdout.rstrip("\r\n")
        if not value:
            raise CredentialError("secret_not_found", "A entrada KeePass não contém o campo solicitado.")
        return value

    @staticmethod
    def _command_settings(vault: dict[str, Any], defaults: dict[str, Any]) -> tuple[list[str], str, float]:
        platform = _platform_name()
        database = vault.get("database", {})
        database_path = _require_string(database.get(platform) if isinstance(database, dict) else None, "invalid_config", "Caminho do cofre não configurado para esta plataforma.")
        command = vault.get("cli_command", ["keepassxc-cli"])
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise CredentialError("invalid_config", "Comando KeePassXC inválido.")
        try:
            timeout = float(defaults.get("timeout_seconds", 30))
        except (TypeError, ValueError) as error:
            raise CredentialError("invalid_config", "Timeout do cofre inválido.") from error
        if timeout <= 0:
            raise CredentialError("invalid_config", "Timeout do cofre inválido.")
        return command, database_path, timeout

    @classmethod
    def _run_write_command(cls, vault: dict[str, Any], defaults: dict[str, Any], arguments: list[str], password: str, extra_input: str = "") -> str:
        command, database_path, timeout = cls._command_settings(vault, defaults)
        try:
            resolved_arguments = [database_path if argument == "__DATABASE__" else argument for argument in arguments]
            result = subprocess.run([*command, *resolved_arguments], input=password + "\n" + extra_input, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
        except FileNotFoundError as error:
            raise CredentialError("cli_not_found", "keepassxc-cli não encontrado.") from error
        except subprocess.TimeoutExpired as error:
            raise CredentialError("timeout", "Tempo limite ao acessar o cofre.") from error
        except OSError as error:
            raise CredentialError("vault_unavailable", "Não foi possível iniciar o KeePassXC.") from error
        if result.returncode:
            raise CredentialError("keepass_error", "KeePassXC recusou a alteração no cofre.")
        return result.stdout.rstrip("\r\n")

    @classmethod
    def _entry_exists(cls, vault: dict[str, Any], defaults: dict[str, Any], entry_path: str, password: str) -> bool:
        output = cls._run_write_command(vault, defaults, ["ls", "-q", "-R", "-f", "__DATABASE__"], password)
        normalized = _normalize_entry_path(entry_path)
        return any(_normalize_entry_path(line.rstrip("/")) == normalized and not line.endswith("/") for line in output.splitlines())

    @classmethod
    def _list_totp(cls, vault: dict[str, Any], defaults: dict[str, Any], password: str) -> list[str]:
        xml = cls._run_write_command(vault, defaults, ["export", "-q", "--format", "xml", "__DATABASE__"], password)
        try:
            root = ET.fromstring(xml)
        except ET.ParseError as error:
            raise CredentialError("keepass_error", "Exportação XML do KeePassXC inválida.") from error
        entries: list[str] = []
        def walk(group: ET.Element, prefix: str) -> None:
            current = "/".join(item for item in (prefix, group.findtext("Name", "")) if item)
            for entry in group.findall("Entry"):
                values = {item.findtext("Key", ""): item.findtext("Value", "") for item in entry.findall("String")}
                if values.get("Title") and values.get("otp"):
                    entries.append("/".join((current, values["Title"])))
            for child in group.findall("Group"):
                walk(child, current)
        for group in root.findall(".//Root/Group"):
            walk(group, "")
        return entries

    @classmethod
    def _ensure_parent_groups(cls, vault: dict[str, Any], defaults: dict[str, Any], entry_path: str, password: str) -> None:
        parents = [part for part in _normalize_entry_path(entry_path).split("/")[:-1] if part]
        if not parents:
            return
        output = cls._run_write_command(vault, defaults, ["ls", "-q", "-R", "-f", "__DATABASE__"], password)
        groups = {_normalize_entry_path(line.rstrip("/")) for line in output.splitlines() if line.endswith("/")}
        current: list[str] = []
        for parent in parents:
            current.append(parent)
            group = "/".join(current)
            if group not in groups:
                cls._run_write_command(vault, defaults, ["mkdir", "-q", "__DATABASE__", group], password)
                groups.add(group)

    @classmethod
    def _add_password(cls, vault: dict[str, Any], defaults: dict[str, Any], entry_path: str, password: str, secret: str) -> None:
        cls._run_write_command(vault, defaults, ["add", "-q", "__DATABASE__", entry_path, "--password-prompt"], password, secret + "\n")

    @classmethod
    def _edit_password(cls, vault: dict[str, Any], defaults: dict[str, Any], entry_path: str, password: str, secret: str) -> None:
        cls._run_write_command(vault, defaults, ["edit", "-q", "__DATABASE__", entry_path, "--password-prompt"], password, secret + "\n")

    @staticmethod
    def _check_access(vault: dict[str, Any], defaults: dict[str, Any], password: str) -> None:
        platform = _platform_name()
        database = vault.get("database", {})
        database_path = _require_string(database.get(platform) if isinstance(database, dict) else None, "invalid_config", "Caminho do cofre não configurado para esta plataforma.")
        command = vault.get("cli_command", ["keepassxc-cli"])
        if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
            raise CredentialError("invalid_config", "Comando KeePassXC inválido.")
        try:
            timeout = float(defaults.get("timeout_seconds", 30))
            if timeout <= 0: raise ValueError
        except (TypeError, ValueError) as error:
            raise CredentialError("invalid_config", "Timeout do cofre inválido.") from error
        try:
            result = subprocess.run([*command, "ls", "-q", database_path], input=password + "\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CredentialError("vault_unavailable", "Não foi possível acessar o KeePassXC.") from error
        if result.returncode:
            raise CredentialError("keepass_error", "KeePassXC recusou o acesso ao cofre.")
