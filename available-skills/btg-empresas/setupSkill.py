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
FIELDS = (
    Field("environment", "Ambiente sandbox ou production", "sandbox", required=True),
    Field("company_id", "CNPJ com 14 dígitos (sandbox: 30306294000145)", required=True, preserve_text=True),
    Field("account_number", "Número da conta (opcional, filtro para descoberta)", "", preserve_text=True),
    Field("branch_code", "Agência (opcional, filtro para descoberta)", "", preserve_text=True),
    Field("vault_profile", "Perfil KeePass com leitura e escrita", required=True),
    Field("vault_entry_path", "Entrada KeePass dedicada", required=True),
    Field("redirect_uri", "Redirect URI cadastrado no BTG", required=True),
    Field("oauth_mode", "OAuth remote (Gateway/Tunnel) ou manual", "remote"),
    Field("scopes", "Escopos oficiais habilitados", "openid empresas.btgpactual.com/accounts.readonly brn:btg:empresas:banking:payments brn:btg:empresas:banking:direct-debit", required=True),
    Field("max_amount", "Limite local por pagamento em BRL", "1000000.00", preserve_text=True),
    Field("monitor_enabled", "Monitorar perfil quando serviço estiver ativo", False),
)

def load(path: Path) -> dict:
    return simple_load(path, DEFAULTS)

def save(path: Path, data: dict) -> tuple[bool, str]:
    try:
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
    parser.add_argument("--action", choices=["describe", "status", "configure", "profile-connect", "profile-schema", "profile-list", "profile-create", "profile-update", "profile-delete", "profile-test"], default="configure")
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
        code, result = handle(action=args.action, profile_name=args.profile, values=args.set, confirm_delete=args.confirm_delete, path=path, load=load, save=save, fields=FIELDS, test=test)
        print(json.dumps(result, ensure_ascii=False))
        return code
    def interactive_test(name: str, path: Path, data: dict) -> tuple[bool, str]:
        if not load_profile(path, name)["account_id"]:
            after_save(name, path)
        return test(name, path, data)
    interactive_configure(root=args.onmyoji_root, path=path, title="BTG Empresas", subtitle="Ao salvar, conecte ao BTG para descobrir a conta automaticamente", integration="btg-empresas", defaults=DEFAULTS, fields=FIELDS, test=interactive_test, save=save, after_save=after_save)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
