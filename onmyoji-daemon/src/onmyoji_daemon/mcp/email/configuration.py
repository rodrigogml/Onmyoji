"""Contrato portátil de contas sem credenciais em texto claro."""
from __future__ import annotations

import json
import re
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from .contracts import identifier, require, text


DEFAULTS = {"timeout_seconds": 30, "page_size": 50, "max_message_bytes": 52428800,
            "max_attachment_bytes": 26214400, "max_body_characters": 65536, "pop_scan_limit": 200}


def empty_configuration() -> dict:
    return {"schema_version": 1, "defaults": dict(DEFAULTS), "accounts": {}}


def validate_configuration(data: dict) -> None:
    require(data.get("schema_version") == 1 and isinstance(data.get("accounts"), dict), "invalid_config", "Configuração de e-mail inválida.")
    limits = data.get("defaults", {})
    maximums = {"timeout_seconds": 300, "page_size": 100, "max_message_bytes": 209715200, "max_attachment_bytes": 104857600, "max_body_characters": 1048576, "pop_scan_limit": 1000}
    for key in DEFAULTS:
        require(type(limits.get(key)) is int and 1 <= limits[key] <= maximums[key], "invalid_config", f"Limite {key} inválido.")
    for name, account in data["accounts"].items():
        identifier(name)
        require(isinstance(account, dict) and type(account.get("enabled")) is bool, "invalid_config", "Estado de conta inválido.")
        require(set(account) <= {"enabled", "description", "address", "display_name", "allowed_senders", "readable_roots", "writable_roots", "sent_copy", "folders", "incoming", "smtp"}, "invalid_config", "Campos desconhecidos na conta de e-mail.")
        text(account.get("description"), "Descrição", 240)
        address = text(account.get("address"), "Endereço", 254)
        require(re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", address) is not None, "invalid_config", "Endereço de e-mail inválido.")
        text(account.get("display_name", "Conta"), "Nome de exibição", 200)
        for key in ("readable_roots", "writable_roots", "allowed_senders"):
            require(isinstance(account.get(key), list) and all(isinstance(v, str) and v.strip() for v in account[key]), "invalid_config", f"{key} inválido.")
        for sender in account["allowed_senders"]:
            require(re.fullmatch(r"[^\s<>@]+@[^\s<>@]+", sender) is not None, "invalid_config", "Alias de remetente inválido.")
        require(bool(account.get("incoming") or account.get("smtp")), "invalid_config", "Configure recebimento ou SMTP.")
        for kind in ("incoming", "smtp"):
            endpoint = account.get(kind)
            if not endpoint:
                continue
            require(isinstance(endpoint, dict), "invalid_config", "Conexão inválida.")
            require(set(endpoint) <= {"protocol", "host", "port", "security", "username", "auth"}, "invalid_config", "Campos desconhecidos na conexão.")
            require(endpoint.get("protocol") in ({"imap", "pop3"} if kind == "incoming" else {"smtp"}), "invalid_config", "Protocolo inválido.")
            host = text(endpoint.get("host"), "Servidor", 253)
            require(not any(c in host for c in "/\\@?# ") and type(endpoint.get("port")) is int and 1 <= endpoint["port"] <= 65535, "invalid_config", "Servidor ou porta inválidos.")
            require(endpoint.get("security") in {"tls", "starttls"}, "invalid_config", "Use TLS ou STARTTLS obrigatório.")
            text(endpoint.get("username"), "Login", 254)
            auth = endpoint.get("auth", {})
            require(isinstance(auth, dict) and set(auth) <= {"mode", "credential_profile", "credential_entry_path", "password_field", "provider", "callback_mode", "client_id", "client_type", "client_secret_field", "tenant", "callback_port", "redirect_uri"}, "invalid_config", "Credenciais devem ser referências do vault.")
            require(auth.get("mode") in {"password", "oauth2"}, "invalid_config", "Autenticação inválida.")
            for field in ("credential_profile", "credential_entry_path"):
                text(auth.get(field), field)
            if auth["mode"] == "password":
                require(auth.get("password_field", "password") in {"username", "password", "url", "notes"}, "invalid_config", "Campo de senha inválido.")
            else:
                require(auth.get("provider") in {"google", "microsoft"}, "invalid_config", "Provedor OAuth inválido.")
                require(auth.get("client_type", "confidential") in {"public", "confidential"}, "invalid_config", "Tipo de cliente OAuth inválido.")
                require(auth.get("callback_mode") in {"local", "remote"}, "invalid_config", "Callback inválido.")
                text(auth.get("client_id"), "Client ID")
                require(auth.get("client_secret_field", "password") == "password", "invalid_config", "Client Secret deve usar password.")
                require(re.fullmatch(r"[A-Za-z0-9._-]+", auth.get("tenant", "common")) is not None, "invalid_config", "Tenant inválido.")
                require(type(auth.get("callback_port", 8765)) is int and 1024 <= auth.get("callback_port", 8765) <= 65535, "invalid_config", "Porta de callback inválida.")
                if auth["callback_mode"] == "remote":
                    uri = urlsplit(auth.get("redirect_uri", ""))
                    require(uri.scheme == "https" and bool(uri.hostname) and not uri.username and not uri.password and not uri.query and not uri.fragment and uri.path == "/oauth/mcp/email/callback", "invalid_config", "Callback público de e-mail inválido.")
        require(isinstance(account.get("folders"), dict) and account.get("sent_copy", "server") in {"server", "append", "none"}, "invalid_config", "Pastas ou cópia de enviados inválidas.")
        require(set(account["folders"]) <= {"inbox", "sent", "drafts", "trash", "archive"}, "invalid_config", "Pasta especial desconhecida.")
        for folder in account["folders"].values():
            if folder:
                text(folder, "Pasta")
        if account.get("sent_copy") == "append":
            require(account.get("incoming", {}).get("protocol") == "imap" and bool(account["folders"].get("sent")), "invalid_config", "Cópia APPEND exige IMAP e pasta de enviados.")


def validate_roots(root: Path, document: dict) -> None:
    roots = [configured for account in document["accounts"].values() for key in ("readable_roots", "writable_roots") for configured in account[key]]
    if not roots: return
    try:
        system = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        workspace = Path(system["codex"]["project_directory"]).expanduser()
        if not workspace.is_absolute(): workspace = root / workspace
        workspace = workspace.resolve()
    except (OSError, ValueError, KeyError) as error:
        from .contracts import EmailError
        raise EmailError("workspace_required", "Configure o workspace antes de autorizar raízes de e-mail.") from error
    require(not workspace.is_relative_to(root.resolve()), "invalid_workspace", "Área de e-mail exige workspace externo ao CODEX_HOME.")
    for configured in roots:
        path = Path(configured).expanduser()
        require(path.is_absolute() and path.resolve().is_relative_to(workspace), "path_not_allowed", "Raízes de e-mail devem ficar no workspace configurado.")


def render_configuration(data: dict) -> str:
    lines = ["schema_version = 1", "", "[defaults]"]
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    for key, value in data["defaults"].items():
        lines.append(f"{key} = {value}")
    for name, account in sorted(data["accounts"].items()):
        prefix = "accounts." + name
        lines.extend(("", f"[{prefix}]"))
        for key in ("enabled", "description", "address", "display_name", "allowed_senders", "readable_roots", "writable_roots", "sent_copy"):
            lines.append(f"{key} = {quote(account[key])}")
        lines.extend(("", f"[{prefix}.folders]"))
        lines.extend(f"{key} = {quote(value)}" for key, value in account["folders"].items())
        for kind in ("incoming", "smtp"):
            if not account.get(kind):
                continue
            lines.extend(("", f"[{prefix}.{kind}]"))
            lines.extend(f"{key} = {quote(value)}" for key, value in account[kind].items() if key != "auth")
            lines.extend(("", f"[{prefix}.{kind}.auth]"))
            lines.extend(f"{key} = {quote(value)}" for key, value in account[kind]["auth"].items())
    return "\n".join(lines) + "\n"
