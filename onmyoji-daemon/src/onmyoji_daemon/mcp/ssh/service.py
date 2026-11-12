"""Sessões SSH com host pinning, cofre interno e arquivos locais confinados."""
from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
import tempfile
import tomllib
from typing import Any, Mapping
import uuid

from ..credentials import CredentialError, KeePassCredentialResolver


OPERATIONS = {"exec", "upload", "download"}


class SshError(ValueError):
    def __init__(self, code: str, message: str): super().__init__(message); self.code = code


def host_key_name(host: str, port: int) -> str:
    return host if port == 22 else f"[{host}]:{port}"


def fetch_host_key(host: str, port: int, timeout: float = 12) -> tuple[Any, str]:
    """Obtém a chave apresentada e sua impressão SHA256, sem confiar nela."""
    try:
        import paramiko
        transport = paramiko.Transport((host, port))
        try: transport.start_client(timeout=timeout); key = transport.get_remote_server_key()
        finally: transport.close()
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode("ascii").rstrip("=")
        return key, f"SHA256:{fingerprint}"
    except Exception as error:
        raise SshError("host_key_unavailable", "Não foi possível obter a chave pública do servidor SSH.") from error


def save_host_key(path: Path, host: str, port: int, key: Any) -> None:
    """Adiciona a chave explicitamente confirmada sem substituir divergências."""
    import paramiko
    target = path.expanduser().resolve(); keys = paramiko.HostKeys()
    try:
        if target.exists(): keys.load(str(target))
        name = host_key_name(host, port); known = keys.lookup(name)
        if known and key.get_name() in known and known[key.get_name()].asbytes() != key.asbytes():
            raise SshError("host_key_changed", "A chave apresentada difere da chave já registrada.")
        target.parent.mkdir(parents=True, exist_ok=True); keys.add(name, key.get_name(), key); keys.save(str(target))
    except SshError: raise
    except Exception as error: raise SshError("known_hosts_write_failed", "Não foi possível gravar o arquivo known_hosts.") from error


class SshService:
    """Executa comandos e transferências sem expor senha, chave ou passphrase."""

    def __init__(self, onmyoji_root: Path, credentials: KeePassCredentialResolver | None = None):
        self.root = onmyoji_root.resolve(); self.path = self.root / "configs" / "mcp" / "ssh.toml"; self.credentials = credentials or KeePassCredentialResolver(self.root)

    def test_connection(self, profile_name: str) -> dict[str, Any]:
        profile, defaults = self._profile(profile_name)
        with self._session(profile, defaults) as client: pass
        return {"profile": profile_name, "authenticated": True, "host": profile["host"], "port": profile["port"]}

    def invoke(self, profile_name: str, operation: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if operation not in OPERATIONS: raise SshError("unsupported_operation", "Operação SSH não suportada.")
        if arguments.get("confirm") is not True: raise SshError("confirmation_required", "Operações SSH exigem confirm=true.")
        profile, defaults = self._profile(profile_name)
        if operation == "exec":
            command = arguments.get("command")
            if not isinstance(command, str) or not command.strip(): raise SshError("invalid_request", "command é obrigatório.")
            try:
                with self._session(profile, defaults) as client:
                    _stdin, stdout, stderr = client.exec_command(command, timeout=defaults["timeout_seconds"])
                    return {"stdout": stdout.read().decode("utf-8", "replace"), "stderr": stderr.read().decode("utf-8", "replace"), "exit_code": stdout.channel.recv_exit_status()}
            except SshError: raise
            except Exception as error: raise SshError("ssh_command_failed", "O comando remoto SSH falhou.") from error
        source, destination = arguments.get("source"), arguments.get("destination")
        if not isinstance(source, str) or not source.strip() or not isinstance(destination, str) or not destination.strip(): raise SshError("invalid_request", "source e destination são obrigatórios.")
        local = self._allowed(profile, source if operation == "upload" else destination, "readable_roots" if operation == "upload" else "writable_roots", operation == "upload")
        if operation == "upload" and not local.is_file(): raise SshError("file_not_found", "A origem local não é um arquivo.")
        if operation == "download" and local.exists() and arguments.get("overwrite") is not True: raise SshError("destination_exists", "O destino local existe; use overwrite=true.")
        try:
            with self._session(profile, defaults) as client:
                sftp = client.open_sftp()
                try:
                    if operation == "upload":
                        sftp.put(str(local), destination)
                    else:
                        local.parent.mkdir(parents=True, exist_ok=True); partial = local.with_name(local.name + ".onmyoji-part-" + uuid.uuid4().hex)
                        try: sftp.get(source, str(partial)); os.replace(partial, local)
                        finally: partial.unlink(missing_ok=True)
                finally: sftp.close()
        except SshError: raise
        except Exception as error: raise SshError("sftp_failed", "A transferência SFTP falhou.") from error
        return {"operation": operation, "completed": True}

    def _profile(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        try: document = tomllib.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError as error: raise SshError("config_not_found", "Configuração SSH MCP não encontrada.") from error
        except (OSError, tomllib.TOMLDecodeError) as error: raise SshError("invalid_config", "Configuração SSH MCP inválida.") from error
        profile, defaults = document.get("profiles", {}).get(name), document.get("defaults", {})
        if document.get("schema_version") != 1 or not isinstance(profile, dict) or not isinstance(defaults, dict): raise SshError("profile_not_found", "Perfil SSH não encontrado.")
        if profile.get("enabled") is not True: raise SshError("profile_disabled", "Perfil SSH está desabilitado.")
        timeout = defaults.get("timeout_seconds")
        if not isinstance(timeout, int) or timeout <= 0 or not isinstance(profile.get("port"), int) or not 1 <= profile["port"] <= 65535 or profile.get("auth_mode") not in {"password", "key"}:
            raise SshError("invalid_config", "Configuração operacional SSH inválida.")
        known_hosts = Path(profile.get("known_hosts", "")).expanduser()
        if not known_hosts.is_file(): raise SshError("known_hosts_missing", "O arquivo known_hosts do perfil SSH não existe.")
        return profile, defaults

    def _connect(self, profile: Mapping[str, Any], defaults: Mapping[str, Any], key_path: Path | None):
        import paramiko
        client = paramiko.SSHClient()
        try:
            client.load_host_keys(str(Path(profile["known_hosts"]).expanduser().resolve())); client.set_missing_host_key_policy(paramiko.RejectPolicy())
            options: dict[str, Any] = {"hostname": profile["host"], "port": profile["port"], "username": profile["username"], "timeout": defaults["timeout_seconds"], "allow_agent": False, "look_for_keys": False}
            if profile["auth_mode"] == "password": options["password"] = self._secret(profile, profile["password_field"])
            else:
                options["key_filename"] = str(key_path)
                if profile.get("passphrase_field"): options["passphrase"] = self._secret(profile, profile["passphrase_field"])
            client.connect(**options); return client
        except paramiko.AuthenticationException as error: client.close(); raise SshError("ssh_authentication", "O servidor SSH recusou a autenticação.") from error
        except (paramiko.SSHException, OSError) as error: client.close(); raise SshError("ssh_connection", "Não foi possível conectar ao servidor SSH.") from error

    def _session(self, profile: Mapping[str, Any], defaults: Mapping[str, Any]):
        service = self
        class Session:
            temporary: tempfile.TemporaryDirectory[str] | None = None
            client: Any = None
            def __enter__(self):
                key_path = None
                if profile["auth_mode"] == "key":
                    self.temporary = tempfile.TemporaryDirectory(prefix="onmyoji-ssh-key-")
                    key_path = Path(self.temporary.name) / "private-key"
                    try: service.credentials.export_attachment(profile["credential_profile"], profile["credential_entry_path"], profile["key_attachment"], key_path)
                    except CredentialError as error: self.temporary.cleanup(); raise SshError("credential_" + error.code, "Não foi possível obter a chave SSH.") from error
                try:
                    self.client = service._connect(profile, defaults, key_path); return self.client
                except Exception:
                    if self.temporary: self.temporary.cleanup()
                    raise
            def __exit__(self, *_args):
                if self.client: self.client.close()
                if self.temporary: self.temporary.cleanup()
        return Session()

    def _secret(self, profile: Mapping[str, Any], field: str) -> str:
        try: return self.credentials.resolve(profile["credential_profile"], profile["credential_entry_path"], field)
        except CredentialError as error: raise SshError("credential_" + error.code, "Não foi possível obter a credencial SSH.") from error

    @staticmethod
    def _allowed(profile: Mapping[str, Any], value: str, roots_key: str, must_exist: bool) -> Path:
        try: candidate = Path(value).expanduser().resolve(strict=must_exist)
        except OSError as error: raise SshError("file_not_found", "O caminho local não existe.") from error
        for configured in profile.get(roots_key, []):
            try: root = Path(configured).expanduser().resolve(strict=True)
            except OSError: continue
            if root.is_dir() and (candidate == root or root in candidate.parents): return candidate
        raise SshError("file_denied", "O caminho local está fora das raízes permitidas do perfil SSH.")
