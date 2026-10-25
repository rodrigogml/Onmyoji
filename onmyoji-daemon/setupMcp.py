#!/usr/bin/env python3
"""Configurador interativo dos perfis MCP locais."""
from __future__ import annotations

import copy
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError
from onmyoji_daemon.mcp.codex import CodexMcpError, CodexMcpRegistration
from onmyoji_daemon.mcp.credentials import KeePassCredentialResolver
from onmyoji_daemon.mcp.todoist import TodoistError, TodoistService


def ask(label: str, current: str = "", required: bool = False) -> str | None:
    value = input(f"{label}" + (f" [{current}]" if current else "") + ": ").strip()
    if value.casefold() in {"x", "\x1b"}: return None
    if value: return value
    return current if current or not required else None


def select(title: str, names: list[str]) -> str | None:
    print(f"\n{title}")
    for index, name in enumerate(names, 1): print(f"  {index}. {name}")
    print("  X. Voltar")
    choice = input("Opção: ").strip().casefold()
    return names[int(choice) - 1] if choice.isdigit() and 1 <= int(choice) <= len(names) else None


def store_windows_password(target: str, password: str) -> None:
    import ctypes
    class Credential(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_byte * 8), ("CredentialBlobSize", ctypes.c_ulong), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", ctypes.c_ulong), ("AttributeCount", ctypes.c_ulong), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]
    encoded = password.encode("utf-16-le"); blob = ctypes.create_string_buffer(encoded)
    credential = Credential(0, 1, target, None, (ctypes.c_byte * 8)(), len(encoded), ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)), 2, 0, None, None, None)
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0): raise OSError("Não foi possível salvar a credencial Windows.")


def profile_auth(name: str) -> dict:
    if os.name == "nt":
        target = ask("Destino no Windows Credential Manager", f"Onmyoji/MCP/KeePass/{name}", True)
        return {"windows": {"mode": "windows_credential_manager", "target": target or ""}, "linux": {"mode": "command", "command": ["secret-tool", "lookup", "service", "onmyoji-mcp", "profile", name]}}
    return {"windows": {"mode": "windows_credential_manager", "target": f"Onmyoji/MCP/KeePass/{name}"}, "linux": {"mode": "command", "command": ["secret-tool", "lookup", "service", "onmyoji-mcp", "profile", name]}}


def save_password(name: str, auth: dict) -> None:
    password = getpass.getpass("Senha mestra KeePass (não será exibida): ")
    if not password: return
    try:
        if os.name == "nt": store_windows_password(auth["windows"]["target"], password)
        else: subprocess.run(["secret-tool", "store", f"--label=Onmyoji MCP KeePass {name}", "service", "onmyoji-mcp", "profile", name], input=password + "\n", text=True, check=True, timeout=20, capture_output=True)
        print("Senha mestra salva no provedor do sistema operacional.")
    except (OSError, subprocess.SubprocessError): print("Não foi possível salvar a senha no provedor do sistema operacional.")


def create_vault_profile(config: McpConfiguration) -> None:
    name = ask("Nome do perfil de vault", required=True)
    if not name: return
    data = config.credentials()
    if name in data["profiles"]: print("Já existe um perfil com esse nome."); return
    origin = ask("1 existente / 2 criar KDBX local", "1", True)
    executable = ask("Executável KeePassXC", "keepassxc-cli", True)
    platform = "windows" if os.name == "nt" else "linux"
    database = str(config.root / "configs" / "mcp" / "vaults" / f"{name}.kdbx") if origin == "2" else ask(f"Arquivo KDBX no {platform}", required=True)
    if not executable or not database: return
    auth = profile_auth(name)
    roots = ask("Raízes de entradas permitidas, separadas por ; (vazio = todas)", "")
    data["vaults"][name] = {"cli_command": [executable], "database": {"windows": database if platform == "windows" else "", "linux": database if platform == "linux" else ""}}
    data["profiles"][name] = {"vault": name, "allowed_entry_roots": [item.strip() for item in (roots or "").split(";") if item.strip()], "auth": auth}
    if origin == "2":
        password = getpass.getpass("Senha mestra do novo vault (não será exibida): ")
        confirm = getpass.getpass("Repita a senha mestra: ")
        if not password or password != confirm: print("Senhas não conferem; nada foi salvo."); return
        try:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
            made = subprocess.run([executable, "db-create", "--set-password", database], input=password + "\n" + password + "\n", text=True, timeout=60, capture_output=True)
            if made.returncode or not Path(database).is_file(): print("KeePassXC não criou o cofre."); return
        except (OSError, subprocess.SubprocessError): print("Não foi possível criar o cofre."); return
        config.save_credentials(data); save_password(name, auth)
    else: config.save_credentials(data)
    print("Perfil de vault salvo.")


def vault_profiles_menu(config: McpConfiguration) -> None:
    while True:
        names = config.vault_profile_names(); print("\nPerfis de vault\n  1. Criar perfil\n  2. Gerenciar perfil\n  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            try: create_vault_profile(config)
            except McpConfigurationError as error: print(error)
        elif choice == "2" and (name := select("Gerenciar perfil de vault", names)):
            data = config.credentials(); profile = data["profiles"][name]; vault = data["vaults"][profile["vault"]]
            print("1. Alterar cofre associado\n2. Alterar executável KeePassXC\n3. Configurar autenticação\n4. Alterar raízes permitidas\n5. Salvar senha mestra\n6. Testar acesso\n7. Remover perfil")
            action = input("Opção: ").strip()
            if action == "1": vault["database"]["windows" if os.name == "nt" else "linux"] = ask("Arquivo KDBX", vault["database"]["windows" if os.name == "nt" else "linux"], True) or vault["database"]["windows" if os.name == "nt" else "linux"]
            elif action == "2": vault["cli_command"] = [ask("Executável", vault["cli_command"][0], True) or vault["cli_command"][0]]
            elif action == "3": profile["auth"] = profile_auth(name)
            elif action == "4": profile["allowed_entry_roots"] = [item.strip() for item in (ask("Raízes separadas por ;", ";".join(profile["allowed_entry_roots"])) or "").split(";") if item.strip()]
            elif action == "5": save_password(name, profile["auth"]); continue
            elif action == "6":
                try: KeePassCredentialResolver(config.root).test_profile_access(name); print("Acesso ao cofre confirmado.")
                except Exception: print("Não foi possível acessar o cofre com este perfil.")
                continue
            elif action == "7":
                if ask("Digite REMOVER para confirmar", required=True) == "REMOVER": del data["profiles"][name]; data["vaults"].pop(profile["vault"], None)
                else: continue
            else: continue
            try: config.save_credentials(data); print("Perfil de vault atualizado.")
            except McpConfigurationError as error: print(error)


def todoist_menu(config: McpConfiguration) -> None:
    while True:
        data = config.todoist(); names = sorted(data["profiles"]); status = "ATIVA" if config.todoist_tool_available() else "inativa"
        print(f"\nTodoist · tool todoist.list_tasks {status}\n  1. Criar perfil\n  2. Gerenciar perfil\n  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            name = ask("Nome do perfil Todoist", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: print("Nome inválido, repetido ou seleção cancelada."); continue
            entry = ask("Entrada KeePass do token", f"APIs/Todoist/{name}", True)
            description = ask("Descrição de quando utilizar este perfil", required=True)
            field = ask("Campo KeePass (password ou notes)", "password", True)
            if not entry or not description or field not in {"password", "notes"}: print("Perfil Todoist inválido."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field, "allowed_operations": ["todoist.list_tasks", "todoist.test_connection"]}
        elif choice == "2" and (name := select("Gerenciar perfil Todoist", names)):
            profile = data["profiles"][name]; print("1. Alterar perfil de vault\n2. Alterar entrada/campo\n3. Alterar descrição\n4. Habilitar\n5. Desabilitar\n6. Testar conexão\n7. Remover perfil")
            action = input("Opção: ").strip()
            if action == "1":
                vault = select("Escolha o perfil de vault", config.vault_profile_names())
                if vault: profile["credential_profile"] = vault
            elif action == "2":
                profile["credential_entry_path"] = ask("Entrada KeePass", profile["credential_entry_path"], True) or profile["credential_entry_path"]
                profile["credential_field"] = ask("Campo", profile["credential_field"], True) or profile["credential_field"]
            elif action == "3": profile["description"] = ask("Descrição", profile["description"], True) or profile["description"]
            elif action == "4": profile["enabled"] = True
            elif action == "5": profile["enabled"] = False
            elif action == "6":
                try: print("Conexão confirmada." if TodoistService(config.root).test_connection(name)["authenticated"] else "Conexão não confirmada.")
                except TodoistError as error: print(error)
                continue
            elif action == "7":
                if ask("Digite REMOVER para confirmar", required=True) == "REMOVER": del data["profiles"][name]
                else: continue
            else: continue
        else: continue
        try: config.save_todoist(data); print("Perfil Todoist atualizado.")
        except McpConfigurationError as error: print(error)


def codex_mcp_menu(root: Path) -> None:
    """Administra somente o registro MCP desta instância no Codex CLI."""
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        codex = system.get("codex", {})
        executable = str(codex.get("executable") or "") if isinstance(codex, dict) else ""
    except (OSError, tomllib.TOMLDecodeError):
        executable = ""
    registration = CodexMcpRegistration(root, executable)
    while True:
        print("\nCodex CLI · Servidor MCP Onmyōji")
        print("  Estado: " + registration.status())
        print("  1. Preparar runtime MCP\n  2. Registrar se necessário\n  3. Atualizar registro\n  4. Remover registro\n  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice == "x": return
        try:
            if choice == "1": print(registration.prepare_runtime())
            elif choice == "2": print(registration.ensure())
            elif choice == "3": print(registration.replace())
            elif choice == "4": print(registration.remove())
        except CodexMcpError as error:
            print(error)


def menu(root: Path) -> int:
    config = McpConfiguration(root)
    while True:
        print("\nMCP — Configuração\n  1. Perfis de vault\n  2. Integrações MCP\n  3. Codex CLI\n  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "1": vault_profiles_menu(config)
        elif choice == "2": todoist_menu(config)
        elif choice == "3": codex_mcp_menu(root)


if __name__ == "__main__": raise SystemExit(menu(ROOT))
