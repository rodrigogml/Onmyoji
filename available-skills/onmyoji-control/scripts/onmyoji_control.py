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


def profile_status(skill: str) -> dict:
    script = ROOT / "available-skills" / skill / "setupSkill.py"
    if not script.is_file(): return {"ok": False, "error": {"code": "unknown_skill", "message": "Skill não encontrada."}}
    process = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(ROOT), "--action", "status", "--json"], cwd=ROOT, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=60)
    try:
        value = json.loads(process.stdout)
        return {"ok": bool(value.get("valid", False)), "skill": skill, "profile_status": value}
    except json.JSONDecodeError: return {"ok": False, "error": {"code": "unsupported_action", "message": "Esta skill ainda não declara status não interativo."}}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("area", choices=["skills", "profiles"]); parser.add_argument("action", choices=["list", "status", "enable", "disable"]); parser.add_argument("skill", nargs="?"); args = parser.parse_args()
    if args.area == "skills":
        if args.action in {"enable", "disable"} and not args.skill: parser.error("skill é obrigatória para habilitar ou desabilitar")
        result = call(["--action", args.action, *( ["--skill", args.skill] if args.skill else [])])
    else:
        if args.action != "status" or not args.skill: parser.error("profiles aceita somente: profiles status <skill>")
        result = profile_status(args.skill)
    print(json.dumps(result, ensure_ascii=False)); return 0 if result.get("ok", False) else 2


if __name__ == "__main__": raise SystemExit(main())
