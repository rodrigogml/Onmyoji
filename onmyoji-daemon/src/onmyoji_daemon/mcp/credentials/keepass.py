"""Recupera segredos de integrações MCP por perfis locais do KeePassXC.

O resolvedor lê somente ``configs/mcp/credentials.toml`` e nunca persiste,
registra ou devolve metadados do cofre ao chamador. A senha mestra é obtida do
provedor de credenciais configurado para o sistema operacional e é usada apenas
como entrada padrão do ``keepassxc-cli``.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import tomllib
from typing import Any


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
        if field not in {"password", "notes"}:
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

    def test_profile_access(self, profile_name: str) -> None:
        """Confirma que o perfil abre seu KDBX sem consultar uma entrada secreta."""
        profile_id = _require_string(profile_name, "invalid_profile", "Perfil de credencial inválido.")
        defaults, profile, vault = self._settings(profile_id)
        password = self._master_password(profile)
        try:
            self._check_access(vault, defaults, password)
        finally:
            del password

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
        attribute = "Password" if field == "password" else "Notes"
        try:
            result = subprocess.run([*command, "show", "-q", "--show-protected", "-a", attribute, database_path, entry_path], input=password + "\n", text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
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
