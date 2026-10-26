#!/usr/bin/env python3
"""Configurador interativo dos perfis MCP locais."""
from __future__ import annotations

import copy
import getpass
import os
from pathlib import Path
import shutil
import subprocess
import sys

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
from onmyoji_daemon.mcp.credentials import CredentialError, KeePassCredentialResolver
from onmyoji_daemon.mcp.todoist import TodoistError, TodoistService
from onmyoji_daemon.mcp.omie import OmieError, OmieService
from onmyoji_daemon.mcp.btg import BtgError, BtgService
from onmyoji_daemon.mcp.cloudflare import CloudflareError, CloudflareService
from onmyoji_daemon.mcp.eccovox import EccoVoxError, EccoVoxService
from onmyoji_daemon.mcp.forward_email import ForwardEmailError, ForwardEmailService
from onmyoji_daemon.mcp.notion import NotionError, NotionService
from onmyoji_daemon.mcp.aws import AwsError, AwsService
from onmyoji_daemon.mcp.mysql import MySQLError, MySQLService
from onmyoji_daemon.mcp.btg.configuration import BtgConfigurationError, DEFAULT_SCOPES, MONITOR_DEFAULTS, validate_profile as validate_btg_profile
from onmyoji_daemon.mcp.runtime import McpRuntimeError, ensure_mcp_runtime


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


def menu_width() -> int:
    """Mantém o resumo das integrações dentro da mesma largura do título."""
    return max(56, min(shutil.get_terminal_size((78, 24)).columns, 96))


def integration_profile_summary(profiles: dict[str, dict]) -> str:
    """Renderiza nomes de perfis sem ultrapassar a largura do menu MCP."""
    if not profiles:
        return Ui.text("inativa", Ui.slate)
    entries = sorted((name, profile.get("enabled") is True) for name, profile in profiles.items())
    budget = menu_width() - 41
    for shown_count in range(len(entries), -1, -1):
        shown = entries[:shown_count]
        omitted = len(entries) - shown_count
        plain = ", ".join(name for name, _ in shown)
        suffix = f" ... +{omitted}" if omitted else ""
        if len(plain) + len(suffix) <= budget:
            rendered = ", ".join(Ui.text(name, Ui.green if enabled else Ui.red) for name, enabled in shown)
            return rendered + Ui.text(suffix, Ui.slate)
    return Ui.text(f"... +{len(entries)}", Ui.slate)


def integration_item(key: str, label: str, profiles: dict[str, dict]) -> None:
    """Imprime uma integração com estados individuais de perfil coloridos."""
    shortcut = Ui.text(key.rjust(4), Ui.bold, Ui.cyan)
    print(f"  {shortcut}  {label:<30}   " + integration_profile_summary(profiles))


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


def manage_vault_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil de vault até voltar ou removê-lo."""
    while True:
        data = config.credentials()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        vault = data["vaults"][profile["vault"]]
        screen("MCP · Perfil de vault", name)
        item("1.", "Alterar cofre associado")
        item("2.", "Alterar executável KeePassXC")
        item("3.", "Configurar autenticação")
        item("4.", "Alterar raízes permitidas")
        item("5.", "Salvar senha mestra")
        item("6.", "Testar acesso")
        item("7.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1": vault["database"]["windows" if os.name == "nt" else "linux"] = ask("Arquivo KDBX", vault["database"]["windows" if os.name == "nt" else "linux"], True) or vault["database"]["windows" if os.name == "nt" else "linux"]
        elif action == "2": vault["cli_command"] = [ask("Executável", vault["cli_command"][0], True) or vault["cli_command"][0]]
        elif action == "3": profile["auth"] = profile_auth(name)
        elif action == "4": profile["allowed_entry_roots"] = [item.strip() for item in (ask("Raízes separadas por ;", ";".join(profile["allowed_entry_roots"])) or "").split(";") if item.strip()]
        elif action == "5":
            save_password(name, profile["auth"])
            continue
        elif action == "6":
            try: KeePassCredentialResolver(config.root).test_profile_access(name); result(True, "Acesso ao cofre confirmado.")
            except Exception: result(False, "Não foi possível acessar o cofre com este perfil.")
            continue
        elif action == "7":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil de vault não removido.")
                continue
            del data["profiles"][name]
            data["vaults"].pop(profile["vault"], None)
            try:
                config.save_credentials(data)
                result(True, "Perfil de vault removido.")
            except McpConfigurationError as error:
                result(False, f"Perfil de vault não removido: {error}")
                continue
            return
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_credentials(data)
            result(True, "Perfil de vault atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


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
            manage_vault_profile(config, name)


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
                    ["Informar a chave e salvar nesta entrada", "Reinformar entrada KeePass"],
                    "Informar a chave e salvar nesta entrada",
                )
                if decision is None:
                    return None
                if decision.startswith("Reinformar"):
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
                ["Usar a chave já salva", "Informar uma nova chave e substituir a salva", "Reinformar entrada KeePass"],
                "Usar a chave já salva",
            )
            if decision is None:
                return None
            if decision.startswith("Usar"):
                return entry, "password"
            if decision.startswith("Reinformar"):
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
            ["Informar a chave e criar esta entrada", "Reinformar entrada KeePass"],
            "Informar a chave e criar esta entrada",
        )
        if decision is None:
            return None
        if decision.startswith("Reinformar"):
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


def configure_vault_entry_fields(config: McpConfiguration, vault_profile: str, entry_path: str, integration_name: str, fields: list[tuple[str, str, str]]) -> tuple[str, dict[str, str]] | None:
    """Configura campos de uma entrada KeePass sem revelar valores existentes."""
    resolver = KeePassCredentialResolver(config.root)
    entry = entry_path
    while True:
        try:
            exists = resolver.entry_exists(vault_profile, entry)
        except CredentialError as error:
            result(False, str(error))
            return None
        configured: dict[str, str] = {}
        restart = False
        for profile_key, label, field in fields:
            try:
                has_value = exists and resolver.field_exists(vault_profile, entry, field)
            except CredentialError as error:
                result(False, str(error))
                return None
            if has_value:
                options = [f"Usar {label} já salvo", f"Informar novo {label} e substituir o salvo", "Reinformar entrada KeePass"]
                decision = choose(f"Credencial · {integration_name}", options, options[0])
                if decision is None:
                    return None
                if decision.startswith("Reinformar"):
                    restart = True
                    break
                if decision.startswith("Usar"):
                    configured[profile_key] = field
                    continue
            else:
                options = [f"Informar {label} e salvar nesta entrada", "Reinformar entrada KeePass"]
                decision = choose(f"Credencial · {integration_name}", options, options[0])
                if decision is None:
                    return None
                if decision.startswith("Reinformar"):
                    restart = True
                    break
            secret = ask_integration_secret(f"{integration_name} · {label}")
            if not secret:
                result(False, f"{label} é obrigatório para atualizar a entrada.")
                restart = True
                break
            try:
                resolver.save_field(vault_profile, entry, field, secret)
            except CredentialError as error:
                result(False, str(error))
                return None
            exists = True
            configured[profile_key] = field
        if not restart:
            return entry, configured
        entry = ask("Entrada KeePass da chave", entry, True)
        if not entry:
            return None


def manage_todoist_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Todoist até voltar ou removê-lo."""
    while True:
        data = config.todoist()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · Perfil Todoist", name)
        item("1.", "Alterar perfil de vault")
        item("2.", "Alterar entrada e chave")
        item("3.", "Alterar descrição")
        item("4.", "Habilitar")
        item("5.", "Desabilitar")
        item("6.", "Testar conexão")
        item("7.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not vault:
                continue
            credential = configure_vault_password_entry(config, vault, profile["credential_entry_path"], "Todoist")
            if credential is None:
                continue
            profile["credential_profile"] = vault
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "2":
            entry = ask("Entrada KeePass da chave", profile["credential_entry_path"], True)
            if not entry:
                continue
            credential = configure_vault_password_entry(config, profile["credential_profile"], entry, "Todoist")
            if credential is None:
                continue
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "3":
            description = ask("Descrição", profile["description"], True)
            if not description:
                continue
            profile["description"] = description
        elif action == "4": profile["enabled"] = True
        elif action == "5": profile["enabled"] = False
        elif action == "6":
            try: result(TodoistService(config.root).test_connection(name)["authenticated"], "Conexão confirmada.")
            except TodoistError as error: result(False, str(error))
            continue
        elif action == "7":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil Todoist não removido.")
                continue
            del data["profiles"][name]
            try:
                config.save_todoist(data)
                result(True, "Perfil Todoist removido.")
            except McpConfigurationError as error:
                result(False, f"Perfil Todoist não removido: {error}")
                continue
            return
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_todoist(data)
            result(True, "Perfil Todoist atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def todoist_menu(config: McpConfiguration) -> None:
    while True:
        data = config.todoist(); names = sorted(data["profiles"]); status = "ATIVA" if config.todoist_tool_available() else "inativa"
        screen("MCP · Todoist", f"Integração MCP completa · {status}")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            name = ask("Nome do perfil Todoist", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass da chave", f"APIs/Todoist/{name}", True)
            if not entry: result(False, "Perfil Todoist inválido."); continue
            credential = configure_vault_password_entry(config, vault, entry, "Todoist")
            if credential is None: continue
            entry, field = credential
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description: result(False, "Perfil Todoist inválido."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field}
        elif choice == "2" and (name := select("MCP · Gerenciar perfil Todoist", names)):
            manage_todoist_profile(config, name)
        else:
            result(False, "Opção inválida.")
            continue
        if choice == "1":
            try: config.save_todoist(data); result(True, "Perfil Todoist atualizado.")
            except McpConfigurationError as error: result(False, str(error))


def manage_omie_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Omie até voltar ou removê-lo."""
    while True:
        data = config.omie()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · Perfil Omie", name)
        item("1.", "Alterar perfil de vault")
        item("2.", "Alterar entrada e credenciais")
        item("3.", "Alterar descrição")
        item("4.", "Habilitar")
        item("5.", "Desabilitar")
        item("6.", "Testar conexão")
        item("7.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not vault:
                continue
            credential = configure_vault_entry_fields(config, vault, profile["credential_entry_path"], "Omie", [("app_key_field", "app_key", profile["app_key_field"]), ("app_secret_field", "app_secret", profile["app_secret_field"])])
            if credential is None:
                continue
            profile["credential_profile"] = vault
            profile["credential_entry_path"], fields = credential
            profile.update(fields)
        elif action == "2":
            entry = ask("Entrada KeePass das credenciais", profile["credential_entry_path"], True)
            if not entry:
                continue
            credential = configure_vault_entry_fields(config, profile["credential_profile"], entry, "Omie", [("app_key_field", "app_key", profile["app_key_field"]), ("app_secret_field", "app_secret", profile["app_secret_field"])])
            if credential is None:
                continue
            profile["credential_entry_path"], fields = credential
            profile.update(fields)
        elif action == "3":
            description = ask("Descrição", profile["description"], True)
            if not description:
                continue
            profile["description"] = description
        elif action == "4": profile["enabled"] = True
        elif action == "5": profile["enabled"] = False
        elif action == "6":
            try: result(OmieService(config.root).test_connection(name)["authenticated"], "Conexão confirmada.")
            except OmieError as error: result(False, str(error))
            continue
        elif action == "7":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil Omie não removido.")
                continue
            del data["profiles"][name]
            try:
                config.save_omie(data)
                result(True, "Perfil Omie removido.")
            except McpConfigurationError as error:
                result(False, f"Perfil Omie não removido: {error}")
                continue
            return
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_omie(data)
            result(True, "Perfil Omie atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def omie_menu(config: McpConfiguration) -> None:
    """Configura perfis Omie que compartilham o cofre MCP."""
    while True:
        data = config.omie(); names = sorted(data["profiles"]); status = "ATIVA" if config.omie_tool_available() else "inativa"
        screen("MCP · Omie", f"Integração MCP · {status}")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "1":
            name = ask("Nome do perfil Omie", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass das credenciais", f"APIs/Omie/{name}", True)
            if not entry:
                result(False, "Perfil Omie inválido.")
                continue
            credential = configure_vault_entry_fields(config, vault, entry, "Omie", [("app_key_field", "app_key", "username"), ("app_secret_field", "app_secret", "password")])
            if credential is None:
                continue
            entry, fields = credential
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description:
                result(False, "Perfil Omie inválido.")
                continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, **fields}
            try: config.save_omie(data); result(True, "Perfil Omie atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil Omie", names)):
            manage_omie_profile(config, name)
        else:
            result(False, "Opção inválida.")


def manage_cloudflare_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Cloudflare até voltar ou removê-lo."""
    while True:
        data = config.cloudflare()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · Perfil Cloudflare", name)
        item("1.", "Alterar perfil de vault")
        item("2.", "Alterar entrada e API Token")
        item("3.", "Alterar descrição")
        item("4.", "Alterar Zone ID padrão", profile.get("zone_id") or "não definido")
        item("5.", "Habilitar")
        item("6.", "Desabilitar")
        item("7.", "Testar conexão")
        item("8.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not vault:
                continue
            credential = configure_vault_password_entry(config, vault, profile["credential_entry_path"], "Cloudflare")
            if credential is None:
                continue
            profile["credential_profile"] = vault
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "2":
            entry = ask("Entrada KeePass do API Token", profile["credential_entry_path"], True)
            if not entry:
                continue
            credential = configure_vault_password_entry(config, profile["credential_profile"], entry, "Cloudflare")
            if credential is None:
                continue
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "3":
            description = ask("Descrição", profile["description"], True)
            if not description:
                continue
            profile["description"] = description
        elif action == "4":
            zone_id = ask("Zone ID padrão (opcional)", profile.get("zone_id", ""))
            if zone_id is None:
                continue
            profile["zone_id"] = zone_id
        elif action == "5":
            profile["enabled"] = True
        elif action == "6":
            profile["enabled"] = False
        elif action == "7":
            try:
                result(CloudflareService(config.root).test_connection(name)["authenticated"], "Conexão confirmada.")
            except CloudflareError as error:
                result(False, str(error))
            continue
        elif action == "8":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil Cloudflare não removido.")
                continue
            del data["profiles"][name]
            try:
                config.save_cloudflare(data)
                result(True, "Perfil Cloudflare removido.")
            except McpConfigurationError as error:
                result(False, f"Perfil Cloudflare não removido: {error}")
                continue
            return
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_cloudflare(data)
            result(True, "Perfil Cloudflare atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def cloudflare_menu(config: McpConfiguration) -> None:
    """Configura perfis Cloudflare que compartilham o cofre MCP."""
    while True:
        data = config.cloudflare(); names = sorted(data["profiles"]); status = "ATIVA" if config.cloudflare_tool_available() else "inativa"
        screen("MCP · Cloudflare", f"Zonas e registros DNS · {status}")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "1":
            name = ask("Nome do perfil Cloudflare", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass do API Token", f"APIs/Cloudflare/{name}", True)
            if not entry:
                result(False, "Perfil Cloudflare inválido.")
                continue
            credential = configure_vault_password_entry(config, vault, entry, "Cloudflare")
            if credential is None:
                continue
            entry, field = credential
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description:
                result(False, "Perfil Cloudflare inválido.")
                continue
            zone_id = ask("Zone ID padrão (opcional)")
            if zone_id is None:
                continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field, "zone_id": zone_id}
            try:
                config.save_cloudflare(data)
                result(True, "Perfil Cloudflare atualizado.")
            except McpConfigurationError as error:
                result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil Cloudflare", names)):
            manage_cloudflare_profile(config, name)
        else:
            result(False, "Opção inválida.")


def manage_forward_email_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Forward Email até voltar ou removê-lo."""
    while True:
        data = config.forward_email()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · Perfil Forward Email", name)
        item("1.", "Alterar perfil de vault")
        item("2.", "Alterar entrada e token")
        item("3.", "Alterar descrição")
        item("4.", "Alterar domínio padrão", profile.get("domain") or "não definido")
        item("5.", "Habilitar")
        item("6.", "Desabilitar")
        item("7.", "Testar conexão")
        item("8.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not vault:
                continue
            credential = configure_vault_password_entry(config, vault, profile["credential_entry_path"], "Forward Email")
            if credential is None:
                continue
            profile["credential_profile"] = vault
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "2":
            entry = ask("Entrada KeePass do token", profile["credential_entry_path"], True)
            if not entry:
                continue
            credential = configure_vault_password_entry(config, profile["credential_profile"], entry, "Forward Email")
            if credential is None:
                continue
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "3":
            description = ask("Descrição", profile["description"], True)
            if not description:
                continue
            profile["description"] = description
        elif action == "4":
            domain = ask("Domínio padrão (opcional)", profile.get("domain", ""))
            if domain is None:
                continue
            profile["domain"] = domain
        elif action == "5":
            profile["enabled"] = True
        elif action == "6":
            profile["enabled"] = False
        elif action == "7":
            try:
                result(ForwardEmailService(config.root).test_connection(name)["authenticated"], "Conexão confirmada.")
            except ForwardEmailError as error:
                result(False, str(error))
            continue
        elif action == "8":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil Forward Email não removido.")
                continue
            del data["profiles"][name]
            try:
                config.save_forward_email(data)
                result(True, "Perfil Forward Email removido.")
            except McpConfigurationError as error:
                result(False, f"Perfil Forward Email não removido: {error}")
                continue
            return
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_forward_email(data)
            result(True, "Perfil Forward Email atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def forward_email_menu(config: McpConfiguration) -> None:
    """Configura perfis Forward Email que compartilham o cofre MCP."""
    while True:
        data = config.forward_email(); names = sorted(data["profiles"]); status = "ATIVA" if config.forward_email_tool_available() else "inativa"
        screen("MCP · Forward Email", f"Domínios e aliases · {status}")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "1":
            name = ask("Nome do perfil Forward Email", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass do token", f"APIs/ForwardEmail/{name}", True)
            if not entry:
                result(False, "Perfil Forward Email inválido.")
                continue
            credential = configure_vault_password_entry(config, vault, entry, "Forward Email")
            if credential is None:
                continue
            entry, field = credential
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description:
                result(False, "Perfil Forward Email inválido.")
                continue
            domain = ask("Domínio padrão (opcional)")
            if domain is None:
                continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field, "domain": domain}
            try:
                config.save_forward_email(data)
                result(True, "Perfil Forward Email atualizado.")
            except McpConfigurationError as error:
                result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil Forward Email", names)):
            manage_forward_email_profile(config, name)
        else:
            result(False, "Opção inválida.")


def manage_notion_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Notion até voltar ou removê-lo."""
    while True:
        data = config.notion()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · Perfil Notion", name)
        item("1.", "Alterar perfil de vault")
        item("2.", "Alterar entrada e token")
        item("3.", "Alterar descrição")
        item("4.", "Alterar raízes de upload", f"{len(profile['readable_roots'])} configurada(s)")
        item("5.", "Habilitar")
        item("6.", "Desabilitar")
        item("7.", "Testar conexão")
        item("8.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not vault:
                continue
            credential = configure_vault_password_entry(config, vault, profile["credential_entry_path"], "Notion")
            if credential is None:
                continue
            profile["credential_profile"] = vault
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "2":
            entry = ask("Entrada KeePass do token", profile["credential_entry_path"], True)
            if not entry:
                continue
            credential = configure_vault_password_entry(config, profile["credential_profile"], entry, "Notion")
            if credential is None:
                continue
            profile["credential_entry_path"], profile["credential_field"] = credential
        elif action == "3":
            description = ask("Descrição", profile["description"], True)
            if not description:
                continue
            profile["description"] = description
        elif action == "4":
            roots = ask("Raízes de upload separadas por ; (vazio desabilita uploads)", ";".join(profile["readable_roots"]))
            if roots is None:
                continue
            profile["readable_roots"] = [value.strip() for value in roots.split(";") if value.strip()]
        elif action == "5":
            profile["enabled"] = True
        elif action == "6":
            profile["enabled"] = False
        elif action == "7":
            try:
                result(NotionService(config.root).test_connection(name)["authenticated"], "Conexão confirmada.")
            except NotionError as error:
                result(False, str(error))
            continue
        elif action == "8":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil Notion não removido.")
                continue
            del data["profiles"][name]
            try:
                config.save_notion(data)
                result(True, "Perfil Notion removido.")
            except McpConfigurationError as error:
                result(False, f"Perfil Notion não removido: {error}")
                continue
            return
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_notion(data)
            result(True, "Perfil Notion atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def notion_menu(config: McpConfiguration) -> None:
    """Configura perfis Notion que compartilham o cofre MCP."""
    while True:
        data = config.notion(); names = sorted(data["profiles"]); status = "ATIVA" if config.notion_tool_available() else "inativa"
        screen("MCP · Notion", f"Conteúdo, bases, views e arquivos · {status}")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "1":
            name = ask("Nome do perfil Notion", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass do token", f"APIs/Notion/{name}", True)
            if not entry:
                result(False, "Perfil Notion inválido.")
                continue
            credential = configure_vault_password_entry(config, vault, entry, "Notion")
            if credential is None:
                continue
            entry, field = credential
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description:
                result(False, "Perfil Notion inválido.")
                continue
            roots = ask("Raízes de upload separadas por ; (opcional)")
            if roots is None:
                continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field, "readable_roots": [value.strip() for value in roots.split(";") if value.strip()]}
            try:
                config.save_notion(data)
                result(True, "Perfil Notion atualizado.")
            except McpConfigurationError as error:
                result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil Notion", names)):
            manage_notion_profile(config, name)
        else:
            result(False, "Opção inválida.")


def aws_credentials(config: McpConfiguration, vault: str, entry: str, access_field: str = "username", secret_field: str = "password") -> tuple[str, dict[str, str]] | None:
    """Configura Access Key ID e Secret Access Key em uma entrada dedicada."""
    note("Use entrada dedicada à AWS: Access Key ID em username e Secret Access Key em password.")
    return configure_vault_entry_fields(config, vault, entry, "AWS", [("access_key_id_field", "Access Key ID", access_field), ("secret_access_key_field", "Secret Access Key", secret_field)])


def manage_aws_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil AWS até voltar ou removê-lo."""
    while True:
        data = config.aws(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        screen("MCP · Perfil AWS", name)
        item("1.", "Alterar perfil de vault"); item("2.", "Alterar entrada e credenciais"); item("3.", "Alterar descrição")
        item("4.", "Alterar região padrão", profile.get("region") or "não definida"); item("5.", "Alterar conta esperada", profile.get("expected_account_id") or "não definida")
        item("6.", "Alterar executável AWS CLI", profile["cli_path"]); item("7.", "Alterar raízes de leitura", f"{len(profile['readable_roots'])} configurada(s)")
        item("8.", "Alterar raízes de escrita", f"{len(profile['writable_roots'])} configurada(s)"); item("9.", "Habilitar"); item("10.", "Desabilitar")
        item("11.", "Testar conexão"); item("12.", "Remover perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2"}:
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action == "1" else profile["credential_profile"]
            if not vault: continue
            entry = profile["credential_entry_path"] if action == "1" else ask("Entrada KeePass das credenciais", profile["credential_entry_path"], True)
            if not entry: continue
            credential = aws_credentials(config, vault, entry, profile["access_key_id_field"], profile["secret_access_key_field"])
            if credential is None: continue
            profile["credential_profile"], profile["credential_entry_path"] = vault, credential[0]
            profile.update(credential[1])
        elif action == "3":
            value = ask("Descrição", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "4":
            value = ask("Região padrão (vazio exige region em cada chamada)", profile.get("region", ""))
            if value is None: continue
            profile["region"] = value
        elif action == "5":
            value = ask("AWS Account ID esperado com 12 dígitos (vazio desabilita a validação)", profile.get("expected_account_id", ""))
            if value is None: continue
            profile["expected_account_id"] = value
        elif action == "6":
            value = ask("Executável AWS CLI", profile["cli_path"], True)
            if not value: continue
            profile["cli_path"] = value
        elif action in {"7", "8"}:
            key = "readable_roots" if action == "7" else "writable_roots"
            value = ask("Raízes separadas por ; (vazio desabilita operações locais)", ";".join(profile[key]))
            if value is None: continue
            profile[key] = [item.strip() for item in value.split(";") if item.strip()]
        elif action == "9": profile["enabled"] = True
        elif action == "10": profile["enabled"] = False
        elif action == "11":
            try:
                status = AwsService(config.root).test_connection(name); result(status["authenticated"], f"Conexão confirmada com a conta {status['account']}.")
            except (AwsError, ValueError) as error: result(False, str(error))
            continue
        elif action == "12":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil AWS não removido."); continue
            del data["profiles"][name]
            try: config.save_aws(data); result(True, "Perfil AWS removido. A entrada KeePass foi preservada.")
            except McpConfigurationError as error: result(False, f"Perfil AWS não removido: {error}"); continue
            return
        else: result(False, "Opção inválida."); continue
        try: config.save_aws(data); result(True, "Perfil AWS atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def aws_menu(config: McpConfiguration) -> None:
    """Configura perfis AWS que compartilham o cofre MCP."""
    while True:
        data = config.aws(); names = sorted(data["profiles"]); status = "ATIVA" if config.aws_tool_available() else "inativa"
        screen("MCP · AWS", f"Identidade, S3 e IAM · {status}")
        item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask("Nome do perfil AWS", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass das credenciais", f"APIs/AWS/{name}", True)
            if not entry: result(False, "Perfil AWS inválido."); continue
            credential = aws_credentials(config, vault, entry)
            if credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description: result(False, "Perfil AWS inválido."); continue
            region = ask("Região padrão (opcional)"); account = ask("AWS Account ID esperado com 12 dígitos (recomendado)")
            cli = ask("Executável AWS CLI", "aws", True); readable = ask("Raízes de leitura separadas por ; (opcional)"); writable = ask("Raízes de escrita separadas por ; (opcional)")
            if None in (region, account, cli, readable, writable): continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": credential[0], **credential[1], "cli_path": cli, "region": region, "expected_account_id": account, "readable_roots": [item.strip() for item in readable.split(";") if item.strip()], "writable_roots": [item.strip() for item in writable.split(";") if item.strip()]}
            try: config.save_aws(data); result(True, "Perfil AWS atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil AWS", names)): manage_aws_profile(config, name)
        else: result(False, "Opção inválida.")


def mysql_credentials(config: McpConfiguration, vault: str, entry: str, username_field: str = "username", password_field: str = "password") -> tuple[str, dict[str, str]] | None:
    note("Use entrada dedicada ao MySQL: usuário em username e senha em password.")
    return configure_vault_entry_fields(config, vault, entry, "MySQL", [("username_field", "Usuário", username_field), ("password_field", "Senha", password_field)])


def manage_mysql_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.mysql(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        screen("MCP · Perfil MySQL", name)
        item("1.", "Alterar perfil de vault"); item("2.", "Alterar entrada e credenciais"); item("3.", "Alterar descrição"); item("4.", "Alterar servidor e banco")
        item("5.", "Alterar socket", profile.get("socket") or "não definido"); item("6.", "Alterar raízes de scripts", f"{len(profile['readable_roots'])} configurada(s)")
        item("7.", "Comandos livres do cliente", "habilitados" if profile["allow_client_commands"] else "desabilitados"); item("8.", "Habilitar"); item("9.", "Desabilitar")
        item("10.", "Testar conexão"); item("11.", "Remover perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2"}:
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action == "1" else profile["credential_profile"]
            if not vault: continue
            entry = profile["credential_entry_path"] if action == "1" else ask("Entrada KeePass das credenciais", profile["credential_entry_path"], True)
            if not entry: continue
            credential = mysql_credentials(config, vault, entry, profile["username_field"], profile["password_field"])
            if credential is None: continue
            profile["credential_profile"], profile["credential_entry_path"] = vault, credential[0]; profile.update(credential[1])
        elif action == "3":
            value = ask("Descrição", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "4":
            host = ask("Servidor MySQL", profile["host"], True); database = ask("Banco padrão (opcional)", profile.get("database", ""))
            if host is None or database is None: continue
            profile["host"], profile["database"] = host, database
        elif action == "5":
            value = ask("Socket MySQL (opcional)", profile.get("socket", ""))
            if value is None: continue
            profile["socket"] = value
        elif action == "6":
            value = ask("Raízes de scripts SQL separadas por ;", ";".join(profile["readable_roots"]))
            if value is None: continue
            profile["readable_roots"] = [item.strip() for item in value.split(";") if item.strip()]
        elif action == "7": profile["allow_client_commands"] = not profile["allow_client_commands"]
        elif action == "8": profile["enabled"] = True
        elif action == "9": profile["enabled"] = False
        elif action == "10":
            try: result(MySQLService(config.root).test_connection(name)["authenticated"], "Conexão MySQL confirmada.")
            except (MySQLError, ValueError) as error: result(False, str(error))
            continue
        elif action == "11":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil MySQL não removido."); continue
            del data["profiles"][name]
            try: config.save_mysql(data); result(True, "Perfil MySQL removido. A entrada KeePass foi preservada.")
            except McpConfigurationError as error: result(False, f"Perfil MySQL não removido: {error}"); continue
            return
        else: result(False, "Opção inválida."); continue
        try: config.save_mysql(data); result(True, "Perfil MySQL atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def mysql_menu(config: McpConfiguration) -> None:
    while True:
        data = config.mysql(); names = sorted(data["profiles"]); status = "ATIVA" if config.mysql_tool_available() else "inativa"
        screen("MCP · MySQL", f"Consultas, scripts e administração · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask("Nome do perfil MySQL", required=True); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass das credenciais", f"APIs/MySQL/{name}", True)
            if not entry: continue
            credential = mysql_credentials(config, vault, entry)
            if credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); host = ask("Servidor MySQL", "127.0.0.1", True); database = ask("Banco padrão (opcional)"); socket = ask("Socket MySQL (opcional)"); roots = ask("Raízes de scripts SQL separadas por ; (opcional)")
            if None in (description, host, database, socket, roots): continue
            data["profiles"][name] = {"enabled": True, "description": description, "host": host, "database": database, "socket": socket, "allow_client_commands": False, "credential_profile": vault, "credential_entry_path": credential[0], **credential[1], "readable_roots": [item.strip() for item in roots.split(";") if item.strip()]}
            try: config.save_mysql(data); result(True, "Perfil MySQL atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil MySQL", names)): manage_mysql_profile(config, name)
        else: result(False, "Opção inválida.")


def btg_credentials(config: McpConfiguration, vault: str, entry: str) -> tuple[str, dict[str, str]] | None:
    """Configura Client ID/Secret preservando notes, reservado ao estado OAuth."""
    note("Use entrada dedicada ao BTG: Client ID em username, Client Secret em password. O estado OAuth permanece em notes.")
    return configure_vault_entry_fields(config, vault, entry, "BTG Empresas", [("client_id_field", "Client ID", "username"), ("client_secret_field", "Client Secret", "password")])


def btg_payment_settings(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.btg()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · BTG · Pagamentos e monitor", name)
        item("1.", "Limite local por pagamento", "BRL " + profile.get("max_amount", "1000000.00"))
        item("2.", "Monitor do perfil", "habilitado" if profile.get("monitor_enabled") else "desabilitado")
        item("3.", "Intervalos de acompanhamento")
        item("4.", "Consultar fila local")
        item("5.", "Consultar pagamentos pendentes", "Um ciclo de leitura; não envia pagamentos")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action == "1":
            value = ask("Limite local em BRL (decimal com ponto)", profile.get("max_amount", "1000000.00"), True)
            if value is None:
                continue
            profile["max_amount"] = value
        elif action == "2":
            selection = choose("Monitor deste perfil", ["Habilitado", "Desabilitado"], "Habilitado" if profile.get("monitor_enabled") else "Desabilitado")
            if selection is None:
                continue
            if selection == "Habilitado":
                try:
                    note(ensure_mcp_runtime(config.root))
                except McpRuntimeError as error:
                    result(False, str(error))
                    continue
            profile["monitor_enabled"] = selection == "Habilitado"
            note("O monitor apenas consulta registros existentes. Para execução contínua, habilite e inicie btg-payments-monitor no daemon; isso não é feito automaticamente.")
        elif action == "3":
            labels = {"pending_interval": "Pendentes: intervalo em segundos", "scheduled_interval": "Agendados: intervalo em segundos", "confirmed_interval": "Confirmados: intervalo em segundos", "confirmation_days": "Confirmados: janela em dias", "max_pages": "Máximo de páginas por recuperação", "max_pending_days": "Após quantos dias reduzir consultas de pendências"}
            cancelled = False
            for key, default in MONITOR_DEFAULTS.items():
                value = ask(labels[key], str(profile.get(key, default)), True)
                if value is None:
                    cancelled = True
                    break
                if not value.isascii() or not value.isdigit() or not 1 <= int(value) <= 100000:
                    result(False, "Informe inteiro de 1 a 100000. Nenhuma alteração foi salva.")
                    cancelled = True
                    break
                profile[key] = int(value)
            if cancelled:
                continue
        elif action in {"4", "5"}:
            try:
                if action == "5":
                    note(ensure_mcp_runtime(config.root))
                status = BtgService(config.root).invoke(name, "get_monitor_status" if action == "4" else "check_pending_payments", {})
                if action == "4":
                    note(f"Fila local: {status['duePayments']} pagamento(s) aguardando consulta" + (" ou mais." if status['hasMore'] else "."))
                else:
                    result(True, f"Ciclo concluído: {len(status['checked'])} consultado(s), {len(status['busy'])} reservado(s) por outra operação.")
                    if any(item.get("lastError") for item in status["checked"]):
                        note("Há consultas com erro; o último estado foi preservado. Confira o histórico pelo MCP.")
                    if status["hasMore"]:
                        note("Ainda há registros para consultar.")
            except (BtgError, McpRuntimeError) as error:
                result(False, str(error))
            continue
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_btg(data)
            result(True, "Configuração de pagamentos e monitor atualizada.")
        except McpConfigurationError as error:
            result(False, str(error))


def manage_btg_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.btg()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        screen("MCP · Perfil BTG Empresas", f"{name} · {profile['environment']} · CNPJ {profile['company_id']}")
        item("1.", "Alterar perfil de vault")
        item("2.", "Alterar entrada e credenciais")
        item("3.", "Alterar descrição")
        item("4.", "Habilitar")
        item("5.", "Desabilitar")
        item("6.", "Diagnosticar credenciais e OAuth", "Consulta local; não acessa o banco")
        item("7.", "Remover perfil")
        item("8.", "Configurar URI de retorno OAuth")
        item("9.", "Conectar ao BTG", "Consentimento remoto e descoberta da conta")
        item("10.", "Consultar autorização OAuth")
        item("11.", "Cancelar autorização pendente")
        item("12.", "Testar conexão bancária", "Consulta somente a conta vinculada")
        item("13.", "Pagamentos e monitor", "Limite local e consultas periódicas")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}:
            return
        if action in {"1", "2"}:
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action == "1" else profile["credential_profile"]
            if not vault:
                continue
            entry = profile["credential_entry_path"] if action == "1" else ask("Entrada KeePass das credenciais", profile["credential_entry_path"], True)
            if not entry:
                continue
            credential = btg_credentials(config, vault, entry)
            if credential is None:
                continue
            profile["credential_profile"] = vault
            profile["credential_entry_path"] = credential[0]
        elif action == "3":
            description = ask("Descrição", profile["description"], True)
            if not description:
                continue
            profile["description"] = description
        elif action == "4": profile["enabled"] = True
        elif action == "5": profile["enabled"] = False
        elif action == "6":
            try:
                status = BtgService(config.root).get_auth_status(name)
                result(True, "Client ID e Client Secret disponíveis no cofre MCP.")
                note("Consentimento local válido." if status["authenticated"] else "Sem consentimento local válido. Use Conectar ao BTG após configurar a URI de retorno.")
                if status["pending"]:
                    note("Há consentimento pendente salvo no cofre.")
                note("Este diagnóstico não consulta o banco nem confirma pagamentos.")
            except BtgError as error:
                result(False, str(error))
            continue
        elif action == "7":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover":
                result(False, "Perfil BTG não removido.")
                continue
            del data["profiles"][name]
            try:
                config.save_btg(data)
                result(True, "Perfil BTG removido. A entrada KeePass foi preservada.")
            except McpConfigurationError as error:
                result(False, f"Perfil BTG não removido: {error}")
                continue
            return
        elif action == "13":
            btg_payment_settings(config, name)
            continue
        elif action == "8":
            origin = choose("URI de retorno BTG", ["Calcular pelo Gateway/Tunnel", "Informar manualmente"], "Calcular pelo Gateway/Tunnel")
            if origin is None:
                continue
            if origin.startswith("Calcular"):
                try:
                    redirect = BtgService(config.root).get_oauth_callback(name)["callbackUrl"]
                    note("Cadastre exatamente esta URI no aplicativo BTG: " + redirect)
                except BtgError as error:
                    result(False, str(error))
                    continue
            else:
                redirect = ask("URI já cadastrada no aplicativo BTG", profile.get("redirect_uri", ""))
            if redirect is None:
                continue
            profile["redirect_uri"] = redirect
        elif action in {"9", "10", "11", "12"}:
            service = BtgService(config.root)
            try:
                if action == "9":
                    confirmation = ask("Digite CONECTAR para iniciar o consentimento no navegador", required=True)
                    if confirmation is None or confirmation.casefold() != "conectar":
                        note("Conexão não iniciada.")
                        continue
                    note(ensure_mcp_runtime(config.root))
                    authorization = service.invoke(name, "start_authorization", {"confirm": True})
                    result(True, "Abra este link no seu navegador para autorizar o acesso:")
                    print(authorization["authorizationUrl"])
                    note("O callback salva os tokens no cofre e descobre a conta automaticamente. Consulte a autorização após concluir no navegador.")
                elif action == "10":
                    status = service.invoke(name, "get_authorization_status", {})
                    result(True, "Situação OAuth: " + str(status.get("status", "consentimento local válido" if status.get("authenticated") else "sem autorização ativa")))
                elif action == "11":
                    confirmation = ask("Digite CANCELAR para encerrar a autorização pendente", required=True)
                    if confirmation is None or confirmation.casefold() != "cancelar":
                        note("Autorização pendente preservada.")
                        continue
                    service.invoke(name, "cancel_authorization", {"confirm": True})
                    result(True, "Autorização pendente cancelada.")
                else:
                    note(ensure_mcp_runtime(config.root))
                    service.invoke(name, "get_account", {})
                    result(True, "Conexão bancária e identidade da conta BTG confirmadas.")
            except (BtgError, McpRuntimeError) as error:
                result(False, str(error))
            continue
        else:
            result(False, "Opção inválida.")
            continue
        try:
            config.save_btg(data)
            result(True, "Perfil BTG atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def btg_menu(config: McpConfiguration) -> None:
    while True:
        data = config.btg()
        screen("MCP · BTG Empresas", "Perfis, OAuth, pagamentos e acompanhamento bancário")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(data['profiles'])} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "2":
            name = select("MCP · Gerenciar perfil BTG", sorted(data["profiles"]))
            if name:
                manage_btg_profile(config, name)
            continue
        if choice != "1":
            result(False, "Opção inválida.")
            continue
        name = ask("Nome do perfil BTG", required=True)
        if not name or name in data["profiles"]:
            result(False, "Nome inválido, repetido ou retorno solicitado.")
            continue
        environment = choose("Ambiente BTG", ["sandbox", "production"], "sandbox")
        if environment is None:
            continue
        company = ask("CNPJ da empresa (14 dígitos)", required=True)
        if company is None:
            continue
        vault = select("Escolha o perfil de vault", config.vault_profile_names())
        if not vault:
            continue
        entry = ask("Entrada KeePass das credenciais", f"Integrations/BTG/{name}", True)
        if not entry:
            continue
        profile = {"enabled": True, "description": "Perfil em configuração", "environment": environment, "company_id": company, "credential_profile": vault, "credential_entry_path": entry, "oauth_mode": "remote", "scopes": DEFAULT_SCOPES, "redirect_uri": "", "max_amount": "1000000.00", "monitor_enabled": False}
        try:
            validate_btg_profile(name, profile)
        except BtgConfigurationError as error:
            result(False, str(error))
            continue
        credential = btg_credentials(config, vault, entry)
        if credential is None:
            continue
        profile["credential_entry_path"] = credential[0]
        description = ask("Descrição de quando utilizar este perfil", required=True)
        if not description:
            continue
        profile["description"] = description
        data["profiles"][name] = profile
        try:
            config.save_btg(data)
            result(True, "Perfil BTG salvo no MCP. Configure a URI de retorno e conecte ao BTG para descobrir a conta.")
        except McpConfigurationError as error:
            result(False, str(error))


def manage_eccovox_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.eccovox(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        screen("MCP · Perfil EccoVox", name)
        item("1.", "Alterar descrição")
        item("2.", "Alterar endpoint local")
        item("3.", "Alterar raízes de leitura")
        item("4.", "Alterar raízes de escrita")
        item("5.", "Habilitar" if not profile["enabled"] else "Desabilitar")
        item("6.", "Testar conexão")
        item("7.", "Remover perfil")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action == "1":
            value = ask("Descrição", profile["description"], True)
            if value is None: continue
            profile["description"] = value
        elif action == "2":
            value = ask("Endpoint HTTP local", profile["base_url"], True)
            if value is None: continue
            profile["base_url"] = value
        elif action in {"3", "4"}:
            key = "readable_roots" if action == "3" else "writable_roots"
            value = ask("Raízes separadas por ;", ";".join(profile[key]), True)
            if value is None: continue
            profile[key] = [item.strip() for item in value.split(";") if item.strip()]
        elif action == "5": profile["enabled"] = not profile["enabled"]
        elif action == "6":
            try:
                EccoVoxService(config.root).health(name); result(True, "Runtime EccoVox respondeu à consulta de saúde.")
            except EccoVoxError as error: result(False, str(error))
            continue
        elif action == "7":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil EccoVox não removido."); continue
            del data["profiles"][name]
            try: config.save_eccovox(data); result(True, "Perfil EccoVox removido.")
            except McpConfigurationError as error: result(False, f"Perfil EccoVox não removido: {error}"); continue
            return
        else: result(False, "Opção inválida."); continue
        try: config.save_eccovox(data); result(True, "Perfil EccoVox atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def eccovox_menu(config: McpConfiguration) -> None:
    while True:
        data = config.eccovox(); names = sorted(data["profiles"])
        screen("MCP · EccoVox", "Perfis locais para transcrição e síntese de voz")
        item("1.", "Criar perfil")
        item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask("Nome do perfil", required=True)
            if not name or name in data["profiles"]: result(False, "Nome inválido ou perfil já existente."); continue
            description = ask("Descrição de quando utilizar este perfil", required=True)
            endpoint = ask("Endpoint HTTP local", "http://127.0.0.1:8870", True)
            readable = ask("Raízes de leitura, separadas por ;", required=True)
            writable = ask("Raízes de escrita, separadas por ;", required=True)
            if not all((description, endpoint, readable, writable)): result(False, "Perfil EccoVox não foi criado."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "base_url": endpoint, "readable_roots": [item.strip() for item in readable.split(";") if item.strip()], "writable_roots": [item.strip() for item in writable.split(";") if item.strip()]}
            try: config.save_eccovox(data); result(True, "Perfil EccoVox salvo no MCP.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2" and (name := select("MCP · Gerenciar perfil EccoVox", names)):
            manage_eccovox_profile(config, name)
        else:
            result(False, "Opção inválida.")


def integrations_menu(config: McpConfiguration) -> None:
    """Lista integrações MCP e mantém cada configurador em seu próprio menu."""
    while True:
        todoist_profiles = config.todoist()["profiles"]
        omie_profiles = config.omie()["profiles"]
        btg_profiles = config.btg()["profiles"]
        cloudflare_profiles = config.cloudflare()["profiles"]
        eccovox_profiles = config.eccovox()["profiles"]
        forward_email_profiles = config.forward_email()["profiles"]
        notion_profiles = config.notion()["profiles"]
        aws_profiles = config.aws()["profiles"]
        mysql_profiles = config.mysql()["profiles"]
        screen("MCP · Integrações", "Selecione uma integração para configurar")
        integration_item("1.", "Todoist", todoist_profiles)
        integration_item("2.", "Omie", omie_profiles)
        integration_item("3.", "BTG Empresas", btg_profiles)
        integration_item("4.", "EccoVox", eccovox_profiles)
        integration_item("5.", "Cloudflare", cloudflare_profiles)
        integration_item("6.", "Forward Email", forward_email_profiles)
        integration_item("7.", "Notion", notion_profiles)
        integration_item("8.", "AWS", aws_profiles)
        integration_item("9.", "MySQL", mysql_profiles)
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "1":
            todoist_menu(config)
        elif choice == "2":
            omie_menu(config)
        elif choice == "3":
            btg_menu(config)
        elif choice == "4":
            eccovox_menu(config)
        elif choice == "5":
            cloudflare_menu(config)
        elif choice == "6":
            forward_email_menu(config)
        elif choice == "7":
            notion_menu(config)
        elif choice == "8":
            aws_menu(config)
        elif choice == "9":
            mysql_menu(config)
        else:
            result(False, "Opção inválida.")


def menu(root: Path) -> int:
    config = McpConfiguration(root)
    while True:
        screen("MCP", "Perfis de vault e integrações MCP locais")
        item("1.", "Perfis de vault", "KeePassXC e autenticação local")
        item("2.", "Integrações MCP")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice == "x": return 0
        if choice == "1": vault_profiles_menu(config)
        elif choice == "2": integrations_menu(config)


if __name__ == "__main__": raise SystemExit(menu(ROOT))
