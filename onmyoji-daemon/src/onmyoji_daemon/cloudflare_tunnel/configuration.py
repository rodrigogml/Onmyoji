"""Configuração local do Tunnel, contendo somente referências de credenciais."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import tomllib
from typing import Any
from urllib.parse import urlsplit

from ..config_publication import publish_file
from ..mcp.credentials import CredentialError, KeePassCredentialResolver
from .exception import TunnelError


@dataclass(frozen=True)
class TunnelDefinition:
    account_id: str
    zone_id: str
    hostname: str
    tunnel_id: str = ""
    tunnel_name: str = "onmyoji-gateway"

    def validate(self) -> None:
        """Rejeita identificadores incompletos e hostnames que não sejam DNS exato."""
        for value in (self.account_id, self.zone_id):
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise TunnelError("invalid_definition", "account_id e zone_id são obrigatórios e não podem conter separadores.")
        if not isinstance(self.tunnel_id, str) or (self.tunnel_id and not re.fullmatch(r"[A-Za-z0-9_-]+", self.tunnel_id)):
            raise TunnelError("invalid_definition", "Identificador de Tunnel inválido.")
        if not isinstance(self.hostname, str) or len(self.hostname) > 253 or "." not in self.hostname:
            raise TunnelError("invalid_hostname", "Hostname público inválido.")
        if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in self.hostname.split(".")):
            raise TunnelError("invalid_hostname", "Hostname público deve ser um nome DNS exato em minúsculas.")
        if not isinstance(self.tunnel_name, str) or not self.tunnel_name.strip() or len(self.tunnel_name) > 100:
            raise TunnelError("invalid_definition", "Nome do Tunnel inválido.")


def validate_origin(gateway_url: str, ca_file: str, require_ca: bool = True) -> None:
    """Aceita somente a origem HTTPS de loopback, com CA explícita quando ativa."""
    if not isinstance(gateway_url, str):
        raise TunnelError("invalid_origin", "A origem deve ser uma URL HTTPS de loopback com porta explícita.")
    try:
        value = urlsplit(gateway_url)
        valid = value.scheme == "https" and value.hostname in {"localhost", "127.0.0.1", "::1"} and value.port is not None and value.port > 0
    except (ValueError, TypeError):
        valid = False
        value = None
    if not valid or value.username is not None or value.password is not None or value.path not in {"", "/"} or value.query or value.fragment:
        raise TunnelError("invalid_origin", "A origem deve ser uma URL HTTPS de loopback com porta explícita.")
    if require_ca and (not isinstance(ca_file, str) or not ca_file or not Path(ca_file).is_absolute() or not Path(ca_file).is_file()):
        raise TunnelError("invalid_ca", "O arquivo absoluto da CA privada do Gateway é obrigatório.")


def write_local_json(root: Path, path: Path, value: Any) -> None:
    """Substitui um estado local atomicamente, usando staging exclusivamente em tmp/."""
    atomic_text(root, path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_text(root: Path, path: Path, content: str) -> None:
    temporary_root = root / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="cloudflare-tunnel-", dir=temporary_root)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        publish_file(Path(temporary), path, mode=0o660)
    finally:
        Path(temporary).unlink(missing_ok=True)


class TunnelConfiguration:
    """Mescla alterações locais sem apagar IDs, referências ou versão instalada."""

    STRING_KEYS = ("account_id", "zone_id", "hostname", "tunnel_id", "tunnel_name", "credential_profile", "credential_entry_path", "credential_field", "cloudflared_executable", "gateway_url", "ca_file", "provisioned_gateway_url", "provisioned_ca_file", "managed_version", "managed_digest", "install_directory", "previous_executable", "previous_version", "previous_digest")
    EXECUTABLE_KEYS = frozenset(("cloudflared_executable", "managed_version", "managed_digest", "install_directory", "previous_executable", "previous_version", "previous_digest"))
    DEFAULTS = {"schema_version": 1, "tunnel_id": "", "tunnel_name": "onmyoji-gateway", "credential_field": "password", "cloudflared_executable": "cloudflared", "gateway_url": "https://localhost:39390", "ca_file": "", "timeout_seconds": 30}

    def __init__(self, root: Path, data_dir: Path | None = None, credentials: KeePassCredentialResolver | None = None):
        self.root = root.resolve()
        self.path = (data_dir or self.root / "configs" / "daemon" / "services" / "cloudflare-tunnel") / "tunnel.toml"
        self.credentials = credentials or KeePassCredentialResolver(self.root)
        self.lock = threading.RLock()

    def settings(self) -> dict[str, Any]:
        # A substituição atômica permite consultar status durante validação do cofre.
        try:
            values = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise TunnelError("invalid_config", "Não foi possível ler a configuração do Tunnel.") from error
        if type(values.get("schema_version")) is not int or values["schema_version"] != 1:
            raise TunnelError("unsupported_config", "Versão de configuração do Tunnel não suportada.")
        return values

    @classmethod
    def is_configured(cls, values: dict[str, Any]) -> bool:
        """Distingue um Tunnel definido de metadados locais apenas do instalador."""
        effective = {**cls.DEFAULTS, **values}
        if any(not isinstance(effective.get(key), str) or not effective[key].strip() for key in ("account_id", "zone_id", "hostname", "credential_profile", "credential_entry_path")):
            return False
        try:
            TunnelDefinition(*(effective[key] for key in ("account_id", "zone_id", "hostname", "tunnel_id", "tunnel_name"))).validate()
        except TunnelError:
            return False
        return effective.get("credential_field") == "password"

    def _persist(self, values: dict[str, Any]) -> None:
        lines = ["schema_version = 1"]
        lines.extend(f"{key} = {json.dumps(values[key], ensure_ascii=False)}" for key in self.STRING_KEYS if key in values)
        lines.append(f"timeout_seconds = {values['timeout_seconds']}")
        atomic_text(self.root, self.path, "\n".join(lines) + "\n")

    def configure_executable(self, values: dict[str, Any]) -> dict[str, Any]:
        """Persiste somente a seleção do binário, sem exigir Tunnel, Gateway ou cofre.

        Campos de identidade, origem e credenciais continuam exclusivos de
        ``configure``. Isso permite instalar e reverter versões antes de iniciar
        a configuração de uma publicação externa.
        """
        if not isinstance(values, dict) or set(values) - self.EXECUTABLE_KEYS:
            raise TunnelError("invalid_config", "A instalação aceita somente metadados do executável cloudflared.")
        with self.lock:
            merged = {**self.DEFAULTS, **self.settings(), **values}
            for key in self.EXECUTABLE_KEYS:
                if key in merged:
                    if not isinstance(merged[key], str):
                        raise TunnelError("invalid_config", "Os metadados do executável devem ser strings.")
                    merged[key] = merged[key].strip()
            if not merged["cloudflared_executable"]:
                raise TunnelError("invalid_executable", "Executável cloudflared obrigatório.")
            if merged.get("install_directory") and not Path(merged["install_directory"]).is_absolute():
                raise TunnelError("invalid_install_directory", "O diretório de instalação deve ser absoluto.")
            for key in ("managed_version", "previous_version"):
                if merged.get(key) and not re.fullmatch(r"v?\d{4}\.\d+\.\d+", merged[key]):
                    raise TunnelError("invalid_release", "Identificador da versão oficial inválido.")
            for key in ("managed_digest", "previous_digest"):
                if merged.get(key) and not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", merged[key]):
                    raise TunnelError("invalid_digest", "SHA-256 registrado do executável inválido.")
            self._persist(merged)
            return merged

    def configure(self, values: dict[str, Any], validate_credentials: bool = True) -> dict[str, Any]:
        """Persiste a configuração mesclada; nunca aceita tokens ou campos desconhecidos."""
        if not isinstance(values, dict) or set(values) - {*self.STRING_KEYS, "schema_version", "timeout_seconds"}:
            raise TunnelError("invalid_config", "Configuração contém campos desconhecidos ou credenciais diretas.")
        with self.lock:
            previous = self.settings()
            merged = {**self.DEFAULTS, **previous, **values}
            if type(merged.get("schema_version")) is not int or merged["schema_version"] != 1:
                raise TunnelError("unsupported_config", "Versão de configuração do Tunnel não suportada.")
            for key in self.STRING_KEYS:
                if key in merged:
                    if not isinstance(merged[key], str):
                        raise TunnelError("invalid_config", "Os campos textuais da configuração devem ser strings.")
                    merged[key] = merged[key].strip()
            for key in ("account_id", "zone_id", "hostname", "credential_profile", "credential_entry_path"):
                if not merged.get(key):
                    raise TunnelError("incomplete_config", "Configuração do Tunnel incompleta.")
            merged["hostname"] = merged["hostname"].lower()
            if any(key in values and merged[key] != previous.get(key) for key in ("account_id", "zone_id", "hostname", "tunnel_id")):
                # Um ID preservado não comprova que a nova origem já foi aplicada.
                for key in ("provisioned_gateway_url", "provisioned_ca_file"):
                    if key not in values:
                        merged.pop(key, None)
            TunnelDefinition(*(merged[key] for key in ("account_id", "zone_id", "hostname", "tunnel_id", "tunnel_name"))).validate()
            if merged["credential_field"] not in {"password"}:
                raise TunnelError("invalid_credential_field", "O token do Tunnel deve usar o campo password protegido do KeePass.")
            if type(merged["timeout_seconds"]) is not int or not 1 <= merged["timeout_seconds"] <= 120:
                raise TunnelError("invalid_timeout", "Timeout Cloudflare deve ficar entre 1 e 120 segundos.")
            if not merged["cloudflared_executable"]:
                raise TunnelError("invalid_executable", "Executável cloudflared obrigatório.")
            validate_origin(merged["gateway_url"], merged["ca_file"], require_ca=False)
            if validate_credentials:
                try:
                    available = self.credentials.password_exists(merged["credential_profile"], merged["credential_entry_path"])
                except CredentialError as error:
                    raise TunnelError("credential_unavailable", "Não foi possível validar a referência KeePass do Tunnel.") from error
                if not available:
                    raise TunnelError("credential_missing", "A entrada do cofre MCP não contém o token do Tunnel.")
            self._persist(merged)
            return merged
