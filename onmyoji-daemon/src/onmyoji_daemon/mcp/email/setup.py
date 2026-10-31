"""Menus de contas de e-mail usando a interface comum do setup MCP."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import tomllib

from .contracts import EmailError
from .oauth import EmailOAuth
from ..runtime import mcp_runtime_python


def run_admin(config, ui, name, action, connection="incoming"):
    """Instala dependências somente no ambiente MCP e herda o terminal do setup."""
    ui.note(ui.ensure_mcp_runtime(config.root))
    source = str(Path(__file__).resolve().parents[3])
    environment = {**os.environ, "PYTHONPATH": source, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    command = [str(mcp_runtime_python(config.root)), "-B", "-m", "onmyoji_daemon.mcp.email.admin", "--onmyoji-root", str(config.root), "--account", name, "--action", action, "--connection", connection]
    completed = subprocess.run(command, env=environment, check=False)
    ui.result(completed.returncode == 0, "Operação de e-mail concluída." if completed.returncode == 0 else "Operação de e-mail não concluída.")


def edit_endpoint(config, ui, current, kind, reuse_auth=None):
    value = copy.deepcopy(current or {})
    protocols = {"IMAP": "imap", "POP3": "pop3"} if kind == "incoming" else {"SMTP": "smtp"}
    selected = ui.flow_choice("Protocolo", list(protocols), next((label for label, code in protocols.items() if code == value.get("protocol")), next(iter(protocols))))
    if not selected: return None
    protocol = protocols[selected]
    host = ui.ask("Servidor", value.get("host", ""), True)
    security = ui.flow_choice("Transporte", ["TLS direto", "STARTTLS obrigatório"], "TLS direto" if value.get("security", "tls") == "tls" else "STARTTLS obrigatório")
    if not host or not security: return None
    security = "tls" if security == "TLS direto" else "starttls"
    default_port = {("imap", "tls"): 993, ("imap", "starttls"): 143, ("pop3", "tls"): 995, ("pop3", "starttls"): 110, ("smtp", "tls"): 465, ("smtp", "starttls"): 587}[protocol, security]
    port = ui.ask("Porta", str(value.get("port", default_port)), True)
    username = ui.ask("Login da caixa (pode diferir do endereço)", value.get("username", ""), True)
    if not port or not username: return None
    if reuse_auth is not None:
        return {"protocol": protocol, "host": host, "port": int(port), "security": security, "username": username, "auth": copy.deepcopy(reuse_auth)}
    default_auth = "OAuth2 Google" if value.get("auth", {}).get("provider") == "google" else "OAuth2 Microsoft" if value.get("auth", {}).get("provider") == "microsoft" else "Senha ou senha de aplicativo"
    mode = ui.flow_choice("Autenticação", ["Senha ou senha de aplicativo", "OAuth2 Google", "OAuth2 Microsoft"], default_auth)
    if not mode: return None
    auth = value.get("auth", {})
    vault = ui.select("Perfil de vault MCP", config.vault_profile_names(), auth.get("credential_profile", ""))
    if not vault: return None
    entry = ui.ask("Entrada KeePass dedicada", auth.get("credential_entry_path", f"Email/{username}/{kind}"), True)
    if not entry: return None
    if mode == "Senha ou senha de aplicativo":
        credential = ui.configure_vault_entry_fields(config, vault, entry, "E-mail", [("password_field", "Senha ou senha de aplicativo", "password")])
        if credential is None: return None
        auth = {"mode": "password", "credential_profile": vault, "credential_entry_path": credential[0], **credential[1]}
    else:
        provider = "google" if mode == "OAuth2 Google" else "microsoft"
        callback = ui.flow_choice("Callback OAuth", ["Local (navegador nesta máquina)", "MiniApps / Tunnel (HTTPS público)"], "Local (navegador nesta máquina)")
        if not callback: return None
        client_type = ui.flow_choice("Tipo do cliente cadastrado no provedor", ["Público / Desktop", "Confidencial / Web"], "Confidencial / Web" if callback == "MiniApps / Tunnel (HTTPS público)" else "Público / Desktop")
        if not client_type: return None
        ui.note("Use entrada dedicada: Client ID em username e Client Secret em password. As notas são reservadas ao estado OAuth.")
        fields = [("client_id_field", "Client ID", "username")]
        if client_type == "Confidencial / Web": fields.append(("client_secret_field", "Client Secret", "password"))
        credential = ui.configure_vault_entry_fields(config, vault, entry, "OAuth de e-mail", fields)
        if credential is None: return None
        if credential[1].get("client_id_field") != "username" or credential[1].get("client_secret_field", "password") != "password":
            raise EmailError("invalid_credential_fields", "Use username para Client ID e password para Client Secret.")
        from ..credentials import KeePassCredentialResolver
        client_id = KeePassCredentialResolver(config.root).resolve(vault, credential[0], "username")
        auth = {"mode": "oauth2", "credential_profile": vault, "credential_entry_path": credential[0], "provider": provider,
                "client_id": client_id, "client_type": "public" if client_type == "Público / Desktop" else "confidential", "client_secret_field": "password", "callback_mode": "local" if callback == "Local (navegador nesta máquina)" else "remote"}
        if provider == "microsoft":
            tenant = ui.ask("Tenant Microsoft", value.get("auth", {}).get("tenant", "common"), True)
            if not tenant: return None
            auth["tenant"] = tenant
        if auth["callback_mode"] == "local":
            callback_port = ui.ask("Porta local do callback", str(value.get("auth", {}).get("callback_port", 8765)), True)
            if not callback_port: return None
            auth["callback_port"] = int(callback_port)
            ui.note(f"Cadastre no provedor: http://127.0.0.1:{callback_port}/oauth/mcp/email/callback")
        else:
            from ..mini_apps.service import MiniAppsMcpService
            try: suggestion = MiniAppsMcpService(config.root).invoke("oauth.callback-url", {"integration": "email"})["callbackUrl"]
            except ValueError: suggestion = value.get("auth", {}).get("redirect_uri", "")
            uri = ui.ask("Callback HTTPS cadastrado no provedor", suggestion, True)
            if not uri: return None
            auth["redirect_uri"] = uri
    return {"protocol": protocol, "host": host, "port": int(port), "security": security, "username": username, "auth": auth}


def edit_roots(config, ui, account):
    try:
        document = tomllib.loads((config.root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8"))
        suggested = str(Path(document["codex"]["project_directory"]) / ".onmyoji" / "email")
    except (OSError, ValueError, KeyError): suggested = ""
    ui.note("Arquivos e anexos ficam no workspace; raízes vazias bloqueiam operações com arquivos.")
    for key, label in (("readable_roots", "Raízes de leitura"), ("writable_roots", "Raízes de escrita")):
        value = ui.ask(label + " separadas por ; (- = nenhuma)", ";".join(account.get(key, [])) or suggested)
        if value is None: return False
        account[key] = [] if value == "-" else [part.strip() for part in value.split(";") if part.strip()]
    return True


def manage(config, ui, name):
    while True:
        data = config.email(); account = data["accounts"].get(name)
        if account is None: return
        ui.screen("MCP · Conta de e-mail", name + " · " + ("ATIVA" if account["enabled"] else "inativa"))
        for key, label in (("1.", "Identidade e descrição"), ("2.", "Recebimento IMAP / POP3"), ("3.", "Envio SMTP"), ("4.", "Pastas e cópia de enviados"), ("5.", "Raízes de anexos e arquivos"), ("6.", "Autorizar OAuth"), ("7.", "Testar conexão"), ("8.", "Desabilitar" if account["enabled"] else "Habilitar"), ("9.", "Remover conta"), ("10.", "Consultar ou cancelar OAuth remoto")):
            ui.item(key, label)
        ui.item("X.", "Voltar"); choice = ui.prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        try:
            if choice == "1":
                for key, label in (("address", "Endereço"), ("display_name", "Nome de exibição"), ("description", "Descrição de quando utilizar esta conta")):
                    value = ui.ask(label, account[key], True)
                    if value is None: break
                    account[key] = value
                else:
                    aliases = ui.ask("Remetentes/aliases adicionais separados por ;", ";".join(account["allowed_senders"]))
                    if aliases is not None: account["allowed_senders"] = [part.strip() for part in aliases.split(";") if part.strip()]
                if value is None or aliases is None: continue
            elif choice in {"2", "3"}:
                kind = "incoming" if choice == "2" else "smtp"
                action = ui.flow_choice("Conexão", ["Configurar", "Remover esta conexão"], "Configurar")
                if not action: continue
                if action == "Remover esta conexão": account.pop(kind, None)
                else:
                    value = edit_endpoint(config, ui, account.get(kind), kind)
                    if value is None: continue
                    account[kind] = value
            elif choice == "4":
                for key, label in (("inbox", "Entrada"), ("sent", "Enviados"), ("drafts", "Rascunhos"), ("trash", "Lixeira"), ("archive", "Arquivo")):
                    value = ui.ask("Pasta " + label + " (- = nenhuma)", account["folders"].get(key, ""))
                    if value is None: break
                    account["folders"][key] = "" if value == "-" else value
                else:
                    options = {"Gerenciada pelo servidor": "server", "Salvar cópia via IMAP APPEND": "append", "Não salvar cópia": "none"}
                    option = ui.flow_choice("Cópia de enviados", list(options), next(label for label, code in options.items() if code == account["sent_copy"]))
                    if option: account["sent_copy"] = options[option]
                if value is None or option is None: continue
            elif choice == "5":
                if not edit_roots(config, ui, account): continue
            elif choice in {"6", "10"}:
                kinds = [kind for kind in ("incoming", "smtp") if account.get(kind, {}).get("auth", {}).get("mode") == "oauth2"]
                kind = ui.select("Conexão OAuth", kinds)
                if not kind: continue
                oauth = EmailOAuth(config.root, account, kind)
                if oauth.auth["callback_mode"] == "local":
                    if choice == "10": ui.note("O consentimento local é concluído no próprio setup."); continue
                    run_admin(config, ui, name, "authorize-local", kind)
                else:
                    from ..mini_apps.service import MiniAppsMcpService
                    service = MiniAppsMcpService(config.root); params = {"integration": "email", "profile": name + "--" + kind, "confirm": True}
                    if choice == "6":
                        ui.note(ui.ensure_mcp_runtime(config.root))
                        result = service.invoke("oauth.start", params)
                        ui.note("Abra o endereço para autorizar: " + result["authorizationUrl"])
                        ui.note("Sessão: " + result["sessionId"])
                    else:
                        session = ui.ask("Identificador da sessão", required=True)
                        action = ui.flow_choice("Autorização remota", ["Consultar", "Cancelar"], "Consultar")
                        if not session or not action: continue
                        result = service.invoke("oauth.status" if action == "Consultar" else "oauth.cancel", {**params, "session_id": session})
                        ui.note(json.dumps(result, ensure_ascii=False))
                continue
            elif choice == "7":
                run_admin(config, ui, name, "test"); continue
            elif choice == "8": account["enabled"] = not account["enabled"]
            elif choice == "9":
                answer = ui.ask("Digite REMOVER para confirmar", required=True)
                if not answer or answer.casefold() != "remover": ui.result(False, "Conta não removida."); continue
                del data["accounts"][name]; config.save_email(data); ui.result(True, "Conta removida. Credenciais e registro de submissões preservados."); return
            else: ui.result(False, "Opção inválida."); continue
            config.save_email(data); ui.result(True, "Conta atualizada.")
        except (ValueError, OSError): ui.result(False, "Não foi possível concluir a operação. Confira configuração, cofre e conexão.")


def email_menu(config, ui):
    while True:
        data = config.email(); names = sorted(data["accounts"])
        ui.screen("MCP · E-mail", "Contas IMAP, POP3 e SMTP")
        ui.item("1.", "Criar conta"); ui.item("2.", "Gerenciar conta", f"{len(names)} configurada(s)"); ui.item("X.", "Voltar")
        choice = ui.prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "2":
            name = ui.select("Conta de e-mail", names)
            if name: manage(config, ui, name)
        elif choice == "1":
            name = ui.ask_new_profile_name("Nome da conta", data["accounts"])
            if not name: continue
            if name in names: ui.result(False, "Nome já utilizado."); continue
            try:
                address = ui.ask("Endereço da caixa", required=True)
                if not address: continue
                account = {"enabled": True, "address": address, "display_name": name, "description": "", "allowed_senders": [], "readable_roots": [], "writable_roots": [], "sent_copy": "server", "folders": {"inbox": "INBOX", "sent": "", "drafts": "", "trash": "", "archive": ""}}
                mode = ui.flow_choice("Conexões", ["Recebimento e envio", "Somente recebimento", "Somente envio"], "Recebimento e envio")
                if not mode: continue
                cancelled = False
                for kind in (["incoming", "smtp"] if mode == "Recebimento e envio" else ["incoming"] if mode == "Somente recebimento" else ["smtp"]):
                    reused = None
                    if kind == "smtp" and account.get("incoming"):
                        reused = ui.flow_choice("Credencial SMTP", ["Reutilizar referência do recebimento", "Configurar credencial distinta"], "Reutilizar referência do recebimento")
                        if reused is None: cancelled = True; break
                    value = edit_endpoint(config, ui, {"username": address}, kind, account["incoming"]["auth"] if reused == "Reutilizar referência do recebimento" else None)
                    if value is None: cancelled = True; break
                    account[kind] = value
                if cancelled: continue
                description = ui.ask("Descrição de quando utilizar esta conta", required=True)
                if not description: continue
                account["description"] = description
                if not edit_roots(config, ui, account): continue
                data["accounts"][name] = account; config.save_email(data); ui.result(True, "Conta criada. Configure pastas e autorize OAuth em Gerenciar conta, quando aplicável.")
            except (ValueError, OSError): ui.result(False, "Conta não criada. Confira parâmetros de conexão e credenciais.")
        else: ui.result(False, "Opção inválida.")
