"""Publicação das operações Spotify como ferramentas MCP."""
from __future__ import annotations

from typing import Any

from ..configuration import McpConfiguration
from .service import OPERATIONS, WRITE_OPERATIONS, SpotifyService


DESCRIPTIONS = {
    "me.get": "Obtém o perfil da conta atual.", "search": "Pesquisa itens no catálogo; query exige q e type e aceita limit de até 10 em Development Mode.",
    "albums.get": "Obtém um álbum por params.id.", "artists.get": "Obtém um artista por params.id.", "tracks.get": "Obtém uma faixa por params.id.",
    "playlists.list": "Lista playlists da conta atual.", "playlists.get": "Obtém uma playlist por params.id.", "playlists.items.list": "Lista itens de uma playlist própria ou colaborativa por params.id.",
    "playlists.create": "Cria uma playlist para a conta atual; body exige name.", "playlists.update": "Altera detalhes da playlist indicada por params.id.", "playlists.items.add": "Adiciona até 100 URIs à playlist indicada por params.id.", "playlists.items.remove": "Remove até 100 itens da playlist indicada por params.id.",
    "library.list": "Lista itens da biblioteca; query.type exige tracks, albums, shows, episodes ou audiobooks e aceita paginação.", "library.save": "Salva até 40 URIs na biblioteca usando query.uris.", "library.remove": "Remove até 40 URIs da biblioteca usando query.uris.",
    "following.list": "Lista artistas seguidos; query usa type=artist.", "following.add": "Segue itens por URIs através da biblioteca genérica usando query.uris.", "following.remove": "Deixa de seguir itens por URIs através da biblioteca genérica usando query.uris.",
    "top.items": "Lista itens mais ouvidos; params.type é artists ou tracks.", "recent.list": "Lista reproduções recentes.",
    "player.state": "Obtém o estado de reprodução.", "player.devices": "Lista dispositivos disponíveis.", "player.current": "Obtém o item em reprodução.", "player.queue": "Obtém a fila.",
    "player.play": "Inicia ou retoma a reprodução.", "player.pause": "Pausa a reprodução.", "player.next": "Avança para o próximo item.", "player.previous": "Volta ao item anterior.", "player.seek": "Altera a posição de reprodução.", "player.volume": "Altera o volume.", "player.shuffle": "Ativa ou desativa a ordem aleatória.", "player.repeat": "Altera o modo de repetição.", "player.queue.add": "Adiciona uma URI à fila.",
}


class SpotifyToolRegistry:
    def __init__(self, configuration: McpConfiguration): self.configuration = configuration

    def definitions(self) -> list[dict[str, Any]]:
        profiles = self.configuration.enabled_spotify_profiles()
        if not profiles: return []
        tools: list[dict[str, Any]] = []
        if len(profiles) > 1:
            tools.append({"name": "spotify.list_profiles", "description": "Lista perfis Spotify habilitados e seus contextos de uso.", "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}})
        for operation in sorted(OPERATIONS):
            properties: dict[str, Any] = {"params": {"type": "object", "description": "Identificadores usados no caminho da API."}, "query": {"type": "object", "description": "Parâmetros de consulta da API."}, "body": {"description": "Corpo JSON da operação."}}
            required: list[str] = []
            if len(profiles) > 1: properties["profile"] = {"type": "string", "enum": [item["name"] for item in profiles]}; required.append("profile")
            if operation in WRITE_OPERATIONS: properties["confirm"] = {"type": "boolean", "const": True}; required.append("confirm")
            schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
            if required: schema["required"] = required
            context = f' Perfil ativo: "{profiles[0]["name"]}" — {profiles[0]["description"]}' if len(profiles) == 1 else " Escolha profile conforme spotify.list_profiles."
            description = DESCRIPTIONS[operation] + (" Exige confirm=true." if operation in WRITE_OPERATIONS else "") + context
            tools.append({"name": "spotify." + operation, "description": description, "input_schema": schema})
        return tools

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        profiles = self.configuration.enabled_spotify_profiles()
        if name == "spotify.list_profiles" and len(profiles) > 1: return {"profiles": profiles}
        selected = profiles[0]["name"] if len(profiles) == 1 else arguments.get("profile")
        if not isinstance(selected, str) or selected not in {item["name"] for item in profiles}: raise ValueError("Perfil Spotify indisponível.")
        operation = name.removeprefix("spotify.") if name.startswith("spotify.") else ""
        if operation not in OPERATIONS: raise ValueError("Tool Spotify indisponível.")
        return SpotifyService(self.configuration.root).invoke(selected, operation, arguments)
