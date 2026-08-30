#!/usr/bin/env python3
"""Configurador interativo da skill Todoist."""
from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MODEL = Path(__file__).resolve().parent / "configs" / "todoist.toml.model"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_ui import choose_keepass_profile, item, prompt, result, screen, suggested_vault_entry

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8", errors="replace")


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


def public_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Return only operator-safe profile metadata; never resolve the Vault secret."""
    return {"name": name, "vault_profile": profile["vault_profile"], "vault_entry_path": profile["vault_entry_path"], "vault_field": profile["vault_field"], "access": profile["access"], "allowed_operations": profile["allowed_operations"], "allowed_attachment_roots": profile["allowed_attachment_roots"]}


def profile_name(value: str | None) -> str:
    name = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name): raise ValueError("O nome do perfil deve usar somente letras, números, hífen ou sublinhado.")
    return name


def split_values(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def profile_result(path: Path, args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    """Official non-interactive profile contract used by onmyoji-control."""
    try: data = load(path)
    except ValueError as error: return 2, {"ok": False, "error": {"code": "invalid_config", "message": str(error)}}
    action = args.action
    if action == "profile-schema":
        return 0, {"ok": True, "fields": [{"name": "vault_profile", "description": "Perfil KeePass", "required_on_create": True}, {"name": "vault_entry_path", "description": "Entrada KeePass", "required_on_create": True}, {"name": "vault_field", "description": "Campo do token", "default": "password"}, {"name": "access", "description": "Acesso", "default": "read_only"}, {"name": "allowed_operations", "description": "Operações permitidas", "default": []}, {"name": "allowed_attachment_roots", "description": "Raízes de anexos", "default": []}]}
    if action == "profile-list":
        valid, message = validate(data)
        return (0 if valid else 2), {"ok": valid, "configured": bool(data["profiles"]), "profiles": [public_profile(name, profile) for name, profile in sorted(data["profiles"].items())], "message": message}
    try: name = profile_name(args.profile)
    except ValueError as error: return 2, {"ok": False, "error": {"code": "invalid_profile_name", "message": str(error)}}
    exists = name in data["profiles"]
    if action == "profile-test":
        if not exists: return 2, {"ok": False, "error": {"code": "profile_not_found", "message": "Perfil Todoist não encontrado."}}
        wrapper = Path(__file__).resolve().parent / "scripts" / "todoist.py"
        try:
            process = subprocess.run([sys.executable, str(wrapper), "--config", str(path), "--profile", name], input=json.dumps({"version": 1, "operation": "user.get"}), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=45)
            payload = json.loads(process.stdout)
        except subprocess.TimeoutExpired: return 2, {"ok": False, "error": {"code": "timeout", "message": "O teste Todoist excedeu 45 segundos."}}
        except (OSError, json.JSONDecodeError): return 2, {"ok": False, "error": {"code": "test_protocol_error", "message": "O teste Todoist não retornou JSON válido."}}
        if process.returncode or not payload.get("ok", False):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            return 2, {"ok": False, "profile": name, "error": {"code": error.get("code", "test_failed"), "message": error.get("message", "O teste de conexão Todoist falhou.")}}
        return 0, {"ok": True, "profile": name, "message": "Conexão e credencial Todoist confirmadas."}
    if action == "profile-create":
        if exists: return 2, {"ok": False, "error": {"code": "profile_exists", "message": "Já existe um perfil com este nome."}}
        if not args.vault_profile or not args.vault_entry: return 2, {"ok": False, "error": {"code": "missing_required_field", "message": "vault-profile e vault-entry são obrigatórios para criar um perfil."}}
        candidate = copy.deepcopy(data)
        candidate["profiles"][name] = {"vault_profile": args.vault_profile.strip(), "vault_entry_path": args.vault_entry.strip(), "vault_field": args.vault_field or "password", "access": args.access or "read_only", "allowed_operations": split_values(args.operations), "allowed_attachment_roots": split_values(args.attachment_roots)}
    elif action == "profile-update":
        if not exists: return 2, {"ok": False, "error": {"code": "profile_not_found", "message": "Perfil Todoist não encontrado."}}
        candidate = copy.deepcopy(data); profile = candidate["profiles"][name]
        fields = {"vault_profile": args.vault_profile, "vault_entry_path": args.vault_entry, "vault_field": args.vault_field, "access": args.access}
        for key, value in fields.items():
            if value is not None:
                profile[key] = value.strip() if isinstance(value, str) else value
        if args.operations is not None: profile["allowed_operations"] = split_values(args.operations)
        if args.attachment_roots is not None: profile["allowed_attachment_roots"] = split_values(args.attachment_roots)
    elif action == "profile-delete":
        if not exists: return 2, {"ok": False, "error": {"code": "profile_not_found", "message": "Perfil Todoist não encontrado."}}
        if args.confirm_delete != "DELETE": return 2, {"ok": False, "error": {"code": "confirmation_required", "message": "Confirme a exclusão com --confirm-delete DELETE após pedido explícito do usuário."}}
        candidate = copy.deepcopy(data); del candidate["profiles"][name]
    else: return 2, {"ok": False, "error": {"code": "unsupported_action", "message": "Ação de perfil não suportada."}}
    ok, message = save(path, candidate)
    if not ok: return 2, {"ok": False, "error": {"code": "validation_failed", "message": message}}
    response: dict[str, Any] = {"ok": True, "message": message, "profile": name}
    if action != "profile-delete": response["configuration"] = public_profile(name, candidate["profiles"][name])
    return 0, response


def choose_profile(profiles: dict[str, Any], action: str) -> str | None:
    screen("Todoist", action, "Escolha um perfil ou pressione X para voltar")
    for index, name in enumerate(sorted(profiles), 1): item(f"{index}.", name)
    item("X.", "Voltar")
    value = prompt("Opção: ").strip().casefold()
    if value in {"x", "\x1b"}: return None
    names = sorted(profiles)
    return names[int(value) - 1] if value.isdigit() and 1 <= int(value) <= len(names) else None


def edit_profile(data: dict[str, Any], path: Path, name: str) -> bool:
    while True:
        profile = data["profiles"][name]
        screen("Todoist", "Editar perfil", name)
        item("1.", "Nome", name); item("2.", "Perfil KeePass", profile['vault_profile']); item("3.", "Entrada KeePass", profile['vault_entry_path']); item("4.", "Campo KeePass", profile['vault_field']); item("5.", "Acesso", profile['access']); item("6.", "Operações permitidas", '; '.join(profile['allowed_operations']) or 'todas conforme acesso'); item("7.", "Diretórios de upload", '; '.join(profile['allowed_attachment_roots']) or 'todos'); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return True
        candidate = copy.deepcopy(data)
        target = candidate["profiles"][name]
        if choice == "1":
            value = prompt(f"Novo nome [{name}]: ").strip()
            if not value or value in candidate["profiles"]: result(False, "Nome vazio ou já existente."); continue
            candidate["profiles"][value] = candidate["profiles"].pop(name); new_name = value
        elif choice in {"2", "3", "4"}:
            key = {"2": "vault_profile", "3": "vault_entry_path", "4": "vault_field"}[choice]; value = choose_keepass_profile(path.parents[1], target[key]) if key == "vault_profile" else prompt(f"Novo valor [{target[key]}]: ").strip()
            if not value: continue
            target[key] = value; new_name = name
        elif choice == "5":
            value = prompt("1. Somente leitura  2. Leitura e escrita [1/2]: ").strip(); target["access"] = "read_write" if value == "2" else "read_only" if value == "1" else target["access"]; new_name = name
        elif choice in {"6", "7"}:
            key = "allowed_operations" if choice == "6" else "allowed_attachment_roots"; value = prompt("Itens separados por ; (vazio = todos): ").strip(); target[key] = [item.strip() for item in value.split(";") if item.strip()]; new_name = name
        else: result(False, "Opção inválida."); continue
        ok, message = save(path, candidate); result(ok, message if ok else f"Não salvo: {message}")
        if ok: data.clear(); data.update(candidate); name = new_name


def configure(root: Path) -> None:
    path = config_path(root)
    try: data = load(path)
    except ValueError as error: result(False, str(error)); return
    while True:
        screen("Todoist", "Configuração", "Perfis de acesso ao Todoist")
        item("1.", "Criar perfil"); item("2.", "Editar perfil"); item("3.", "Remover perfil"); item("4.", "Ajustar padrão de rede"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = prompt("Nome do perfil [X cancela]: ").strip()
            if not name or name.casefold() in {"x", "\x1b"} or name in data["profiles"]: result(False, "Nome inválido ou já existente."); continue
            vault_profile = choose_keepass_profile(root)
            if vault_profile is None: continue
            suggested_entry = suggested_vault_entry("Todoist", name)
            candidate = copy.deepcopy(data); candidate["profiles"][name] = {"vault_profile": vault_profile, "vault_entry_path": prompt(f"Entrada KeePass [{suggested_entry}]: ").strip() or suggested_entry, "vault_field": "password", "access": "read_only", "allowed_operations": [], "allowed_attachment_roots": []}
            ok, message = save(path, candidate); result(ok, message if ok else f"Não salvo: {message}")
            if ok: data = candidate; edit_profile(data, path, name)
        elif choice == "2":
            if not data["profiles"]: result(False, "Nenhum perfil criado."); continue
            name = choose_profile(data["profiles"], "Editar perfil")
            if name: edit_profile(data, path, name)
        elif choice == "3":
            if not data["profiles"]: result(False, "Nenhum perfil criado."); continue
            name = choose_profile(data["profiles"], "Remover perfil")
            if name and prompt(f"Digite REMOVER para excluir {name}: ").strip() == "REMOVER":
                candidate = copy.deepcopy(data); del candidate["profiles"][name]; ok, message = save(path, candidate); result(ok, message if ok else f"Não removido: {message}")
                if ok: data = candidate
        elif choice == "4":
            candidate = copy.deepcopy(data)
            try: candidate["defaults"]["timeout_seconds"] = float(prompt(f"Timeout [{data['defaults']['timeout_seconds']}]: ").strip() or data["defaults"]["timeout_seconds"]); candidate["defaults"]["max_retries"] = int(prompt(f"Tentativas [{data['defaults']['max_retries']}]: ").strip() or data["defaults"]["max_retries"])
            except ValueError: result(False, "Valor numérico inválido."); continue
            ok, message = save(path, candidate); result(ok, message if ok else f"Não salvo: {message}")
            if ok: data = candidate
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, default=ROOT)
    parser.add_argument("--action", choices=["describe", "status", "configure", "profile-schema", "profile-list", "profile-create", "profile-update", "profile-delete", "profile-test"], default="configure")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--vault-profile")
    parser.add_argument("--vault-entry")
    parser.add_argument("--vault-field", choices=["password", "notes"])
    parser.add_argument("--access", choices=["read_only", "read_write"])
    parser.add_argument("--operations", help="Operações separadas por ponto e vírgula; vazio limpa a restrição.")
    parser.add_argument("--attachment-roots", help="Raízes separadas por ponto e vírgula; vazio libera todas.")
    parser.add_argument("--confirm-delete")
    args = parser.parse_args(); path = config_path(args.onmyoji_root)
    if args.action == "describe":
        value = {"id": "todoist", "title": "Todoist", "description": "Tarefas, projetos e sincronização com token no KeePass Vault."}; print(json.dumps(value, ensure_ascii=False) if args.json else value["title"]); return 0
    if args.action == "status":
        try: data = load(path)
        except ValueError as error: print(json.dumps({"configured": False, "valid": False, "message": str(error)}, ensure_ascii=False) if args.json else error); return 2
        valid, message = validate(data); value = {"configured": bool(data["profiles"]), "valid": valid, "profiles": sorted(data["profiles"]), "message": message}; print(json.dumps(value, ensure_ascii=False) if args.json else message); return 0 if valid else 2
    if args.action.startswith("profile-"):
        code, value = profile_result(path, args)
        print(json.dumps(value, ensure_ascii=False) if args.json else value.get("message", value.get("error", {}).get("message", "Falha ao processar perfil.")))
        return code
    configure(args.onmyoji_root); return 0


if __name__ == "__main__": raise SystemExit(main())
