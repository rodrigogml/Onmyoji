"""Publicação dinâmica das ferramentas Mercado por união de módulos."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .contracts import AUTH_OPERATIONS, MODULE_TOOLS, TOOL_MODULE, auth_schema, description, tool_schema
from .exception import MercadoError


class MercadoToolRegistry:
    """Publica a união das capacidades e revalida o perfil em toda chamada."""

    def __init__(self, configuration: McpConfiguration):
        self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_mercado_profiles()
        if not profiles:
            return []
        names = [profile["name"] for profile in profiles]
        union = {module for profile in profiles for module in profile["modules"]}
        tools = [self._definition("mercado.list_profiles", {"type": "object", "properties": {}, "additionalProperties": False}, profiles)]
        providers = {provider for profile in profiles for provider in ("mercadolivre", "mercadopago") if provider in profile}
        for provider in sorted(providers):
            for operation in AUTH_OPERATIONS:
                tools.append(self._definition(f"{provider}.{operation}", auth_schema(operation), profiles))
        for module in MODULE_TOOLS:
            if module in union:
                tools.extend(self._definition(name, tool_schema(name), profiles) for name in MODULE_TOOLS[module])
        if len(names) == 1:
            return tools
        return tools

    @staticmethod
    def _definition(name: str, schema: dict[str, Any], profiles: list[dict[str, Any]]) -> dict[str, Any]:
        if len(profiles) > 1 and name != "mercado.list_profiles":
            schema["properties"]["profile"] = {"type": "string", "enum": [profile["name"] for profile in profiles]}
            schema.setdefault("required", []).append("profile")
        context = f' Perfil ativo: "{profiles[0]["name"]}".' if len(profiles) == 1 else " Informe qualquer perfil habilitado; a capacidade será validada durante a chamada."
        return {"name": name, "description": description(name) + context, "input_schema": schema}

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_mercado_profiles()
        configured = self.configuration.mercado().get("profiles", {})
        if not profiles:
            requested = arguments.get("profile") if isinstance(arguments, dict) else None
            if requested is None and len(configured) == 1:
                requested = next(iter(configured))
            if requested in configured and configured[requested].get("enabled") is False:
                raise MercadoError("profile_disabled", "Perfil Mercado está desabilitado.", profile=requested)
            raise MercadoError("profile_not_found", "Não há perfil Mercado habilitado.")
        if name == "mercado.list_profiles":
            self._require_arguments(arguments, set())
            return {"profiles": [self._public_profile(profile) for profile in profiles]}
        known = set(TOOL_MODULE)
        known.update(f"{provider}.{operation}" for provider in ("mercadolivre", "mercadopago") for operation in AUTH_OPERATIONS)
        if name not in known:
            raise MercadoError("invalid_request", "Ferramenta Mercado não registrada.", tool=name)
        profile_name = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        profile = next((item for item in profiles if item["name"] == profile_name), None)
        if profile is None:
            if profile_name in configured and configured[profile_name].get("enabled") is False:
                raise MercadoError("profile_disabled", "Perfil Mercado está desabilitado.", profile=profile_name)
            raise MercadoError("profile_not_found", "Perfil Mercado não encontrado ou desabilitado.", profile=profile_name)
        expected_module = TOOL_MODULE.get(name)
        if expected_module is not None and expected_module not in profile["modules"]:
            raise MercadoError("profile_module_unavailable", "O perfil informado não possui integração com este método.", profile=profile_name, module=expected_module, tool=name)
        provider = name.split(".", 1)[0]
        if provider not in profile:
            raise MercadoError("profile_provider_unavailable", "O perfil não possui integração com este provedor.", profile=profile_name, provider=provider, tool=name)
        schema = auth_schema(name.split(".", 1)[1]) if expected_module is None else tool_schema(name)
        allowed = set(schema["properties"]) | ({"profile"} if len(profiles) > 1 else set())
        self._require_arguments(arguments, allowed)
        from .service import MercadoService
        return MercadoService(self.configuration.root).invoke(profile_name, name, {key: value for key, value in arguments.items() if key != "profile"})

    @staticmethod
    def _require_arguments(arguments: Any, allowed: set[str]) -> None:
        if not isinstance(arguments, dict) or set(arguments) - allowed:
            raise MercadoError("invalid_request", "Argumentos Mercado inválidos.")

    def _public_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        providers: dict[str, Any] = {}
        for provider in ("mercadolivre", "mercadopago"):
            if provider in profile:
                mode = profile[provider].get("auth_mode", "authorization_code")
                state = "on_demand" if mode == "client_credentials" else "unavailable"
                if mode == "authorization_code":
                    try:
                        from .oauth import MercadoOAuth
                        state = MercadoOAuth(self.configuration.root, provider, profile["name"]).status()["state"]
                    except (MercadoError, OSError, KeyError, TypeError, ValueError):
                        pass
                providers[provider] = {"authMode": mode, "authorization": state}
        return {"name": profile["name"], "description": profile["description"], "modules": list(profile["modules"]), "providers": providers}
