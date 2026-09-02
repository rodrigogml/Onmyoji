#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL.parent))

from setup_profile_api import Field, handle as handle_profile, interactive_configure, recover_simple_load, simple_load, simple_save, wrapper_test
from setup_ui import suggested_vault_entry


DEFAULTS = {"java_path": "java", "timeout_seconds": 180, "encoding": "utf-8", "locale": "pt-BR"}


def vault_entry(kind: str):
    return lambda profile: suggested_vault_entry(f"BIS10CMD:{kind}", profile)


PROFILE_FIELDS = (
    Field("jar_path", "Arquivo JAR", "C:/opt/BIS10CMD/BISCMD-10.0.jar", True),
    Field("working_dir", "Diretório de trabalho", "C:/opt/BIS10CMD", True),
    Field("java_path", "Executável Java", "java", True),
    Field("host", "Host", "127.0.0.1", True),
    Field("port", "Porta", 8080, True),
    Field("locale", "Locale", "pt-BR", True),
    Field("timeout_seconds", "Timeout (segundos)", 180, True),
    Field("encoding", "Codificação", "utf-8", True),
    Field("jndi_vault_profile", "Perfil KeePass JNDI", required=True),
    Field("jndi_vault_entry_path", "Entrada KeePass JNDI", required=True, suggest=vault_entry("JNDI")),
    Field("jndi_username_field", "Campo de usuário JNDI", "username", True),
    Field("jndi_password_field", "Campo de senha JNDI", "password", True),
    Field("bis_vault_profile", "Perfil KeePass BIS10", required=True),
    Field("bis_vault_entry_path", "Entrada KeePass BIS10", required=True, suggest=vault_entry("BIS10")),
    Field("bis_username_field", "Campo de usuário BIS10", "username", True),
    Field("bis_password_field", "Campo de senha BIS10", "password", True),
)


def config_path(root: Path) -> Path:
    return root / "configs" / "bis10cmd.toml"


def profile_test(name: str, path: Path, data: dict) -> tuple[bool, str]:
    return wrapper_test(SKILL / "scripts" / "bis10cmd.py", path, name, {"version": 1, "commands": [{"name": "connect", "args": []}, {"name": "ping", "args": []}]}, "BIS10CMD", timeout=210, include_error_details=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", default="configure")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--onmyoji-root", type=Path)
    parser.add_argument("--profile")
    parser.add_argument("--set", action="append")
    parser.add_argument("--confirm-delete")
    args = parser.parse_args()
    root = args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1]
    info = {"id": "bis10cmd", "title": "BIS10CMD", "description": "Cliente BIS10 via Java, com credenciais JNDI e BIS10 no KeePass Vault."}
    if args.action == "describe":
        print(json.dumps(info, ensure_ascii=False) if args.json else info["title"])
        return 0
    if args.action == "status":
        print(json.dumps({"configured": config_path(root).exists(), "valid": True}, ensure_ascii=False))
        return 0
    if args.action == "repair":
        try:
            data = recover_simple_load(config_path(root), DEFAULTS)
            ok, message = simple_save(config_path(root), data)
            response = {"ok": ok, "message": message}
        except (OSError, tomllib.TOMLDecodeError, ValueError) as error:
            response = {"ok": False, "error": {"code": "repair_failed", "message": f"Não foi possível recuperar a configuração: {error}"}}
        print(json.dumps(response, ensure_ascii=False))
        return 0 if response["ok"] else 2
    if args.action.startswith("profile-"):
        code, value = handle_profile(action=args.action, profile_name=args.profile, values=args.set, confirm_delete=args.confirm_delete, path=config_path(root), load=lambda path: simple_load(path, DEFAULTS), save=simple_save, fields=PROFILE_FIELDS, test=profile_test)
        print(json.dumps(value, ensure_ascii=False))
        return code
    interactive_configure(root=root, path=config_path(root), title="BIS10CMD", subtitle="Perfis de acesso ao cliente BIS10", integration="BIS10CMD", defaults=DEFAULTS, fields=PROFILE_FIELDS, test=profile_test, load=recover_simple_load)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
