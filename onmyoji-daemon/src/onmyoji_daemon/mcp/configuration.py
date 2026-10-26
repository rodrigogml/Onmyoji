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
from .btg.configuration import BtgConfigurationError, configuration_lock as btg_configuration_lock, empty_configuration as empty_btg, render_configuration as render_btg, validate_configuration as validate_btg


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


def empty_cloudflare() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"api_base": "https://api.cloudflare.com/client/v4", "timeout_seconds": 30, "max_retries": 2}, "profiles": {}}


def empty_forward_email() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"api_base": "https://api.forwardemail.net/v1", "timeout_seconds": 30, "max_retries": 2}, "profiles": {}}


def empty_notion() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"api_base": "https://api.notion.com", "notion_version": "2026-03-11", "timeout_seconds": 30, "max_retries": 2, "page_size": 100}, "profiles": {}}


def empty_aws() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"timeout_seconds": 30, "max_attempts": 3}, "profiles": {}}


def empty_mysql() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"executable": "mysql", "port": 3306, "connect_timeout": 10, "timeout_seconds": 120}, "profiles": {}}


def empty_eccovox() -> dict[str, Any]:
    """Modelo da integração local de voz, sem segredos."""
    return {"schema_version": 1, "defaults": {"request_timeout_seconds": 120, "max_audio_bytes": 10485760, "max_text_characters": 4000}, "profiles": {}}


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

    @property
    def cloudflare_path(self) -> Path:
        return self.root / "configs" / "mcp" / "cloudflare.toml"

    @property
    def forward_email_path(self) -> Path:
        return self.root / "configs" / "mcp" / "forward-email.toml"

    @property
    def notion_path(self) -> Path:
        return self.root / "configs" / "mcp" / "notion.toml"

    @property
    def aws_path(self) -> Path:
        return self.root / "configs" / "mcp" / "aws.toml"

    @property
    def mysql_path(self) -> Path:
        return self.root / "configs" / "mcp" / "mysql.toml"

    @property
    def btg_path(self) -> Path:
        return self.root / "configs" / "mcp" / "btg-empresas.toml"

    @property
    def eccovox_path(self) -> Path:
        return self.root / "configs" / "mcp" / "eccovox.toml"

    def credentials(self) -> dict[str, Any]:
        return self._load(self.credentials_path, empty_credentials())

    def todoist(self) -> dict[str, Any]:
        return self._load(self.todoist_path, empty_todoist())

    def omie(self) -> dict[str, Any]:
        return self._load(self.omie_path, empty_omie())

    def cloudflare(self) -> dict[str, Any]:
        return self._load(self.cloudflare_path, empty_cloudflare())

    def forward_email(self) -> dict[str, Any]:
        return self._load(self.forward_email_path, empty_forward_email())

    def notion(self) -> dict[str, Any]:
        return self._load(self.notion_path, empty_notion())

    def aws(self) -> dict[str, Any]:
        return self._load(self.aws_path, empty_aws())

    def mysql(self) -> dict[str, Any]:
        return self._load(self.mysql_path, empty_mysql())

    def btg(self) -> dict[str, Any]:
        return self._load(self.btg_path, empty_btg())

    def eccovox(self) -> dict[str, Any]:
        return self._load(self.eccovox_path, empty_eccovox())

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

    def save_cloudflare(self, data: dict[str, Any]) -> None:
        self._validate_cloudflare(data)
        self._save(self.cloudflare_path, self._render_cloudflare(data))
        McpReconciliation(self.root).mark_pending()

    def save_forward_email(self, data: dict[str, Any]) -> None:
        self._validate_forward_email(data)
        self._save(self.forward_email_path, self._render_forward_email(data))
        McpReconciliation(self.root).mark_pending()

    def save_notion(self, data: dict[str, Any]) -> None:
        self._validate_notion(data)
        self._save(self.notion_path, self._render_notion(data))
        McpReconciliation(self.root).mark_pending()

    def save_aws(self, data: dict[str, Any]) -> None:
        self._validate_aws(data)
        self._save(self.aws_path, self._render_aws(data))
        McpReconciliation(self.root).mark_pending()

    def save_mysql(self, data: dict[str, Any]) -> None:
        self._validate_mysql(data)
        self._save(self.mysql_path, self._render_mysql(data))
        McpReconciliation(self.root).mark_pending()

    def save_btg(self, data: dict[str, Any]) -> None:
        try:
            validate_btg(data)
            with btg_configuration_lock(self.root):
                previous = self.btg()["profiles"]
                candidate = copy.deepcopy(data)
                for name, incoming in candidate["profiles"].items():
                    existing = previous.get(name, {})
                    if existing.get("account_id"):
                        if any(existing.get(key) != incoming.get(key) for key in ("environment", "company_id")):
                            raise BtgConfigurationError("Use outro perfil para mudar empresa ou ambiente de uma conta descoberta.")
                        for key in ("account_id", "account_number", "branch_code"):
                            if incoming.get(key) and incoming[key] != existing[key]:
                                raise BtgConfigurationError("A conta foi descoberta enquanto o perfil era editado; reabra o perfil.")
                            incoming[key] = existing[key]
                validate_btg(candidate)
                self._save(self.btg_path, render_btg(candidate))
        except BtgConfigurationError as error:
            raise McpConfigurationError(str(error)) from error
        McpReconciliation(self.root).mark_pending()

    def save_eccovox(self, data: dict[str, Any]) -> None:
        self._validate_eccovox(data)
        self._save(self.eccovox_path, self._render_eccovox(data))
        McpReconciliation(self.root).mark_pending()

    def update_btg_profile(self, name: str, updater) -> None:
        """Atualiza um perfil sob a mesma trava usada pelo menu e pelo callback."""
        with btg_configuration_lock(self.root):
            data = self.btg()
            if name not in data["profiles"]:
                raise McpConfigurationError("Perfil BTG não encontrado.")
            updater(data["profiles"][name])
            validate_btg(data)
            self._save(self.btg_path, render_btg(data))
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

    def cloudflare_tool_available(self) -> bool:
        try:
            vault_profiles = self.credentials()["profiles"]
            return any(profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles for profile in self.cloudflare()["profiles"].values())
        except (KeyError, McpConfigurationError):
            return False

    def forward_email_tool_available(self) -> bool:
        try:
            vault_profiles = self.credentials()["profiles"]
            return any(profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles for profile in self.forward_email()["profiles"].values())
        except (KeyError, McpConfigurationError):
            return False

    def notion_tool_available(self) -> bool:
        try:
            vault_profiles = self.credentials()["profiles"]
            return any(profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles for profile in self.notion()["profiles"].values())
        except (KeyError, McpConfigurationError):
            return False

    def aws_tool_available(self) -> bool:
        try:
            vault_profiles = self.credentials()["profiles"]
            return any(profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles for profile in self.aws()["profiles"].values())
        except (KeyError, McpConfigurationError):
            return False

    def mysql_tool_available(self) -> bool:
        try:
            vault_profiles = self.credentials()["profiles"]
            return any(profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles for profile in self.mysql()["profiles"].values())
        except (KeyError, McpConfigurationError):
            return False

    def enabled_todoist_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.todoist()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_omie_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.omie()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_cloudflare_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.cloudflare()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_forward_email_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.forward_email()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_notion_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.notion()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_aws_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.aws()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_mysql_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.mysql()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_btg_profiles(self) -> list[dict[str, str]]:
        vault_profiles = self.credentials()["profiles"]
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.btg()["profiles"].items()) if profile.get("enabled") is True and profile.get("credential_profile") in vault_profiles]

    def enabled_eccovox_profiles(self) -> list[dict[str, str]]:
        return [{"name": name, "description": profile["description"]} for name, profile in sorted(self.eccovox()["profiles"].items()) if profile.get("enabled") is True]

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

    def _validate_cloudflare(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis Cloudflare inválida.")
        defaults = data["defaults"]
        api_base = defaults.get("api_base")
        if not isinstance(api_base, str) or not api_base.startswith("https://"):
            raise McpConfigurationError("Endpoint Cloudflare deve usar HTTPS.")
        if not isinstance(defaults.get("timeout_seconds"), int) or defaults["timeout_seconds"] <= 0 or not isinstance(defaults.get("max_retries"), int) or defaults["max_retries"] < 0:
            raise McpConfigurationError("Timeout ou tentativas Cloudflare inválidos.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil Cloudflare inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil Cloudflare é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "credential_field")):
                raise McpConfigurationError("Referência de vault do perfil Cloudflare incompleta.")
            if profile["credential_field"] not in {"password", "notes"}:
                raise McpConfigurationError("Campo KeePass do perfil Cloudflare inválido.")
            if not isinstance(profile.get("zone_id", ""), str):
                raise McpConfigurationError("Zone ID do perfil Cloudflare inválido.")

    def _validate_forward_email(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis Forward Email inválida.")
        defaults = data["defaults"]
        api_base = defaults.get("api_base")
        if not isinstance(api_base, str) or api_base.rstrip("/") != "https://api.forwardemail.net/v1":
            raise McpConfigurationError("Endpoint Forward Email deve usar a API oficial v1.")
        if not isinstance(defaults.get("timeout_seconds"), int) or defaults["timeout_seconds"] <= 0 or not isinstance(defaults.get("max_retries"), int) or defaults["max_retries"] < 0:
            raise McpConfigurationError("Timeout ou tentativas Forward Email inválidos.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil Forward Email inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil Forward Email é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "credential_field")):
                raise McpConfigurationError("Referência de vault do perfil Forward Email incompleta.")
            if profile["credential_field"] not in {"password", "notes"}:
                raise McpConfigurationError("Campo KeePass do perfil Forward Email inválido.")
            if not isinstance(profile.get("domain", ""), str):
                raise McpConfigurationError("Domínio do perfil Forward Email inválido.")

    def _validate_notion(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis Notion inválida.")
        defaults = data["defaults"]
        if defaults.get("api_base") != "https://api.notion.com" or defaults.get("notion_version") != "2026-03-11":
            raise McpConfigurationError("Endpoint ou versão Notion não suportados.")
        if not isinstance(defaults.get("timeout_seconds"), int) or defaults["timeout_seconds"] <= 0 or not isinstance(defaults.get("max_retries"), int) or defaults["max_retries"] < 0 or not isinstance(defaults.get("page_size"), int) or not 1 <= defaults["page_size"] <= 100:
            raise McpConfigurationError("Timeout, tentativas ou paginação Notion inválidos.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil Notion inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil Notion é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "credential_field")):
                raise McpConfigurationError("Referência de vault do perfil Notion incompleta.")
            if profile["credential_field"] not in {"password", "notes"}:
                raise McpConfigurationError("Campo KeePass do perfil Notion inválido.")
            roots = profile.get("readable_roots")
            if not isinstance(roots, list) or not all(isinstance(value, str) and value.strip() for value in roots):
                raise McpConfigurationError("Raízes de leitura do perfil Notion inválidas.")

    def _validate_aws(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis AWS inválida.")
        defaults = data["defaults"]
        if not isinstance(defaults.get("timeout_seconds"), int) or defaults["timeout_seconds"] <= 0 or not isinstance(defaults.get("max_attempts"), int) or defaults["max_attempts"] <= 0:
            raise McpConfigurationError("Timeout ou tentativas AWS inválidos.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil AWS inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil AWS é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("credential_profile", "credential_entry_path", "access_key_id_field", "secret_access_key_field", "cli_path")):
                raise McpConfigurationError("Credencial ou executável do perfil AWS incompleto.")
            if profile["access_key_id_field"] not in {"username", "password", "url", "notes"} or profile["secret_access_key_field"] not in {"username", "password", "url", "notes"}:
                raise McpConfigurationError("Campos KeePass do perfil AWS inválidos.")
            region = profile.get("region", "")
            account = profile.get("expected_account_id", "")
            if not isinstance(region, str) or not isinstance(account, str) or account and (len(account) != 12 or not account.isascii() or not account.isdigit()):
                raise McpConfigurationError("Região ou conta esperada do perfil AWS inválida.")
            for key in ("readable_roots", "writable_roots"):
                roots = profile.get(key)
                if not isinstance(roots, list) or not all(isinstance(value, str) and value.strip() for value in roots):
                    raise McpConfigurationError("Raízes de arquivos do perfil AWS inválidas.")

    def _validate_mysql(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict): raise McpConfigurationError("Estrutura de perfis MySQL inválida.")
        defaults = data["defaults"]
        if not isinstance(defaults.get("executable"), str) or not defaults["executable"] or not isinstance(defaults.get("port"), int) or not 1 <= defaults["port"] <= 65535 or not isinstance(defaults.get("connect_timeout"), int) or defaults["connect_timeout"] <= 0 or not isinstance(defaults.get("timeout_seconds"), int) or defaults["timeout_seconds"] <= 0: raise McpConfigurationError("Configuração operacional MySQL inválida.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool: raise McpConfigurationError("Estado do perfil MySQL inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240: raise McpConfigurationError("Descrição do perfil MySQL é obrigatória e deve ter até 240 caracteres.")
            if not all(isinstance(profile.get(key), str) and profile[key] for key in ("host", "credential_profile", "credential_entry_path", "username_field", "password_field")): raise McpConfigurationError("Conexão ou credencial MySQL incompleta.")
            if profile["username_field"] not in {"username", "password", "url", "notes"} or profile["password_field"] not in {"username", "password", "url", "notes"}: raise McpConfigurationError("Campos KeePass do perfil MySQL inválidos.")
            if not isinstance(profile.get("database", ""), str) or not isinstance(profile.get("socket", ""), str) or type(profile.get("allow_client_commands")) is not bool: raise McpConfigurationError("Opções do perfil MySQL inválidas.")
            roots = profile.get("readable_roots")
            if not isinstance(roots, list) or not all(isinstance(value, str) and value.strip() for value in roots): raise McpConfigurationError("Raízes de scripts MySQL inválidas.")

    def _validate_eccovox(self, data: dict[str, Any]) -> None:
        if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
            raise McpConfigurationError("Estrutura de perfis EccoVox inválida.")
        defaults = data["defaults"]
        for key, maximum in (("request_timeout_seconds", 300), ("max_audio_bytes", 104857600), ("max_text_characters", 100000)):
            value = defaults.get(key)
            if not isinstance(value, int) or not 1 <= value <= maximum:
                raise McpConfigurationError("Limites padrão EccoVox inválidos.")
        for name, profile in data["profiles"].items():
            _profile_name(name)
            if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
                raise McpConfigurationError("Estado do perfil EccoVox inválido.")
            if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
                raise McpConfigurationError("Descrição do perfil EccoVox é obrigatória e deve ter até 240 caracteres.")
            base_url = profile.get("base_url")
            if not isinstance(base_url, str) or not base_url.strip():
                raise McpConfigurationError("Endpoint EccoVox inválido.")
            for key in ("readable_roots", "writable_roots"):
                roots = profile.get(key)
                if not isinstance(roots, list) or not all(isinstance(value, str) and value.strip() for value in roots):
                    raise McpConfigurationError("Raízes de arquivos EccoVox inválidas.")

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

    @staticmethod
    def _render_cloudflare(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"api_base = {_quote(defaults['api_base'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"credential_field = {_quote(profile['credential_field'])}", f"zone_id = {_quote(profile.get('zone_id', ''))}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_forward_email(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"api_base = {_quote(defaults['api_base'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"credential_field = {_quote(profile['credential_field'])}", f"domain = {_quote(profile.get('domain', ''))}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_notion(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"api_base = {_quote(defaults['api_base'])}", f"notion_version = {_quote(defaults['notion_version'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}", f"page_size = {defaults['page_size']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"credential_field = {_quote(profile['credential_field'])}", f"readable_roots = {_array(profile['readable_roots'])}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_aws(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_attempts = {defaults['max_attempts']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"access_key_id_field = {_quote(profile['access_key_id_field'])}", f"secret_access_key_field = {_quote(profile['secret_access_key_field'])}", f"cli_path = {_quote(profile['cli_path'])}", f"region = {_quote(profile.get('region', ''))}", f"expected_account_id = {_quote(profile.get('expected_account_id', ''))}", f"readable_roots = {_array(profile['readable_roots'])}", f"writable_roots = {_array(profile['writable_roots'])}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_mysql(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"executable = {_quote(defaults['executable'])}", f"port = {defaults['port']}", f"connect_timeout = {defaults['connect_timeout']}", f"timeout_seconds = {defaults['timeout_seconds']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"host = {_quote(profile['host'])}", f"database = {_quote(profile.get('database', ''))}", f"socket = {_quote(profile.get('socket', ''))}", f"allow_client_commands = {'true' if profile['allow_client_commands'] else 'false'}", f"credential_profile = {_quote(profile['credential_profile'])}", f"credential_entry_path = {_quote(profile['credential_entry_path'])}", f"username_field = {_quote(profile['username_field'])}", f"password_field = {_quote(profile['password_field'])}", f"readable_roots = {_array(profile['readable_roots'])}"]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _render_eccovox(data: dict[str, Any]) -> str:
        defaults = data["defaults"]
        lines = ["schema_version = 1", "", "[defaults]", f"request_timeout_seconds = {defaults['request_timeout_seconds']}", f"max_audio_bytes = {defaults['max_audio_bytes']}", f"max_text_characters = {defaults['max_text_characters']}"]
        for name, profile in sorted(data["profiles"].items()):
            lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {_quote(profile['description'])}", f"base_url = {_quote(profile['base_url'])}", f"readable_roots = {_array(profile['readable_roots'])}", f"writable_roots = {_array(profile['writable_roots'])}"]
        return "\n".join(lines) + "\n"
