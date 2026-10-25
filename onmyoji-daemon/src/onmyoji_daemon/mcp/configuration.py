"""Configuração local e transacional de perfis das integrações MCP."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import tomllib
from typing import Any

from .credentials import mcp_credentials_path
from .reconciliation import McpReconciliation
from .todoist import todoist_config_path


def omie_config_path(onmyoji_root: Path) -> Path:
    """Retorna a configuração privada da integração Omie para o MCP."""
    return onmyoji_root.resolve() / "configs" / "mcp" / "omie.toml"


class McpConfigurationError(ValueError):
    """Informa um erro seguro de configuração que pode ser exibido ao operador."""


def empty_credentials() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"timeout_seconds": 30}, "vaults": {}, "profiles": {}}


def empty_todoist() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"api_base": "https://api.todoist.com/api/v1", "timeout_seconds": 30, "max_retries": 2}, "profiles": {}}


def empty_omie() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"timeout_seconds": 30, "max_retries": 2}, "profiles": {}}


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _array(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def _profile_name(value: str) -> str:
    if not isinstance(value, str) or not value or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in value):
        raise McpConfigurationError("O nome do perfil deve usar apenas letras, números, hífen ou sublinhado.")
    return value


class McpConfiguration:
    """Lê, valida e grava os arquivos em ``configs/mcp``.

    Cofres são manipulados somente como parte de um perfil de vault. O registro
    interno ``vaults`` existe para descrever o KDBX, mas cada perfil criado pelo
    setup recebe seu próprio identificador de vault e somente um cofre associado.
    """

    def __init__(self, onmyoji_root: Path):
        self.root = onmyoji_root.resolve()

    @property
    def credentials_path(self) -> Path:
        return mcp_credentials_path(self.root)

    @property
    def todoist_path(self) -> Path:
        return todoist_config_path(self.root)

    @property
    def omie_path(self) -> Path:
        return omie_config_path(self.root)

    def credentials(self) -> dict[str, Any]:
        return self._load(self.credentials_path, empty_credentials())

    def todoist(self) -> dict[str, Any]:
        return self._load(self.todoist_path, empty_todoist())

    def omie(self) -> dict[str, Any]:
        return self._load(self.omie_path, empty_omie())

    def save_credentials(self, data: dict[str, Any]) -> None:
        self._validate_credentials(data)
        self._save(self.credentials_path, self._render_credentials(data))
        McpReconciliation(self.root).mark_pending()

    def save_todoist(self, data: dict[str, Any]) -> None:
        self._validate_todoist(data)
        self._save(self.todoist_path, self._render_todoist(data))
        McpReconciliation(self.root).mark_pending()

    def save_omie(self, data: dict[str, Any]) -> None:
        self._validate_omie(data)
        self._save(self.omie_path, self._render_omie(data))
        McpReconciliation(self.root).mark_pending()

    def vault_profile_names(self) -> list[str]:
        return sorted(self.credentials()["profiles"])

    def todoist_tool_available(self) -> bool:
        """Indica se ao menos um perfil Todoist habilitado tem vault válido."""
        try:
            vault_profiles = self.credentials()["profiles"]
            for profile in self.todoist()["profiles"].values():
                if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles:
                    return True
        except (KeyError, McpConfigurationError):
            return False
        return False

    def omie_tool_available(self) -> bool:
        try:
            vault_profiles = self.credentials()["profiles"]
            return any(profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles for profile in self.omie()["profiles"].values())
        except (KeyError, McpConfigurationError):
            return False

    def enabled_todoist_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.todoist()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_omie_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.omie()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    @staticmethod
    def _load(path: Path, empty: dict[str, Any]) -> dict[str, Any]:
        if not path.exists():
            return empty
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise McpConfigurationError("Não foi possível ler a configuração MCP.") from error
        if not isinstance(data, dict):
            raise McpConfigurationError("Configuração MCP inválida.")
        # TOML não materializa tabelas vazias. Após remover o último perfil,
        # o arquivo salvo não possui uma seção ``profiles``; complete-a com o
        # modelo para que o menu continue navegável.
        merged = copy.deepcopy(empty)
        merged.update(data)
        return merged

    @staticmethod
    def _save(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent, text=True)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                target.write(content)
            tomllib.loads(Path(temporary).read_text(encoding="utf-8"))
            os.replace(temporary, path)
        except Exception:
            Path(temporary).unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_credentials(data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("vaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis de vault inválida.")
        timeout = data["defaults"].get("timeout_seconds")
        if not isinstance(timeout, int) or timeout <= 0:
            raise McpConfigurationError("Timeout do cofre inválido.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or profile.get("vault") not in data["vaults"]:
                raise McpConfigurationError("Perfil de vault referencia um cofre inexistente.")
            vault = data["vaults"][profile["vault"]]
            if not isinstance(vault, dict) or not isinstance(vault.get("cli_command"), list) or not vault["cli_command"] or not all(isinstance(value, str) and value for value in vault["cli_command"]):
                raise McpConfigurationError("Executável KeePassXC inválido.")
            database = vault.get("database")
            if not isinstance(database, dict) or not all(isinstance(database.get(platform), str) for platform in ("windows", "linux")):
                raise McpConfigurationError("Caminhos do cofre inválidos.")
            roots = profile.get("allowed_entry_roots")
            if not isinstance(roots, list) or not all(isinstance(value, str) for value in roots):
                raise McpConfigurationError("Raízes permitidas do perfil de vault inválidas.")
            auth = profile.get("auth")
            if not isinstance(auth, dict) or not isinstance(auth.get("windows"), dict) or not isinstance(auth.get("linux"), dict):
                raise McpConfigurationError("Autenticação do perfil de vault incompleta.")

    def _validate_todoist(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis Todoist inválida.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil Todoist inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil Todoist é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "credential_field")):
                raise McpConfigurationError("Referência de vault do perfil Todoist incompleta.")
            if profile["credential_field"] not in {"password", "notes"}:
                raise McpConfigurationError("Campo KeePass do perfil Todoist inválido.")
            # Configurações anteriores podem conter allowed_operations. O
            # campo deixou de controlar a publicação: um perfil habilitado
            # expõe toda a superfície Todoist do MCP.
            allowed = profile.get("allowed_operations")
            if allowed is not None and (not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed)):
                raise McpConfigurationError("Operações Todoist legadas inválidas.")

    def _validate_omie(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis Omie inválida.")
        defaults = data["defaults"]
        if not isinstance(defaults.get("timeout_seconds"), int) or defaults["timeout_seconds"] <= 0 or not isinstance(defaults.get("max_retries"), int) or defaults["max_retries"] < 0:
            raise McpConfigurationError("Timeout ou tentativas Omie inválidos.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil Omie inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil Omie é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "app_key_field", "app_secret_field")):
                raise McpConfigurationError("Referência de vault do perfil Omie incompleta.")
            if profile["app_key_field"] not in {"username", "password", "url", "notes"} or profile["app_secret_field"] not in {"username", "password", "url", "notes"}:
                raise McpConfigurationError("Campos KeePass do perfil Omie inválidos.")

    @staticmethod
    def _render_credentials(data: dict[str, Any]) -> str:
        lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {data['defaults']['timeout_seconds']}"]
        for name, vault in sorted(data["vaults"].items()):
            lines += ["", f"[vaults.{name}]", f"cli_command = {_array(vault['cli_command'])}", "", f"[vaults.{name}.database]", f"windows = {_quote(vault['database']['windows'])}", f"linux = {_quote(vault['database']['linux'])}"]
        for name, profile in sorted(data["profiles"].items()):
            auth = profile["auth"]
            lines += ["", f"[profiles.{name}]", f"vault = {_quote(profile['vault'])}", f"allowed_entry_roots = {_array(profile['allowed_entry_roots'])}", "", f"[profiles.{name}.auth.windows]", f"mode = {_quote(auth['windows']['mode'])}", f"target = {_quote(auth['windows'].get('target', ''))}", "", f"[profiles.{name}.auth.linux]", f"mode = {_quote(auth['linux']['mode'])}", f"command = {_array(auth['linux'].get('command', []))}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_todoist(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"api_base = {_quote(defaults['api_base'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"credential_field = {_quote(profile['credential_field'])}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_omie(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"app_key_field = {_quote(profile['app_key_field'])}", f"app_secret_field = {_quote(profile['app_secret_field'])}"]
        return "\n".join(lines) + "\n"
