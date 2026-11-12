#!/usr/bin/env python3
"""Configurador interativo dos perfis MCP locais."""
from __future__ import annotations

import copy
import getpass
import hashlib
import os
from pathlib import Path
import shutil
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
from setupOnmyoji import Ui, alert, flow_choice, flow_heading, install_linux_packages, instance_identity, item as render_item, note, prompt, result, screen as render_screen, vault_entry_path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from onmyoji_daemon.mcp.configuration import McpConfiguration, McpConfigurationError
from onmyoji_daemon.config_publication import apply_shared_file_mode
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
from onmyoji_daemon.mcp.ssh import SshError, SshService, fetch_host_key, save_host_key
from onmyoji_daemon.mcp.bis2cmd import Bis2CmdError, Bis2CmdService
from onmyoji_daemon.mcp.bis10cmd import Bis10CmdError, Bis10CmdService
from onmyoji_daemon.mcp.google import GoogleError, GoogleService
from onmyoji_daemon.mcp.google.oauth import authorize as authorize_google
from onmyoji_daemon.mcp.spotify import SpotifyError, SpotifyService
from onmyoji_daemon.mcp.spotify.oauth import authorize as authorize_spotify
from onmyoji_daemon.mcp.memory import MemoryError, MemoryService
from onmyoji_daemon.mcp.data_tables import DataTablesError, SchemaCatalog
from onmyoji_daemon.mcp.btg.configuration import BtgConfigurationError, DEFAULT_SCOPES, MONITOR_DEFAULTS, validate_profile as validate_btg_profile
from onmyoji_daemon.mcp.runtime import McpRuntimeError, ensure_mcp_runtime
from onmyoji_daemon.mcp.email.setup import email_menu
from onmyoji_daemon.mcp.document_processing.setup import document_menu
from onmyoji_daemon.mcp.excel.config.setup import excel_menu
from onmyoji_daemon.mcp.mercado.configuration import MODULES, MODULE_PROVIDER
from onmyoji_daemon.mcp.mercado.exception import MercadoError
from onmyoji_daemon.mcp.mercado.service import MercadoService


def ask(label: str, current: str = "", required: bool = False) -> str | None:
    value = prompt(f"{label}" + (f" [{current}]" if current else "") + ": ").strip()
    if value.casefold() in {"x", "\x1b"}: return None
    if value: return value
    return current if current or not required else None


def ask_new_profile_name(label: str, profiles: dict[str, object]) -> str | None:
    """Sugere ``default`` apenas enquanto esse nome ainda estiver disponível."""
    return ask(label, "default" if "default" not in profiles else "", required=True)


def eccovox_workspace(config: McpConfiguration) -> Path:
    """Resolve o Work externo que contém os arquivos autorizados do EccoVox."""
    try:
        system = tomllib.loads((config.root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        workspace = Path(str(system["codex"]["project_directory"])).expanduser()
        if not workspace.is_absolute():
            workspace = config.root / workspace
        workspace = workspace.resolve()
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError) as error:
        raise ValueError("Configure o workspace antes de criar o perfil EccoVox.") from error
    if workspace.is_relative_to(config.root.resolve()):
        raise ValueError("O workspace do EccoVox deve ficar fora da instância Onmyōji.")
    return workspace


def eccovox_media_roots(config: McpConfiguration, values: str = "") -> list[str]:
    """Cria raízes de mídia do EccoVox, padrão no Work inteiro."""
    workspace = eccovox_workspace(config)
    roots = [Path(value.strip()).expanduser().resolve() for value in values.split(";") if value.strip()] or [workspace]
    for root in roots:
        if not root.is_relative_to(workspace):
            raise ValueError("As raízes do EccoVox devem ficar dentro do workspace configurado.")
        root.mkdir(parents=True, exist_ok=True)
    return [str(root) for root in roots]


def eccovox_default_output_directory(config: McpConfiguration, value: str = "") -> str:
    """Prepara o diretório padrão de resultados, separado das raízes autorizadas."""
    workspace = eccovox_workspace(config)
    directory = Path(value.strip()).expanduser().resolve() if value.strip() else workspace / ".onmyoji" / "eccovox" / "output"
    if not directory.is_relative_to(workspace):
        raise ValueError("A pasta padrão de saída do EccoVox deve ficar dentro do workspace configurado.")
    directory.mkdir(parents=True, exist_ok=True)
    return str(directory)


def eccovox_capability_options(capabilities: dict, key: str) -> list[tuple[str, str]]:
    """Extrai opções públicas de TTS anunciadas por um runtime EccoVox."""
    try:
        tts = capabilities["capabilities"]["tts"]
        status, values = tts["status"], tts[key]
    except (KeyError, TypeError) as error:
        raise EccoVoxError("runtime_error", "EccoVox retornou capacidades inválidas.") from error
    if status not in {"ready", "degraded"}:
        raise EccoVoxError("runtime_unavailable", "A síntese de voz não está disponível no EccoVox.")
    if not isinstance(values, list):
        raise EccoVoxError("runtime_error", "EccoVox retornou capacidades inválidas.")
    options: list[tuple[str, str]] = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str) or not value["id"].strip():
            continue
        identifier = value["id"].strip()
        label = str(value.get("label") or identifier).strip() or identifier
        options.append((identifier, f"{label} ({identifier})"))
    return options


def choose_eccovox_capability(capabilities: dict, key: str, title: str, current: str = "") -> str | None:
    """Permite herdar o runtime ou escolher exclusivamente um item anunciado."""
    options = eccovox_capability_options(capabilities, key)
    default = "Padrão do runtime EccoVox"
    labels = [default, *(label for _, label in options)]
    selected = default
    for identifier, label in options:
        if identifier == current:
            selected = label
            break
    choice = flow_choice(title, labels, selected)
    if choice is None:
        return None
    return "" if choice == default else next(identifier for identifier, label in options if label == choice)


def select(title: str, names: list[str], current: str = "") -> str | None:
    """Seleciona um perfil como etapa do fluxo atual, sem abrir um submenu."""
    if not names:
        result(False, "Nenhum perfil está configurado.")
        return None
    return flow_choice(title, names, current)


def manage_selected_profile(config: McpConfiguration, title: str, names: list[str], manager) -> None:
    """Abre um perfil somente quando a seleção não foi cancelada."""
    if name := select(title, names):
        manager(config, name)


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


_editing_profile: dict[str, object] | None = None


def _profile_values(label: str) -> list[tuple[str, object]]:
    """Relaciona cada ação de edição aos atributos que ela altera."""
    if _editing_profile is None:
        return []
    text = label.casefold()
    mappings = (
        ("identidade e descrição", ("address", "display_name", "description", "allowed_senders")),
        ("recebimento", ("incoming.protocol", "incoming.host", "incoming.port", "incoming.security", "incoming.username", "incoming.auth.credential_profile", "incoming.auth.credential_entry_path")),
        ("envio smtp", ("smtp.protocol", "smtp.host", "smtp.port", "smtp.security", "smtp.username", "smtp.auth.credential_profile", "smtp.auth.credential_entry_path")),
        ("pastas", ("folders", "sent_copy")), ("anexos e arquivos", ("readable_roots", "writable_roots")),
        ("descrição", ("description",)), ("domínio", ("domain",)), ("nome de exibição", ("display_name",)),
        ("perfil de vault", ("credential_profile",)), ("entrada", ("credential_entry_path",)),
        ("servidor e banco", ("host", "port", "database", "username")), ("servidor, porta e usuário", ("host", "port", "username")),
        ("host", ("host", "port")), ("jar", ("jar_path", "working_dir")), ("diretório", ("working_dir",)),
        ("região", ("region",)), ("conta esperada", ("expected_account_id",)), ("socket", ("socket",)),
        ("raízes de scripts", ("readable_roots",)), ("raízes de arquivos", ("readable_roots", "writable_roots")),
        ("locale", ("locale",)), ("ambiente", ("environment",)), ("empresa", ("company_id",)),
        ("endpoint", ("base_url",)), ("cofre associado", ("vault",)), ("executável", ("cli_command",)),
        ("raízes de importação", ("readable_roots",)), ("raízes de exportação", ("writable_roots",)),
        ("upload", ("readable_roots",)), ("download", ("writable_roots",)), ("leitura", ("readable_roots",)),
        ("escrita", ("writable_roots",)), ("habilitar", ("enabled",)), ("desabilitar", ("enabled",)),
        ("monitor", ("monitor_enabled",)), ("limite", ("max_amount",)), ("autenticação", ("auth_mode",)),
    )
    fields = next((fields for fragment, fields in mappings if fragment in text), ())
    def value_at(path: str) -> object | None:
        value: object = _editing_profile
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value

    return [(field, value) for field in fields if (value := value_at(field)) is not None]


def _render_profile_value(value: object) -> str:
    if isinstance(value, list):
        return "; ".join(str(item) for item in value) or "(vazio)"
    if isinstance(value, bool):
        return "sim" if value else "não"
    return str(value) or "(vazio)"


def item(key: str, label: str, value: str = "") -> None:
    """Mostra o valor atual na coluna da ação que permite alterá-lo."""
    values = _profile_values(label)
    if not values:
        render_item(key, label, value)
        return
    first_field, first_value = values[0]
    render_item(key, label, f"{first_field}: {_render_profile_value(first_value)}")
    for field, configured in values[1:]:
        print(" " * 41 + f"{field}: {_render_profile_value(configured)}")


def screen(title: str, subtitle: str = "") -> None:
    """Abre uma tela comum e remove o contexto do perfil anteriormente editado."""
    global _editing_profile
    _editing_profile = None
    render_screen(title, subtitle)


def profile_screen(title: str, subtitle: str, profile: dict[str, object]) -> None:
    """Abre uma tela de perfil, vinculando valores atuais às opções de edição."""
    global _editing_profile
    _editing_profile = profile
    render_screen(title, subtitle)


def store_windows_password(target: str, password: str) -> None:
    import ctypes
    class Credential(ctypes.Structure):
        _fields_ = [("Flags", ctypes.c_ulong), ("Type", ctypes.c_ulong), ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p), ("LastWritten", ctypes.c_byte * 8), ("CredentialBlobSize", ctypes.c_ulong), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)), ("Persist", ctypes.c_ulong), ("AttributeCount", ctypes.c_ulong), ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p), ("UserName", ctypes.c_wchar_p)]
    encoded = password.encode("utf-16-le"); blob = ctypes.create_string_buffer(encoded)
    credential = Credential(0, 1, target, None, (ctypes.c_byte * 8)(), len(encoded), ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte)), 2, 0, None, None, None)
    if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0): raise OSError("Não foi possível salvar a credencial Windows.")


def profile_auth(name: str, root: Path = ROOT) -> dict:
    identity = instance_identity(root)
    target = f"{identity}/MCP/KeePass/{name}"
    linux_command = ["secret-tool", "lookup", "service", "shikigami-mcp", "shikigami", identity, "profile", name]
    if os.name == "nt":
        target = ask("Destino no Windows Credential Manager", target, True)
        return {"windows": {"mode": "windows_credential_manager", "target": target or ""}, "linux": {"mode": "command", "command": linux_command}}
    if shutil.which("secret-tool") and os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        linux = {"mode": "command", "command": linux_command}
    else:
        filename = hashlib.sha256(f"{identity}\0{name}".encode("utf-8")).hexdigest() + ".secret"
        linux = {"mode": "protected_file", "path": str(root.resolve() / "configs" / "mcp" / "secrets" / filename)}
    return {"windows": {"mode": "windows_credential_manager", "target": target}, "linux": linux}


def store_protected_password(path: Path, password: str) -> None:
    """Persiste o segredo para as duas contas autorizadas da instância."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o2770)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o660)
    try:
        os.fchmod(descriptor, 0o660)
        os.write(descriptor, password.encode("utf-8"))
    finally:
        os.close(descriptor)


def save_password(name: str, auth: dict, root: Path = ROOT, password: str | None = None) -> bool:
    """Grava a senha no cofre do sistema, solicitando-a somente se necessário."""
    if password is None:
        password = getpass.getpass(Ui.text("› Senha mestra KeePass (não será exibida): ", Ui.violet, Ui.bold))
    if not password: return False
    try:
        if os.name == "nt": store_windows_password(auth["windows"]["target"], password)
        elif auth["linux"].get("mode") == "protected_file":
            store_protected_password(Path(auth["linux"]["path"]), password)
        else:
            identity = instance_identity(root)
            subprocess.run(["secret-tool", "store", f"--label={identity} MCP KeePass {name}", "service", "shikigami-mcp", "shikigami", identity, "profile", name], input=password + "\n", text=True, check=True, timeout=20, capture_output=True)
        result(True, "Senha mestra salva no provedor do sistema operacional.")
        return True
    except (OSError, subprocess.SubprocessError):
        result(False, "Não foi possível salvar a senha no provedor do sistema operacional.")
        return False


def keepassxc_command_available(command: list[str]) -> bool:
    """Confirma que o comando selecionado inicia o CLI, sem depender do vault."""
    if not command:
        return False
    executable = command[0]
    if not (shutil.which(executable) or Path(executable).expanduser().is_file()):
        return False
    try:
        checked = subprocess.run([*command, "--version"], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return checked.returncode == 0


def discover_keepassxc_command() -> list[str] | None:
    """Procura instalação nativa e, no Linux, uma instalação Flatpak do usuário."""
    if executable := shutil.which("keepassxc-cli"):
        command = [executable]
        if keepassxc_command_available(command):
            return command
    if os.name == "nt" or not shutil.which("flatpak"):
        return None
    command = ["flatpak", "run", "--command=keepassxc-cli", "org.keepassxc.KeePassXC"]
    return command if keepassxc_command_available(command) else None


def install_user_keepassxc() -> list[str] | None:
    """Instala o KeePassXC no escopo da conta atual por Flatpak."""
    flatpak = shutil.which("flatpak")
    if not flatpak:
        result(False, "Flatpak não está disponível para uma instalação local desta conta.")
        return None
    flow_heading("Instalação assistida", "KeePassXC para a identidade em execução")
    note("O KeePassXC será instalado somente para esta conta pelo repositório Flathub.")
    if prompt("Instalar o KeePassXC nesta conta? [S/n]: ").strip().casefold() in {"n", "nao", "não", "x", "\x1b"}:
        result(False, "Instalação local do KeePassXC cancelada.")
        return None
    try:
        remote = subprocess.run([flatpak, "remote-add", "--user", "--if-not-exists", "flathub", "https://dl.flathub.org/repo/flathub.flatpakrepo"], check=False)
        installed = subprocess.run([flatpak, "install", "--user", "--noninteractive", "-y", "flathub", "org.keepassxc.KeePassXC"], check=False) if remote.returncode == 0 else remote
    except OSError as error:
        result(False, f"Não foi possível executar o Flatpak: {error}")
        return None
    command = [flatpak, "run", "--command=keepassxc-cli", "org.keepassxc.KeePassXC"]
    ready = installed.returncode == 0 and keepassxc_command_available(command)
    result(ready, "KeePassXC instalado para esta conta." if ready else "O Flatpak não disponibilizou o keepassxc-cli.")
    return command if ready else None


def select_keepassxc_command() -> list[str] | None:
    """Obtém um CLI funcional e oferece correção no Linux antes de continuar."""
    if command := discover_keepassxc_command():
        note(f"KeePassXC CLI detectado: {' '.join(command)}")
        return command
    if os.name == "nt":
        executable = ask("Executável KeePassXC", "keepassxc-cli", True)
        command = [executable] if executable else []
        if keepassxc_command_available(command):
            return command
        result(False, "keepassxc-cli não encontrado ou não executável.")
        return None
    options = []
    alert("KeePassXC CLI não encontrado; escolha uma forma de disponibilizá-lo.")
    if shutil.which("flatpak"):
        options.append("Instalar KeePassXC somente para esta conta (Flatpak)")
    options.extend(["Instalar KeePassXC pelo gerenciador do sistema", "Informar outro executável"])
    selected = flow_choice("KeePassXC CLI não encontrado", options, options[0])
    if selected is None:
        return None
    if selected.startswith("Instalar KeePassXC somente"):
        return install_user_keepassxc()
    if selected.startswith("Instalar KeePassXC pelo"):
        installed = install_linux_packages("KeePassXC CLI", {
            "apt-get": ["keepassxc"], "dnf": ["keepassxc"], "yum": ["keepassxc"],
            "zypper": ["keepassxc"], "pacman": ["keepassxc"],
        })
        command = discover_keepassxc_command() if installed else None
        if command is None and installed:
            result(False, "A instalação terminou, mas keepassxc-cli não ficou acessível nesta conta.")
        return command
    executable = ask("Executável KeePassXC", "keepassxc-cli", True)
    command = [str(Path(executable).expanduser())] if executable else []
    if keepassxc_command_available(command):
        return command
    result(False, "keepassxc-cli não encontrado ou não executável.")
    return None


def scope_flatpak_to_database(command: list[str], database: str) -> list[str]:
    """Concede ao Flatpak somente acesso de escrita à pasta do KDBX selecionado."""
    if "org.keepassxc.KeePassXC" not in command or not command or Path(command[0]).name != "flatpak":
        return command
    permission = f"--filesystem={Path(database).expanduser().resolve().parent}"
    return [command[0], "run", permission, *command[2:]]


def keepassxc_creation_error(completed: subprocess.CompletedProcess[str]) -> str:
    """Resume a falha do CLI sem repetir prompts ou conteúdo sensível."""
    detail = (completed.stderr or completed.stdout or "").strip()
    lines = []
    for line in detail.splitlines():
        normalized = " ".join(line.split())
        if not normalized or normalized.casefold().startswith(("enter password", "repeat password")):
            continue
        lines.append(normalized)
    summary = " ".join(lines)[:500]
    return f"KeePassXC não criou o cofre: {summary}" if summary else f"KeePassXC não criou o cofre (código {completed.returncode})."


def ensure_linux_secret_tool() -> bool:
    """Prepara o provedor usado para persistir a senha antes de solicitá-la."""
    if os.name == "nt" or shutil.which("secret-tool"):
        return True
    result(False, "secret-tool não está disponível; ele é necessário para salvar a senha mestra com segurança.")
    selected = flow_choice("Provedor de segredos não encontrado", ["Instalar o provedor do sistema", "Cancelar"], "Instalar o provedor do sistema")
    if selected != "Instalar o provedor do sistema":
        return False
    installed = install_linux_packages("provedor seguro de senhas", {
        "apt-get": ["libsecret-tools"], "dnf": ["libsecret"], "yum": ["libsecret"],
        "zypper": ["libsecret-tools"], "pacman": ["libsecret"],
    })
    ready = installed and shutil.which("secret-tool") is not None
    if installed and not ready:
        result(False, "A instalação terminou, mas secret-tool não ficou acessível nesta conta.")
    return ready


def create_vault_profile(config: McpConfiguration) -> None:
    data = config.credentials()
    name = ask_new_profile_name("Nome do perfil de vault", data["profiles"])
    if not name: return
    if name in data["profiles"]: result(False, "Já existe um perfil com esse nome."); return
    origin = flow_choice("Cofre KeePassXC", ["Associar arquivo KDBX existente", "Criar cofre KDBX local"], "Associar arquivo KDBX existente")
    if origin is None: return
    command = select_keepassxc_command()
    if not command: return
    platform = "windows" if os.name == "nt" else "linux"
    database = str(config.root / "configs" / "mcp" / "vaults" / f"{name}.kdbx") if origin.startswith("Criar") else ask(f"Arquivo KDBX no {platform}", required=True)
    if not database: return
    command = scope_flatpak_to_database(command, database)
    auth = profile_auth(name, config.root)
    if os.name != "nt" and auth.get("linux", {}).get("mode") == "command" and not ensure_linux_secret_tool(): return
    roots = ask("Raízes de entradas permitidas, separadas por ; (vazio = todas)", "")
    data["vaults"][name] = {"cli_command": command, "database": {"windows": database if platform == "windows" else "", "linux": database if platform == "linux" else ""}}
    data["profiles"][name] = {"vault": name, "allowed_entry_roots": [item.strip() for item in (roots or "").split(";") if item.strip()], "auth": auth}
    if origin.startswith("Criar"):
        password = getpass.getpass(Ui.text("› Senha mestra do novo vault (não será exibida): ", Ui.violet, Ui.bold))
        confirm = getpass.getpass(Ui.text("› Repita a senha mestra: ", Ui.violet, Ui.bold))
        if not password or password != confirm: result(False, "Senhas não conferem; nada foi salvo."); return
        try:
            if Path(database).exists():
                result(False, f"Já existe um arquivo em {database}. Associe esse KDBX como cofre existente ou escolha outro nome de perfil.")
                return
            Path(database).parent.mkdir(parents=True, exist_ok=True)
            made = subprocess.run([*command, "db-create", "--set-password", database], input=password + "\n" + password + "\n", text=True, timeout=60, capture_output=True)
            if made.returncode:
                result(False, keepassxc_creation_error(made)); return
            if not Path(database).is_file(): result(False, "KeePassXC informou sucesso, mas o arquivo do cofre não foi criado."); return
            apply_shared_file_mode(Path(database))
        except (OSError, subprocess.SubprocessError): result(False, "Não foi possível criar o cofre."); return
        if not save_password(name, auth, config.root, password):
            result(False, f"O cofre foi criado em {database}, mas o perfil não foi salvo.")
            return
        config.save_credentials(data)
    else:
        password = getpass.getpass(Ui.text("› Senha mestra do vault existente (não será exibida): ", Ui.violet, Ui.bold))
        if not password:
            result(False, "Senha mestra não informada; perfil não foi salvo.")
            return
        try:
            KeePassCredentialResolver.validate_vault_access(data["vaults"][name], data.get("defaults", {}), password)
        except CredentialError:
            result(False, "Não foi possível acessar o cofre com a senha informada.")
            return
        if not save_password(name, auth, config.root, password):
            result(False, "O perfil não foi salvo porque a senha mestra não pôde ser protegida.")
            return
        config.save_credentials(data)
    result(True, "Perfil de vault salvo.")


def manage_vault_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil de vault até voltar ou removê-lo."""
    while True:
        data = config.credentials()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        linux_auth = profile.get("auth", {}).get("linux", {})
        if linux_auth.get("mode") == "protected_file" and not linux_auth.get("path"):
            repaired = profile_auth(name, config.root)["linux"]
            if repaired.get("mode") == "protected_file":
                profile["auth"]["linux"] = repaired
                config.save_credentials(data)
        vault = data["vaults"][profile["vault"]]
        profile_screen("MCP · Perfil de vault", name, {**profile, "vault_configurado": vault})
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
        elif action == "3": profile["auth"] = profile_auth(name, config.root)
        elif action == "4": profile["allowed_entry_roots"] = [item.strip() for item in (ask("Raízes separadas por ;", ";".join(profile["allowed_entry_roots"])) or "").split(";") if item.strip()]
        elif action == "5":
            save_password(name, profile["auth"], config.root)
            continue
        elif action == "6":
            try: KeePassCredentialResolver(config.root).test_profile_access(name); result(True, "Acesso ao cofre confirmado.")
            except CredentialError as error: result(False, f"Não foi possível acessar o cofre com este perfil: {error}")
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
            continue
        try:
            config.save_credentials(data)
            result(True, "Perfil de vault atualizado.")
        except McpConfigurationError as error:
            result(False, str(error))


def vault_profiles_menu(config: McpConfiguration) -> None:
    while True:
        try:
            names = config.vault_profile_names()
        except McpConfigurationError as error:
            result(False, str(error))
            return
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
                decision = flow_choice(
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
            decision = flow_choice(
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
        decision = flow_choice(
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
                decision = flow_choice(f"Credencial · {integration_name}", options, options[0])
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
                decision = flow_choice(f"Credencial · {integration_name}", options, options[0])
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
        profile_screen("MCP · Perfil Todoist", name, profile)
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
            name = ask_new_profile_name("Nome do perfil Todoist", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass da chave", vault_entry_path(config.root, "MCP", "Todoist", name, "Token"), True)
            if not entry: result(False, "Perfil Todoist inválido."); continue
            credential = configure_vault_password_entry(config, vault, entry, "Todoist")
            if credential is None: continue
            entry, field = credential
            description = ask("Descrição de quando utilizar este perfil", required=True)
            if not description: result(False, "Perfil Todoist inválido."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": entry, "credential_field": field}
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Todoist", names, manage_todoist_profile)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
        profile_screen("MCP · Perfil Omie", name, profile)
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
            name = ask_new_profile_name("Nome do perfil Omie", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass das credenciais", vault_entry_path(config.root, "MCP", "Omie", name, "Credenciais"), True)
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
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Omie", names, manage_omie_profile)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def manage_cloudflare_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Cloudflare até voltar ou removê-lo."""
    while True:
        data = config.cloudflare()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        profile_screen("MCP · Perfil Cloudflare", name, profile)
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
            name = ask_new_profile_name("Nome do perfil Cloudflare", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass do API Token", vault_entry_path(config.root, "MCP", "Cloudflare", name, "ApiToken"), True)
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
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Cloudflare", names, manage_cloudflare_profile)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def manage_forward_email_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Forward Email até voltar ou removê-lo."""
    while True:
        data = config.forward_email()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        profile_screen("MCP · Perfil Forward Email", name, profile)
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
            name = ask_new_profile_name("Nome do perfil Forward Email", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass do token", vault_entry_path(config.root, "MCP", "ForwardEmail", name, "Token"), True)
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
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Forward Email", names, manage_forward_email_profile)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def manage_notion_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil Notion até voltar ou removê-lo."""
    while True:
        data = config.notion()
        profile = data["profiles"].get(name)
        if not isinstance(profile, dict):
            return
        profile_screen("MCP · Perfil Notion", name, profile)
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
            name = ask_new_profile_name("Nome do perfil Notion", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]:
                result(False, "Nome inválido, repetido ou retorno solicitado.")
                continue
            entry = ask("Entrada KeePass do token", vault_entry_path(config.root, "MCP", "Notion", name, "Token"), True)
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
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Notion", names, manage_notion_profile)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def aws_credentials(config: McpConfiguration, vault: str, entry: str, access_field: str = "username", secret_field: str = "password") -> tuple[str, dict[str, str]] | None:
    """Configura Access Key ID e Secret Access Key em uma entrada dedicada."""
    note("Use entrada dedicada à AWS: Access Key ID em username e Secret Access Key em password.")
    return configure_vault_entry_fields(config, vault, entry, "AWS", [("access_key_id_field", "Access Key ID", access_field), ("secret_access_key_field", "Secret Access Key", secret_field)])


def manage_aws_profile(config: McpConfiguration, name: str) -> None:
    """Mantém o operador no perfil AWS até voltar ou removê-lo."""
    while True:
        data = config.aws(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Perfil AWS", name, profile)
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
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
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
            name = ask_new_profile_name("Nome do perfil AWS", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass das credenciais", vault_entry_path(config.root, "MCP", "AWS", name, "Credentials"), True)
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
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil AWS", names, manage_aws_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def mysql_credentials(config: McpConfiguration, vault: str, entry: str, username_field: str = "username", password_field: str = "password") -> tuple[str, dict[str, str]] | None:
    note("Use entrada dedicada ao MySQL: usuário em username e senha em password.")
    return configure_vault_entry_fields(config, vault, entry, "MySQL", [("username_field", "Usuário", username_field), ("password_field", "Senha", password_field)])


def manage_mysql_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.mysql(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Perfil MySQL", name, profile)
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
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_mysql(data); result(True, "Perfil MySQL atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def mysql_menu(config: McpConfiguration) -> None:
    while True:
        data = config.mysql(); names = sorted(data["profiles"]); status = "ATIVA" if config.mysql_tool_available() else "inativa"
        screen("MCP · MySQL", f"Consultas, scripts e administração · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask_new_profile_name("Nome do perfil MySQL", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass das credenciais", vault_entry_path(config.root, "MCP", "MySQL", name, "Credentials"), True)
            if not entry: continue
            credential = mysql_credentials(config, vault, entry)
            if credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); host = ask("Servidor MySQL", "127.0.0.1", True); database = ask("Banco padrão (opcional)"); socket = ask("Socket MySQL (opcional)"); roots = ask("Raízes de scripts SQL separadas por ; (opcional)")
            if None in (description, host, database, socket, roots): continue
            data["profiles"][name] = {"enabled": True, "description": description, "host": host, "database": database, "socket": socket, "allow_client_commands": False, "credential_profile": vault, "credential_entry_path": credential[0], **credential[1], "readable_roots": [item.strip() for item in roots.split(";") if item.strip()]}
            try: config.save_mysql(data); result(True, "Perfil MySQL atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil MySQL", names, manage_mysql_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def trust_ssh_host(host: str, port: int, known_hosts: str) -> bool:
    """Exibe a fingerprint antes de persistir a chave apresentada pelo host."""
    try:
        key, fingerprint = fetch_host_key(host, port)
        print("\n  " + Ui.text("Confirmar identidade do servidor SSH", Ui.bold, Ui.violet))
        note(f"Servidor: {host if port == 22 else f'[{host}]:{port}'}"); note(f"Chave: {key.get_name()} · {fingerprint}"); note(f"Arquivo: {known_hosts}")
        confirmation = ask("Digite CONFIAR para gravar esta chave", required=True)
        if confirmation is None:
            result(False, "Chave SSH não gravada: confirmação cancelada.")
            return False
        if confirmation.casefold() != "confiar":
            result(False, "Chave SSH não gravada: digite exatamente CONFIAR para confirmar a fingerprint exibida.")
            return False
        save_host_key(Path(known_hosts), host, port, key); result(True, "Chave SSH confirmada e gravada."); return True
    except SshError as error: result(False, str(error)); return False


def ssh_authentication(config: McpConfiguration, vault: str, entry: str, mode: str, password_field: str = "password") -> dict[str, str] | None:
    if mode == "password":
        configured = configure_vault_entry_fields(config, vault, entry, "SSH", [("password_field", "Senha SSH", password_field)])
        if configured is None: return None
        return {"credential_entry_path": configured[0], **configured[1], "key_attachment": "", "passphrase_field": ""}
    try:
        if not KeePassCredentialResolver(config.root).entry_exists(vault, entry):
            result(False, "A entrada KeePass deve existir e conter o anexo da chave privada."); return None
    except CredentialError as error: result(False, str(error)); return None
    attachment = ask("Nome do anexo da chave privada", "id_ed25519", True); passphrase = ask("Campo da passphrase (vazio = chave sem passphrase)")
    if attachment is None or passphrase is None: return None
    return {"credential_entry_path": entry, "password_field": "password", "key_attachment": attachment, "passphrase_field": passphrase}


def manage_ssh_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.ssh(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Perfil SSH", name, profile)
        item("1.", "Alterar servidor, porta e usuário"); item("2.", "Alterar autenticação e credencial"); item("3.", "Alterar descrição")
        item("4.", "Confirmar chave do servidor"); item("5.", "Alterar raízes de upload", f"{len(profile['readable_roots'])} configurada(s)"); item("6.", "Alterar raízes de download", f"{len(profile['writable_roots'])} configurada(s)")
        item("7.", "Habilitar"); item("8.", "Desabilitar"); item("9.", "Testar conexão"); item("10.", "Remover perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action == "1":
            host = ask("Host", profile["host"], True); port = ask("Porta", str(profile["port"]), True); username = ask("Usuário", profile["username"], True)
            if not host or not port or not port.isdigit() or not username: result(False, "Servidor, porta ou usuário inválido."); continue
            profile.update(host=host, port=int(port), username=username)
            if not trust_ssh_host(host, int(port), profile["known_hosts"]): continue
        elif action == "2":
            vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not vault: continue
            mode_label = flow_choice("Autenticação SSH", ["Senha", "Chave privada"], "Senha" if profile["auth_mode"] == "password" else "Chave privada")
            if mode_label is None: continue
            mode = "password" if mode_label == "Senha" else "key"; entry = ask("Entrada KeePass", profile["credential_entry_path"], True)
            if not entry: continue
            authentication = ssh_authentication(config, vault, entry, mode, profile.get("password_field", "password"))
            if authentication is None: continue
            profile.update(authentication); profile["credential_profile"], profile["auth_mode"] = vault, mode
        elif action == "3":
            value = ask("Descrição", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "4":
            trust_ssh_host(profile["host"], profile["port"], profile["known_hosts"]); continue
        elif action in {"5", "6"}:
            key = "readable_roots" if action == "5" else "writable_roots"; value = ask("Raízes separadas por ;", ";".join(profile[key]))
            if value is None: continue
            profile[key] = [item.strip() for item in value.split(";") if item.strip()]
        elif action == "7": profile["enabled"] = True
        elif action == "8": profile["enabled"] = False
        elif action == "9":
            try: result(SshService(config.root).test_connection(name)["authenticated"], "Conexão e autenticação SSH confirmadas; nenhum comando foi executado.")
            except (SshError, ValueError) as error: result(False, str(error))
            continue
        elif action == "10":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil SSH não removido."); continue
            del data["profiles"][name]
            try: config.save_ssh(data); result(True, "Perfil SSH removido. Credenciais e known_hosts foram preservados.")
            except McpConfigurationError as error: result(False, f"Perfil SSH não removido: {error}"); continue
            return
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_ssh(data); result(True, "Perfil SSH atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def ssh_menu(config: McpConfiguration) -> None:
    while True:
        data = config.ssh(); names = sorted(data["profiles"]); status = "ATIVA" if config.ssh_tool_available() else "inativa"
        screen("MCP · SSH", f"Comandos remotos e transferências SFTP · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask_new_profile_name("Nome do perfil SSH", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            host = ask("Host", required=True); port = ask("Porta", "22", True); username = ask("Usuário", required=True)
            mode_label = flow_choice("Autenticação SSH", ["Senha", "Chave privada"], "Chave privada")
            if not host or not port or not port.isdigit() or not username or mode_label is None: result(False, "Perfil SSH inválido."); continue
            mode = "password" if mode_label == "Senha" else "key"
            entry = ask("Entrada KeePass", vault_entry_path(config.root, "MCP", "SSH", name, "Password" if mode == "password" else "PrivateKey"), True)
            if not entry: continue
            authentication = ssh_authentication(config, vault, entry, mode)
            if authentication is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); readable = ask("Raízes de upload separadas por ; (opcional)"); writable = ask("Raízes de download separadas por ; (opcional)")
            if None in (description, readable, writable): continue
            known_hosts = str(config.root / "configs" / "mcp" / "ssh" / "known_hosts")
            if not trust_ssh_host(host, int(port), known_hosts): continue
            data["profiles"][name] = {"enabled": True, "description": description, "host": host, "port": int(port), "username": username, "auth_mode": mode, "credential_profile": vault, **authentication, "known_hosts": known_hosts, "readable_roots": [item.strip() for item in readable.split(";") if item.strip()], "writable_roots": [item.strip() for item in writable.split(";") if item.strip()]}
            try: config.save_ssh(data); result(True, "Perfil SSH atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil SSH", names, manage_ssh_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def bis2cmd_credentials(config: McpConfiguration, vault: str, entry: str, username_field: str = "username", password_field: str = "password") -> tuple[str, dict[str, str]] | None:
    note("Use entrada dedicada ao BIS2CMD: usuário WildFly em username e senha em password.")
    return configure_vault_entry_fields(config, vault, entry, "BIS2CMD", [("username_field", "Usuário WildFly", username_field), ("password_field", "Senha WildFly", password_field)])


def manage_bis2cmd_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.bis2cmd(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Perfil BIS2CMD", name, profile)
        item("1.", "Alterar perfil de vault"); item("2.", "Alterar entrada e credenciais"); item("3.", "Alterar descrição"); item("4.", "Alterar JAR e diretório de trabalho")
        item("5.", "Alterar host e porta"); item("6.", "Alterar raízes de importação", f"{len(profile['readable_roots'])} configurada(s)"); item("7.", "Alterar raízes de exportação", f"{len(profile['writable_roots'])} configurada(s)")
        item("8.", "Habilitar"); item("9.", "Desabilitar"); item("10.", "Testar conexão"); item("11.", "Remover perfil"); item("12.", "Renomear perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2"}:
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action == "1" else profile["credential_profile"]
            if not vault: continue
            entry = profile["credential_entry_path"] if action == "1" else ask("Entrada KeePass", profile["credential_entry_path"], True)
            if not entry: continue
            credential = bis2cmd_credentials(config, vault, entry, profile["username_field"], profile["password_field"])
            if credential is None: continue
            profile["credential_profile"], profile["credential_entry_path"] = vault, credential[0]; profile.update(credential[1])
        elif action == "3":
            value = ask("Descrição", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "4":
            jar = ask("Arquivo JAR BIS2CMD", profile["jar_path"], True); working = ask("Diretório de trabalho", profile["working_dir"], True)
            if not jar or not working: continue
            profile["jar_path"], profile["working_dir"] = jar, working
        elif action == "5":
            host = ask("Host WildFly", profile["host"], True); port = ask("Porta", str(profile["port"]), True)
            if not host or not port or not port.isdigit(): result(False, "Host ou porta inválido."); continue
            profile["host"], profile["port"] = host, int(port)
        elif action in {"6", "7"}:
            key = "readable_roots" if action == "6" else "writable_roots"; value = ask("Raízes separadas por ;", ";".join(profile[key]))
            if value is None: continue
            profile[key] = [item.strip() for item in value.split(";") if item.strip()]
        elif action == "8": profile["enabled"] = True
        elif action == "9": profile["enabled"] = False
        elif action == "10":
            try: result(Bis2CmdService(config.root).test_connection(name)["authenticated"], "Conexão BIS2CMD confirmada.")
            except (Bis2CmdError, ValueError) as error: result(False, str(error))
            continue
        elif action == "11":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil BIS2CMD não removido."); continue
            del data["profiles"][name]
            try: config.save_bis2cmd(data); result(True, "Perfil BIS2CMD removido. A entrada KeePass foi preservada.")
            except McpConfigurationError as error: result(False, f"Perfil BIS2CMD não removido: {error}"); continue
            return
        elif action == "12":
            replacement = ask("Novo nome do perfil", name, True)
            if not replacement: continue
            if replacement == name:
                note("O perfil já possui esse nome."); continue
            if replacement in data["profiles"]:
                result(False, "Já existe um perfil BIS2CMD com esse nome."); continue
            if prompt(f"Renomear o perfil BIS2CMD '{name}' para '{replacement}'? [s/N]: ").strip().casefold() not in {"s", "sim"}:
                result(False, "Perfil BIS2CMD não renomeado."); continue
            data["profiles"][replacement] = data["profiles"].pop(name)
            try: config.save_bis2cmd(data); result(True, "Perfil BIS2CMD renomeado. A entrada KeePass foi preservada.")
            except McpConfigurationError as error: result(False, f"Perfil BIS2CMD não renomeado: {error}"); continue
            return
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_bis2cmd(data); result(True, "Perfil BIS2CMD atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def bis2cmd_menu(config: McpConfiguration) -> None:
    while True:
        data = config.bis2cmd(); names = sorted(data["profiles"]); status = "ATIVA" if config.bis2cmd_tool_available() else "inativa"
        screen("MCP · BIS2CMD", f"Cliente BIS2 por EJB remoto · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask_new_profile_name("Nome do perfil BIS2CMD", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass", vault_entry_path(config.root, "MCP", "BIS2CMD", name, "Credentials"), True)
            if not entry: continue
            credential = bis2cmd_credentials(config, vault, entry)
            if credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); jar = ask("Arquivo JAR BIS2CMD", required=True); working = ask("Diretório de trabalho", required=True); host = ask("Host WildFly", "127.0.0.1", True); port = ask("Porta", "8080", True); readable = ask("Raízes de importação separadas por ; (opcional)"); writable = ask("Raízes de exportação separadas por ; (opcional)")
            if not description or not jar or not working or not host or not port or not port.isdigit() or readable is None or writable is None: result(False, "Perfil BIS2CMD inválido."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "jar_path": jar, "working_dir": working, "host": host, "port": int(port), "java_options": "", "credential_profile": vault, "credential_entry_path": credential[0], **credential[1], "readable_roots": [item.strip() for item in readable.split(";") if item.strip()], "writable_roots": [item.strip() for item in writable.split(";") if item.strip()]}
            try: config.save_bis2cmd(data); result(True, "Perfil BIS2CMD atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil BIS2CMD", names, manage_bis2cmd_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def bis10cmd_credentials(config: McpConfiguration, vault: str, entry: str, kind: str, username_field: str = "username", password_field: str = "password") -> tuple[str, dict[str, str]] | None:
    prefix = "jndi" if kind == "JNDI" else "bis"
    note(f"Use entrada dedicada ao BIS10CMD {kind}: usuário em username e senha em password.")
    return configure_vault_entry_fields(config, vault, entry, f"BIS10CMD {kind}", [(f"{prefix}_username_field", f"Usuário {kind}", username_field), (f"{prefix}_password_field", f"Senha {kind}", password_field)])


def manage_bis10cmd_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.bis10cmd(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Perfil BIS10CMD", name, profile)
        item("1.", "Alterar vault JNDI"); item("2.", "Alterar entrada JNDI"); item("3.", "Alterar vault BIS10"); item("4.", "Alterar entrada BIS10")
        item("5.", "Alterar descrição"); item("6.", "Alterar JAR e diretório de trabalho"); item("7.", "Alterar host, porta e locale")
        item("8.", "Habilitar"); item("9.", "Desabilitar"); item("10.", "Testar conexão"); item("11.", "Remover perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2", "3", "4"}:
            jndi = action in {"1", "2"}; prefix = "jndi" if jndi else "bis"; kind = "JNDI" if jndi else "BIS10"
            vault_key = prefix + "_credential_profile"; entry_key = prefix + "_credential_entry_path"
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action in {"1", "3"} else profile[vault_key]
            if not vault: continue
            entry = profile[entry_key] if action in {"1", "3"} else ask(f"Entrada KeePass {kind}", profile[entry_key], True)
            if not entry: continue
            credential = bis10cmd_credentials(config, vault, entry, kind, profile[prefix + "_username_field"], profile[prefix + "_password_field"])
            if credential is None: continue
            profile[vault_key], profile[entry_key] = vault, credential[0]; profile.update(credential[1])
        elif action == "5":
            value = ask("Descrição", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "6":
            jar = ask("Arquivo JAR BIS10CMD", profile["jar_path"], True); working = ask("Diretório de trabalho", profile["working_dir"], True)
            if not jar or not working: continue
            profile["jar_path"], profile["working_dir"] = jar, working
        elif action == "7":
            host = ask("Host WildFly", profile["host"], True); port = ask("Porta", str(profile["port"]), True); locale = ask("Locale", profile["locale"], True)
            if not host or not port or not port.isdigit() or not locale: result(False, "Host, porta ou locale inválido."); continue
            profile["host"], profile["port"], profile["locale"] = host, int(port), locale
        elif action == "8": profile["enabled"] = True
        elif action == "9": profile["enabled"] = False
        elif action == "10":
            try: result(Bis10CmdService(config.root).test_connection(name)["authenticated"], "Conexão BIS10CMD confirmada.")
            except (Bis10CmdError, ValueError) as error: result(False, str(error))
            continue
        elif action == "11":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil BIS10CMD não removido."); continue
            del data["profiles"][name]
            try: config.save_bis10cmd(data); result(True, "Perfil BIS10CMD removido. As entradas KeePass foram preservadas.")
            except McpConfigurationError as error: result(False, f"Perfil BIS10CMD não removido: {error}"); continue
            return
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_bis10cmd(data); result(True, "Perfil BIS10CMD atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def bis10cmd_menu(config: McpConfiguration) -> None:
    while True:
        data = config.bis10cmd(); names = sorted(data["profiles"]); status = "ATIVA" if config.bis10cmd_tool_available() else "inativa"
        screen("MCP · BIS10CMD", f"Cliente BIS10 por EJB remoto · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask_new_profile_name("Nome do perfil BIS10CMD", data["profiles"])
            if not name or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            jndi_vault = select("Escolha o perfil de vault JNDI", config.vault_profile_names())
            if not jndi_vault: continue
            jndi_entry = ask("Entrada KeePass JNDI", vault_entry_path(config.root, "MCP", "BIS10CMD", name, "JNDI"), True)
            if not jndi_entry: continue
            jndi_credential = bis10cmd_credentials(config, jndi_vault, jndi_entry, "JNDI")
            if jndi_credential is None: continue
            bis_vault = select("Escolha o perfil de vault BIS10", config.vault_profile_names())
            if not bis_vault: continue
            bis_entry = ask("Entrada KeePass BIS10", vault_entry_path(config.root, "MCP", "BIS10CMD", name, "BIS10"), True)
            if not bis_entry: continue
            bis_credential = bis10cmd_credentials(config, bis_vault, bis_entry, "BIS10")
            if bis_credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); jar = ask("Arquivo JAR BIS10CMD", "C:/opt/BIS10CMD/BISCMD-10.0.jar", True); working = ask("Diretório de trabalho", "C:/opt/BIS10CMD", True); host = ask("Host WildFly", "127.0.0.1", True); port = ask("Porta", "8080", True); locale = ask("Locale", "pt-BR", True)
            if not description or not jar or not working or not host or not port or not port.isdigit() or not locale: result(False, "Perfil BIS10CMD inválido."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "jar_path": jar, "working_dir": working, "host": host, "port": int(port), "locale": locale, "jndi_credential_profile": jndi_vault, "jndi_credential_entry_path": jndi_credential[0], **jndi_credential[1], "bis_credential_profile": bis_vault, "bis_credential_entry_path": bis_credential[0], **bis_credential[1]}
            try: config.save_bis10cmd(data); result(True, "Perfil BIS10CMD atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil BIS10CMD", names, manage_bis10cmd_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


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
        profile_screen("MCP · BTG · Pagamentos e monitor", name, profile)
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
            selection = flow_choice("Monitor deste perfil", ["Habilitado", "Desabilitado"], "Habilitado" if profile.get("monitor_enabled") else "Desabilitado")
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
        profile_screen("MCP · Perfil BTG Empresas", f"{name} · {profile['environment']} · CNPJ {profile['company_id']}", profile)
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
            origin = flow_choice("URI de retorno BTG", ["Calcular pelo Gateway/Tunnel", "Informar manualmente"], "Calcular pelo Gateway/Tunnel")
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
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
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")
            continue
        name = ask_new_profile_name("Nome do perfil BTG", data["profiles"])
        if not name or name in data["profiles"]:
            result(False, "Nome inválido, repetido ou retorno solicitado.")
            continue
        environment = flow_choice("Ambiente BTG", ["sandbox", "production"], "sandbox")
        if environment is None:
            continue
        company = ask("CNPJ da empresa (14 dígitos)", required=True)
        if company is None:
            continue
        vault = select("Escolha o perfil de vault", config.vault_profile_names())
        if not vault:
            continue
        entry = ask("Entrada KeePass das credenciais", vault_entry_path(config.root, "MCP", "BTG", environment, f"{name}:OAuth"), True)
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
            result(False, "A descrição de quando utilizar o perfil BTG é obrigatória.")
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
        profile_screen("MCP · Perfil EccoVox", name, profile)
        item("1.", "Alterar descrição")
        item("2.", "Alterar endpoint local")
        item("3.", "Alterar voz padrão", profile.get("voice") or "Padrão configurado no EccoVox")
        item("4.", "Alterar idioma padrão", profile.get("language") or "Padrão configurado no EccoVox")
        item("5.", "Alterar pasta padrão de saída", profile.get("default_output_directory") or "<Work>/.onmyoji/eccovox/output")
        item("6.", "Alterar raízes de leitura")
        item("7.", "Alterar raízes de escrita")
        item("8.", "Habilitar" if not profile["enabled"] else "Desabilitar")
        item("9.", "Consultar capacidades")
        item("10.", "Remover perfil")
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
            try:
                capabilities = EccoVoxService(config.root).capabilities(name)
                key, title = ("voices", "Voz padrão EccoVox") if action == "3" else ("languages", "Idioma padrão EccoVox")
                value = choose_eccovox_capability(capabilities, key, title, str(profile.get("voice" if action == "3" else "language", "")))
            except EccoVoxError as error:
                result(False, str(error)); continue
            if value is None: continue
            profile["voice" if action == "3" else "language"] = value
        elif action == "5":
            value = ask("Pasta padrão de saída", profile.get("default_output_directory") or eccovox_default_output_directory(config), True)
            if value is None: continue
            profile["default_output_directory"] = eccovox_default_output_directory(config, value)
        elif action in {"6", "7"}:
            key = "readable_roots" if action == "6" else "writable_roots"
            defaults = eccovox_media_roots(config)
            suggested = ";".join(profile[key] or [defaults[0]])
            value = ask("Raízes separadas por ;", suggested, True)
            if value is None: continue
            profile[key] = eccovox_media_roots(config, value)
        elif action == "8": profile["enabled"] = not profile["enabled"]
        elif action == "9":
            try:
                capabilities = EccoVoxService(config.root).capabilities(name)
                voices = eccovox_capability_options(capabilities, "voices")
                languages = eccovox_capability_options(capabilities, "languages")
                result(True, f"EccoVox disponível: {len(voices)} voz(es) e {len(languages)} idioma(s) para síntese.")
            except EccoVoxError as error: result(False, str(error))
            continue
        elif action == "10":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil EccoVox não removido."); continue
            del data["profiles"][name]
            try: config.save_eccovox(data); result(True, "Perfil EccoVox removido.")
            except McpConfigurationError as error: result(False, f"Perfil EccoVox não removido: {error}"); continue
            return
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_eccovox(data); result(True, "Perfil EccoVox atualizado.")
        except (McpConfigurationError, ValueError) as error: result(False, str(error))


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
            name = ask_new_profile_name("Nome do perfil", data["profiles"])
            if not name or name in data["profiles"]: result(False, "Nome inválido ou perfil já existente."); continue
            description = ask("Descrição de quando utilizar este perfil", required=True)
            endpoint = ask("Endpoint HTTP local", "http://127.0.0.1:8870", True)
            if not description or not endpoint:
                result(False, "Perfil EccoVox não foi criado."); continue
            try:
                capabilities = EccoVoxService.capabilities_at(endpoint)
                voice = choose_eccovox_capability(capabilities, "voices", "Voz padrão EccoVox")
                if voice is None: continue
                language = choose_eccovox_capability(capabilities, "languages", "Idioma padrão EccoVox")
                if language is None: continue
            except EccoVoxError as error:
                result(False, f"Não foi possível consultar as capacidades do EccoVox: {error}"); continue
            defaults = eccovox_media_roots(config)
            readable = ask("Raízes de leitura, separadas por ;", defaults[0], True)
            writable = ask("Raízes de escrita, separadas por ;", defaults[0], True)
            output = ask("Pasta padrão de saída", eccovox_default_output_directory(config), True)
            if not all((readable, writable)) or output is None: result(False, "Perfil EccoVox não foi criado."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "base_url": endpoint, "voice": voice, "language": language, "default_output_directory": eccovox_default_output_directory(config, output), "readable_roots": eccovox_media_roots(config, readable), "writable_roots": eccovox_media_roots(config, writable)}
            try: config.save_eccovox(data); result(True, "Perfil EccoVox salvo no MCP.")
            except (McpConfigurationError, ValueError) as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil EccoVox", names, manage_eccovox_profile)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def google_credentials(config: McpConfiguration, vault: str, entry: str, client_id_field: str = "username", client_secret_field: str = "password") -> tuple[str, dict[str, str]] | None:
    note("Use entrada dedicada ao Google: Client ID em username e Client Secret em password. Os refresh tokens permanecem em notes.")
    return configure_vault_entry_fields(config, vault, entry, "Google", [("client_id_field", "Client ID", client_id_field), ("client_secret_field", "Client Secret", client_secret_field)])


def manage_google_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.google(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Perfil Google", name, profile)
        item("1.", "Alterar perfil de vault"); item("2.", "Alterar entrada e credenciais"); item("3.", "Alterar descrição")
        item("4.", "Alterar identificador OAuth"); item("5.", "Alterar usuário Gmail"); item("6.", "Alterar raízes de arquivos")
        item("7.", "Habilitar"); item("8.", "Desabilitar"); item("9.", "Autorizar conta no Google")
        item("10.", "Testar conexão"); item("11.", "Remover perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2"}:
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action == "1" else profile["credential_profile"]
            if not vault: continue
            entry = profile["credential_entry_path"] if action == "1" else ask("Entrada KeePass", profile["credential_entry_path"], True)
            if not entry: continue
            credential = google_credentials(config, vault, entry, profile["client_id_field"], profile["client_secret_field"])
            if credential is None: continue
            profile["credential_profile"], profile["credential_entry_path"] = vault, credential[0]; profile.update(credential[1])
        elif action == "3":
            value = ask("Descrição de quando utilizar este perfil", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "4":
            value = ask("Identificador do token OAuth dentro da entrada", profile["oauth_profile"], True)
            if not value: continue
            profile["oauth_profile"] = value
        elif action == "5":
            value = ask("Usuário Gmail", profile["user_id"], True)
            if not value: continue
            profile["user_id"] = value
        elif action == "6":
            readable = ask("Raízes de leitura separadas por ;", ";".join(profile["readable_roots"])); writable = ask("Raízes de escrita separadas por ;", ";".join(profile["writable_roots"]))
            if readable is None or writable is None: continue
            profile["readable_roots"] = [item.strip() for item in readable.split(";") if item.strip()]; profile["writable_roots"] = [item.strip() for item in writable.split(";") if item.strip()]
        elif action == "7": profile["enabled"] = True
        elif action == "8": profile["enabled"] = False
        elif action == "9":
            try:
                authorize_google(GoogleService(config.root), name, url_consumer=lambda url: note("Se o navegador não abrir, acesse nesta máquina: " + url)); result(True, "Conta Google autorizada; refresh token salvo no vault.")
            except GoogleError as error: result(False, str(error))
            continue
        elif action == "10":
            try: result(GoogleService(config.root).test_connection(name)["authenticated"], "Conexão Google confirmada.")
            except (GoogleError, ValueError) as error: result(False, str(error))
            continue
        elif action == "11":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil Google não removido."); continue
            del data["profiles"][name]
            try: config.save_google(data); result(True, "Perfil Google removido. A entrada KeePass e seus tokens foram preservados.")
            except McpConfigurationError as error: result(False, f"Perfil Google não removido: {error}"); continue
            return
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_google(data); result(True, "Perfil Google atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def google_menu(config: McpConfiguration) -> None:
    while True:
        data = config.google(); names = sorted(data["profiles"]); status = "ATIVA" if config.google_tool_available() else "inativa"
        screen("MCP · Google", f"Gmail, People, Drive e Calendar · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask_new_profile_name("Nome do perfil Google", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass", vault_entry_path(config.root, "MCP", "Google", name, "OAuth"), True)
            if not entry: continue
            credential = google_credentials(config, vault, entry)
            if credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); oauth_profile = ask("Identificador do token OAuth dentro da entrada", name, True); user_id = ask("Usuário Gmail", "me", True); readable = ask("Raízes de leitura separadas por ; (opcional)"); writable = ask("Raízes de escrita separadas por ; (opcional)")
            if not description or not oauth_profile or not user_id or readable is None or writable is None: result(False, "Perfil Google não foi criado."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "oauth_profile": oauth_profile, "credential_profile": vault, "credential_entry_path": credential[0], **credential[1], "user_id": user_id, "readable_roots": [item.strip() for item in readable.split(";") if item.strip()], "writable_roots": [item.strip() for item in writable.split(";") if item.strip()]}
            try: config.save_google(data); result(True, "Perfil Google salvo. Use Gerenciar perfil > Autorizar conta no Google para concluir o OAuth.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Google", names, manage_google_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def spotify_credentials(config: McpConfiguration, vault: str, entry: str, client_id_field: str = "username") -> tuple[str, dict[str, str]] | None:
    note("Use entrada dedicada ao Spotify: Client ID em username. O fluxo OAuth PKCE gravará o refresh token em password.")
    return configure_vault_entry_fields(config, vault, entry, "Spotify", [("client_id_field", "Client ID", client_id_field)])


def manage_spotify_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.spotify(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        redirect_uri = f"http://127.0.0.1:{profile['callback_port']}/callback"
        profile_screen("MCP · Perfil Spotify", name, profile)
        item("1.", "Alterar perfil de vault"); item("2.", "Alterar entrada e Client ID"); item("3.", "Alterar descrição")
        item("4.", "Alterar porta de callback", redirect_uri); item("5.", "Habilitar"); item("6.", "Desabilitar")
        item("7.", "Autorizar conta no Spotify"); item("8.", "Testar conexão"); item("9.", "Remover perfil"); item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2"}:
            vault = select("Escolha o perfil de vault", config.vault_profile_names()) if action == "1" else profile["credential_profile"]
            if not vault: continue
            entry = profile["credential_entry_path"] if action == "1" else ask("Entrada KeePass", profile["credential_entry_path"], True)
            if not entry: continue
            credential = spotify_credentials(config, vault, entry, profile["client_id_field"])
            if credential is None: continue
            profile["credential_profile"], profile["credential_entry_path"] = vault, credential[0]; profile.update(credential[1])
        elif action == "3":
            value = ask("Descrição de quando utilizar este perfil", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "4":
            value = ask("Porta do callback local", str(profile["callback_port"]), True)
            if not value or not value.isdigit(): result(False, "Porta de callback inválida."); continue
            profile["callback_port"] = int(value)
        elif action == "5": profile["enabled"] = True
        elif action == "6": profile["enabled"] = False
        elif action == "7":
            note(f"Cadastre exatamente esta Redirect URI no Spotify Dashboard: {redirect_uri}")
            try:
                authorize_spotify(SpotifyService(config.root), name, url_consumer=lambda url: note("Se o navegador não abrir, acesse nesta máquina: " + url)); result(True, "Conta Spotify autorizada; refresh token salvo no vault.")
            except SpotifyError as error: result(False, str(error))
            continue
        elif action == "8":
            try: result(SpotifyService(config.root).test_connection(name)["authenticated"], "Conexão Spotify confirmada.")
            except (SpotifyError, ValueError) as error: result(False, str(error))
            continue
        elif action == "9":
            confirmation = ask("Digite REMOVER para confirmar", required=True)
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Perfil Spotify não removido."); continue
            del data["profiles"][name]
            try: config.save_spotify(data); result(True, "Perfil Spotify removido. A entrada KeePass e seu token foram preservados.")
            except McpConfigurationError as error: result(False, f"Perfil Spotify não removido: {error}"); continue
            return
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        try: config.save_spotify(data); result(True, "Perfil Spotify atualizado.")
        except McpConfigurationError as error: result(False, str(error))


def spotify_menu(config: McpConfiguration) -> None:
    while True:
        data = config.spotify(); names = sorted(data["profiles"]); status = "ATIVA" if config.spotify_tool_available() else "inativa"
        screen("MCP · Spotify", f"Catálogo, biblioteca, playlists e player · {status}"); item("1.", "Criar perfil"); item("2.", "Gerenciar perfil", f"{len(names)} configurado(s)"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask_new_profile_name("Nome do perfil Spotify", data["profiles"]); vault = select("Escolha o perfil de vault", config.vault_profile_names())
            if not name or not vault or name in data["profiles"]: result(False, "Nome inválido, repetido ou retorno solicitado."); continue
            entry = ask("Entrada KeePass", vault_entry_path(config.root, "MCP", "Spotify", name, "OAuth"), True)
            if not entry: continue
            credential = spotify_credentials(config, vault, entry)
            if credential is None: continue
            description = ask("Descrição de quando utilizar este perfil", required=True); callback_port = ask("Porta do callback local", "8888", True)
            if not description or not callback_port or not callback_port.isdigit(): result(False, "Perfil Spotify não foi criado."); continue
            data["profiles"][name] = {"enabled": True, "description": description, "credential_profile": vault, "credential_entry_path": credential[0], **credential[1], "refresh_token_field": "password", "callback_port": int(callback_port)}
            try: config.save_spotify(data); result(True, "Perfil Spotify salvo. Cadastre a Redirect URI exibida e use Gerenciar perfil > Autorizar conta no Spotify.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2": manage_selected_profile(config, "MCP · Gerenciar perfil Spotify", names, manage_spotify_profile)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def manage_memory_instruction(config: McpConfiguration, identifier: str) -> None:
    """Permite ao operador corrigir ou excluir uma regra sem criar ciclo de vida."""
    service = MemoryService(config.root, max_results=config.memory()["max_results"])
    while True:
        try: entry = service.invoke("get_instruction", {"id": identifier})["instruction"]
        except MemoryError as error: result(False, str(error)); return
        screen("MCP · Memória · Instrução", str(entry["applies_when"]))
        item("1.", "Quando aplicar", str(entry["applies_when"])); item("2.", "Instrução", str(entry["instruction"])); item("3.", "Palavras-chave", ", ".join(entry["keywords"])); item("4.", "Excluir definitivamente"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "4":
            confirmation = ask("Digite REMOVER para excluir definitivamente")
            if confirmation is None or confirmation.casefold() != "remover": result(False, "Instrução não removida."); continue
            try: service.invoke("delete_instruction", {"id": identifier, "confirm": True}); result(True, "Instrução removida definitivamente."); return
            except MemoryError as error: result(False, str(error)); continue
        if choice not in {"1", "2", "3"}: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        applies_when, instruction, keywords = str(entry["applies_when"]), str(entry["instruction"]), list(entry["keywords"])
        if choice == "1":
            value = ask("Nova condição de aplicação", applies_when, True)
            if value is None: continue
            applies_when = value
        elif choice == "2":
            value = ask("Nova instrução", instruction, True)
            if value is None: continue
            instruction = value
        else:
            value = ask("Palavras-chave separadas por vírgula", ", ".join(keywords), True)
            if value is None: continue
            keywords = [part.strip() for part in value.split(",") if part.strip()]
        try:
            service.invoke("revise_instruction", {"id": identifier, "applies_when": applies_when, "instruction": instruction, "keywords": keywords, "confirm": True}); result(True, "Instrução atualizada.")
        except MemoryError as error: result(False, str(error))


def memory_instructions_menu(config: McpConfiguration) -> None:
    service = MemoryService(config.root, max_results=config.memory()["max_results"])
    while True:
        data = service.invoke("list_instructions", {"limit": 100, "offset": 0}); entries = data["instructions"]
        screen("MCP · Memória · Instruções", f"{data['total']} instrução(ões) vigente(s)")
        for index, entry in enumerate(entries, 1): item(f"{index}.", str(entry["applies_when"])[:70])
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice.isdigit() and 1 <= int(choice) <= len(entries): manage_memory_instruction(config, str(entries[int(choice) - 1]["id"]))
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def memory_menu(config: McpConfiguration) -> None:
    """Configura o serviço único de memória e sua recuperação contextual."""
    while True:
        data = config.memory(); enabled = data["enabled"] is True
        screen("MCP · Memória", "Livro de instruções atuais do Shikigami")
        item("1.", "Desabilitar" if enabled else "Habilitar", "Publica ou remove as ferramentas e workflows MCP na próxima sessão do Codex")
        item("2.", "Máximo de resultados", str(data["max_results"])); item("3.", "Gerenciar instruções"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            data["enabled"] = not enabled
            try: config.save_memory(data); result(True, "Memória habilitada; ferramentas e workflows MCP estarão disponíveis na próxima sessão do Codex." if not enabled else "Memória desabilitada; suas ferramentas e workflows MCP não serão publicados na próxima sessão do Codex.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "2":
            value = ask("Quantidade máxima por busca (1 a 50)", str(data["max_results"]), True)
            if value is None: continue
            if not value.isdigit() or not 1 <= int(value) <= 10: result(False, "Informe um número entre 1 e 10."); continue
            data["max_results"] = int(value)
            try: config.save_memory(data); result(True, "Limite de busca atualizado.")
            except McpConfigurationError as error: result(False, str(error))
        elif choice == "3": memory_instructions_menu(config)
        else: alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def data_tables_menu(config: McpConfiguration) -> None:
    """Instala schemas versionados sem expor migrations ao agente MCP."""
    while True:
        catalog = SchemaCatalog(config.root)
        try: schemas = catalog.discover()
        except DataTablesError as error: result(False, str(error)); return
        state = config.data_tables(); enabled = [identifier for identifier, value in state["schemas"].items() if value.get("enabled")]
        screen("MCP · Tabelas de dados", "Schemas versionados pelas skills do Shikigami")
        item("1.", "Schemas disponíveis", f"{len(schemas)} encontrado(s)")
        item("2.", "Instalar ou atualizar schema")
        item("3.", "Habilitar ou desabilitar schema", f"{len(enabled)} habilitado(s)")
        item("4.", "Validar integridade")
        item("5.", "Permitir autoria de schemas", "Habilitada" if state["schema_authoring_enabled"] else "Desabilitada")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice not in {"1", "2", "3", "4", "5"}: alert("Opção inválida. Escolha uma das alternativas ou cancele."); continue
        if choice == "5":
            state["schema_authoring_enabled"] = not state["schema_authoring_enabled"]
            try: config.save_data_tables(state); result(True, "Autoria de schemas habilitada." if state["schema_authoring_enabled"] else "Autoria de schemas desabilitada.")
            except McpConfigurationError as error: result(False, str(error))
            continue
        if not schemas: result(False, "Nenhum schema encontrado em shikigami-skills/*/schemas/*/schema.toml."); continue
        names = sorted(schemas)
        if choice == "1":
            for identifier in names:
                plan = catalog.plan(schemas[identifier]); item("·", identifier, f"instaladas: {len(plan['installed'])}; pendentes: {len(plan['pending'])}")
            continue
        identifier = select("MCP · Tabelas de dados · Schema", names)
        if not identifier: continue
        if choice == "2":
            try:
                plan = catalog.plan(schemas[identifier])
                if not plan["pending"]: result(True, "Schema já está atualizado."); continue
                confirmation = ask(f"Digite APLICAR para instalar migrations {plan['pending']}")
                if confirmation is None or confirmation.casefold() != "aplicar": result(False, "Migrations não aplicadas."); continue
                catalog.apply(identifier); result(True, "Schema atualizado com sucesso.")
            except DataTablesError as error: result(False, str(error))
        elif choice == "3":
            try:
                if catalog.plan(schemas[identifier])["pending"]: result(False, "Instale todas as migrations antes de habilitar o schema."); continue
                entry = state["schemas"].setdefault(identifier, {"enabled": False}); entry["enabled"] = not entry["enabled"]
                config.save_data_tables(state); result(True, "Schema habilitado." if entry["enabled"] else "Schema desabilitado.")
            except (DataTablesError, McpConfigurationError) as error: result(False, str(error))
        else:
            try:
                plan = catalog.plan(schemas[identifier]); result(True, "Schema íntegro." if not plan["pending"] else f"Schema íntegro, com migrations pendentes: {plan['pending']}.")
            except DataTablesError as error: result(False, str(error))


def choose_mercado_modules(current: list[str]) -> list[str] | None:
    """Seleciona módulos individualmente, sem aplicar presets implícitos."""
    selected = list(current)
    while True:
        labels = [f"{'✓' if module in selected else ' '} {module}" for module in MODULES] + ["Concluir seleção"]
        choice = flow_choice("Módulos Mercado", labels, "Concluir seleção")
        if choice is None:
            return None
        if choice == "Concluir seleção":
            return selected
        module = choice[2:]
        if module in selected:
            selected.remove(module)
        else:
            selected.append(module)
        note("Selecionados: " + (", ".join(selected) if selected else "nenhum módulo"))


def _mercado_callback(config: McpConfiguration, provider: str) -> str:
    from onmyoji_daemon.rpc import RpcError, call
    from onmyoji_daemon.supervisor import endpoint
    try:
        host, port, token = endpoint(config.root)
        response = call(host, port, token, "mini-apps.oauth.callback-url", {"integration": provider}, timeout=10)
        return str(response["callbackUrl"])
    except (OSError, ValueError, KeyError, RuntimeError, RpcError) as error:
        raise MercadoError("network_error", "Inicie o Gateway e o Tunnel para calcular o callback HTTPS.") from error


def _mercado_app_references(data: dict, provider: str, application: str) -> list[str]:
    return [name for name, profile in data["profiles"].items() if profile.get(provider, {}).get("application") == application]


def create_mercado_application(config: McpConfiguration, provider: str) -> None:
    data = config.mercado()
    catalog = data["applications"][provider]
    name = ask_new_profile_name("Nome do aplicativo", catalog)
    if not name or name in catalog:
        result(False, "Nome de aplicativo inválido, repetido ou criação cancelada.")
        return
    environment = flow_choice("Ambiente", ["production", "sandbox"], "production")
    if environment is None:
        return
    vault = select("Perfil de vault do aplicativo", config.vault_profile_names())
    if not vault:
        return
    label = "MercadoLivre" if provider == "mercadolivre" else "MercadoPago"
    entry = ask("Entrada KeePass do aplicativo", vault_entry_path(config.root, "MCP", label, "Applications", name), True)
    if not entry:
        return
    note("Client ID será salvo em username e Client Secret em password. Tokens de usuário não usam esta entrada.")
    credential = configure_vault_entry_fields(config, vault, entry, label, [("client_id", "Client ID", "username"), ("client_secret", "Client Secret", "password")])
    if credential is None:
        return
    try:
        callback = _mercado_callback(config, provider)
        note("Cadastre exatamente este callback HTTPS no portal do provedor: " + callback)
    except MercadoError as error:
        note(str(error))
        callback = ""
    redirect = ask("Redirect URI já cadastrada no provedor", callback, True)
    scopes_value = ask("Escopos separados por ;", "read;write;offline_access", True)
    if not redirect or not scopes_value:
        return
    scopes = [value.strip() for value in scopes_value.split(";") if value.strip()]
    catalog[name] = {"environment": environment, "credential_profile": vault, "credential_entry_path": credential[0], "redirect_uri": redirect, "scopes": scopes}
    try:
        config.save_mercado(data)
        result(True, "Aplicativo salvo. O setup não cria o aplicativo no portal do provedor.")
    except McpConfigurationError as error:
        result(False, str(error))


def manage_mercado_application(config: McpConfiguration, provider: str, name: str) -> None:
    while True:
        data = config.mercado(); application = data["applications"][provider].get(name)
        if not isinstance(application, dict):
            return
        profile_screen("MCP · Mercado · Aplicativo", f"{provider} · {name}", application)
        item("1.", "Alterar ambiente")
        item("2.", "Alterar entrada e credenciais")
        item("3.", "Alterar Redirect URI")
        item("4.", "Alterar escopos")
        item("5.", "Validar credenciais", "Somente presença; valores não são exibidos")
        item("6.", "Remover aplicativo")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action == "1":
            value = flow_choice("Ambiente", ["production", "sandbox"], application["environment"])
            if value is None: continue
            application["environment"] = value
        elif action == "2":
            vault = select("Perfil de vault", config.vault_profile_names(), application["credential_profile"])
            if not vault: continue
            entry = ask("Entrada KeePass", application["credential_entry_path"], True)
            if not entry: continue
            saved = configure_vault_entry_fields(config, vault, entry, provider, [("client_id", "Client ID", "username"), ("client_secret", "Client Secret", "password")])
            if saved is None: continue
            application["credential_profile"], application["credential_entry_path"] = vault, saved[0]
        elif action == "3":
            try:
                note("Callback calculado pelo Gateway: " + _mercado_callback(config, provider))
            except MercadoError as error: note(str(error))
            value = ask("Redirect URI cadastrada", application["redirect_uri"], True)
            if not value: continue
            application["redirect_uri"] = value
        elif action == "4":
            value = ask("Escopos separados por ;", ";".join(application["scopes"]), True)
            if not value: continue
            application["scopes"] = [item.strip() for item in value.split(";") if item.strip()]
        elif action == "5":
            try:
                resolver = KeePassCredentialResolver(config.root)
                username = resolver.field_exists(application["credential_profile"], application["credential_entry_path"], "username")
                password = resolver.field_exists(application["credential_profile"], application["credential_entry_path"], "password")
                result(username and password, "Client ID e Client Secret estão disponíveis." if username and password else "Credenciais incompletas.")
            except CredentialError as error: result(False, str(error))
            continue
        elif action == "6":
            references = _mercado_app_references(data, provider, name)
            if references:
                result(False, "Aplicativo ainda referenciado por: " + ", ".join(references))
                continue
            if prompt("Remover este registro de aplicativo? [s/N]: ").strip().casefold() not in {"s", "sim"}: continue
            del data["applications"][provider][name]
            try: config.save_mercado(data); result(True, "Aplicativo removido; a entrada KeePass foi preservada.")
            except McpConfigurationError as error: result(False, str(error)); continue
            return
        else:
            result(False, "Opção inválida."); continue
        try: config.save_mercado(data); result(True, "Aplicativo atualizado. Perfis vinculados exigirão nova autorização se o binding mudou.")
        except McpConfigurationError as error: result(False, str(error))


def mercado_applications_menu(config: McpConfiguration, provider: str) -> None:
    while True:
        data = config.mercado(); catalog = data["applications"][provider]
        screen("MCP · Mercado · Aplicativos", provider)
        item("1.", "Criar aplicativo")
        item("2.", "Gerenciar aplicativo", f"{len(catalog)} configurado(s)")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action == "1": create_mercado_application(config, provider)
        elif action == "2":
            selected = select("Aplicativo", sorted(catalog))
            if selected: manage_mercado_application(config, provider, selected)
        else: result(False, "Opção inválida.")


def _mercado_provider_label(provider: str) -> str:
    return "Mercado Livre" if provider == "mercadolivre" else "Mercado Pago"


def _mercado_missing_applications(data: dict, modules: list[str]) -> list[str]:
    """Identifica provedores selecionados sem aplicativo antes de configurar autorizações."""
    providers = {MODULE_PROVIDER[module] for module in modules}
    return [provider for provider in ("mercadolivre", "mercadopago") if provider in providers and not data["applications"][provider]]


def resolve_mercado_application_dependencies(config: McpConfiguration, modules: list[str]) -> tuple[list[str], dict] | None:
    """Permite corrigir dependências sem perder módulos ou dados já informados do perfil."""
    selected = list(modules)
    while True:
        data = config.mercado()
        if not selected:
            alert("Selecione pelo menos um módulo para criar o perfil Mercado.")
            revised = choose_mercado_modules(selected)
            if revised is None:
                return None
            selected = revised
            continue
        missing = _mercado_missing_applications(data, selected)
        if not missing:
            return selected, data
        provider = missing[0]
        label = _mercado_provider_label(provider)
        required_modules = ", ".join(module for module in selected if MODULE_PROVIDER[module] == provider)
        alert(f"Nenhum aplicativo {label} configurado para: {required_modules}.")
        options = ["Revisar módulos selecionados", f"Cadastrar aplicativo {label} agora"]
        action = flow_choice("Dependência de aplicativo Mercado", options, options[0])
        if action is None:
            return None
        if action == options[0]:
            revised = choose_mercado_modules(selected)
            if revised is not None:
                selected = revised
        else:
            create_mercado_application(config, provider)
            # O cadastro grava seu próprio estado; sempre releia antes de continuar.


def configure_mercado_profile_provider(config: McpConfiguration, data: dict, profile_name: str, provider: str, current: dict | None = None) -> dict | None:
    applications = sorted(data["applications"][provider])
    if not applications:
        result(False, f"Nenhum aplicativo {_mercado_provider_label(provider)} configurado. Cadastre um aplicativo antes de vincular este provedor.")
        return None
    application = select(f"Aplicativo {provider}", applications, (current or {}).get("application", ""))
    if not application:
        return None
    site = ask("Site Mercado", (current or {}).get("site_id", "MLB"), True)
    if not site:
        return None
    mode = "authorization_code"
    if provider == "mercadopago":
        mode = flow_choice("Autenticação Mercado Pago", ["authorization_code", "client_credentials"], (current or {}).get("auth_mode", "authorization_code"))
        if mode is None: return None
    configured = {"application": application, "site_id": site, "auth_mode": mode}
    if mode == "authorization_code":
        vault = select("Perfil de vault da autorização", config.vault_profile_names(), (current or {}).get("authorization_credential_profile", ""))
        if not vault: return None
        label = "MercadoLivre" if provider == "mercadolivre" else "MercadoPago"
        entry = ask("Entrada KeePass da autorização", (current or {}).get("authorization_entry_path", vault_entry_path(config.root, "MCP", label, "Authorizations", profile_name)), True)
        if not entry: return None
        try:
            resolver = KeePassCredentialResolver(config.root)
            if not resolver.entry_exists(vault, entry):
                resolver.save_field(vault, entry, "notes", "{}")
            elif not resolver.field_exists(vault, entry, "notes"):
                resolver.save_field(vault, entry, "notes", "{}")
        except CredentialError as error:
            result(False, str(error)); return None
        configured.update({"authorization_credential_profile": vault, "authorization_entry_path": entry})
    return configured


def create_mercado_profile(config: McpConfiguration) -> None:
    data = config.mercado()
    name = ask_new_profile_name("Nome do perfil Mercado", data["profiles"])
    if not name or name in data["profiles"]:
        result(False, "Nome de perfil inválido, repetido ou criação cancelada."); return
    description = ask("Descrição de uso", required=True)
    if not description: return
    modules = choose_mercado_modules([])
    if modules is None: return
    resolved = resolve_mercado_application_dependencies(config, modules)
    if resolved is None:
        return
    modules, data = resolved
    profile: dict = {"enabled": False, "description": description, "modules": modules}
    for provider in sorted({MODULE_PROVIDER[module] for module in modules}):
        configured = configure_mercado_profile_provider(config, data, name, provider)
        if configured is None: return
        profile[provider] = configured
    enable = prompt("Habilitar o perfil agora? [s/N]: ").strip().casefold() in {"s", "sim"}
    profile["enabled"] = enable
    data["profiles"][name] = profile
    try:
        config.save_mercado(data)
        result(True, "Perfil Mercado salvo. Reconecte clientes que mantenham tools/list em cache.")
    except McpConfigurationError as error: result(False, str(error))


def _mercado_profile_action(config: McpConfiguration, name: str, provider: str, action: str) -> None:
    service = MercadoService(config.root)
    try:
        if action == "status":
            status = service.invoke(name, f"{provider}.get_auth_status", {})
            result(True, f"{provider}: {status['state']}")
        elif action == "authorize":
            authorization = service.invoke(name, f"{provider}.start_authorization", {"confirm": True})
            result(True, "Abra este link no navegador para autorizar:")
            print(authorization["authorizationUrl"])
        elif action == "cancel":
            service.invoke(name, f"{provider}.cancel_authorization", {"confirm": True})
            result(True, "Sessão pendente cancelada; tokens anteriores foram preservados.")
        else:
            service.invoke(name, f"{provider}.get_auth_status", {})
            result(True, "Configuração e estado local consultados. Use uma ferramenta de leitura para validar a permissão remota.")
    except MercadoError as error: result(False, str(error))


def manage_mercado_profile(config: McpConfiguration, name: str) -> None:
    while True:
        data = config.mercado(); profile = data["profiles"].get(name)
        if not isinstance(profile, dict): return
        profile_screen("MCP · Mercado · Perfil", name, profile)
        item("1.", "Alterar descrição")
        item("2.", "Habilitar/desabilitar módulos")
        item("3.", "Configurar Mercado Livre")
        item("4.", "Configurar Mercado Pago")
        item("5.", "Habilitar perfil" if not profile["enabled"] else "Desabilitar perfil")
        item("6.", "Autorizar provedor")
        item("7.", "Consultar autorização")
        item("8.", "Cancelar autorização pendente")
        item("9.", "Testar configuração")
        item("10.", "Remover perfil", "Entradas KeePass serão preservadas")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action == "1":
            value = ask("Descrição", profile["description"], True)
            if not value: continue
            profile["description"] = value
        elif action == "2":
            modules = choose_mercado_modules(profile["modules"])
            if modules is None: continue
            profile["modules"] = modules
            needed = {MODULE_PROVIDER[module] for module in modules}
            for provider in needed:
                if provider not in profile:
                    configured = configure_mercado_profile_provider(config, data, name, provider)
                    if configured is None: break
                    profile[provider] = configured
            else: pass
        elif action in {"3", "4"}:
            provider = "mercadolivre" if action == "3" else "mercadopago"
            configured = configure_mercado_profile_provider(config, data, name, provider, profile.get(provider))
            if configured is None: continue
            profile[provider] = configured
        elif action == "5": profile["enabled"] = not profile["enabled"]
        elif action in {"6", "7", "8", "9"}:
            providers = [provider for provider in ("mercadolivre", "mercadopago") if provider in profile]
            provider = select("Provedor", providers)
            if not provider: continue
            _mercado_profile_action(config, name, provider, {"6": "authorize", "7": "status", "8": "cancel", "9": "test"}[action]); continue
        elif action == "10":
            if prompt("Remover o perfil e preservar as entradas KeePass? [s/N]: ").strip().casefold() not in {"s", "sim"}: continue
            del data["profiles"][name]
            try: config.save_mercado(data); result(True, "Perfil removido; entradas KeePass preservadas.")
            except McpConfigurationError as error: result(False, str(error)); continue
            return
        else: result(False, "Opção inválida."); continue
        try: config.save_mercado(data); result(True, "Perfil atualizado. A lista de ferramentas muda no próximo tools/list; reconecte clientes com cache.")
        except McpConfigurationError as error: result(False, str(error))


def mercado_menu(config: McpConfiguration) -> None:
    while True:
        data = config.mercado()
        screen("MCP · Mercado Livre e Mercado Pago", "Aplicativos compartilhados, perfis e módulos")
        item("1.", "Aplicativos Mercado Livre", f"{len(data['applications']['mercadolivre'])} configurado(s)")
        item("2.", "Aplicativos Mercado Pago", f"{len(data['applications']['mercadopago'])} configurado(s)")
        item("3.", "Criar perfil")
        item("4.", "Gerenciar perfil", f"{len(data['profiles'])} configurado(s)")
        item("X.", "Voltar")
        action = prompt("Opção: ").strip().casefold()
        if action in {"x", "\x1b"}: return
        if action in {"1", "2"}: mercado_applications_menu(config, "mercadolivre" if action == "1" else "mercadopago")
        elif action == "3": create_mercado_profile(config)
        elif action == "4":
            selected = select("Perfil Mercado", sorted(data["profiles"]))
            if selected: manage_mercado_profile(config, selected)
        else: result(False, "Opção inválida.")


def integrations_menu(config: McpConfiguration) -> None:
    """Lista vault e integrações MCP em uma única tela de navegação."""
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
        ssh_profiles = config.ssh()["profiles"]
        bis2cmd_profiles = config.bis2cmd()["profiles"]
        bis10cmd_profiles = config.bis10cmd()["profiles"]
        google_profiles = config.google()["profiles"]
        spotify_profiles = config.spotify()["profiles"]
        memory_enabled = config.memory_tool_available()
        data_tables_enabled = config.data_tables_tool_available()
        mercado_profiles = config.mercado()["profiles"]
        screen("MCP", "Perfis de vault e integrações MCP locais")
        item("V.", "Perfis de vault", "KeePassXC e autenticação local")
        print("\n  INTEGRAÇÕES DISPONÍVEIS")
        integration_item("1.", "Todoist", todoist_profiles)
        integration_item("2.", "Omie", omie_profiles)
        integration_item("3.", "BTG Empresas", btg_profiles)
        integration_item("4.", "EccoVox", eccovox_profiles)
        integration_item("5.", "Cloudflare", cloudflare_profiles)
        integration_item("6.", "Forward Email", forward_email_profiles)
        integration_item("7.", "Notion", notion_profiles)
        integration_item("8.", "AWS", aws_profiles)
        integration_item("9.", "MySQL", mysql_profiles)
        integration_item("10.", "SSH", ssh_profiles)
        integration_item("11.", "BIS2CMD", bis2cmd_profiles)
        integration_item("12.", "BIS10CMD", bis10cmd_profiles)
        integration_item("13.", "Google", google_profiles)
        integration_item("14.", "Spotify", spotify_profiles)
        integration_item("15.", "Memória", {"ativa": {"enabled": True}} if memory_enabled else {})
        integration_item("16.", "Tabelas de dados", {"ativa": {"enabled": True}} if data_tables_enabled else {})
        integration_item("17.", "E-mail", config.email()["accounts"])
        integration_item("18.", "PDF", config.pdf()["profiles"])
        integration_item("19.", "OCR", config.ocr()["profiles"])
        integration_item("20.", "Excel", config.excel()["profiles"])
        integration_item("21.", "Mercado Livre / Mercado Pago", mercado_profiles)
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}:
            return
        if choice == "v":
            vault_profiles_menu(config)
        elif choice == "1":
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
        elif choice == "10":
            ssh_menu(config)
        elif choice == "11":
            bis2cmd_menu(config)
        elif choice == "12":
            bis10cmd_menu(config)
        elif choice == "13":
            google_menu(config)
        elif choice == "14":
            spotify_menu(config)
        elif choice == "15":
            memory_menu(config)
        elif choice == "16":
            data_tables_menu(config)
        elif choice == "17":
            email_menu(config, sys.modules[__name__])
        elif choice in {"18", "19"}:
            document_menu(config, sys.modules[__name__], "pdf" if choice == "18" else "ocr")
        elif choice == "20":
            excel_menu(config, sys.modules[__name__])
        elif choice == "21":
            mercado_menu(config)
        else:
            alert("Opção inválida. Escolha uma das alternativas ou cancele.")


def menu(root: Path) -> int:
    config = McpConfiguration(root)
    try:
        integrations_menu(config)
    except McpConfigurationError as error:
        result(False, str(error))
        return 2
    return 0


if __name__ == "__main__": raise SystemExit(menu(ROOT))
