"""Perfis documentais portáteis, restritos ao workspace da instância."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import tomllib


DEFAULTS = {"max_input_bytes": 262144000, "max_pages": 2000, "max_pixels": 50000000,
            "timeout_seconds": 1800, "retention_hours": 24, "max_queue": 16}


def empty_configuration() -> dict:
    return {"schema_version": 1, "defaults": dict(DEFAULTS), "profiles": {}}


def workspace_for(root: Path) -> Path:
    """Resolve o workspace externo; configuração ausente ou vazia é inválida."""
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        value = system["codex"]["project_directory"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError
        workspace = Path(value).expanduser()
        workspace = workspace if workspace.is_absolute() else root / workspace
        from .paths import reject_links
        reject_links(workspace)
        workspace = workspace.resolve()
        if workspace.is_relative_to(root.resolve()) or root.resolve().is_relative_to(workspace):
            raise ValueError
        return workspace
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ValueError("Configure um workspace externo à instância antes de configurar PDF/OCR.") from error


def profile_defaults(root: Path, namespace: str) -> dict:
    workspace = workspace_for(root); base = workspace / ".onmyoji" / namespace
    profile = {"enabled": True, "description": "Documentos do workspace", "readable_roots": [str(workspace)],
               "writable_roots": [str(workspace)], "work_directory": str(base / "work")}
    if namespace == "pdf":
        profile.update(ocr_profile="", ghostscript_path="gswin64c" if os.name == "nt" else "gs", verapdf_path="verapdf", icc_profile_path="")
    else:
        profile.update(engine="tesseract", languages=["por", "eng"], dpi=300, tesseract_path="tesseract",
                       tessdata_dir="", advanced_model_directory=str(root.resolve() / "state" / "mcp" / "ocr" / "models"))
    return profile


def validate_profile_directories(root: Path, profile: dict) -> Path:
    """Revalida permissões contra o workspace atual, inclusive em jobs já enviados.

    O trabalho deve ser uma subpasta própria. Nenhuma raiz pode ficar dentro
    dele: fontes/resultados não participam da limpeza dos jobs transitórios.
    """
    from .paths import reject_links
    workspace = workspace_for(root)
    paths = []
    for key in ("readable_roots", "writable_roots"):
        values = profile.get(key)
        if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError("Raízes documentais inválidas.")
        paths.extend(values)
    value = profile.get("work_directory")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Diretório de trabalho documental obrigatório.")
    work = Path(value).expanduser()
    reject_links(work)
    if not work.is_absolute() or not work.resolve().is_relative_to(workspace) or work.resolve() == workspace:
        raise ValueError("O diretório de trabalho deve ser uma subpasta própria do workspace.")
    work = work.resolve()
    for value in paths:
        path = Path(value).expanduser()
        reject_links(path)
        if not path.is_absolute() or not path.resolve().is_relative_to(workspace):
            raise ValueError("Diretórios documentais devem estar contidos no workspace configurado.")
        if path.resolve().is_relative_to(work):
            raise ValueError("Diretórios de documentos não podem ficar dentro do trabalho temporário.")
    return work


def pdf_resource_path(root: Path, value: str) -> Path:
    """Valida o ICC administrativo sem ampliar as permissões de documentos."""
    from .paths import contained_path
    return contained_path(value, [str(workspace_for(root)), str(root.resolve() / "configs" / "mcp" / "pdf" / "resources")], existing=True)


def validate_configuration(root: Path, namespace: str, data: dict) -> None:
    if namespace not in {"pdf", "ocr"} or not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Configuração documental inválida.")
    if not isinstance(data.get("profiles"), dict) or not isinstance(data.get("defaults"), dict):
        raise ValueError("Perfis ou limites documentais inválidos.")
    maximums = {"max_input_bytes": 2147483648, "max_pages": 100000, "max_pixels": 200000000,
                "timeout_seconds": 86400, "retention_hours": 8760, "max_queue": 100}
    if set(data["defaults"]) - set(DEFAULTS):
        raise ValueError("Limite documental desconhecido.")
    for key, maximum in maximums.items():
        value = data["defaults"].get(key)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"Limite documental inválido: {key}.")
    if not data["profiles"]:
        return
    workspace_for(root)
    common = {"enabled", "description", "readable_roots", "writable_roots", "work_directory"}
    extra = {"ocr_profile", "ghostscript_path", "verapdf_path", "icc_profile_path"} if namespace == "pdf" else {"engine", "languages", "dpi", "tesseract_path", "tessdata_dir", "advanced_model_directory", "rotate_pages", "deskew"}
    for name, profile in data["profiles"].items():
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
            raise ValueError("Nome de perfil documental inválido.")
        if not isinstance(profile, dict) or set(profile) - (common | extra) or type(profile.get("enabled")) is not bool:
            raise ValueError("Atributos do perfil documental inválidos.")
        if not isinstance(profile.get("description"), str) or not 1 <= len(profile["description"].strip()) <= 240:
            raise ValueError("Informe uma descrição do perfil com até 240 caracteres.")
        validate_profile_directories(root, profile)
        if namespace == "pdf":
            if not all(isinstance(profile.get(key, ""), str) for key in extra):
                raise ValueError("Referência do perfil OCR inválida.")
            if profile.get("icc_profile_path"):
                pdf_resource_path(root, profile["icc_profile_path"])
        else:
            if any(type(profile.get(key, False)) is not bool for key in ("rotate_pages", "deskew")):
                raise ValueError("Opções de pré-processamento OCR inválidas.")
            if profile.get("engine") not in {"tesseract", "paddleocr"}:
                raise ValueError("Motor OCR inválido.")
            if type(profile.get("dpi")) is not int or not 72 <= profile["dpi"] <= 600:
                raise ValueError("DPI deve estar entre 72 e 600.")
            if not isinstance(profile.get("languages"), list) or not profile["languages"] or not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]+", value) for value in profile["languages"]):
                raise ValueError("Idiomas OCR inválidos.")
            for key in ("tesseract_path", "tessdata_dir", "advanced_model_directory"):
                if not isinstance(profile.get(key, ""), str):
                    raise ValueError("Caminho do runtime OCR inválido.")
            from ..ocr.runtime import model_directory
            model_directory(root, profile)


def render_configuration(data: dict) -> str:
    lines = ["schema_version = 1", "", "[defaults]"]
    lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in data["defaults"].items())
    lines.extend(("", "[profiles]"))
    for name, profile in sorted(data["profiles"].items()):
        lines.extend(("", f"[profiles.{name}]"))
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in profile.items())
    return "\n".join(lines) + "\n"


def load_configuration(root: Path, namespace: str) -> dict:
    path = root / "configs" / "mcp" / f"{namespace}.toml"
    if not path.exists():
        return empty_configuration()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        data["defaults"] = {**DEFAULTS, **data.get("defaults", {})}
        validate_configuration(root, namespace, data)
        return copy.deepcopy(data)
    except (OSError, TypeError, ValueError) as error:
        raise ValueError(f"Configuração {namespace.upper()} inválida: {error}") from error
