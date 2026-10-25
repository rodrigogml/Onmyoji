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

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from setupOnmyoji import Ui, choose, item, note, prompt, result, screen

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError
from onmyoji_daemon.mcp.codex import CodexMcpError, CodexMcpRegistration
from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver
from onmyoji_daemon.mcp.todoist import TodoistError, TodoistService


def ask(label: str, current: str = "", required: bool = False) -> str | None:
    value = prompt(f"{label}" + (f" [{current}]" if current else "") + ": ").strip()
    if value.casefold() in {"x", "\x1b"}: return None
    if value: return value
    return current if current or not required else None


def select(title: str, names: list[str]) -> str | None:
    screen(title, "Selecione um perfil ou pressione X para voltar")
    if not names:
        note("Nenhum perfil está configurado.")
    for index, name in enumerate(names, 1): item(f"{index}.", name)
    item("X.", "Voltar")
    while True:
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return None
        if choice.isdigit() and 1 <= int(choice) <= len(names): return names[int(choice) - 1]
        result(False, "Opção inválida.")


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
    password = getpass.getpass(Ui.text("› Senha mestra KeePass (não será exibida): ", Ui.violet, Ui.bold))
    if not password: return
    try:
        if os.name == "nt": store_windows_password(auth["windows"]["target"], password)
        else: subprocess.run(["secret-tool", "store", f"--label=Onmyoji MCP KeePass {name}", "service", "onmyoji-mcp", "profile", name], input=password + "\n", text=True, check=True, timeout=20, capture_output=True)
        result(True, "Senha mestra salva no provedor do sistema operacional.")
    except (OSError, subprocess.SubprocessError): result(False, "Não foi possível salvar a senha no provedor do sistema operacional.")


def create_vault_profile(config: McpConfiguration) -> None:
    name = ask("Nome do perfil de vault", required=True)
    if not name: return
    data = config.credentials()
    if name in data["profiles"]: result(False, "Já existe um perfil com esse nome."); return
    origin = choose("Cofre KeePassXC", ["Associar arquivo KDBX existente", "Criar cofre KDBX local"], "Associar arquivo KDBX existente")
    if origin is None: return
    executable = ask("Executável KeePassXC", "keepassxc-cli", True)
    platform = "windows" if os.name == "nt" else "linux"
    database = str(config.root / "configs" / "mcp" / "vaults" / f"{name}.kdbx") if origin.startswith("Criar") else ask(f"Arquivo KDBX no {platform}", required=True)
    if not executable or not database: return
    auth = profile_auth(name)
    roots = ask("Raízes de entradas permitidas, separadas por ; (vazio = todas)", "")
    data["vaults"][name] = {"cli_command": [executable], "database": {"windows": database if platform == "windows" else "", "linux": database if platform == "linux" else ""}}
    data["profiles"][name] = {"vault": name, "allowed_entry_roots": [item.strip() for item in (roots or "").split(";") if item.strip()], "auth": auth}
    if origin.startswith("Criar"):
        password = getpass.getpass(Ui.text("› Senha mestra do novo vault (não será exibida): ", Ui.violet, Ui.bold))
        confirm = getpass.getpass(Ui.text("› Repita a senha mestra: ", Ui.violet, Ui.bold))
        if not password or password != confirm: result(False, "Senhas não conferem; nada foi salvo."); return
        try:
            Path(database).parent.mkdir(parents=True, exist_ok=True)
            made = subprocess.run([executable, "db-create", "--set-password", database], input=password + "\n" + password + "\n", text=True, timeout=60, capture_output=True)
            if made.returncode or not Path(database).is_file(): result(False, "KeePassXC não criou o cofre."); return
        except (OSError, subprocess.SubprocessError): result(False, "Não foi possível criar o cofre."); return
        config.save_credentials(data); save_password(name, auth)
    else: config.save_credentials(data)
    result(True, "Perfil de vault salvo.")


def vault_profiles_menu(config: McpConfiguration) -> None:
    while True:
        names = config.vault_profile_names()
        screen("MCP · Perfis de vault", "Cada perfil associa um único cofre KeePassXC")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            try: create_vault_profile(config)
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil de vault", names)):
            data = config.credentials(); profile = data["profiles"][name]; vault = data["vaults"][profile["vault"]]
            screen("MCP · Perfil de vault", name)
            item("1.", "Alterar cofre associado")
            item("2.", "Alterar executável KeePassXC")
            item("3.", "Configurar autenticação")
            item("4.", "Alterar raízes permitidas")
            item("5.", "Salvar senha mestra")
            item("6.", "Testar acesso")
            item("7.", "Remover perfil")
            item("X.", "Cancelar")
            action = prompt("Opção: ").strip().casefold()
            if action in {"x", "\x1b"}: continue
            if action == "1": vault["database"]["windows" if os.name == "nt" else "linux"] = ask("Arquivo KDBX", vault["database"]["windows" if os.name == "nt" else "linux"], True) or vault["database"]["windows" if os.name == "nt" else "linux"]
            elif action == "2": vault["cli_command"] = [ask("Executável", vault["cli_command"][0], True) or vault["cli_command"][0]]
            elif action == "3": profile["auth"] = profile_auth(name)
            elif action == "4": profile["allowed_entry_roots"] = [item.strip() for item in (ask("Raízes separadas por ;", ";".join(profile["allowed_entry_roots"])) or "").split(";") if item.strip()]
            elif action == "5": save_password(name, profile["auth"]); continue
            elif action == "6":
                try: KeePassCredentialResolver(config.root).test_profile_access(name); result(True, "Acesso ao cofre confirmado.")
                except Exception: result(False, "Não foi possível acessar o cofre com este perfil.")
                continue
            elif action == "7":
                if ask("Digite REMOVER para confirmar", required=True) == "REMOVER": del data["profiles"][name]; data["vaults"].pop(profile["vault"], None)
                else: continue
            else: continue
            try: config.save_credentials(data); result(True, "Perfil de vault atualizado.")
            except McpConfigurationError as error: result(False, str(error))


def ask_integration_secret(integration_name: str) -> str | None:
    """Solicita uma chave de integração sem imprimi-la nem persistir no TOML."""
    try:
        token = getpass.getpass(Ui.text(f"› Chave de autenticação {integration_name} (não será exibida): ", Ui.violet, Ui.bold))
    except (EOFError, KeyboardInterrupt):
        return None
    return token.strip() or None


def configure_vault_password_entry(config: McpConfiguration, vault_profile: str, entry_path: str, integration_name: str) -> tuple[str, str] | None:
    """Aplica o fluxo padrão de reutilização ou gravação de uma chave no vault.

    Esta é a única interface de setup para criar ou substituir senhas de
    integrações MCP. O conteúdo da entrada nunca é exibido: para uma entrada
    existente, o usuário escolhe preservá-la ou substituí-la; para uma
    inexistente, informa a chave para criar a entrada em prompt protegido.
    """
    resolver = KeePassCredentialResolver(config.root)
    entry = entry_path
    while True:
        try:
            exists = resolver.entry_exists(vault_profile, entry)
        except CredentialError as error:
            result(False, str(error))
            return None
        if exists:
            try:
                has_password = resolver.password_exists(vault_profile, entry)
            except CredentialError as error:
                result(False, str(error))
                return None
            if not has_password:
                decision = choose(
                    f"Credencial · {integration_name}",
                    ["Informar a chave e salvar nesta entrada", "Escolher outra entrada KeePass"],
                    "Informar a chave e salvar nesta entrada",
                )
                if decision is None:
                    return None
                if decision.startswith("Escolher"):
                    entry = ask("Entrada KeePass da chave", entry, True)
                    if not entry:
                        return None
                    continue
                token = ask_integration_secret(integration_name)
                if not token:
                    result(False, "A chave de autenticação é obrigatória para atualizar a entrada.")
                    continue
                try:
                    resolver.save_password(vault_profile, entry, token)
                except CredentialError as error:
                    result(False, str(error))
                    return None
                return entry, "password"
            decision = choose(
                f"Credencial · {integration_name}",
                ["Usar a chave já salva", "Informar uma nova chave e substituir a salva", "Escolher outra entrada KeePass"],
                "Usar a chave já salva",
            )
            if decision is None:
                return None
            if decision.startswith("Usar"):
                return entry, "password"
            if decision.startswith("Escolher"):
                entry = ask("Entrada KeePass da chave", entry, True)
                if not entry:
                    return None
                continue
            token = ask_integration_secret(integration_name)
            if not token:
                result(False, "A chave de autenticação é obrigatória para substituir a entrada.")
                continue
            try:
                resolver.save_password(vault_profile, entry, token)
            except CredentialError as error:
                result(False, str(error))
                return None
            return entry, "password"
        decision = choose(
            f"Credencial · {integration_name}",
            ["Informar a chave e criar esta entrada", "Escolher outra entrada KeePass"],
            "Informar a chave e criar esta entrada",
        )
        if decision is None:
            return None
        if decision.startswith("Escolher"):
            entry = ask("Entrada KeePass da chave", entry, True)
            if not entry:
                return None
            continue
        token = ask_integration_secret(integration_name)
        if not token:
            result(False, "A chave de autenticação é obrigatória para criar a entrada.")
            continue
        try:
            resolver.save_password(vault_profile, entry, token)
        except CredentialError as error:
            result(False, str(error))
            return None
        return entry, "password"


def todoist_menu(config: McpConfiguration) -> None:
    while True:
        data = config.todoist(); names = sorted(data["profiles"]); status = "ATIVA" if config.todoist_tool_available() else "inativa"
        screen("MCP · Todoist", f"Ferramenta todoist.list_tasks · {status}")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            name = ask("Nome do perfil Todoist", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou seleção cancelada."); continue
            entry = ask("Entrada KeePass da chave", f"APIs/Todoist/{name}", True)
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not entry or not description: result(False, "Perfil Todoist inválido."); continue
            credential = configure_vault_password_entry(config, vault, entry, "Todoist")
            if credential is None: continue
            entry, field = credential
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field, "allowed_operations": ["todoist.list_tasks", "todoist.test_connection"]}
        elif choice == "2" and (name := select("MCP · Gerenciar perfil Todoist", names)):
            profile = data["profiles"][name]
            screen("MCP · Perfil Todoist", name)
            item("1.", "Alterar perfil de vault")
            item("2.", "Alterar entrada e campo")
            item("3.", "Alterar descrição")
            item("4.", "Habilitar")
            item("5.", "Desabilitar")
            item("6.", "Testar conexão")
            item("7.", "Remover perfil")
            item("X.", "Cancelar")
            action = prompt("Opção: ").strip().casefold()
            if action in {"x", "\x1b"}: continue
            if action == "1":
                vault = select("Escolha o perfil de vault", config.vault_profile_names())
                if vault:
                    credential = configure_vault_password_entry(config, vault, profile["credential_entry_path"], "Todoist")
                    if credential is None: continue
                    profile["credential_profile"] = vault
                    profile["credential_entry_path"], profile["credential_field"] = credential
            elif action == "2":
                entry = ask("Entrada KeePass da chave", profile["credential_entry_path"], True)
                if not entry: continue
                credential = configure_vault_password_entry(config, profile["credential_profile"], entry, "Todoist")
                if credential is None: continue
                profile["credential_entry_path"], profile["credential_field"] = credential
            elif action == "3": profile["description"] = ask("Descrição", profile["description"], True) or profile["description"]
            elif action == "4": profile["enabled"] = True
            elif action == "5": profile["enabled"] = False
            elif action == "6":
                try: result(TodoistService(config.root).test_connection(name)["authenticated"], "Conexão confirmada.")
                except TodoistError as error: result(False, str(error))
                continue
            elif action == "7":
                if ask("Digite REMOVER para confirmar", required=True) == "REMOVER": del data["profiles"][name]
                else: continue
            else: continue
        else: continue
        try: config.save_todoist(data); result(True, "Perfil Todoist atualizado.")
        except McpConfigurationError as error: result(False, str(error))


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
        screen("MCP · Codex CLI", registration.status())
        item("1.", "Preparar runtime MCP")
        item("2.", "Registrar se necessário")
        item("3.", "Atualizar registro")
        item("4.", "Remover registro")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        try:
            if choice == "1": result(True, registration.prepare_runtime())
            elif choice == "2": result(True, registration.ensure())
            elif choice == "3": result(True, registration.replace())
            elif choice == "4": result(True, registration.remove())
            else: result(False, "Opção inválida.")
        except CodexMcpError as error:
            result(False, str(error))


def menu(root: Path) -> int:
    config = McpConfiguration(root)
    while True:
        screen("MCP", "Perfis de vault, integrações e conexão com o Codex CLI")
        item("1.", "Perfis de vault", "KeePassXC e autenticação local")
        item("2.", "Integrações MCP", "Todoist")
        item("3.", "Codex CLI", "Runtime e registro do servidor stdio")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "1": vault_profiles_menu(config)
        elif choice == "2": todoist_menu(config)
        elif choice == "3": codex_mcp_menu(root)


if __name__ == "__main__": raise SystemExit(menu(ROOT))
