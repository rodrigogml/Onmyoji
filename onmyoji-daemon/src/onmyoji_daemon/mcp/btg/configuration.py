"""Configuração privada dos perfis BTG Empresas no MCP."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time
from typing import Any
from urllib.parse import urlsplit


DEFAULT_SCOPES = "openid empresas.btgpactual.com/accounts.readonly brn:btg:empresas:banking:payments brn:btg:empresas:banking:direct-debit"


@contextmanager
def configuration_lock(root: Path):
    """Serializa gravações do menu e do callback com trava liberada pelo SO."""
    path = root / "configs" / "mcp" / "btg-config.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0"); stream.flush()
        deadline = time.monotonic() + 30
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BtgConfigurationError("Outra operação está alterando os perfis BTG.")
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class BtgConfigurationError(ValueError):
    """Configuração BTG MCP inválida, sem incluir valores sensíveis no erro."""


def empty_configuration() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": {"timeout_seconds": 30}, "profiles": {}}


def validate_profile(name: str, profile: dict[str, Any]) -> None:
    """Valida identidade e referências do perfil sem abrir o cofre ou iniciar OAuth."""
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", name):
        raise BtgConfigurationError("O nome do perfil deve usar apenas letras, números, hífen ou sublinhado.")
    if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
        raise BtgConfigurationError("Estado do perfil BTG inválido.")
    description = profile.get("description")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 240:
        raise BtgConfigurationError("Descrição do perfil BTG é obrigatória e deve ter até 240 caracteres.")
    if not isinstance(profile.get("environment"), str) or profile["environment"] not in {"sandbox", "production"}:
        raise BtgConfigurationError("Selecione sandbox ou production para o BTG.")
    company = profile.get("company_id")
    if not isinstance(company, str) or not re.fullmatch(r"[0-9]{14}", company):
        raise BtgConfigurationError("O CNPJ da empresa deve conter 14 dígitos.")
    for field in ("credential_profile", "credential_entry_path"):
        if not isinstance(profile.get(field), str) or not profile[field].strip():
            raise BtgConfigurationError("Referência de vault do perfil BTG incompleta.")
    for field in ("account_id", "account_number", "branch_code"):
        value = profile.get(field, "")
        if not isinstance(value, str) or (value and (len(value) > 2000 or any(ord(char) < 32 for char in value))):
            raise BtgConfigurationError("Identificadores da conta BTG inválidos.")
        if field != "account_id" and value and not re.fullmatch(r"[0-9]+", value):
            raise BtgConfigurationError("Número da conta e agência BTG devem conter somente dígitos.")
    if profile.get("account_id") and not (profile.get("account_number") and profile.get("branch_code")):
        raise BtgConfigurationError("Conta identificada exige número e agência.")
    redirect = profile.get("redirect_uri", "")
    if not isinstance(redirect, str):
        raise BtgConfigurationError("URI de retorno OAuth BTG inválida.")
    if redirect:
        try:
            uri = urlsplit(redirect)
            valid = uri.scheme == "https" or uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1"}
            if not valid or not uri.hostname or uri.username or uri.password or uri.fragment or any(ord(char) < 32 for char in redirect):
                raise ValueError()
        except ValueError as error:
            raise BtgConfigurationError("URI de retorno deve usar HTTPS ou HTTP de loopback, sem usuário, senha ou fragmento.") from error
    if not isinstance(profile.get("oauth_mode", "remote"), str) or profile.get("oauth_mode", "remote") not in {"remote", "manual"}:
        raise BtgConfigurationError("Modo OAuth BTG inválido.")
    scopes = profile.get("scopes", DEFAULT_SCOPES)
    if not isinstance(scopes, str) or not set(DEFAULT_SCOPES.split()[:3]) <= set(scopes.split()):
        raise BtgConfigurationError("Escopos obrigatórios do aplicativo BTG ausentes.")
    amount = profile.get("max_amount", "1000000.00")
    try:
        if not isinstance(amount, str):
            raise ValueError()
        parsed = Decimal(amount)
        if not parsed.is_finite() or not Decimal(0) < parsed <= Decimal("999999999999.99") or parsed != parsed.quantize(Decimal("0.01")):
            raise ValueError()
    except (ValueError, InvalidOperation) as error:
        raise BtgConfigurationError("Limite BTG deve ser texto decimal positivo com até duas casas.") from error
    if type(profile.get("monitor_enabled", False)) is not bool:
        raise BtgConfigurationError("Estado do monitor BTG inválido.")


def validate_configuration(data: dict[str, Any]) -> None:
    if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), dict) or not isinstance(data.get("profiles"), dict):
        raise BtgConfigurationError("Estrutura de perfis BTG inválida.")
    timeout = data["defaults"].get("timeout_seconds")
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise BtgConfigurationError("Timeout BTG deve ficar entre 1 e 60 segundos.")
    for name, profile in data["profiles"].items():
        validate_profile(name, profile)


def render_configuration(data: dict[str, Any]) -> str:
    lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {data['defaults']['timeout_seconds']}"]
    defaults = {"account_id": "", "account_number": "", "branch_code": "", "redirect_uri": "", "oauth_mode": "remote", "scopes": DEFAULT_SCOPES, "max_amount": "1000000.00", "monitor_enabled": False}
    fields = ("enabled", "description", "environment", "company_id", "account_id", "account_number", "branch_code", "credential_profile", "credential_entry_path", "redirect_uri", "oauth_mode", "scopes", "max_amount", "monitor_enabled")
    for name, profile in sorted(data["profiles"].items()):
        lines += ["", f"[profiles.{name}]"]
        for key in fields:
            lines.append(f"{key} = {json.dumps(profile.get(key, defaults.get(key)), ensure_ascii=False)}")
    return "\n".join(lines) + "\n"
