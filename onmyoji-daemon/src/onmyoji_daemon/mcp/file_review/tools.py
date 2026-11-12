"""Ferramentas de abertura e acompanhamento de revisão humana de arquivos."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .exception import FileReviewMcpError
from .service import FileReviewMcpService


class FileReviewToolRegistry:
    """Expõe revisões por referências locais, sem ferramenta de aprovação pelo agente."""

    _TOOLS = {
        "create": (
            "Publica uma revisão de um arquivo original e sua proposta .proposed.md. Retorna a URL de claim para o usuário; somente ele aplica o resultado pela interface.",
            True,
            {
                "original_path": {"type": "string", "minLength": 1, "description": "Caminho absoluto do arquivo original existente."},
                "proposed_path": {"type": "string", "minLength": 1, "description": "Caminho absoluto da proposta .proposed.md no mesmo diretório."},
                "claim_timeout": {"type": "integer", "minimum": 1, "description": "Prazo de uso do segredo de claim, em segundos; omitir preserva o padrão de Mini Apps."},
                "lifetime": {"type": "integer", "minimum": 1, "description": "Duração da sessão Mini App, em segundos; omitir preserva o padrão existente."},
                "idle_timeout": {"type": "integer", "minimum": 1, "description": "Expiração por inatividade, em segundos; omitir preserva o padrão existente."},
            },
            ["original_path", "proposed_path"],
        ),
        "status": (
            "Consulta decisões e resultado da revisão sem retornar conteúdo dos arquivos nem o segredo de claim.",
            False,
            {"review_id": {"type": "string", "minLength": 1}},
            ["review_id"],
        ),
        "cancel": (
            "Cancela a revisão e encerra sua publicação sem modificar o original nem descartar a proposta.",
            True,
            {"review_id": {"type": "string", "minLength": 1}},
            ["review_id"],
        ),
    }

    def __init__(self, root: Path):
        self.root = root.resolve()

    def definitions(self) -> list[dict[str, Any]]:
        """Publica parâmetros fechados e confirmação para criação e cancelamento."""
        result = []
        for action, (description, writes, properties, required) in self._TOOLS.items():
            properties = dict(properties)
            required = list(required)
            if writes:
                properties["confirm"] = {"type": "boolean", "const": True}
                required.append("confirm")
            result.append({"name": "file_review." + action, "description": description, "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False}})
        return result

    def call(self, name: str, arguments: dict[str, Any]) -> Any:
        """Valida o contrato antes do RPC e exige ``confirm=true`` nas mutações.

        Lança ``FileReviewMcpError`` com ``invalid_request`` para ferramenta,
        campos ou tipos inválidos e ``confirmation_required`` quando faltar a
        confirmação de criação ou cancelamento.
        """
        if not isinstance(name, str):
            raise FileReviewMcpError("Ferramenta ou argumentos de revisão de arquivos inválidos.")
        action = name.removeprefix("file_review.")
        definition = self._TOOLS.get(action)
        if not name.startswith("file_review.") or definition is None or not isinstance(arguments, dict):
            raise FileReviewMcpError("Ferramenta ou argumentos de revisão de arquivos inválidos.")
        _description, writes, properties, required = definition
        allowed = set(properties) | ({"confirm"} if writes else set())
        if set(arguments) - allowed:
            raise FileReviewMcpError("Argumentos de revisão de arquivos desconhecidos.")
        if writes and arguments.get("confirm") is not True:
            raise FileReviewMcpError("Confirme a operação de revisão com confirm=true.", "confirmation_required")
        if any(key not in arguments for key in required):
            raise FileReviewMcpError("Argumentos obrigatórios de revisão de arquivos ausentes.")
        for key, value in arguments.items():
            if key == "confirm":
                continue
            schema = properties[key]
            if schema["type"] == "string" and (not isinstance(value, str) or not value.strip()):
                raise FileReviewMcpError("Os caminhos e identificadores de revisão devem ser textos não vazios.")
            if schema["type"] == "integer" and (type(value) is not int or value < schema["minimum"]):
                raise FileReviewMcpError("Os prazos da revisão devem ser inteiros positivos.")
        return FileReviewMcpService(self.root).invoke(action, dict(arguments))
