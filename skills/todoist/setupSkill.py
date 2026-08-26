#!/usr/bin/env python3
"""Configurador interativo da skill Todoist."""
from __future__ import annotations

import argparse
import copy
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODEL = Path(__file__).resolve().parent / "configs" / "todoist.toml.model"


def config_path(root: Path) -> Path: return root / "configs" / "todoist.toml"
def empty() -> dict[str, Any]: return {"schema_version": 1, "defaults": {"api_base": "https://api.todoist.com/api/v1", "timeout_seconds": 30, "max_retries": 2}, "profiles": {}}
def quote(value: str) -> str: return json.dumps(value, ensure_ascii=False)
def list_toml(values: list[str]) -> str: return json.dumps(values, ensure_ascii=False)


def load(path: Path) -> dict[str, Any]:
    if not path.exists(): return empty()
    try: return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error: raise ValueError(f"Não foi possível ler a configuração existente: {error}") from error


def render(data: dict[str, Any]) -> str:
    defaults = data["defaults"]
    lines = ["schema_version = 1", "", "[defaults]", f"api_base = {quote(defaults['api_base'])}", f"timeout_seconds = {defaults['timeout_seconds']}", f"max_retries = {defaults['max_retries']}"]
    for name, profile in sorted(data["profiles"].items()):
        lines += ["", f"[profiles.{name}]", f"vault_profile = {quote(profile['vault_profile'])}", f"vault_entry_path = {quote(profile['vault_entry_path'])}", f"vault_field = {quote(profile['vault_field'])}", f"access = {quote(profile['access'])}", f"allowed_operations = {list_toml(profile['allowed_operations'])}", f"allowed_attachment_roots = {list_toml(profile['allowed_attachment_roots'])}"]
    return "\n".join(lines) + "\n"


def validate(data: dict[str, Any]) -> tuple[bool, str]:
    defaults, profiles = data.get("defaults"), data.get("profiles")
    if not isinstance(defaults, dict) or not isinstance(profiles, dict): return False, "Seções defaults e profiles são obrigatórias."
    if not str(defaults.get("api_base", "")).startswith("https://api.todoist.com/api/v1"): return False, "A API deve usar https://api.todoist.com/api/v1."
    try:
        if float(defaults["timeout_seconds"]) <= 0 or int(defaults["max_retries"]) < 0: return False, "Timeout ou tentativas inválidos."
    except (KeyError, TypeError, ValueError): return False, "Timeout ou tentativas inválidos."
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name or not isinstance(profile, dict): return False, "Perfil inválido."
        if not all(isinstance(profile.get(key), str) and profile[key] for key in ("vault_profile", "vault_entry_path")): return False, f"Perfil {name}: KeePass Vault incompleto."
        if profile.get("vault_field") not in {"password", "notes"} or profile.get("access") not in {"read_only", "read_write"}: return False, f"Perfil {name}: campo ou acesso inválido."
        for key in ("allowed_operations", "allowed_attachment_roots"):
            if not isinstance(profile.get(key), list) or not all(isinstance(value, str) for value in profile[key]): return False, f"Perfil {name}: {key} inválido."
    return True, "Configuração Todoist válida."


def save(path: Path, data: dict[str, Any]) -> tuple[bool, str]:
    valid, message = validate(data)
    if not valid: return False, message
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(render(data), encoding="utf-8", newline="\n"); tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        if old is None: path.unlink(missing_ok=True)
        else: path.write_text(old, encoding="utf-8", newline="\n")
        return False, f"Falha ao salvar; alteração desfeita: {error}"
    return True, message


def screen(title: str, subtitle: str = "") -> None:
    print("\n" + "═" * 66); print(f"  ONMYŌJI  ·  TODOIST  ·  {title.upper()}")
    if subtitle: print(f"  {subtitle}")
    print("═" * 66)


def choose_profile(profiles: dict[str, Any], action: str) -> str | None:
    screen(action, "Escolha um perfil")
    for index, name in enumerate(sorted(profiles), 1): print(f"  {index}. {name}")
    print("  X. Voltar")
    value = input("Opção: ").strip().casefold()
    if value in {"x", "\x1b"}: return None
    names = sorted(profiles)
    return names[int(value) - 1] if value.isdigit() and 1 <= int(value) <= len(names) else None


def edit_profile(data: dict[str, Any], path: Path, name: str) -> bool:
    while True:
        profile = data["profiles"][name]
        screen("Editar perfil", name)
        print(f"  1. Nome: {name}\n  2. Perfil KeePass: {profile['vault_profile']}\n  3. Entrada KeePass: {profile['vault_entry_path']}\n  4. Campo KeePass: {profile['vault_field']}\n  5. Acesso: {profile['access']}\n  6. Operações permitidas: {'; '.join(profile['allowed_operations']) or 'todas conforme acesso'}\n  7. Diretórios de upload: {'; '.join(profile['allowed_attachment_roots']) or 'todos'}\n  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return True
        candidate = copy.deepcopy(data)
        target = candidate["profiles"][name]
        if choice == "1":
            value = input(f"Novo nome [{name}]: ").strip()
            if not value or value in candidate["profiles"]: print("Nome vazio ou já existente."); continue
            candidate["profiles"][value] = candidate["profiles"].pop(name); new_name = value
        elif choice in {"2", "3", "4"}:
            key = {"2": "vault_profile", "3": "vault_entry_path", "4": "vault_field"}[choice]; value = input(f"Novo valor [{target[key]}]: ").strip()
            if not value: continue
            target[key] = value; new_name = name
        elif choice == "5":
            value = input("1. Somente leitura  2. Leitura e escrita [1/2]: ").strip(); target["access"] = "read_write" if value == "2" else "read_only" if value == "1" else target["access"]; new_name = name
        elif choice in {"6", "7"}:
            key = "allowed_operations" if choice == "6" else "allowed_attachment_roots"; value = input("Itens separados por ; (vazio = todos): ").strip(); target[key] = [item.strip() for item in value.split(";") if item.strip()]; new_name = name
        else: print("Opção inválida."); continue
        ok, message = save(path, candidate); print(message if ok else f"Não salvo: {message}")
        if ok: data.clear(); data.update(candidate); name = new_name


def configure(root: Path) -> None:
    path = config_path(root)
    try: data = load(path)
    except ValueError as error: print(error); return
    while True:
        screen("Configuração", "Perfis de acesso ao Todoist")
        print("  1. Criar perfil\n  2. Editar perfil\n  3. Remover perfil\n  4. Ajustar padrão de rede\n  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = input("Nome do perfil [X cancela]: ").strip()
            if not name or name.casefold() in {"x", "\x1b"} or name in data["profiles"]: print("Nome inválido ou já existente."); continue
            candidate = copy.deepcopy(data); candidate["profiles"][name] = {"vault_profile": input("Perfil KeePass: ").strip(), "vault_entry_path": input("Entrada KeePass [APIs/Todoist]: ").strip() or "APIs/Todoist", "vault_field": "password", "access": "read_only", "allowed_operations": [], "allowed_attachment_roots": []}
            ok, message = save(path, candidate); print(message if ok else f"Não salvo: {message}")
            if ok: data = candidate; edit_profile(data, path, name)
        elif choice == "2":
            if not data["profiles"]: print("Nenhum perfil criado."); continue
            name = choose_profile(data["profiles"], "Editar perfil")
            if name: edit_profile(data, path, name)
        elif choice == "3":
            if not data["profiles"]: print("Nenhum perfil criado."); continue
            name = choose_profile(data["profiles"], "Remover perfil")
            if name and input(f"Digite REMOVER para excluir {name}: ").strip() == "REMOVER":
                candidate = copy.deepcopy(data); del candidate["profiles"][name]; ok, message = save(path, candidate); print(message if ok else f"Não removido: {message}")
                if ok: data = candidate
        elif choice == "4":
            candidate = copy.deepcopy(data)
            try: candidate["defaults"]["timeout_seconds"] = float(input(f"Timeout [{data['defaults']['timeout_seconds']}]: ").strip() or data["defaults"]["timeout_seconds"]); candidate["defaults"]["max_retries"] = int(input(f"Tentativas [{data['defaults']['max_retries']}]: ").strip() or data["defaults"]["max_retries"])
            except ValueError: print("Valor numérico inválido."); continue
            ok, message = save(path, candidate); print(message if ok else f"Não salvo: {message}")
            if ok: data = candidate
        else: print("Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root", type=Path, default=ROOT); parser.add_argument("--action", choices=["describe", "status", "configure"], default="configure"); parser.add_argument("--json", action="store_true"); args = parser.parse_args(); path = config_path(args.onmyoji_root)
    if args.action == "describe":
        value = {"id": "todoist", "title": "Todoist", "description": "Tarefas, projetos e sincronização com token no KeePass Vault."}; print(json.dumps(value, ensure_ascii=False) if args.json else value["title"]); return 0
    if args.action == "status":
        try: data = load(path)
        except ValueError as error: print(json.dumps({"configured": False, "valid": False, "message": str(error)}, ensure_ascii=False) if args.json else error); return 2
        valid, message = validate(data); value = {"configured": bool(data["profiles"]), "valid": valid, "message": message}; print(json.dumps(value, ensure_ascii=False) if args.json else message); return 0 if valid else 2
    configure(args.onmyoji_root); return 0


if __name__ == "__main__": raise SystemExit(main())
