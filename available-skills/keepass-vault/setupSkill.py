#!/usr/bin/env python3
"""Configurador interativo e transacional da skill KeePass Vault."""
from __future__ import annotations

import argparse
import ctypes
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

class Ui:
    enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
    reset = "\x1b[0m"; bold = "\x1b[1m"; violet = "\x1b[38;5;141m"; cyan = "\x1b[38;5;80m"; slate = "\x1b[38;5;245m"; green = "\x1b[38;5;78m"; red = "\x1b[38;5;203m"
    @classmethod
    def text(cls, value: str, *styles: str) -> str: return "".join(styles) + value + cls.reset if cls.enabled and styles else value


def screen(title: str, subtitle: str = "") -> None:
    width = max(56, min(shutil.get_terminal_size((78, 24)).columns, 96))
    try:
        "╭─╮│╰╯".encode(sys.stdout.encoding or "utf-8")
        left, line, right, side, bottom_left, bottom_right = "╭", "─", "╮", "│", "╰", "╯"
    except UnicodeEncodeError:
        left, line, right, side, bottom_left, bottom_right = "+", "-", "+", "|", "+", "+"
    print("\n" + Ui.text(left + line * (width - 2) + right, Ui.violet))
    print(side + Ui.text(f" KEEPASS VAULT  /  {title.upper()}".ljust(width - 2), Ui.bold, Ui.violet) + side)
    if subtitle: print(side + " " + Ui.text(subtitle[:width - 4].ljust(width - 4), Ui.slate) + " " + side)
    print(Ui.text(bottom_left + line * (width - 2) + bottom_right, Ui.violet))


def menu_item(key: str, label: str, value: str = "") -> None:
    shortcut = Ui.text(f"{key:>4}", Ui.bold, Ui.cyan)
    print(f"  {shortcut}  {label:<30}" + (Ui.text(value, Ui.slate) if value else ""))


def result(ok: bool, message: str) -> None:
    label, color = ("+ OK", Ui.green) if ok else ("! ERRO", Ui.red)
    print("\n    " + Ui.text(label, Ui.bold, color) + "  " + Ui.text(message, color))


def prompt(label: str) -> str:
    try: "›".encode(sys.stdout.encoding or "utf-8"); marker = "›"
    except UnicodeEncodeError: marker = ">"
    return input(Ui.text(f"{marker} {label}", Ui.bold, Ui.violet))

SKILL_DIR = Path(__file__).resolve().parent
MODEL = SKILL_DIR / "configs" / "keepass.toml.model"
READ_OPS = ["list", "list.totp", "read", "attachment.export"]
WRITE_OPS = READ_OPS + ["add", "edit", "delete", "copy", "attachment.import", "attachment.delete"]


class WizardCancelled(Exception): pass


def root_for(value: str | None) -> Path: return Path(value).resolve() if value else SKILL_DIR.parents[1]
def config_for(root: Path) -> Path: return root / "configs" / "keepass.toml"
def local_vault_for(root: Path, vault_name: str) -> Path: return root / "configs" / "vaults" / f"{vault_name}.kdbx"
def describe() -> dict[str, object]: return {"id": "keepass-vault", "title": "KeePass Vault", "description": "Cofres KeePassXC, TOTPs e anexos", "actions": ["configure"]}
def quote(value: str) -> str: return json.dumps(value, ensure_ascii=False)
def list_toml(values: list[str]) -> str: return "[" + ", ".join(quote(value) for value in values) + "]"


def empty_config() -> dict[str, Any]: return {"schema_version": 1, "defaults": {"timeout_seconds": 30}, "vaults": {}, "profiles": {}}


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists(): return empty_config()
    try: return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error: raise ValueError(f"TOML inválido: {error}") from error


def remove_legacy_model_example(data: dict[str, Any]) -> bool:
    """Remove apenas o perfil de exemplo literal criado pelos primeiros setups."""
    profile = data.get("profiles", {}).get("example")
    vault = data.get("vaults", {}).get("example")
    if not isinstance(profile, dict) or not isinstance(vault, dict): return False
    database = vault.get("database", {})
    matches = profile.get("vault") == "example" and vault.get("cli_command") == ["keepassxc-cli"] and database == {"windows": "C:/caminho/para/cofre.kdbx", "linux": "/caminho/para/cofre.kdbx"}
    if not matches: return False
    del data["profiles"]["example"]
    if not any(item.get("vault") == "example" for item in data["profiles"].values()): del data["vaults"]["example"]
    return True


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
        value = prompt(f"{label}{suffix}: ").strip()
        if value.casefold() == "x" or value == "\x1b": raise WizardCancelled
        if value: return value
        if current: return current
        if not required: return ""
        result(False, "Este valor é obrigatório.")


def ask_choice(label: str, choices: dict[str, str], current: str) -> str:
    screen(label, "Escolha uma opção ou pressione X para cancelar")
    for key, value in choices.items(): menu_item(f"{key}.", value)
    while True:
        value = prompt(f"Opção [{current}]: ").strip() or current
        if value.casefold() == "x" or value == "\x1b": raise WizardCancelled
        if value in choices: return value
        result(False, "Opção inválida.")


def choose_profile(profiles: dict[str, Any], action: str) -> str | None:
    names = sorted(profiles)
    screen(f"Perfil para {action}", "Escolha um perfil ou pressione X para voltar")
    for index, name in enumerate(names, 1): menu_item(f"{index}.", name)
    menu_item("X.", "Voltar")
    while True:
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if choice.isdigit() and 1 <= int(choice) <= len(names): return names[int(choice) - 1]
        result(False, "Opção inválida.")


def authentication_for(vault_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        {"mode": "windows_credential_manager", "target": f"Onmyoji/KeePass/{vault_name}"},
        {"mode": "command", "command": ["secret-tool", "lookup", "service", "onmyoji", "vault", vault_name]},
    )


def create_local_vault(executable: str, database: Path, password: str) -> tuple[bool, str]:
    if not (Path(executable).is_file() or shutil.which(executable)): return False, f"Executável KeePassXC não encontrado: {executable}."
    if database.exists(): return False, f"Já existe um arquivo no destino: {database}."
    try:
        database.parent.mkdir(parents=True, exist_ok=True)
        created = subprocess.run([executable, "db-create", "--set-password", str(database)], input=password + "\n" + password + "\n", text=True, capture_output=True, check=False, timeout=60)
        if created.returncode != 0 or not database.is_file():
            database.unlink(missing_ok=True)
            detail = created.stderr.strip() or created.stdout.strip() or "KeePassXC não criou o arquivo KDBX."
            return False, detail
    except (OSError, subprocess.SubprocessError) as error:
        database.unlink(missing_ok=True)
        return False, f"Falha ao criar o vault: {error}"
    return True, "Vault local criado."


def ask_new_password() -> str:
    try:
        password = getpass.getpass("Senha mestra do novo vault (não será exibida): ")
        confirmation = getpass.getpass("Repita a senha mestra: ")
    except (EOFError, KeyboardInterrupt) as error: raise WizardCancelled from error
    if not password: raise WizardCancelled
    if password != confirmation:
        result(False, "As senhas não conferem; nenhuma alteração foi gravada.")
        raise WizardCancelled
    return password


def create_profile(data: dict[str, Any], path: Path, root: Path, profile_name: str) -> None:
    candidate = json.loads(json.dumps(data))
    profile = candidate["profiles"].get(profile_name, {})
    current_vault = profile.get("vault", profile_name)
    vault = candidate["vaults"].get(current_vault, {"cli_command": ["keepassxc-cli"], "database": {"windows": "", "linux": ""}})
    print(f"\nPerfil: {profile_name}")
    vault_origin = ask_choice("Origem do vault", {"1": "Usar um arquivo KDBX existente", "2": "Criar vault local isolado em configs/vaults"}, "1")
    vault_name = ask("Identificador do vault", current_vault, True)
    if not re.fullmatch(r"[A-Za-z0-9_-]+", vault_name):
        result(False, "Nome de vault inválido; nenhuma alteração foi salva.")
        return
    executable = ask("Executável do KeePassXC", vault["cli_command"][0] if vault["cli_command"] else "keepassxc-cli", True)
    platform = "windows" if os.name == "nt" else "linux"
    platform_name = "Windows" if platform == "windows" else "Linux"
    creating_local = vault_origin == "2"
    current_path = str(local_vault_for(root, vault_name)) if creating_local else ask(f"Arquivo KDBX no {platform_name}", vault["database"].get(platform, ""), True)
    windows_path = current_path if platform == "windows" else vault["database"].get("windows", "")
    linux_path = current_path if platform == "linux" else vault["database"].get("linux", "")
    access = ask_choice("Acesso do perfil", {"1": "Somente leitura", "2": "Leitura e escrita"}, "2" if profile.get("access") == "read_write" else "1")
    existing_auth = profile.get("auth", {})
    default_windows, default_linux = authentication_for(vault_name)
    windows = existing_auth.get("windows", default_windows)
    linux = existing_auth.get("linux", default_linux)
    if creating_local:
        windows, linux = default_windows, default_linux
        print(f"Vault local: {current_path}")
        print(f"A senha será salva no provedor seguro do {platform_name}.")
    elif platform == "windows":
        selection = ask_choice("Autenticação no Windows", {"1": "Windows Credential Manager", "2": "Senha via stdin", "3": "Prompt interativo"}, {"windows_credential_manager": "1", "stdin": "2", "prompt": "3"}.get(windows.get("mode"), "1"))
        windows = {"mode": {"1": "windows_credential_manager", "2": "stdin", "3": "prompt"}[selection], "target": ask("Target da credencial Windows", windows.get("target", f"Onmyoji/KeePass/{vault_name}"), True) if selection == "1" else ""}
    else:
        selection = ask_choice("Autenticação no Linux", {"1": "secret-tool", "2": "Senha via stdin", "3": "Prompt interativo"}, "1" if linux.get("mode") == "command" else {"stdin": "2", "prompt": "3"}.get(linux.get("mode"), "1"))
        linux = {"mode": "command" if selection == "1" else {"2": "stdin", "3": "prompt"}[selection], "command": ["secret-tool", "lookup", "service", "onmyoji", "vault", vault_name] if selection == "1" else []}
    entry_roots = ask("Raízes de entradas permitidas (separe por ;, vazio = todas)", ";".join(profile.get("allowed_entry_roots", [])))
    attachment_roots = ask("Diretórios permitidos para anexos (separe por ;, vazio = todos)", ";".join(profile.get("allowed_attachment_roots", [])))
    candidate["vaults"][vault_name] = {"cli_command": [executable], "database": {"windows": windows_path, "linux": linux_path}}
    candidate["profiles"][profile_name] = {"vault": vault_name, "access": "read_write" if access == "2" else "read_only", "allowed_operations": WRITE_OPS if access == "2" else READ_OPS, "allowed_entry_roots": [item.strip() for item in entry_roots.split(";") if item.strip()], "allowed_attachment_roots": [item.strip() for item in attachment_roots.split(";") if item.strip()], "auth": {"allowed_modes": ["configured", "stdin", "prompt", "windows_credential_manager"], "windows": windows, "linux": linux}}
    password = ask_new_password() if creating_local else None
    database = Path(current_path)
    if creating_local:
        created, message = create_local_vault(executable, database, password or "")
        if not created:
            result(False, f"Vault local não criado: {message}")
            return
    ok, message = save_transactional(path, candidate)
    if not ok:
        if creating_local: database.unlink(missing_ok=True)
        result(False, f"Não salvo: {message}")
        return
    if creating_local:
        stored, message = write_system_password(profile_name, candidate["profiles"][profile_name], password or "")
        if not stored:
            restored, restore_message = save_transactional(path, data)
            database.unlink(missing_ok=True)
            suffix = "" if restored else f" Também não foi possível restaurar a configuração: {restore_message}"
            result(False, f"Vault local removido porque a senha não pôde ser salva no SO: {message}.{suffix}")
            return
    data.clear(); data.update(candidate)
    result(True, "Perfil e vault local criados; senha salva no provedor do SO." if creating_local else message)


def commit_profile_change(data: dict[str, Any], path: Path, candidate: dict[str, Any]) -> bool:
    ok, message = save_transactional(path, candidate)
    if ok: data.clear(); data.update(candidate)
    result(ok, message if ok else f"Não salvo: {message}")
    return ok


def store_windows_credential(target: str, password: str) -> None:
    class Credential(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32), ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_byte * 8), ("CredentialBlobSize", ctypes.c_uint32), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]
    encoded = password.encode("utf-16-le")
    blob = ctypes.create_string_buffer(encoded)
    credential = Credential(0, 1, target, None, (ctypes.c_byte * 8)(), len(encoded), ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)), 2, 0, None, None, None)
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0): raise OSError("CredWriteW falhou.")


def write_system_password(profile_name: str, profile: dict[str, Any], password: str) -> tuple[bool, str]:
    try:
        if os.name == "nt":
            auth = profile["auth"]["windows"]
            if auth.get("mode") != "windows_credential_manager" or not auth.get("target"):
                return False, "Configure a autenticação Windows como Windows Credential Manager antes de armazenar a senha"
            store_windows_credential(str(auth["target"]), password)
            return True, "Senha salva no Windows Credential Manager"
        else:
            auth = profile["auth"]["linux"]
            command = auth.get("command", [])
            if auth.get("mode") != "command" or not isinstance(command, list) or len(command) < 2 or command[1] != "lookup":
                return False, "Configure a autenticação Linux como secret-tool antes de armazenar a senha"
            store_command = [str(command[0]), "store", f"--label=Onmyoji KeePass {profile_name}", *[str(value) for value in command[2:]]]
            subprocess.run(store_command, input=password + "\n", text=True, capture_output=True, check=True, timeout=20)
            return True, "Senha salva no keyring Linux"
    except (OSError, subprocess.SubprocessError): return False, "Não foi possível salvar a senha no provedor do sistema operacional"


def store_password(profile_name: str, profile: dict[str, Any]) -> None:
    password = None
    try:
        password = getpass.getpass("Senha mestra KeePass (não será exibida): ")
        if not password: result(False, "Nenhuma senha informada; operação cancelada."); return
        ok, message = write_system_password(profile_name, profile, password)
        result(ok, message + ".")
    except (EOFError, KeyboardInterrupt): result(False, "Armazenamento da senha cancelado.")
    finally:
        if password is not None: del password


def test_vault_access(profile_name: str, profile: dict[str, Any], vault: dict[str, Any], defaults: dict[str, Any]) -> None:
    valid, message = validate({"schema_version": 1, "defaults": defaults, "vaults": {profile["vault"]: vault}, "profiles": {profile_name: profile}})
    if not valid: result(False, f"Teste não iniciado: {message}"); return
    sys.path.insert(0, str(SKILL_DIR / "scripts"))
    import keepass_vault
    password = None
    try:
        password, key_file = keepass_vault.password_for({"auth": {"mode": "configured"}}, profile)
        client = keepass_vault.KeePass(vault, password, key_file, int(defaults["timeout_seconds"]))
        client.run(["ls", "-q", "__DATABASE__"])
        result(True, "Acesso ao vault confirmado.")
    except keepass_vault.VaultError as error: result(False, f"Falha ao acessar o vault: {error.message}")
    except (OSError, ValueError): result(False, "Falha inesperada ao iniciar o teste de acesso ao vault.")
    finally:
        if password is not None: del password


def edit_profile(data: dict[str, Any], path: Path, profile_name: str) -> None:
    platform = "windows" if os.name == "nt" else "linux"
    platform_name = "Windows" if platform == "windows" else "Linux"
    while True:
        profile = data["profiles"][profile_name]
        vault = data["vaults"][profile["vault"]]
        auth = profile["auth"][platform]
        auth_value = auth["mode"] if platform == "linux" else f"{auth['mode']} ({auth.get('target') or 'sem target'})"
        screen(f"Editar perfil · {profile_name}", "Selecione o campo a alterar")
        menu_item("1.", "Nome do perfil", profile_name)
        menu_item("2.", "Identificador do vault", profile['vault'])
        menu_item("3.", "Executável KeePassXC", vault['cli_command'][0])
        menu_item("4.", f"Arquivo KDBX no {platform_name}", vault['database'][platform])
        menu_item("5.", "Acesso", profile['access'])
        menu_item("6.", f"Autenticação no {platform_name}", auth_value)
        menu_item("7.", "Raízes de entradas", '; '.join(profile['allowed_entry_roots']) or 'todas')
        menu_item("8.", "Diretórios de anexos", '; '.join(profile['allowed_attachment_roots']) or 'todos')
        menu_item("9.", "Salvar senha no provedor do SO")
        menu_item("10.", "Testar acesso ao vault")
        menu_item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        candidate = json.loads(json.dumps(data))
        candidate_profile = candidate["profiles"][profile_name]
        candidate_vault = candidate["vaults"][candidate_profile["vault"]]
        try:
            if choice == "1":
                new_name = ask("Novo nome do perfil", profile_name, True)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", new_name): result(False, "Nome inválido."); continue
                if new_name != profile_name and new_name in candidate["profiles"]: result(False, "Esse perfil já existe."); continue
                if new_name != profile_name:
                    candidate["profiles"][new_name] = candidate["profiles"].pop(profile_name)
                    if commit_profile_change(data, path, candidate): profile_name = new_name
            elif choice == "2":
                new_vault = ask("Identificador do vault", candidate_profile["vault"], True)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", new_vault): result(False, "Nome de vault inválido."); continue
                if new_vault not in candidate["vaults"]: candidate["vaults"][new_vault] = candidate["vaults"][candidate_profile["vault"]]
                candidate_profile["vault"] = new_vault
                commit_profile_change(data, path, candidate)
            elif choice == "3":
                candidate_vault["cli_command"] = [ask("Executável do KeePassXC", candidate_vault["cli_command"][0], True)]
                commit_profile_change(data, path, candidate)
            elif choice == "4":
                candidate_vault["database"][platform] = ask(f"Arquivo KDBX no {platform_name}", candidate_vault["database"][platform], True)
                commit_profile_change(data, path, candidate)
            elif choice == "5":
                access = ask_choice("Acesso do perfil", {"1": "Somente leitura", "2": "Leitura e escrita"}, "2" if candidate_profile["access"] == "read_write" else "1")
                candidate_profile["access"] = "read_write" if access == "2" else "read_only"
                candidate_profile["allowed_operations"] = WRITE_OPS if access == "2" else READ_OPS
                commit_profile_change(data, path, candidate)
            elif choice == "6":
                if platform == "windows":
                    selected = ask_choice("Autenticação no Windows", {"1": "Windows Credential Manager", "2": "Senha via stdin", "3": "Prompt interativo"}, {"windows_credential_manager": "1", "stdin": "2", "prompt": "3"}.get(auth["mode"], "1"))
                    candidate_profile["auth"]["windows"] = {"mode": {"1": "windows_credential_manager", "2": "stdin", "3": "prompt"}[selected], "target": ask("Target da credencial Windows", auth.get("target", f"Onmyoji/KeePass/{candidate_profile['vault']}"), True) if selected == "1" else ""}
                else:
                    selected = ask_choice("Autenticação no Linux", {"1": "secret-tool", "2": "Senha via stdin", "3": "Prompt interativo"}, "1" if auth["mode"] == "command" else {"stdin": "2", "prompt": "3"}.get(auth["mode"], "1"))
                    candidate_profile["auth"]["linux"] = {"mode": "command" if selected == "1" else {"2": "stdin", "3": "prompt"}[selected], "command": ["secret-tool", "lookup", "service", "onmyoji", "vault", candidate_profile["vault"]] if selected == "1" else []}
                commit_profile_change(data, path, candidate)
            elif choice == "7":
                value = ask("Raízes de entradas (separe por ;, vazio = todas)", ";".join(candidate_profile["allowed_entry_roots"]))
                candidate_profile["allowed_entry_roots"] = [item.strip() for item in value.split(";") if item.strip()]
                commit_profile_change(data, path, candidate)
            elif choice == "8":
                value = ask("Diretórios de anexos (separe por ;, vazio = todos)", ";".join(candidate_profile["allowed_attachment_roots"]))
                candidate_profile["allowed_attachment_roots"] = [item.strip() for item in value.split(";") if item.strip()]
                commit_profile_change(data, path, candidate)
            elif choice == "9": store_password(profile_name, profile)
            elif choice == "10": test_vault_access(profile_name, profile, vault, data["defaults"])
            else: result(False, "Opção inválida.")
        except WizardCancelled: result(False, "Edição cancelada; nenhuma alteração foi gravada.")


def configure(root: Path) -> int:
    path = config_for(root)
    try: data = load_config(path)
    except ValueError as error: result(False, str(error)); return 2
    if remove_legacy_model_example(data):
        ok, message = save_transactional(path, data)
        if not ok: result(False, f"Não foi possível migrar o perfil de exemplo: {message}"); return 2
        result(True, "Perfil demonstrativo 'example' removido da configuração local.")
    if not path.exists():
        ok, message = save_transactional(path, data)
        if not ok: result(False, f"Não foi possível criar a configuração inicial: {message}"); return 2
        result(True, f"Configuração inicial criada: {path}")
    while True:
        screen("Configuração", "Perfis, vaults e autenticação da instância")
        menu_item("1.", "Criar perfil")
        menu_item("2.", "Editar perfil", f"{len(data['profiles'])} configurado(s)")
        menu_item("3.", "Remover perfil")
        menu_item("4.", "Ajustar timeout", f"{data['defaults']['timeout_seconds']} segundos")
        menu_item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "1":
            try:
                name = ask("Nome do perfil (letras, números, _ ou -)", required=True)
                if not re.fullmatch(r"[A-Za-z0-9_-]+", name): result(False, "Nome inválido.")
                elif name in data["profiles"]: result(False, "Esse perfil já existe.")
                else: create_profile(data, path, root, name)
            except WizardCancelled: result(False, "Configuração cancelada; nenhuma alteração foi gravada.")
        elif choice == "2":
            if not data["profiles"]: result(False, "Nenhum perfil criado."); continue
            name = choose_profile(data["profiles"], "editar")
            if name is not None:
                try: edit_profile(data, path, name)
                except WizardCancelled: result(False, "Configuração cancelada; nenhuma alteração foi gravada.")
        elif choice == "3":
            if not data["profiles"]: result(False, "Nenhum perfil criado."); continue
            name = choose_profile(data["profiles"], "remover")
            if name is not None:
                result(False, f"ATENÇÃO: o perfil '{name}' deixará de poder ser usado nesta instância.")
                confirmation = prompt("Digite REMOVER para confirmar: ").strip()
                if confirmation == "REMOVER":
                    candidate = json.loads(json.dumps(data)); del candidate["profiles"][name]
                    ok, message = save_transactional(path, candidate)
                    if ok: data = candidate
                    result(ok, message if ok else f"Não removido: {message}")
                else: result(False, "Remoção cancelada.")
        elif choice == "4":
            try:
                value = ask("Timeout em segundos", str(data["defaults"]["timeout_seconds"]), True)
                candidate = json.loads(json.dumps(data)); candidate["defaults"]["timeout_seconds"] = int(value)
                ok, message = save_transactional(path, candidate)
                if ok: data = candidate
                result(ok, message if ok else f"Não salvo: {message}")
            except ValueError: result(False, "Informe um número inteiro.")
            except WizardCancelled: result(False, "Configuração cancelada; nenhuma alteração foi gravada.")
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--onmyoji-root"); parser.add_argument("--action", choices=["describe", "status", "configure", "profile-schema", "profile-list"], default="configure"); parser.add_argument("--json", action="store_true"); args = parser.parse_args()
    root = root_for(args.onmyoji_root)
    if args.action == "describe": print(json.dumps(describe(), ensure_ascii=False) if args.json else describe()["title"]); return 0
    if args.action == "status":
        try: ok, message = validate(load_config(config_for(root)))
        except ValueError as error: ok, message = False, str(error)
        value = {"configured": bool(load_config(config_for(root)).get("profiles", {})) if ok else False, "valid": ok, "message": message}
        print(json.dumps(value, ensure_ascii=False) if args.json else message); return 0 if ok else 2
    if args.action == "profile-schema":
        value = {"ok": True, "read_only": True, "message": "O controle automático do KeePass permite somente consulta de perfis; criação, edição e remoção exigem o setup interativo do operador.", "fields": [{"name": "vault", "description": "Vault associado"}, {"name": "access", "description": "Nível de acesso"}, {"name": "allowed_operations", "description": "Operações permitidas"}, {"name": "allowed_entry_roots", "description": "Raízes de entradas permitidas"}, {"name": "allowed_attachment_roots", "description": "Raízes de anexos permitidas"}]}
        print(json.dumps(value, ensure_ascii=False) if args.json else value["message"]); return 0
    if args.action == "profile-list":
        try: data = load_config(config_for(root)); ok, message = validate(data)
        except ValueError as error: data, ok, message = empty_config(), False, str(error)
        profiles = []
        for name, profile in sorted(data.get("profiles", {}).items()):
            if isinstance(profile, dict): profiles.append({"name": name, "vault": profile.get("vault"), "access": profile.get("access"), "allowed_operations": profile.get("allowed_operations", []), "allowed_entry_roots": profile.get("allowed_entry_roots", []), "allowed_attachment_roots": profile.get("allowed_attachment_roots", [])})
        value = {"ok": ok, "configured": bool(profiles), "profiles": profiles, "message": message}
        print(json.dumps(value, ensure_ascii=False) if args.json else message); return 0 if ok else 2
    return configure(root)


if __name__ == "__main__": raise SystemExit(main())
