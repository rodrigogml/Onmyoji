"""Contrato de aplicativos e perfis Mercado."""
from __future__ import annotations

import copy
import tomllib

import pytest

from onmyoji_daemon.mcp.mercado.configuration import empty_configuration, render_configuration, validate_configuration
from onmyoji_daemon.mcp.mercado.exception import MercadoError


def configured() -> dict:
    data = empty_configuration()
    for provider in ("mercadolivre", "mercadopago"):
        data["applications"][provider]["main"] = {
            "environment": "production", "credential_profile": "vault", "credential_entry_path": f"Applications/{provider}",
            "redirect_uri": f"https://apps.example.com/oauth/mcp/{provider}/callback", "scopes": ["read", "write", "offline_access"],
        }
    data["profiles"]["personal"] = {
        "enabled": True, "description": "Conta pessoal.",
        "modules": ["mercadolivre.account", "mercadopago.payments"],
        "mercadolivre": {"application": "main", "site_id": "MLB", "auth_mode": "authorization_code", "authorization_credential_profile": "vault", "authorization_entry_path": "Authorizations/ml"},
        "mercadopago": {"application": "main", "site_id": "MLB", "auth_mode": "client_credentials"},
    }
    return data


def test_configuration_round_trip_preserves_applications_profiles_and_modules():
    data = configured()
    validate_configuration(data, {"vault"})
    parsed = tomllib.loads(render_configuration(data))
    assert parsed == data


def test_enabled_profiles_require_modules_provider_application_and_vault():
    data = configured()
    data["profiles"]["personal"]["modules"] = []
    with pytest.raises(MercadoError, match="pelo menos um módulo"):
        validate_configuration(data, {"vault"})
    data = configured(); del data["profiles"]["personal"]["mercadopago"]
    with pytest.raises(MercadoError, match="não configura o provedor"):
        validate_configuration(data, {"vault"})
    data = configured(); data["profiles"]["personal"]["mercadolivre"]["application"] = "missing"
    with pytest.raises(MercadoError) as failure:
        validate_configuration(data, {"vault"})
    assert failure.value.code == "application_not_found"
    data = configured(); data["applications"]["mercadolivre"]["main"]["credential_profile"] = "missing"
    with pytest.raises(MercadoError, match="vault inexistente"):
        validate_configuration(data, {"vault"})


def test_disabled_profile_may_have_no_modules_but_present_provider_is_validated():
    data = configured(); profile = data["profiles"]["personal"]
    profile["enabled"] = False; profile["modules"] = []; profile.pop("mercadolivre"); profile.pop("mercadopago")
    validate_configuration(data, {"vault"})
    invalid = copy.deepcopy(data); invalid["profiles"]["personal"]["mercadopago"] = {"application": "missing", "site_id": "MLB", "auth_mode": "client_credentials"}
    with pytest.raises(MercadoError):
        validate_configuration(invalid, {"vault"})


def test_disabled_profile_may_be_saved_without_description():
    data = configured(); profile = data["profiles"]["personal"]
    profile.update({"enabled": False, "description": "", "modules": []})
    profile.pop("mercadolivre"); profile.pop("mercadopago")
    validate_configuration(data, {"vault"})


def test_auth_modes_and_redirects_are_closed():
    data = configured(); data["profiles"]["personal"]["mercadolivre"]["auth_mode"] = "client_credentials"
    with pytest.raises(MercadoError, match="exige authorization_code"):
        validate_configuration(data, {"vault"})
    data = configured(); data["applications"]["mercadopago"]["main"]["redirect_uri"] = "http://remote.example/callback"
    with pytest.raises(MercadoError, match="Redirect URI"):
        validate_configuration(data, {"vault"})
