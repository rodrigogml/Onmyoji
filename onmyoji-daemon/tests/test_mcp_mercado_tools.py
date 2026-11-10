from pathlib import Path

import pytest

from onmyoji_daemon.mcp.configuration import McpConfiguration
from onmyoji_daemon.mcp.mercado.exception import MercadoError
from onmyoji_daemon.mcp.mercado.tools import MercadoToolRegistry


def profile(name, modules, providers):
    value = {"name": name, "enabled": True, "description": f"Perfil {name}", "modules": modules}
    value.update({provider: {"auth_mode": mode} for provider, mode in providers.items()})
    return value


def registry(monkeypatch, profiles):
    configuration = McpConfiguration(Path("."))
    monkeypatch.setattr(configuration, "enabled_mercado_profiles", lambda: profiles)
    monkeypatch.setattr(configuration, "mercado", lambda: {"profiles": {item["name"]: item for item in profiles}})
    return MercadoToolRegistry(configuration)


def test_sem_perfil_nao_publica_ferramentas(monkeypatch):
    assert registry(monkeypatch, []).definitions() == []


def test_um_perfil_publica_uniao_sem_argumento_profile(monkeypatch):
    tools = registry(monkeypatch, [profile("pessoal", ["mercadolivre.purchases"], {"mercadolivre": "authorization_code"})]).definitions()
    names = {tool["name"] for tool in tools}
    assert "mercado.list_profiles" in names
    assert "mercadolivre.purchases_list" in names
    assert "mercadolivre.account_get" not in names
    assert "mercadopago.get_auth_status" not in names
    assert all("profile" not in tool["input_schema"]["properties"] for tool in tools)


def test_varios_perfis_publicam_uniao_com_todos_no_enum(monkeypatch):
    profiles = [
        profile("compras", ["mercadolivre.purchases"], {"mercadolivre": "authorization_code"}),
        profile("recebimentos", ["mercadopago.payments"], {"mercadopago": "client_credentials"}),
    ]
    tools = registry(monkeypatch, profiles).definitions()
    payment = next(tool for tool in tools if tool["name"] == "mercadopago.payment_get")
    assert payment["input_schema"]["properties"]["profile"]["enum"] == ["compras", "recebimentos"]
    assert "profile" in payment["input_schema"]["required"]


def test_perfil_sem_modulo_retorna_erro_estruturado(monkeypatch):
    profiles = [
        profile("compras", ["mercadolivre.purchases"], {"mercadolivre": "authorization_code"}),
        profile("recebimentos", ["mercadopago.payments"], {"mercadopago": "client_credentials"}),
    ]
    with pytest.raises(MercadoError) as captured:
        registry(monkeypatch, profiles).call("mercadopago.payment_get", {"profile": "compras", "payment_id": "1"})
    assert captured.value.as_dict() == {"code": "profile_module_unavailable", "message": "O perfil informado não possui integração com este método.", "profile": "compras", "module": "mercadopago.payments", "tool": "mercadopago.payment_get"}


def test_lista_de_perfis_nao_expoe_caminhos_ou_segredos(monkeypatch):
    configured = profile("pessoal", ["mercadolivre.account"], {"mercadolivre": "authorization_code"})
    configured["mercadolivre"].update({"authorization_entry_path": "segredo", "site_id": "MLB"})
    result = registry(monkeypatch, [configured]).call("mercado.list_profiles", {})
    assert result == {"profiles": [{"name": "pessoal", "description": "Perfil pessoal", "modules": ["mercadolivre.account"], "providers": {"mercadolivre": {"authMode": "authorization_code", "authorization": "unavailable"}}}]}
    assert "segredo" not in str(result)


def test_cached_tool_rejects_profile_disabled(monkeypatch):
    configuration = McpConfiguration(Path("."))
    disabled = profile("pessoal", ["mercadolivre.account"], {"mercadolivre": "authorization_code"})
    disabled["enabled"] = False
    monkeypatch.setattr(configuration, "enabled_mercado_profiles", lambda: [])
    monkeypatch.setattr(configuration, "mercado", lambda: {"profiles": {"pessoal": disabled}})
    with pytest.raises(MercadoError) as captured:
        MercadoToolRegistry(configuration).call("mercadolivre.account_get", {})
    assert captured.value.code == "profile_disabled"
