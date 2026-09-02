"""Contrato JSON comum para perfis de skills de integração.

Este módulo nunca é chamado pelo agente diretamente. Cada setupSkill declara o seu
esquema e chama ``handle``; assim o setup permanece a autoridade exclusiva para a
configuração, validação e persistência da respectiva skill.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from setup_ui import choose_keepass_profile, item, prompt, result, screen, suggested_vault_entry


@dataclass(frozen=True)
class Field:
    name: str
    description: str
    default: Any = None
    required: bool = False
    secret: bool = False
    suggest: Callable[[str], Any] | None = None


def _name(value: str | None) -> str:
    value = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value): raise ValueError("O nome do perfil deve usar somente letras, números, hífen ou sublinhado.")
    return value


def _value(raw: str) -> Any:
    try: return json.loads(raw)
    except json.JSONDecodeError: return raw


def _updates(values: list[str] | None, fields: tuple[Field, ...]) -> dict[str, Any]:
    allowed = {field.name for field in fields}
    result: dict[str, Any] = {}
    for item in values or []:
        key, separator, raw = item.partition("=")
        if not separator or key not in allowed: raise ValueError(f"Campo inválido em --set: {key or item}.")
        result[key] = _value(raw)
    return result


def _public(name: str, profile: dict[str, Any], fields: tuple[Field, ...]) -> dict[str, Any]:
    return {"name": name, **{field.name: "[CONFIGURADO]" if field.secret and profile.get(field.name) else profile.get(field.name) for field in fields if field.name in profile}}


def _schema(fields: tuple[Field, ...]) -> list[dict[str, Any]]:
    return [{"name": field.name, "description": field.description, "required_on_create": field.required, "default": "[CONFIGURADO]" if field.secret and field.default else field.default} for field in fields]


def handle(*, action: str, profile_name: str | None, values: list[str] | None, confirm_delete: str | None, path: Path, load: Callable[[Path], dict[str, Any]], save: Callable[[Path, dict[str, Any]], tuple[bool, str]], fields: tuple[Field, ...], test: Callable[[str, Path, dict[str, Any]], tuple[bool, str]] | None = None) -> tuple[int, dict[str, Any]]:
    """Execute a ação de perfil declarada por um setupSkill."""
    try: data = load(path)
    except Exception as error: return 2, {"ok": False, "error": {"code": "invalid_config", "message": f"Não foi possível ler a configuração: {error}"}}
    profiles = data.get("profiles")
    if not isinstance(profiles, dict): return 2, {"ok": False, "error": {"code": "invalid_config", "message": "A seção profiles é inválida."}}
    if action == "profile-schema": return 0, {"ok": True, "fields": _schema(fields)}
    if action == "profile-list": return 0, {"ok": True, "configured": bool(profiles), "profiles": [_public(name, profile, fields) for name, profile in sorted(profiles.items()) if isinstance(profile, dict)]}
    try: name = _name(profile_name)
    except ValueError as error: return 2, {"ok": False, "error": {"code": "invalid_profile_name", "message": str(error)}}
    if action == "profile-test":
        if name not in profiles: return 2, {"ok": False, "error": {"code": "profile_not_found", "message": "Perfil não encontrado."}}
        if test is None: return 2, {"ok": False, "error": {"code": "test_not_supported", "message": "Esta skill ainda não declara um teste de perfil não interativo."}}
        ok, message = test(name, path, data)
        return (0 if ok else 2), {"ok": ok, "profile": name, "message": message}
    if action == "profile-delete":
        if name not in profiles: return 2, {"ok": False, "error": {"code": "profile_not_found", "message": "Perfil não encontrado."}}
        if confirm_delete != "DELETE": return 2, {"ok": False, "error": {"code": "confirmation_required", "message": "Confirme a exclusão com --confirm-delete DELETE após pedido explícito do usuário."}}
        candidate = copy.deepcopy(data); del candidate["profiles"][name]
    else:
        try: updates = _updates(values, fields)
        except ValueError as error: return 2, {"ok": False, "error": {"code": "invalid_field", "message": str(error)}}
        exists = name in profiles
        if action == "profile-create":
            if exists: return 2, {"ok": False, "error": {"code": "profile_exists", "message": "Já existe um perfil com este nome."}}
            candidate_profile = {field.name: copy.deepcopy(field.default) for field in fields if field.default is not None}
            candidate_profile.update(updates)
            missing = [field.name for field in fields if field.required and not candidate_profile.get(field.name)]
            if missing: return 2, {"ok": False, "error": {"code": "missing_required_field", "message": "Campos obrigatórios ausentes: " + ", ".join(missing) + "."}}
            candidate = copy.deepcopy(data); candidate["profiles"][name] = candidate_profile
        elif action == "profile-update":
            if not exists: return 2, {"ok": False, "error": {"code": "profile_not_found", "message": "Perfil não encontrado."}}
            if not updates: return 2, {"ok": False, "error": {"code": "missing_update", "message": "Informe pelo menos um --set campo=valor."}}
            candidate = copy.deepcopy(data); candidate["profiles"][name].update(updates)
        else: return 2, {"ok": False, "error": {"code": "unsupported_action", "message": "Ação de perfil não suportada."}}
    ok, message = save(path, candidate)
    if not ok: return 2, {"ok": False, "error": {"code": "validation_failed", "message": message}}
    response: dict[str, Any] = {"ok": True, "profile": name, "message": message}
    if action != "profile-delete": response["configuration"] = _public(name, candidate["profiles"][name], fields)
    return 0, response


def simple_load(path: Path, defaults: dict[str, Any]) -> dict[str, Any]:
    if not path.exists(): return {"schema_version": 1, "defaults": copy.deepcopy(defaults), "profiles": {}}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def simple_render(data: dict[str, Any]) -> str:
    lines = ["schema_version = 1", "", "[defaults]"]
    lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in data["defaults"].items())
    for name, profile in sorted(data["profiles"].items()):
        lines.extend(["", f"[profiles.{name}]"])
        lines.extend(f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in profile.items())
    return "\n".join(lines) + "\n"


def simple_save(path: Path, data: dict[str, Any]) -> tuple[bool, str]:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(simple_render(data), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        if old is None: path.unlink(missing_ok=True)
        else: path.write_text(old, encoding="utf-8", newline="\n")
        return False, f"Não salvo: {error}"
    return True, "Configuração salva e validada."


def interactive_configure(*, root: Path, path: Path, title: str, subtitle: str, integration: str, defaults: dict[str, Any], fields: tuple[Field, ...], test: Callable[[str, Path, dict[str, Any]], tuple[bool, str]] | None = None) -> None:
    """Configure perfis locais sem expor segredos ou duplicar menus de skills."""
    def choose(profiles: dict[str, Any], action: str) -> str | None:
        names = sorted(profiles)
        screen(title, action, "Escolha um perfil ou pressione X para voltar")
        for index, name in enumerate(names, 1): item(f"{index}.", name)
        item("X.", "Voltar")
        value = prompt("Perfil: ").strip().casefold()
        if value in {"x", "\x1b"}: return None
        if value.isdigit() and 1 <= int(value) <= len(names): return names[int(value) - 1]
        result(False, "Opção inválida.")
        return None

    def field_value(field: Field, profile_name: str, current: Any = None) -> Any | None:
        default = current if current is not None and current != "" else field.suggest(profile_name) if field.suggest else suggested_vault_entry(integration, profile_name) if field.name == "vault_entry_path" else field.default
        if field.name == "vault_profile" or field.name.endswith("_vault_profile"): return choose_keepass_profile(root, str(default or ""))
        shown = "" if default is None else str(default)
        value = prompt(f"{field.description} [{shown}]: ").strip()
        if value.casefold() in {"x", "\x1b"}: return None
        value = default if not value else _value(value)
        if field.required and not value:
            result(False, f"{field.description} é obrigatório.")
            return None
        return value

    while True:
        try: data = simple_load(path, defaults)
        except (OSError, tomllib.TOMLDecodeError) as error:
            result(False, f"Não foi possível ler a configuração: {error}")
            return
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            result(False, "A seção profiles da configuração é inválida.")
            return
        screen(title, "Configuração", subtitle)
        item("1.", "Criar perfil"); item("2.", "Editar perfil"); item("3.", "Excluir perfil")
        if test is not None: item("4.", "Testar perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action == "1":
            name = prompt("Nome do perfil [X cancela]: ").strip()
            try: name = _name(name)
            except ValueError as error: result(False, str(error)); continue
            if name in profiles:
                result(False, "Esse perfil já existe.")
                continue
            candidate: dict[str, Any] = {}
            for field in fields:
                value = field_value(field, name)
                if value is None:
                    result(False, "Criação cancelada; nenhuma alteração foi gravada.")
                    break
                candidate[field.name] = value
            else:
                profiles[name] = candidate
                ok, message = simple_save(path, data); result(ok, message)
            continue
        allowed = {"2", "3"} | ({"4"} if test is not None else set())
        if action not in allowed:
            result(False, "Opção inválida.")
            continue
        if not profiles:
            result(False, "Nenhum perfil configurado.")
            continue
        labels = {"2": "Editar perfil", "3": "Excluir perfil", "4": "Testar perfil"}
        name = choose(profiles, labels[action])
        if name is None: continue
        if action == "3":
            if prompt(f"Digite EXCLUIR para remover {name}: ").strip() == "EXCLUIR":
                del profiles[name]; ok, message = simple_save(path, data); result(ok, message)
            else: result(False, "Operação cancelada.")
            continue
        if action == "4":
            ok, message = test(name, path, data); result(ok, message)
            continue
        profile = profiles[name]
        if not isinstance(profile, dict):
            result(False, "Perfil inválido.")
            continue
        screen(title, "Editar perfil", name)
        for index, field in enumerate(fields, 1): item(f"{index}.", field.description, str(profile.get(field.name, "")))
        item("X.", "Voltar")
        choice = prompt("Campo: ").strip().casefold()
        if choice in {"x", "\x1b"}: continue
        if not choice.isdigit() or not 1 <= int(choice) <= len(fields):
            result(False, "Opção inválida.")
            continue
        field = fields[int(choice) - 1]
        value = field_value(field, name, profile.get(field.name))
        if value is None:
            result(False, "Edição cancelada; nenhuma alteração foi gravada.")
            continue
        profile[field.name] = value
        ok, message = simple_save(path, data); result(ok, message)


def wrapper_test(script: Path, config: Path, profile: str, request: dict[str, Any], label: str, timeout: float = 60) -> tuple[bool, str]:
    """Execute uma leitura inofensiva pelo wrapper oficial, sem expor a resposta."""
    try:
        process = subprocess.run([sys.executable, str(script), "--config", str(config), "--profile", profile], input=json.dumps(request), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout)
        payload = json.loads(process.stdout)
    except subprocess.TimeoutExpired: return False, f"O teste {label} excedeu {int(timeout)} segundos."
    except (OSError, json.JSONDecodeError): return False, f"O wrapper {label} não retornou JSON válido."
    if process.returncode or not payload.get("ok", False):
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        return False, f"Teste {label} falhou: {error.get('message', 'a integração recusou a solicitação.')}"
    return True, f"Conexão e credencial {label} confirmadas."
