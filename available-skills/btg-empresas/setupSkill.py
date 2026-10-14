#!/usr/bin/env python3
"""Setup BTG Empresas; consentimento separado e teste somente leitura."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
from setup_profile_api import Field, handle, interactive_configure, simple_load, simple_save, wrapper_test
from client import load_profile, validate_profile
from contracts import BtgError

DEFAULTS = {"timeout_seconds": 30}
FIELDS = (
    Field("environment", "Ambiente sandbox ou production", "sandbox", required=True),
    Field("company_id", "CNPJ com 14 dígitos", required=True),
    Field("account_id", "ID da conta BTG", required=True),
    Field("account_number", "Número da conta", required=True),
    Field("branch_code", "Agência", "50", required=True),
    Field("workspace", "Workspace externo absoluto", required=True),
    Field("vault_profile", "Perfil KeePass com leitura e escrita", required=True),
    Field("vault_entry_path", "Entrada KeePass dedicada", required=True),
    Field("client_id", "Client ID do aplicativo", required=True),
    Field("redirect_uri", "Redirect URI cadastrado no BTG", required=True),
    Field("scopes", "Escopos oficiais habilitados", "openid empresas.btgpactual.com/accounts.readonly brn:btg:empresas:banking:payments brn:btg:empresas:banking:direct-debit", required=True),
    Field("max_amount", "Limite local por pagamento em BRL", "1000000.00"),
    Field("monitor_enabled", "Monitorar perfil quando serviço estiver ativo", False),
)

def load(path: Path) -> dict:
    return simple_load(path, DEFAULTS)

def save(path: Path, data: dict) -> tuple[bool, str]:
    try:
        for name, value in data["profiles"].items():
            validate_profile(path, name, value)
    except (BtgError, ValueError, TypeError, KeyError) as error:
        return False, str(error)
    return simple_save(path, data)

def test(name: str, path: Path, data: dict) -> tuple[bool, str]:
    try:
        load_profile(path, name)
    except BtgError as error:
        return False, str(error)
    return wrapper_test(Path(__file__).parent / "scripts" / "btg.py", path, name, {"version": 1, "operation": "accounts.get"}, "BTG Empresas", timeout=150)

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, default=ROOT)
    parser.add_argument("--action", choices=["describe", "status", "configure", "profile-schema", "profile-list", "profile-create", "profile-update", "profile-delete", "profile-test"], default="configure")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--profile")
    parser.add_argument("--set", action="append")
    parser.add_argument("--confirm-delete")
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
            for name in data["profiles"]:
                try:
                    load_profile(path, name)
                except BtgError as error:
                    errors.append({"profile": name, "code": error.code})
            print(json.dumps({"configured": bool(data["profiles"]), "valid": not errors, "errors": errors}))
            return 2 if errors else 0
        except (ValueError, OSError):
            print(json.dumps({"configured": False, "valid": False}))
            return 2
    if args.action.startswith("profile-"):
        code, result = handle(action=args.action, profile_name=args.profile, values=args.set, confirm_delete=args.confirm_delete, path=path, load=load, save=save, fields=FIELDS, test=test)
        print(json.dumps(result, ensure_ascii=False))
        return code
    interactive_configure(root=args.onmyoji_root, path=path, title="BTG Empresas", subtitle="Configure e obtenha consentimento OAuth pelo wrapper", integration="btg-empresas", defaults=DEFAULTS, fields=FIELDS, test=test)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
