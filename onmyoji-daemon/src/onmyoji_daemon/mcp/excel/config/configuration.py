"""Perfis Excel com permissões explícitas e arquivos limitados ao workspace."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import re
import tomllib

from ...document_processing.configuration import validate_profile_directories, workspace_for


DEFAULTS = {
    "max_input_bytes": 262144000,
    "max_cells": 1000000,
    "max_operations": 1000,
    "max_result_bytes": 33554432,
    "max_package_bytes": 1073741824,
    "max_zip_entries": 100000,
    "max_memory_bytes": 2147483648,
    "timeout_seconds": 1800,
    "retention_hours": 24,
    "max_queue": 16,
}
POLICIES = ("allow_write", "allow_vba_source", "allow_macros", "allow_refresh", "allow_object_api", "allow_signature_changes",
            "allow_unverified_active_content", "allow_addins", "trusted_content")


def empty_configuration() -> dict:
    """Retorna configuração inativa, sem iniciar Excel nem criar diretórios."""
    return {"schema_version": 1, "defaults": dict(DEFAULTS), "profiles": {}}


def profile_defaults(root: Path) -> dict:
    """Sugere o Work externo; código e fontes externas exigem habilitação própria."""
    work = workspace_for(root)
    return {
        "enabled": True,
        "description": "Pastas Excel do workspace",
        "readable_roots": [str(work)],
        "writable_roots": [str(work)],
        "work_directory": str(work / ".onmyoji" / "excel" / "work"),
        "allow_write": True,
        "allow_vba_source": False,
        "allow_macros": False,
        "allow_refresh": False,
        "allow_object_api": False,
        "allow_signature_changes": False,
        "allow_unverified_active_content": False,
        "allow_addins": False,
        "trusted_content": False,
    }


def validate_configuration(root: Path, data: dict) -> None:
    """Rejeita atributos desconhecidos, limites inválidos e permissões fora do Work.

    Código VBA executável requer conteúdo explicitamente confiável. A validação
    não modifica Trust Center, políticas do Office ou documentos existentes.
    """
    if not isinstance(data, dict) or set(data) != {"schema_version", "defaults", "profiles"} or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ValueError("Configuração Excel inválida.")
    if not isinstance(data["defaults"], dict) or set(data["defaults"]) != set(DEFAULTS):
        raise ValueError("Limites Excel desconhecidos ou incompletos.")
    maximums = {"max_input_bytes": 2147483648, "max_cells": 100000000, "max_operations": 10000,
                "max_result_bytes": 1073741824, "max_package_bytes": 8589934592, "max_zip_entries": 1000000, "max_memory_bytes": 34359738368,
                "timeout_seconds": 86400, "retention_hours": 8760, "max_queue": 100}
    for key, maximum in maximums.items():
        if type(data["defaults"][key]) is not int or not 1 <= data["defaults"][key] <= maximum:
            raise ValueError(f"Limite Excel inválido: {key}.")
    if not isinstance(data["profiles"], dict):
        raise ValueError("Perfis Excel inválidos.")
    allowed = {"enabled", "description", "readable_roots", "writable_roots", "work_directory", *POLICIES}
    for name, profile in data["profiles"].items():
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name) is None:
            raise ValueError("Nome de perfil Excel inválido.")
        if not isinstance(profile, dict) or set(profile) != allowed:
            raise ValueError("Atributos do perfil Excel desconhecidos ou incompletos.")
        if any(type(profile[key]) is not bool for key in ("enabled", *POLICIES)):
            raise ValueError("As permissões Excel devem ser booleanas.")
        if not isinstance(profile["description"], str) or not 1 <= len(profile["description"].strip()) <= 240:
            raise ValueError("Informe uma descrição do perfil Excel com até 240 caracteres.")
        if not profile["readable_roots"] or not profile["writable_roots"]:
            raise ValueError("Informe ao menos uma raiz de leitura e de escrita.")
        validate_profile_directories(root, profile)
        if any(profile[field] for field in ("allow_macros", "allow_unverified_active_content", "allow_addins")) and not profile["trusted_content"]:
            raise ValueError("Execução VBA, suplementos e conteúdo não inspecionável exigem perfil de conteúdo confiável.")


def render_configuration(data: dict) -> str:
    """Serializa TOML UTF-8 sem incluir documentos ou credenciais."""
    lines = ["schema_version = 1", "", "[defaults]"]
    lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in data["defaults"].items())
    lines.extend(("", "[profiles]"))
    for name, profile in sorted(data["profiles"].items()):
        lines.extend(("", f"[profiles.{name}]"))
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in profile.items())
    return "\n".join(lines) + "\n"


def load_configuration(root: Path) -> dict:
    """Carrega perfis; arquivo ausente mantém o módulo desabilitado."""
    path = root / "configs" / "mcp" / "excel.toml"
    if not path.exists():
        return empty_configuration()
    try:
        from ...document_processing.paths import reject_links
        reject_links(path)
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        data["defaults"] = {**DEFAULTS, **data.get("defaults", {})}
        validate_configuration(root, data)
        return copy.deepcopy(data)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f"Configuração Excel inválida: {error}") from error
