"""Contrato persistente de aplicativos e perfis Mercado."""
from __future__ import annotations

import json
from pathlib import Path
import re
import tomllib
from typing import Any, Mapping
from urllib.parse import urlsplit

from .exception import MercadoError


PROVIDERS = ("mercadolivre", "mercadopago")
MODULES = (
    "mercadolivre.account",
    "mercadolivre.favorites",
    "mercadolivre.purchases",
    "mercadolivre.shipping",
    "mercadolivre.billing",
    "mercadopago.payments",
    "mercadopago.reports",
)
MODULE_PROVIDER = {module: module.split(".", 1)[0] for module in MODULES}
DEFAULTS = {
    "timeout_seconds": 30,
    "max_retries": 2,
    "page_size": 50,
    "max_page_size": 100,
    "max_response_bytes": 10485760,
    "max_report_bytes": 104857600,
    "report_cache_ttl_seconds": 900,
}
NAME = re.compile(r"^[A-Za-z0-9_-]+$")
SITE = re.compile(r"^ML[A-Z]$")


def config_path(root: Path) -> Path:
    return root.resolve() / "configs" / "mcp" / "mercado.toml"


def empty_configuration() -> dict[str, Any]:
    return {"schema_version": 1, "defaults": dict(DEFAULTS), "applications": {provider: {} for provider in PROVIDERS}, "profiles": {}}


def load_configuration(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.exists():
        return empty_configuration()
    try:
        loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise MercadoError("invalid_config", "Configuração Mercado ausente ou inválida.") from error
    baseline = empty_configuration()
    baseline.update(loaded)
    applications = baseline.get("applications")
    if isinstance(applications, dict):
        for provider in PROVIDERS:
            applications.setdefault(provider, {})
    validate_configuration(baseline)
    return baseline


def validate_configuration(data: Mapping[str, Any], vault_profiles: set[str] | None = None) -> None:
    if data.get("schema_version") != 1 or not isinstance(data.get("defaults"), Mapping) or not isinstance(data.get("applications"), Mapping) or not isinstance(data.get("profiles"), Mapping):
        raise MercadoError("invalid_config", "Estrutura da configuração Mercado inválida.")
    defaults = data["defaults"]
    limits = {
        "timeout_seconds": (1, 300), "max_retries": (0, 10), "page_size": (1, 100),
        "max_page_size": (1, 1000), "max_response_bytes": (1024, 104857600),
        "max_report_bytes": (1024, 524288000), "report_cache_ttl_seconds": (1, 86400),
    }
    for field, (minimum, maximum) in limits.items():
        value = defaults.get(field)
        if type(value) is not int or not minimum <= value <= maximum:
            raise MercadoError("invalid_config", f"Valor padrão Mercado inválido: {field}.")
    if defaults["page_size"] > defaults["max_page_size"]:
        raise MercadoError("invalid_config", "page_size não pode exceder max_page_size.")
    applications = data["applications"]
    if set(applications) - set(PROVIDERS):
        raise MercadoError("invalid_config", "Provedor de aplicativo Mercado desconhecido.")
    for provider in PROVIDERS:
        catalog = applications.get(provider, {})
        if not isinstance(catalog, Mapping):
            raise MercadoError("invalid_config", "Catálogo de aplicativos Mercado inválido.")
        for name, application in catalog.items():
            _validate_name(name, "aplicativo")
            _validate_application(provider, application, vault_profiles)
    for name, profile in data["profiles"].items():
        _validate_name(name, "perfil")
        _validate_profile(name, profile, applications, vault_profiles)


def _validate_name(value: Any, label: str) -> None:
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise MercadoError("invalid_config", f"Nome de {label} Mercado inválido.")


def _validate_application(provider: str, application: Any, vault_profiles: set[str] | None) -> None:
    if not isinstance(application, Mapping) or application.get("environment") not in {"sandbox", "production"}:
        raise MercadoError("invalid_config", f"Aplicativo {provider} inválido.")
    for field in ("credential_profile", "credential_entry_path", "redirect_uri"):
        if not isinstance(application.get(field), str) or not application[field].strip():
            raise MercadoError("invalid_config", f"Aplicativo {provider} possui campo obrigatório ausente: {field}.")
    if vault_profiles is not None and application["credential_profile"] not in vault_profiles:
        raise MercadoError("invalid_config", f"Aplicativo {provider} referencia perfil de vault inexistente.")
    uri = urlsplit(application["redirect_uri"])
    loopback = uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1"}
    if not ((uri.scheme == "https" or loopback) and uri.hostname and not uri.username and not uri.password and not uri.query and not uri.fragment):
        raise MercadoError("invalid_config", f"Redirect URI do aplicativo {provider} inválida.")
    scopes = application.get("scopes")
    if not isinstance(scopes, list) or not scopes or len(scopes) != len(set(scopes)) or not all(item in {"read", "write", "offline_access"} for item in scopes):
        raise MercadoError("invalid_config", f"Escopos do aplicativo {provider} inválidos.")


def _validate_profile(name: str, profile: Any, applications: Mapping[str, Any], vault_profiles: set[str] | None) -> None:
    if not isinstance(profile, Mapping) or type(profile.get("enabled")) is not bool:
        raise MercadoError("invalid_config", f"Estado do perfil Mercado {name} inválido.")
    description = profile.get("description")
    if not isinstance(description, str) or len(description.strip()) > 240 or (profile["enabled"] and not description.strip()):
        raise MercadoError("invalid_config", f"Descrição do perfil Mercado {name} é obrigatória e deve ter até 240 caracteres.")
    modules = profile.get("modules")
    if not isinstance(modules, list) or len(modules) != len(set(modules)) or not all(item in MODULES for item in modules):
        raise MercadoError("invalid_config", f"Módulos do perfil Mercado {name} são inválidos.")
    if profile["enabled"] and not modules:
        raise MercadoError("invalid_config", f"Perfil Mercado {name} habilitado deve possuir pelo menos um módulo.")
    provider_keys = set(profile) & set(PROVIDERS)
    for provider in provider_keys:
        _validate_provider_profile(name, provider, profile[provider], applications, vault_profiles)
    if profile["enabled"]:
        for module in modules:
            provider = MODULE_PROVIDER[module]
            if provider not in provider_keys:
                raise MercadoError("invalid_config", f"Perfil Mercado {name} não configura o provedor exigido por {module}.")


def _validate_provider_profile(name: str, provider: str, configured: Any, applications: Mapping[str, Any], vault_profiles: set[str] | None) -> None:
    if not isinstance(configured, Mapping):
        raise MercadoError("invalid_config", f"Configuração {provider} do perfil {name} inválida.")
    application = configured.get("application")
    site = configured.get("site_id")
    if not isinstance(application, str) or application not in applications.get(provider, {}):
        raise MercadoError("application_not_found", f"Aplicativo {provider} do perfil {name} não foi encontrado.")
    if not isinstance(site, str) or not SITE.fullmatch(site):
        raise MercadoError("invalid_config", f"Site do perfil {provider} inválido.")
    auth_mode = configured.get("auth_mode", "authorization_code")
    if provider == "mercadolivre" and auth_mode != "authorization_code":
        raise MercadoError("invalid_config", "Mercado Livre exige authorization_code.")
    if provider == "mercadopago" and auth_mode not in {"authorization_code", "client_credentials"}:
        raise MercadoError("invalid_config", "Modo de autenticação Mercado Pago inválido.")
    if auth_mode == "authorization_code":
        for field in ("authorization_credential_profile", "authorization_entry_path"):
            if not isinstance(configured.get(field), str) or not configured[field].strip():
                raise MercadoError("invalid_config", f"Autorização {provider} do perfil {name} está incompleta.")
        if vault_profiles is not None and configured["authorization_credential_profile"] not in vault_profiles:
            raise MercadoError("invalid_config", f"Autorização {provider} referencia perfil de vault inexistente.")
    else:
        for field in ("authorization_credential_profile", "authorization_entry_path"):
            if field in configured and configured[field] not in {None, ""}:
                raise MercadoError("invalid_config", "Client Credentials não deve configurar entrada de autorização.")


def render_configuration(data: Mapping[str, Any]) -> str:
    validate_configuration(data)
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    lines = ["schema_version = 1", "", "[defaults]"]
    for field in DEFAULTS:
        lines.append(f"{field} = {data['defaults'][field]}")
    for provider in PROVIDERS:
        for name, application in sorted(data["applications"].get(provider, {}).items()):
            lines += ["", f"[applications.{provider}.{name}]", f"environment = {quote(application['environment'])}", f"credential_profile = {quote(application['credential_profile'])}", f"credential_entry_path = {quote(application['credential_entry_path'])}", f"redirect_uri = {quote(application['redirect_uri'])}", f"scopes = {quote(application['scopes'])}"]
    for name, profile in sorted(data["profiles"].items()):
        lines += ["", f"[profiles.{name}]", f"enabled = {'true' if profile['enabled'] else 'false'}", f"description = {quote(profile['description'])}", f"modules = {quote(profile['modules'])}"]
        for provider in PROVIDERS:
            if provider not in profile:
                continue
            configured = profile[provider]
            lines += ["", f"[profiles.{name}.{provider}]", f"application = {quote(configured['application'])}", f"site_id = {quote(configured['site_id'])}", f"auth_mode = {quote(configured.get('auth_mode', 'authorization_code'))}"]
            if configured.get("auth_mode", "authorization_code") == "authorization_code":
                lines += [f"authorization_credential_profile = {quote(configured['authorization_credential_profile'])}", f"authorization_entry_path = {quote(configured['authorization_entry_path'])}"]
    return "\n".join(lines) + "\n"


def enabled_profiles(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [{"name": name, **dict(profile)} for name, profile in sorted(data["profiles"].items()) if profile.get("enabled") is True]


def load_profile(root: Path, name: str, provider: str | None = None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    data = load_configuration(root)
    profile = data["profiles"].get(name)
    if not isinstance(profile, dict):
        raise MercadoError("profile_not_found", "Perfil Mercado não encontrado.", profile=name)
    if profile.get("enabled") is not True:
        raise MercadoError("profile_disabled", "Perfil Mercado está desabilitado.", profile=name)
    configured = None
    if provider is not None:
        configured = profile.get(provider)
        if not isinstance(configured, dict):
            raise MercadoError("profile_provider_unavailable", "O perfil não possui integração com este provedor.", profile=name, provider=provider)
    return profile, data["defaults"], configured
