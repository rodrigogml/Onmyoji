import copy
import importlib.util
from pathlib import Path
import sys


def setup_module():
    path = Path(__file__).resolve().parents[1] / "setupMcp.py"
    specification = importlib.util.spec_from_file_location("setup_mcp_mercado_test", path)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


def test_modules_are_selected_manually_and_can_be_toggled(monkeypatch):
    module = setup_module()
    answers = iter(["  mercadolivre.account", "  mercadopago.payments", "✓ mercadolivre.account", "Concluir seleção"])
    monkeypatch.setattr(module, "flow_choice", lambda *args: next(answers))
    monkeypatch.setattr(module, "note", lambda *args: None)
    assert module.choose_mercado_modules([]) == ["mercadopago.payments"]


def test_referenced_application_is_detected_before_removal():
    module = setup_module()
    data = {"profiles": {"pessoal": {"mercadolivre": {"application": "principal"}}, "outro": {"mercadopago": {"application": "principal"}}}}
    assert module._mercado_app_references(data, "mercadolivre", "principal") == ["pessoal"]
    assert module._mercado_app_references(data, "mercadopago", "principal") == ["outro"]


class _MercadoConfigStub:
    def __init__(self, mercado_livre=True, mercado_pago=False):
        self.data = {
            "applications": {
                "mercadolivre": {"Onmyoji": {}} if mercado_livre else {},
                "mercadopago": {"Pagamentos": {}} if mercado_pago else {},
            },
            "profiles": {},
        }
        self.saved = []

    def mercado(self):
        return copy.deepcopy(self.data)

    def save_mercado(self, data):
        self.data = copy.deepcopy(data)
        self.saved.append(self.data)


def _mock_profile_creation(monkeypatch, module, module_choices):
    names = []
    descriptions = []
    providers = []
    choices = iter(module_choices)

    def ask_name(label, profiles):
        names.append(label)
        return "rodrigogml"

    def ask_description(label, current="", required=False):
        descriptions.append(label)
        return "Conta pessoal"

    def configure(config, data, profile_name, provider, current=None):
        providers.append(provider)
        return {"application": next(iter(data["applications"][provider])), "site_id": "MLB", "auth_mode": "authorization_code", "authorization_credential_profile": "default", "authorization_entry_path": f"Akuma/MCP/{provider}/Authorizations/{profile_name}"}

    monkeypatch.setattr(module, "ask_new_profile_name", ask_name)
    monkeypatch.setattr(module, "ask", ask_description)
    monkeypatch.setattr(module, "choose_mercado_modules", lambda current: next(choices))
    monkeypatch.setattr(module, "configure_mercado_profile_provider", configure)
    monkeypatch.setattr(module, "prompt", lambda label: "s")
    monkeypatch.setattr(module, "result", lambda *args: None)
    return names, descriptions, providers


def test_missing_applications_lists_only_providers_required_by_selected_modules():
    module = setup_module()
    config = _MercadoConfigStub()
    modules = ["mercadolivre.account", "mercadopago.payments"]
    assert module._mercado_missing_applications(config.mercado(), modules) == ["mercadopago"]
    assert module._mercado_missing_applications(config.mercado(), ["mercadolivre.purchases"]) == []
    assert module._mercado_missing_applications(_MercadoConfigStub(False, False).mercado(), modules) == ["mercadolivre", "mercadopago"]


def test_profile_creation_can_revise_missing_provider_without_reentering_inputs(monkeypatch):
    module = setup_module()
    config = _MercadoConfigStub()
    names, descriptions, providers = _mock_profile_creation(
        monkeypatch, module,
        [["mercadolivre.account", "mercadopago.payments"], ["mercadolivre.account"]],
    )
    warnings = []
    monkeypatch.setattr(module, "alert", warnings.append)
    monkeypatch.setattr(module, "flow_choice", lambda title, options, default: options[0])
    monkeypatch.setattr(module, "create_mercado_application", lambda *_args: None)

    module.create_mercado_profile(config)

    profile = config.data["profiles"]["rodrigogml"]
    assert profile["enabled"] is True
    assert profile["description"] == "Conta pessoal"
    assert profile["modules"] == ["mercadolivre.account"]
    assert providers == ["mercadolivre"]
    assert len(config.saved) == 1
    assert len(names) == len(descriptions) == 1
    assert any("Mercado Pago" in warning and "mercadopago.payments" in warning for warning in warnings)


def test_profile_creation_can_register_missing_app_without_losing_inputs(monkeypatch):
    module = setup_module()
    config = _MercadoConfigStub()
    names, descriptions, providers = _mock_profile_creation(
        monkeypatch, module,
        [["mercadolivre.account", "mercadopago.payments"]],
    )
    registered = []
    monkeypatch.setattr(module, "alert", lambda *_args: None)
    monkeypatch.setattr(module, "flow_choice", lambda title, options, default: options[1])

    def register(config, provider):
        registered.append(provider)
        config.data["applications"][provider]["Pagamentos"] = {}

    monkeypatch.setattr(module, "create_mercado_application", register)
    module.create_mercado_profile(config)

    profile = config.data["profiles"]["rodrigogml"]
    assert registered == ["mercadopago"]
    assert providers == ["mercadolivre", "mercadopago"]
    assert profile["modules"] == ["mercadolivre.account", "mercadopago.payments"]
    assert len(names) == len(descriptions) == 1
    assert len(config.saved) == 1


def test_cancelled_app_registration_returns_to_dependency_repair(monkeypatch):
    module = setup_module()
    config = _MercadoConfigStub()
    names, descriptions, providers = _mock_profile_creation(
        monkeypatch, module,
        [["mercadolivre.purchases", "mercadopago.payments"], ["mercadolivre.purchases"]],
    )
    actions = iter([1, 0])
    registrations = []
    monkeypatch.setattr(module, "alert", lambda *_args: None)
    monkeypatch.setattr(module, "flow_choice", lambda title, options, default: options[next(actions)])
    monkeypatch.setattr(module, "create_mercado_application", lambda _config, provider: registrations.append(provider))

    module.create_mercado_profile(config)

    assert registrations == ["mercadopago"]
    assert config.data["profiles"]["rodrigogml"]["modules"] == ["mercadolivre.purchases"]
    assert providers == ["mercadolivre"]
    assert len(names) == len(descriptions) == 1


def test_cancelling_missing_dependency_does_not_save_profile_or_enter_vault(monkeypatch):
    module = setup_module()
    config = _MercadoConfigStub()
    _mock_profile_creation(monkeypatch, module, [["mercadolivre.account", "mercadopago.payments"]])
    monkeypatch.setattr(module, "alert", lambda *_args: None)
    monkeypatch.setattr(module, "flow_choice", lambda *_args: None)
    monkeypatch.setattr(module, "configure_mercado_profile_provider", lambda *_args: (_ for _ in ()).throw(AssertionError("Não deve configurar autorização")))

    module.create_mercado_profile(config)

    assert config.data["profiles"] == {}
    assert config.saved == []


def test_provider_without_application_has_specific_message(monkeypatch):
    module = setup_module()
    config = _MercadoConfigStub()
    reports = []
    monkeypatch.setattr(module, "result", lambda success, message: reports.append((success, message)))
    monkeypatch.setattr(module, "select", lambda *_args: (_ for _ in ()).throw(AssertionError("Não deve abrir seleção vazia")))

    outcome = module.configure_mercado_profile_provider(config, config.mercado(), "rodrigogml", "mercadopago")

    assert outcome is None
    assert reports == [(False, "Nenhum aplicativo Mercado Pago configurado. Cadastre um aplicativo antes de vincular este provedor.")]


def test_empty_module_selection_is_revised_before_creating_profile(monkeypatch):
    module = setup_module()
    config = _MercadoConfigStub()
    names, descriptions, providers = _mock_profile_creation(monkeypatch, module, [[], ["mercadolivre.account"]])
    warnings = []
    monkeypatch.setattr(module, "alert", warnings.append)
    monkeypatch.setattr(module, "flow_choice", lambda *_args: (_ for _ in ()).throw(AssertionError("Sem dependências externas")))

    module.create_mercado_profile(config)

    assert config.data["profiles"]["rodrigogml"]["modules"] == ["mercadolivre.account"]
    assert providers == ["mercadolivre"]
    assert len(names) == len(descriptions) == 1
    assert any("Selecione pelo menos um módulo" in warning for warning in warnings)
