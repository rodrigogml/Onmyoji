"""Tools MCP publicadas para os perfis EccoVox habilitados."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import EccoVoxService


class EccoVoxToolRegistry:
    """Aplica a seleção implícita de um perfil ou explícita de vários."""

    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_eccovox_profiles()
        if not profiles: return []
        context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme eccovox.list_profiles e seu contexto."
        profile = {"type": "string", "enum": [entry["name"] for entry in profiles]}
        shared = {"profile": profile} if len(profiles) > 1 else {}
        required_profile = ["profile"] if len(profiles) > 1 else []
        tools = []
        if len(profiles) > 1: tools.append({"name": "eccovox.list_profiles", "description": "Lista perfis EccoVox habilitados e seu contexto de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        tools += [
            {"name": "eccovox.get_health", "description": "Consulta a saúde do runtime local EccoVox." + context, "input_schema": {"type": "object", "properties": shared, "required": required_profile, "additionalProperties": False}},
            {"name": "eccovox.transcribe_audio", "description": "Transcreve um arquivo de áudio autorizado." + context, "input_schema": {"type": "object", "properties": shared | {"audio_path": {"type": "string"}, "language": {"type": "string"}, "runtime_profile": {"type": "string"}}, "required": required_profile + ["audio_path"], "additionalProperties": False}},
            {"name": "eccovox.synthesize_speech", "description": "Sintetiza fala em arquivo autorizado. Exige confirm=true porque escreve o arquivo." + context, "input_schema": {"type": "object", "properties": shared | {"text": {"type": "string"}, "output_path": {"type": "string"}, "response_format": {"type": "string", "enum": ["mp3", "wav", "opus", "flac"]}, "voice": {"type": "string"}, "language": {"type": "string"}, "speed": {"type": "number"}, "runtime_profile": {"type": "string"}, "confirm": {"type": "boolean"}}, "required": required_profile + ["text", "output_path", "confirm"], "additionalProperties": False}},
        ]
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_eccovox_profiles()
        if name == "eccovox.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}: raise ValueError("Perfil EccoVox indisponível.")
        service = EccoVoxService(self.configuration.root)
        if name == "eccovox.get_health": return service.health(selected)
        if name == "eccovox.transcribe_audio": return service.transcribe(selected, arguments.get("audio_path", ""), language=arguments.get("language"), runtime_profile=arguments.get("runtime_profile"))
        if name == "eccovox.synthesize_speech":
            if arguments.get("confirm") is not True: raise ValueError("Confirme a geração de áudio com confirm=true.")
            return service.synthesize(selected, arguments.get("text", ""), arguments.get("output_path", ""), arguments.get("response_format"), voice=arguments.get("voice"), language=arguments.get("language"), speed=arguments.get("speed"), runtime_profile=arguments.get("runtime_profile"))
        raise ValueError("Tool EccoVox indisponível.")
