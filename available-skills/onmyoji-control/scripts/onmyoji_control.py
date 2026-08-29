#!/usr/bin/env python3
"""Cliente seguro das ações não interativas do setup Onmyōji."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8", errors="replace")


ROOT = Path(__file__).resolve().parents[3]


def call(arguments: list[str]) -> dict:
    process = subprocess.run([sys.executable, str(ROOT / "setupOnmyoji.py"), *arguments, "--json"], cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    try: value = json.loads(process.stdout)
    except json.JSONDecodeError: value = {"ok": False, "error": {"code": "setup_protocol_error", "message": "O setup não retornou JSON válido."}}
    if process.returncode and value.get("ok", False): value = {"ok": False, "error": {"code": "setup_failed", "message": "A ação do setup falhou."}}
    return value


def profile_call(skill: str, action: str, arguments: list[str]) -> dict:
    script = ROOT / "available-skills" / skill / "setupSkill.py"
    if not script.is_file(): return {"ok": False, "error": {"code": "unknown_skill", "message": "Skill não encontrada."}}
    try: process = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(ROOT), "--action", action, *arguments, "--json"], cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    except subprocess.TimeoutExpired: return {"ok": False, "error": {"code": "setup_timeout", "message": "A ação de configuração excedeu 60 segundos."}}
    try:
        value = json.loads(process.stdout)
    except json.JSONDecodeError: return {"ok": False, "error": {"code": "setup_protocol_error", "message": "A skill não retornou JSON válido."}}
    if action == "status": return {"ok": bool(value.get("valid", False)), "skill": skill, "profile_status": value}
    if process.returncode and value.get("ok", False): return {"ok": False, "error": {"code": "setup_failed", "message": "A ação de perfil falhou."}}
    return {"skill": skill, **value}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("area", choices=["skills", "profiles"])
    parser.add_argument("action")
    parser.add_argument("skill", nargs="?")
    parser.add_argument("--profile")
    parser.add_argument("--vault-profile")
    parser.add_argument("--vault-entry")
    parser.add_argument("--vault-field", choices=["password", "notes"])
    parser.add_argument("--access", choices=["read_only", "read_write"])
    parser.add_argument("--operations")
    parser.add_argument("--attachment-roots")
    parser.add_argument("--confirm-delete")
    args = parser.parse_args()
    if args.area == "skills":
        if args.action not in {"list", "status", "enable", "disable"}: parser.error("skills aceita: list, status, enable ou disable")
        if args.action in {"enable", "disable"} and not args.skill: parser.error("skill é obrigatória para habilitar ou desabilitar")
        result = call(["--action", args.action, *(["--skill", args.skill] if args.skill else [])])
    else:
        actions = {"status": "status", "list": "profile-list", "create": "profile-create", "update": "profile-update", "delete": "profile-delete", "test": "profile-test"}
        if args.action not in actions or not args.skill: parser.error("profiles aceita: status, list, create, update, delete ou test; informe a skill")
        command = []
        for flag, value in (("--profile", args.profile), ("--vault-profile", args.vault_profile), ("--vault-entry", args.vault_entry), ("--vault-field", args.vault_field), ("--access", args.access), ("--operations", args.operations), ("--attachment-roots", args.attachment_roots), ("--confirm-delete", args.confirm_delete)):
            if value is not None: command += [flag, value]
        result = profile_call(args.skill, actions[args.action], command)
    print(json.dumps(result, ensure_ascii=False)); return 0 if result.get("ok", False) else 2


if __name__ == "__main__": raise SystemExit(main())
