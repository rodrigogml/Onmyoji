#!/usr/bin/env python3
"""Configurador interativo de perfis Google OAuth."""
from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))

from setup_profile_api import Field, handle as handle_profile, simple_load, simple_save, wrapper_test
from setup_ui import choose_keepass_profile, item, note, prompt, result, screen


DEFAULTS = {
    "api_base": "https://www.googleapis.com",
    "oauth_base": "https://oauth2.googleapis.com",
    "scopes": ["openid", "email", "profile", "https://mail.google.com/", "https://www.googleapis.com/auth/contacts", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/calendar"],
    "timeout_seconds": 30,
    "max_retries": 2,
    "page_size": 100,
    "user_id": "me",
    "download_dir": "",
}
PROFILE_FIELDS = (
    Field("oauth_profile", "Perfil OAuth", required=True),
    Field("vault_profile", "Perfil KeePass", required=True),
    Field("vault_entry_path", "Entrada KeePass", required=True),
    Field("credentials_file", "Arquivo JSON OAuth Desktop (legado, opcional)", ""),
    Field("client_id_field", "Campo client_id", "username"),
    Field("client_secret_field", "Campo client_secret", "password"),
    Field("profiles_field", "Campo dos tokens", "notes"),
)


def config_path(root: Path) -> Path:
    return root / "configs" / "google.toml"


def shikigami_identity(root: Path) -> str:
    try:
        data = tomllib.loads((root / "shikigami" / "instance.toml").read_text(encoding="utf-8"))
        identity = data.get("shikigami", {}).get("identity", "")
        if isinstance(identity, str) and identity.strip():
            return identity.strip()
    except (OSError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return root.name.removeprefix("Shikigami-") or "Shikigami"


def suggested_google_vault_entry(root: Path, profile: str) -> str:
    profile_name = "".join(part[:1].upper() + part[1:] for part in re.findall(r"[A-Za-z0-9]+", profile))
    return f"APIs/{shikigami_identity(root)}:GoogleOAuth:{profile_name}"


def profile_test(name: str, file: Path, data: dict[str, object]) -> tuple[bool, str]:
    return wrapper_test(SKILL / "scripts" / "google.py", file, name, {"version": 1, "service": "gmail", "operation": "profile.get"}, "Google")


def choose_profile(profiles: dict[str, object], action: str) -> str | None:
    names = sorted(profiles)
    screen("Google", action, "Escolha um perfil ou pressione X para voltar")
    for index, name in enumerate(names, 1):
        item(f"{index}.", name)
    item("X.", "Voltar")
    value = prompt("Perfil: ").strip().casefold()
    if value in {"x", "\x1b"}:
        return None
    if value.isdigit() and 1 <= int(value) <= len(names):
        return names[int(value) - 1]
    result(False, "Opção inválida.")
    return None


def value_for(root: Path, field: Field, profile_name: str, current: object = "") -> str | None:
    default = str(current or (profile_name if field.name == "oauth_profile" else suggested_google_vault_entry(root, profile_name) if field.name == "vault_entry_path" else field.default or ""))
    if field.name == "vault_profile":
        return choose_keepass_profile(root, default)
    value = prompt(f"{field.description} [{default}]: ").strip() or default
    if value.casefold() in {"x", "\x1b"}:
        return None
    if field.required and not value:
        result(False, f"{field.description} é obrigatório.")
        return None
    return value


def create_profile(root: Path, path: Path, data: dict[str, object]) -> None:
    profiles = data["profiles"]
    assert isinstance(profiles, dict)
    name = prompt("Nome do perfil [X cancela]: ").strip()
    if name.casefold() in {"x", "\x1b"}:
        result(False, "Criação cancelada.")
        return
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name) or name in profiles:
        result(False, "Nome inválido ou já existente.")
        return
    profile: dict[str, str] = {}
    note("Recomendado: deixe o arquivo JSON vazio. O client_id e o client_secret serão lidos da entrada KeePass selecionada. Use o JSON apenas para uma credencial OAuth Desktop legada.")
    for field in PROFILE_FIELDS:
        value = value_for(root, field, name)
        if value is None:
            result(False, "Criação cancelada; nenhuma alteração foi gravada.")
            return
        profile[field.name] = value
    profiles[name] = profile
    ok, message = simple_save(path, data)
    result(ok, message)


def edit_profile(root: Path, path: Path, data: dict[str, object], name: str) -> None:
    profiles = data["profiles"]
    assert isinstance(profiles, dict)
    profile = profiles[name]
    if not isinstance(profile, dict):
        result(False, "Perfil inválido.")
        return
    while True:
        screen("Google", "Editar perfil", name)
        for index, field in enumerate(PROFILE_FIELDS, 1):
            item(f"{index}.", field.description, str(profile.get(field.name, "")))
        item("X.", "Voltar")
        choice = prompt("Campo: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if not choice.isdigit() or not 1 <= int(choice) <= len(PROFILE_FIELDS):
            result(False, "Opção inválida.")
            continue
        field = PROFILE_FIELDS[int(choice) - 1]
        value = value_for(root, field, name, profile.get(field.name, ""))
        if value is None:
            result(False, "Edição cancelada; nenhuma alteração foi gravada.")
            continue
        profile[field.name] = value
        ok, message = simple_save(path, data)
        result(ok, message)


def configure(root: Path) -> None:
    path = config_path(root)
    while True:
        data = simple_load(path, DEFAULTS)
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            result(False, "A seção profiles da configuração é inválida.")
            return
        screen("Google", "Configuração", "Perfis OAuth para Gmail, Drive, Calendar e Contatos")
        item("1.", "Criar perfil")
        item("2.", "Editar perfil")
        item("3.", "Excluir perfil")
        item("4.", "Testar perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            create_profile(root, path, data)
            continue
        if action not in {"2", "3", "4"}:
            result(False, "Opção inválida.")
            continue
        if not profiles:
            result(False, "Nenhum perfil configurado.")
            continue
        labels = {"2": "Editar perfil", "3": "Excluir perfil", "4": "Testar perfil"}
        name = choose_profile(profiles, labels[action])
        if name is None:
            continue
        if action == "2":
            edit_profile(root, path, data, name)
        elif action == "3":
            if prompt(f"Digite EXCLUIR para remover {name}: ").strip() == "EXCLUIR":
                del profiles[name]
                ok, message = simple_save(path, data)
                result(ok, message)
            else:
                result(False, "Operação cancelada.")
        else:
            ok, message = profile_test(name, path, data)
            result(ok, message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", default="configure")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--onmyoji-root", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--set", action="append")
    parser.add_argument("--confirm-delete")
    args = parser.parse_args()
    root = args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1]
    info = {"id": "google", "title": "Google", "description": "Gmail, Drive, Calendar e Contatos via OAuth e KeePass Vault."}
    if args.action == "describe":
        print(json.dumps(info, ensure_ascii=False) if args.json else info["title"])
        return 0
    if args.action == "status":
        print(json.dumps({"configured": bool(simple_load(config_path(root), DEFAULTS)["profiles"]), "valid": True}, ensure_ascii=False))
        return 0
    if args.action.startswith("profile-"):
        code, value = handle_profile(action=args.action, profile_name=args.profile, values=args.set, confirm_delete=args.confirm_delete, path=config_path(root), load=lambda path: simple_load(path, DEFAULTS), save=simple_save, fields=PROFILE_FIELDS, test=profile_test)
        print(json.dumps(value, ensure_ascii=False))
        return code
    configure(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
