#!/usr/bin/env python3
"""Configurador genérico de uma instância local do Onmyōji."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANAGED_BEGIN = "# BEGIN ONMYOJI MANAGED SKILLS"
MANAGED_END = "# END ONMYOJI MANAGED SKILLS"


@dataclass(frozen=True)
class Skill:
    identifier: str
    title: str
    script: Path
    description: str


def discover(root: Path = ROOT) -> list[Skill]:
    found: list[Skill] = []
    for script in sorted((root / "skills").glob("*/setupSkill.py")):
        result = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "describe", "--json"], text=True, capture_output=True)
        if result.returncode:
            continue
        try:
            data = json.loads(result.stdout)
            found.append(Skill(data["id"], data["title"], script, data.get("description", "")))
        except (json.JSONDecodeError, KeyError):
            continue
    return found


def config_path(root: Path) -> Path:
    return root / "config.toml"


def enabled_ids(root: Path) -> set[str]:
    path = config_path(root)
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    if MANAGED_BEGIN not in text or MANAGED_END not in text:
        return set()
    block = text.split(MANAGED_BEGIN, 1)[1].split(MANAGED_END, 1)[0]
    return {Path(line.split('"', 2)[1]).name for line in block.splitlines() if line.strip().startswith("path =") and '"' in line}


def write_enabled(root: Path, identifiers: set[str], known: list[Skill]) -> None:
    path = config_path(root)
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    if path.exists():
        shutil.copy2(path, path.with_suffix(".toml.bak"))
    paths = {skill.identifier: skill.script.parent.resolve().as_posix() for skill in known}
    lines = [MANAGED_BEGIN, "# Gerado por setupOnmyoji.py; preserve as demais configurações deste arquivo."]
    for identifier in sorted(identifiers):
        if identifier in paths:
            lines.extend(["[[skills.config]]", f'path = "{paths[identifier]}"', "enabled = true", ""])
    lines.append(MANAGED_END)
    block = "\n".join(lines)
    if MANAGED_BEGIN in old and MANAGED_END in old:
        before, remainder = old.split(MANAGED_BEGIN, 1)
        _, after = remainder.split(MANAGED_END, 1)
        new = before.rstrip() + "\n\n" + block + after
    else:
        new = old.rstrip() + ("\n\n" if old.strip() else "") + block + "\n"
    path.write_text(new, encoding="utf-8", newline="\n")


def invoke(skill: Skill, action: str, root: Path) -> int:
    return subprocess.run([sys.executable, str(skill.script), "--onmyoji-root", str(root), "--action", action]).returncode


def menu(skills: list[Skill], root: Path) -> int:
    while True:
        enabled = enabled_ids(root)
        print("\nOnmyōji — skills de integração")
        for index, skill in enumerate(skills, 1):
            state = "habilitada" if skill.identifier in enabled else "desabilitada"
            print(f"  {index}. {skill.title} ({state})")
        print("  0. Sair")
        choice = input("Selecione uma skill: ").strip()
        if choice == "0":
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(skills):
            print("Opção inválida.")
            continue
        skill = skills[int(choice) - 1]
        print("  1. Habilitar  2. Desabilitar  3. Inicializar perfil  4. Configurar  5. Validar")
        action = input("Ação: ").strip()
        if action == "1":
            enabled.add(skill.identifier); write_enabled(root, enabled, skills)
        elif action == "2":
            enabled.discard(skill.identifier); write_enabled(root, enabled, skills)
        elif action in {"3", "4", "5"}:
            invoke(skill, {"3": "init", "4": "configure", "5": "validate"}[action], root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--action", choices=["menu", "list", "status", "enable", "disable", "setup"], default="menu")
    parser.add_argument("--skill")
    parser.add_argument("--skill-action", default="status")
    args = parser.parse_args()
    skills = discover()
    by_id = {skill.identifier: skill for skill in skills}
    if args.action in {"list", "status"}:
        enabled = enabled_ids(ROOT)
        for skill in skills: print(f"{skill.identifier}\t{'enabled' if skill.identifier in enabled else 'disabled'}\t{skill.description}")
        return 0
    if args.action == "menu": return menu(skills, ROOT)
    if not args.skill or args.skill not in by_id:
        parser.error("--skill deve identificar uma skill descoberta")
    enabled = enabled_ids(ROOT)
    if args.action == "enable": enabled.add(args.skill); write_enabled(ROOT, enabled, skills); return 0
    if args.action == "disable": enabled.discard(args.skill); write_enabled(ROOT, enabled, skills); return 0
    return invoke(by_id[args.skill], args.skill_action, ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
