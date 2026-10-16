#!/usr/bin/env python3
"""Setup BTG Empresas com descoberta autenticada da conta e teste de leitura."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from setup_profile_api import Field, handle, interactive_configure, simple_load, wrapper_test
from client import Client, load_profile, validate_profile
from contracts import BtgError
from account_setup import save_configuration
from setup_ui import prompt, result

DEFAULTS = {"timeout_seconds": 30}
REQUIRED_SCOPES = (
    {"value": "openid", "purpose": "Identidade OAuth e emissão do ID token."},
    {"value": "empresas.btgpactual.com/accounts.readonly", "purpose": "Consulta das contas PJ e descoberta do account_id."},
    {"value": "brn:btg:empresas:banking:payments", "purpose": "Preparação, autorização, consulta e comprovante de pagamentos."},
)
OPTIONAL_SCOPES = (
    {"value": "brn:btg:empresas:banking:direct-debit", "purpose": "Consulta e operação de Débito Direto Autorizado (DDA), quando habilitado no BTG."},
)
DEFAULT_SCOPES = " ".join(item["value"] for item in REQUIRED_SCOPES + OPTIONAL_SCOPES)
FIELDS = (
    Field("environment", "Ambiente sandbox ou production", "sandbox", required=True),
    Field("company_id", "CNPJ com 14 dígitos (sandbox: 30306294000145)", required=True, preserve_text=True),
    Field("account_number", "Número da conta (opcional, filtro para descoberta)", "", preserve_text=True),
    Field("branch_code", "Agência (opcional, filtro para descoberta)", "", preserve_text=True),
    Field("vault_profile", "Perfil KeePass com leitura e escrita", required=True),
    Field("vault_entry_path", "Entrada KeePass dedicada", required=True),
    # Mantido apenas para compatibilidade com perfis/CLI antigos; o menu não o solicita.
    Field("redirect_uri", "Redirect URI calculado pelo Tunnel", ""),
    Field("oauth_mode", "OAuth remote (Gateway/Tunnel) ou manual", "remote"),
    # Compatibilidade para perfis/CLI antigos; o menu usa os escopos oficiais fixos.
    Field("scopes", "Escopos oficiais definidos pela skill", DEFAULT_SCOPES),
    Field("max_amount", "Limite local por pagamento em BRL", "1000000.00", preserve_text=True),
    Field("monitor_enabled", "Monitorar perfil quando serviço estiver ativo", False),
)
INTERACTIVE_FIELDS = tuple(field for field in FIELDS if field.name not in {"redirect_uri", "scopes"})

def load(path: Path) -> dict:
    return simple_load(path, DEFAULTS)


def callback_info(root: Path) -> dict:
    """Obtenha do daemon a URI pública derivada do Tunnel e da rota da skill."""
    source = root / "onmyoji-daemon" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    try:
        from onmyoji_daemon.rpc import call
        from onmyoji_daemon.supervisor import endpoint
        host, port, token = endpoint(root)
        return call(host, port, token, "mini-apps.oauth.callback-url", {"skill": "btg-empresas"}, timeout=5)
    except (OSError, ValueError, KeyError, ImportError, RuntimeError) as error:
        raise BtgError("callback_unavailable", "Não foi possível calcular a URI: inicie o Gateway e o Tunnel provisionado.") from error


def application_info() -> dict:
    """Descreva as chaves que devem ser cadastradas no aplicativo BTG."""
    return {"requiredScopes": list(REQUIRED_SCOPES), "optionalScopes": list(OPTIONAL_SCOPES)}

def save(path: Path, data: dict) -> tuple[bool, str]:
    try:
        existing = load(path).get("profiles", {}) if path.exists() else {}
        legacy_redirect = next((value.get("redirect_uri") for value in existing.values() if isinstance(value, dict) and value.get("redirect_uri")), None)
        for profile in data.get("profiles", {}).values():
            if profile.get("oauth_mode", "remote") == "remote" and not profile.get("redirect_uri"):
                # Preserve a known URI for offline profile duplication; new
                # installations calculate it from the running Tunnel below.
                profile["redirect_uri"] = legacy_redirect or callback_info(path.parent.parent)["callbackUrl"]
            if not profile.get("scopes"):
                profile["scopes"] = DEFAULT_SCOPES
        return save_configuration(path, data)
    except (BtgError, OSError, ValueError, TypeError, KeyError) as error:
        return False, str(error)


def connect(name: str, path: Path, *, confirm: bool) -> dict:
    """Inicie consentimento remoto; o callback consulta e salva a conta no perfil."""
    client = Client(load_profile(path, name))
    if client.profile["oauth_mode"] != "remote":
        raise BtgError("remote_mode_required", "Use oauth_mode=remote para conectar pelo setup.")
    client.client_id()
    return client.remote_oauth("start", confirm=confirm)


def clear_vault_pending(name: str, path: Path) -> bool:
    """Cancele o estado OAuth pendente persistido no KeePass."""
    client = Client(load_profile(path, name))
    pending = client.auth_data().get("pending")
    if not isinstance(pending, dict) or not pending.get("sessionId"):
        return False
    client.auth_cancel(str(pending["sessionId"]))
    return True


def after_save(name: str, path: Path) -> None:
    """Ofereça conexão após salvar; aguarde o navegador sem ler código ou token."""
    try:
        profile = load_profile(path, name)
        if profile["account_id"]:
            return
        answer = prompt("Conectar ao BTG para descobrir e salvar a conta agora? [S/n]: ").strip().casefold()
        if answer not in {"", "s", "sim"}:
            result(False, "Perfil salvo, mas conta pendente. Use Testar perfil para retomar a conexão.")
            return
        try:
            session = connect(name, path, confirm=True)
        except BtgError as error:
            if error.code == "remote_oauth_rejected" and "vault_failed" in str(error):
                retry = prompt("O KeePass recusou temporariamente a operação. Tentar novamente? [S/n]: ").strip().casefold()
                if retry in {"", "s", "sim"}:
                    time.sleep(2)
                    session = connect(name, path, confirm=True)
                else:
                    result(False, "Operação KeePass não concluída; nenhuma autorização foi iniciada.")
                    return
            elif error.code == "remote_oauth_rejected" and "authorization_pending" in str(error):
                retry = prompt("Há uma autorização pendente salva no KeePass. Limpar e iniciar uma nova? [S/n]: ").strip().casefold()
                if retry not in {"", "s", "sim"}:
                    result(False, "Autorização pendente do KeePass preservada.")
                    return
                clear_vault_pending(name, path)
                session = connect(name, path, confirm=True)
            else:
                if error.code != "remote_oauth_rejected" or "autorização ativa" not in str(error).casefold():
                    raise
                replace = prompt("Já existe uma autorização pendente para este perfil. Substituir e gerar um novo link? [S/n]: ").strip().casefold()
                if replace not in {"", "s", "sim"}:
                    result(False, "Autorização pendente preservada. Use Testar perfil para continuar.")
                    return
                client = Client(load_profile(path, name))
                client.remote_oauth("cancel-active", confirm=True)
                session = connect(name, path, confirm=True)
        print("Abra este link no seu navegador (ou encaminhe-o pelo chat):", flush=True)
        print(session["authorizationUrl"], flush=True)
        client = Client(profile)
        deadline = time.monotonic() + session["expiresIn"] + 30
        while time.monotonic() < deadline:
            state = client.remote_oauth("status", session_id=session["sessionId"])
            if state["status"] == "completed":
                updated = load_profile(path, name)
                result(bool(updated["account_id"]), "Conta consultada e salva: " + updated["account_id"])
                return
            if state["status"] not in {"creating", "awaiting", "processing"}:
                result(False, "Conexão não concluída: " + state["status"] + ". Perfil preservado; use Testar perfil para tentar novamente.")
                return
            time.sleep(2)
        result(False, "Tempo de espera encerrado. Confira o consentimento antes de iniciar outra conexão.")
    except KeyboardInterrupt:
        print("\nEspera interrompida; o link continua válido até expirar. Confira o resultado antes de iniciar outra conexão.")
    except (BtgError, OSError, ValueError, KeyError) as error:
        result(False, str(error))

def test(name: str, path: Path, data: dict) -> tuple[bool, str]:
    try:
        load_profile(path, name)
    except BtgError as error:
        return False, str(error)
    return wrapper_test(Path(__file__).parent / "scripts" / "btg.py", path, name, {"version": 1, "operation": "accounts.get"}, "BTG Empresas", timeout=150)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, default=ROOT)
    parser.add_argument("--action", choices=["describe", "status", "configure", "callback-info", "application-info", "profile-connect", "profile-schema", "profile-list", "profile-create", "profile-update", "profile-delete", "profile-test"], default="configure")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--set", action="append")
    parser.add_argument("--confirm-delete")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    path = args.onmyoji_root / "configs" / "btg-empresas.toml"
    if args.action == "describe":
        print(json.dumps({"id": "btg-empresas", "title": "BTG Empresas", "description": "Conta PJ, pagamentos individuais e memória rastreável; OAuth no KeePass."}, ensure_ascii=False))
        return 0
    if args.action == "callback-info":
        try:
            data = callback_info(args.onmyoji_root)
            data["instructions"] = "Cadastre exatamente callbackUrl no aplicativo BTG antes de concluir o OAuth."
            print(json.dumps({"ok": True, "data": data}, ensure_ascii=False))
            return 0
        except BtgError as error:
            print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}, ensure_ascii=False))
            return 2
    if args.action == "application-info":
        print(json.dumps({"ok": True, "data": application_info()}, ensure_ascii=False))
        return 0
    if args.action == "status":
        try:
            data = load(path)
            errors = []
            pending = []
            for name in data["profiles"]:
                try:
                    if not load_profile(path, name)["account_id"]:
                        pending.append(name)
                except BtgError as error:
                    errors.append({"profile": name, "code": error.code})
            print(json.dumps({"configured": bool(data["profiles"]), "valid": not errors, "ready": bool(data["profiles"]) and not errors and not pending, "pending_accounts": pending, "errors": errors}))
            return 2 if errors else 0
        except (ValueError, OSError):
            print(json.dumps({"configured": False, "valid": False}))
            return 2
    if args.action == "profile-connect":
        try:
            if not args.confirm:
                raise BtgError("confirmation_required", "Confirme o consentimento com --confirm.")
            print(json.dumps({"ok": True, "data": connect(args.profile, path, confirm=True)}, ensure_ascii=False))
            return 0
        except (BtgError, OSError, ValueError) as error:
            print(json.dumps({"ok": False, "error": {"code": getattr(error, "code", "invalid_config"), "message": str(error)}}, ensure_ascii=False))
            return 2
    if args.action.startswith("profile-"):
        code, response = handle(action=args.action, profile_name=args.profile, values=args.set, confirm_delete=args.confirm_delete, path=path, load=load, save=save, fields=FIELDS, test=test)
        print(json.dumps(response, ensure_ascii=False))
        return code
    def interactive_test(name: str, path: Path, data: dict) -> tuple[bool, str]:
        if not load_profile(path, name)["account_id"]:
            after_save(name, path)
        return test(name, path, data)
    try:
        callback = callback_info(args.onmyoji_root)["callbackUrl"]
        app = application_info()
        print("Configure o aplicativo BTG com estas chaves OAuth obrigatórias:", flush=True)
        for item in app["requiredScopes"]:
            print(f"  - {item['value']}: {item['purpose']}", flush=True)
        print("Chaves OAuth opcionais:", flush=True)
        for item in app["optionalScopes"]:
            print(f"  - {item['value']}: {item['purpose']}", flush=True)
        print("Cadastre esta URI de redirect no aplicativo BTG (calculada pelo Tunnel):", callback, flush=True)
    except BtgError as error:
        result(False, str(error))
        return 2
    interactive_configure(root=args.onmyoji_root, path=path, title="BTG Empresas", subtitle="A URI de redirect é calculada automaticamente; ao salvar, conecte ao BTG para descobrir a conta", integration="btg-empresas", defaults=DEFAULTS, fields=INTERACTIVE_FIELDS, test=interactive_test, save=save, after_save=after_save)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
