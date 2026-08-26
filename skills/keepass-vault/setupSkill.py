#!/usr/bin/env python3
"""Configurador interativo e transacional da skill KeePass Vault."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parent
MODEL = SKILL_DIR / "configs" / "keepass.toml.model"
READ_OPS = ["list", "list.totp", "read", "attachment.export"]
WRITE_OPS = READ_OPS + ["add", "edit", "delete", "copy", "attachment.import", "attachment.delete"]


class WizardCancelled(Exception): pass


def root_for(value: str | None) -> Path: return Path(value).resolve() if value else SKILL_DIR.parents[1]
def config_for(root: Path) -> Path: return root / "configs" / "keepass.toml"
def describe() -> dict[str, object]: return {"id": "keepass-vault", "title": "KeePass Vault", "description": "Cofres KeePassXC, TOTPs e anexos", "actions": ["configure"]}
def quote(value: str) -> str: return json.dumps(value, ensure_ascii=False)
def list_toml(values: list[str]) -> str: return "[" + ", ".join(quote(value) for value in values) + "]"


def empty_config() -> dict[str, Any]: return {"schema_version": 1, "defaults": {"timeout_seconds": 30}, "vaults": {}, "profiles": {}}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists(): return empty_config()
    try: return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error: raise ValueError(f"TOML inválido: {error}") from error


def render(data: dict[str, Any]) -> str:
    lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {int(data['defaults']['timeout_seconds'])}"]
    for name, vault in sorted(data["vaults"].items()):
        lines += ["", f"[vaults.{name}]", f"cli_command = {list_toml(vault['cli_command'])}", "", f"[vaults.{name}.database]", f"windows = {quote(vault['database']['windows'])}", f"linux = {quote(vault['database']['linux'])}"]
    for name, profile in sorted(data["profiles"].items()):
        auth = profile["auth"]
        lines += ["", f"[profiles.{name}]", f"vault = {quote(profile['vault'])}", f"access = {quote(profile['access'])}", f"allowed_operations = {list_toml(profile['allowed_operations'])}", f"allowed_entry_roots = {list_toml(profile['allowed_entry_roots'])}", f"allowed_attachment_roots = {list_toml(profile['allowed_attachment_roots'])}", "", f"[profiles.{name}.auth]", f"allowed_modes = {list_toml(auth['allowed_modes'])}", "", f"[profiles.{name}.auth.windows]", f"mode = {quote(auth['windows']['mode'])}", f"target = {quote(auth['windows'].get('target', ''))}", "", f"[profiles.{name}.auth.linux]", f"mode = {quote(auth['linux']['mode'])}", f"command = {list_toml(auth['linux'].get('command', []))}"]
    return "\n".join(lines) + "\n"


def validate(data: dict[str, Any]) -> tuple[bool, str]:
    if data.get("schema_version") != 1: return False, "schema_version deve ser 1."
    if not isinstance(data.get("defaults", {}).get("timeout_seconds"), int) or data["defaults"]["timeout_seconds"] <= 0: return False, "timeout_seconds deve ser inteiro positivo."
    if not isinstance(data.get("vaults"), dict) or not isinstance(data.get("profiles"), dict): return False, "As seções vaults e profiles são obrigatórias."
    for name, profile in data["profiles"].items():
        if profile.get("vault") not in data["vaults"]: return False, f"O perfil {name} referencia um vault inexistente."
        vault = data["vaults"][profile["vault"]]
        if not vault.get("cli_command") or not all(isinstance(value, str) and value for value in vault["cli_command"]): return False, f"O executável do vault {profile['vault']} é obrigatório."
        executable = vault["cli_command"][0]
        if not (Path(executable).is_file() or shutil.which(executable)):
            return False, f"Executável KeePassXC não encontrado: {executable}. Instale-o ou informe seu caminho completo."
        database = vault.get("database", {})
        if not isinstance(database.get("windows"), str) or not isinstance(database.get("linux"), str): return False, f"O vault {profile['vault']} precisa de caminhos Windows e Linux (um pode ficar vazio)."
        current = database["windows" if os.name == "nt" else "linux"]
        if not current: return False, f"Informe a localização do cofre para a plataforma atual no vault {profile['vault']}."
        if not Path(current).is_file(): return False, f"Arquivo KDBX não encontrado: {current}. Corrija o caminho do vault {profile['vault']}."
        auth = profile.get("auth", {})
        if not isinstance(auth.get("windows"), dict) or not isinstance(auth.get("linux"), dict): return False, f"A autenticação do perfil {name} está incompleta."
    return True, "Configuração válida."


def save_transactional(path: Path, data: dict[str, Any]) -> tuple[bool, str]:
    valid, message = validate(data)
    if not valid: return False, message
    backup = path.with_suffix(".toml.backup")
    existed = path.exists()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if existed: shutil.copy2(path, backup)
        path.write_text(render(data), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        if existed: shutil.copy2(backup, path)
        else: path.unlink(missing_ok=True)
        return False, f"Falha ao salvar; alteração desfeita: {error}"
    finally: backup.unlink(missing_ok=True)
    return True, "Configuração salva e validada."


def ask(label: str, current: str = "", required: bool = False) -> str:
    suffix = f" [{current}]" if current else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value.casefold() == "x" or value == "\x1b": raise WizardCancelled
        if value: return value
        if current: return current
        if not required: return ""
        print("Este valor é obrigatório.")


def ask_choice(label: str, choices: dict[str, str], current: str) -> str:
    print(label)
    for key, value in choices.items(): print(f"  {key}. {value}")
    while True:
        value = input(f"Opção [{current}]: ").strip() or current
        if value.casefold() == "x" or value == "\x1b": raise WizardCancelled
        if value in choices: return value
        print("Opção inválida.")


def edit_profile(data: dict[str, Any], path: Path, profile_name: str) -> None:
    candidate = json.loads(json.dumps(data))
    profile = candidate["profiles"].get(profile_name, {})
    current_vault = profile.get("vault", profile_name)
    vault = candidate["vaults"].get(current_vault, {"cli_command": ["keepassxc-cli"], "database": {"windows": "", "linux": ""}})
    print(f"\nPerfil: {profile_name}")
    vault_name = ask("Identificador do vault", current_vault, True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", vault_name):
        print("Nome de vault inválido; nenhuma alteração foi salva.")
        return
    executable = ask("Executável do KeePassXC", vault["cli_command"][0] if vault["cli_command"] else "keepassxc-cli", True)
    platform = "windows" if os.name == "nt" else "linux"
    platform_name = "Windows" if platform == "windows" else "Linux"
    current_path = ask(f"Arquivo KDBX no {platform_name}", vault["database"].get(platform, ""), True)
    windows_path = current_path if platform == "windows" else vault["database"].get("windows", "")
    linux_path = current_path if platform == "linux" else vault["database"].get("linux", "")
    access = ask_choice("Acesso do perfil", {"1": "Somente leitura", "2": "Leitura e escrita"}, "2" if profile.get("access") == "read_write" else "1")
    existing_auth = profile.get("auth", {})
    windows = existing_auth.get("windows", {"mode": "windows_credential_manager", "target": f"Onmyoji/KeePass/{vault_name}"})
    linux = existing_auth.get("linux", {"mode": "command", "command": ["secret-tool", "lookup", "service", "onmyoji", "vault", vault_name]})
    if platform == "windows":
        selection = ask_choice("Autenticação no Windows", {"1": "Windows Credential Manager", "2": "Senha via stdin", "3": "Prompt interativo"}, {"windows_credential_manager": "1", "stdin": "2", "prompt": "3"}.get(windows.get("mode"), "1"))
        windows = {"mode": {"1": "windows_credential_manager", "2": "stdin", "3": "prompt"}[selection], "target": ask("Target da credencial Windows", windows.get("target", f"Onmyoji/KeePass/{vault_name}"), True) if selection == "1" else ""}
    else:
        selection = ask_choice("Autenticação no Linux", {"1": "secret-tool", "2": "Senha via stdin", "3": "Prompt interativo"}, "1" if linux.get("mode") == "command" else {"stdin": "2", "prompt": "3"}.get(linux.get("mode"), "1"))
        linux = {"mode": "command" if selection == "1" else {"2": "stdin", "3": "prompt"}[selection], "command": ["secret-tool", "lookup", "service", "onmyoji", "vault", vault_name] if selection == "1" else []}
    entry_roots = ask("Raízes de entradas permitidas (separe por ;, vazio = todas)", ";".join(profile.get("allowed_entry_roots", [])))
    attachment_roots = ask("Diretórios permitidos para anexos (separe por ;, vazio = todos)", ";".join(profile.get("allowed_attachment_roots", [])))
    candidate["vaults"][vault_name] = {"cli_command": [executable], "database": {"windows": windows_path, "linux": linux_path}}
    candidate["profiles"][profile_name] = {"vault": vault_name, "access": "read_write" if access == "2" else "read_only", "allowed_operations": WRITE_OPS if access == "2" else READ_OPS, "allowed_entry_roots": [item.strip() for item in entry_roots.split(";") if item.strip()], "allowed_attachment_roots": [item.strip() for item in attachment_roots.split(";") if item.strip()], "auth": {"allowed_modes": ["configured", "stdin", "prompt", "windows_credential_manager"], "windows": windows, "linux": linux}}
    ok, message = save_transactional(path, candidate)
    if ok: data.clear(); data.update(candidate)
    print(message if ok else f"Não salvo: {message}")


def configure(root: Path) -> int:
    path = config_for(root)
    try: data = load_config(path)
    except ValueError as error: print(error); return 2
    if not path.exists():
        ok, message = save_transactional(path, data)
        if not ok: print(f"Não foi possível criar a configuração inicial: {message}"); return 2
        print(f"Configuração inicial criada: {path}")
    while True:
        print("\nKeePass Vault — configuração")
        print("  1. Criar perfil")
        print("  2. Editar perfil")
        print("  3. Remover perfil")
        print("  4. Ajustar timeout")
        print("  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "1":
            try:
                name = ask("Nome do perfil (letras, números, _ ou -)", required=True)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name): print("Nome inválido.")
                elif name in data["profiles"]: print("Esse perfil já existe.")
                else: edit_profile(data, path, name)
            except WizardCancelled: print("Configuração cancelada; nenhuma alteração foi gravada.")
        elif choice == "2":
            if not data["profiles"]: print("Nenhum perfil criado."); continue
            print("Perfis: " + ", ".join(sorted(data["profiles"])))
            try:
                name = ask("Perfil", required=True)
                if name in data["profiles"]: edit_profile(data, path, name)
                else: print("Perfil não encontrado.")
            except WizardCancelled: print("Configuração cancelada; nenhuma alteração foi gravada.")
        elif choice == "3":
            try:
                name = ask("Perfil a remover", required=True)
                if name not in data["profiles"]: print("Perfil não encontrado.")
                elif input(f"Remover o perfil {name}? [s/N]: ").strip().casefold() == "s":
                    candidate = json.loads(json.dumps(data)); del candidate["profiles"][name]
                    ok, message = save_transactional(path, candidate)
                    if ok: data = candidate
                    print(message if ok else f"Não removido: {message}")
            except WizardCancelled: print("Configuração cancelada; nenhuma alteração foi gravada.")
        elif choice == "4":
            try:
                value = ask("Timeout em segundos", str(data["defaults"]["timeout_seconds"]), True)
                candidate = json.loads(json.dumps(data)); candidate["defaults"]["timeout_seconds"] = int(value)
                ok, message = save_transactional(path, candidate)
                if ok: data = candidate
                print(message if ok else f"Não salvo: {message}")
            except ValueError: print("Informe um número inteiro.")
            except WizardCancelled: print("Configuração cancelada; nenhuma alteração foi gravada.")
        else: print("Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root"); parser.add_argument("--action", choices=["describe", "status", "configure"], default="configure"); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
    root = root_for(args.onmyoji_root)
    if args.action == "describe": print(json.dumps(describe(), ensure_ascii=False) if args.json else describe()["title"]); return 0
    if args.action == "status":
        try: ok, message = validate(load_config(config_for(root)))
        except ValueError as error: ok, message = False, str(error)
        print(message); return 0 if ok else 2
    return configure(root)


if __name__ == "__main__": raise SystemExit(main())
