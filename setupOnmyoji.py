#!/usr/bin/env python3
"""Menu principal de configuração de uma instância Onmyōji."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
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
    skills: list[Skill] = []
    for script in sorted((root / "skills").glob("*/setupSkill.py")):
        result = subprocess.run([sys.executable, str(script), "--onmyoji-root", str(root), "--action", "describe", "--json"], text=True, capture_output=True)
        try:
            data = json.loads(result.stdout) if result.returncode == 0 else {}
            skills.append(Skill(data["id"], data["title"], script, data.get("description", "")))
        except (json.JSONDecodeError, KeyError):
            continue
    return skills


def config_path(root: Path) -> Path: return root / "config.toml"


def enabled_ids(root: Path) -> set[str]:
    path = config_path(root)
    if not path.exists(): return set()
    text = path.read_text(encoding="utf-8")
    if MANAGED_BEGIN not in text or MANAGED_END not in text: return set()
    block = text.split(MANAGED_BEGIN, 1)[1].split(MANAGED_END, 1)[0]
    return {Path(line.split('"', 2)[1]).name for line in block.splitlines() if line.strip().startswith("path =") and '"' in line}


def render_enabled(old: str, identifiers: set[str], skills: list[Skill]) -> str:
    locations = {skill.identifier: skill.script.parent.resolve().as_posix() for skill in skills}
    lines = [MANAGED_BEGIN, "# Gerado por setupOnmyoji.py; preserve as demais configurações deste arquivo."]
    for identifier in sorted(identifiers):
        if identifier in locations: lines.extend(["[[skills.config]]", f'path = "{locations[identifier]}"', "enabled = true", ""])
    lines.append(MANAGED_END)
    block = "\n".join(lines)
    if MANAGED_BEGIN in old and MANAGED_END in old:
        before, remainder = old.split(MANAGED_BEGIN, 1); _, after = remainder.split(MANAGED_END, 1)
        return before.rstrip() + "\n\n" + block + after
    return old.rstrip() + ("\n\n" if old.strip() else "") + block + "\n"


def save_enabled(root: Path, identifiers: set[str], skills: list[Skill]) -> tuple[bool, str]:
    path, backup = config_path(root), config_path(root).with_suffix(".toml.backup")
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        if old is not None: shutil.copy2(path, backup)
        path.write_text(render_enabled(old or "", identifiers, skills), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        if old is None: path.unlink(missing_ok=True)
        else: shutil.copy2(backup, path)
        return False, f"Não foi possível salvar a configuração: {error}"
    finally:
        backup.unlink(missing_ok=True)
    return True, "Configuração atualizada e validada."


def invoke(skill: Skill, root: Path) -> None:
    subprocess.run([sys.executable, str(skill.script), "--onmyoji-root", str(root), "--action", "configure"], check=False)


def skill_menu(skill: Skill, root: Path, skills: list[Skill]) -> None:
    while True:
        enabled = skill.identifier in enabled_ids(root)
        state = "Desabilitar" if enabled else "Habilitar"
        print(f"\n{skill.title} — {'habilitada' if enabled else 'desabilitada'}")
        print(f"  1. {state}")
        print("  2. Configurar")
        print("  X. Voltar")
        choice = input("Opção: ").strip().casefold()
        if choice == "x": return
        if choice == "1":
            identifiers = enabled_ids(root)
            (identifiers.discard if enabled else identifiers.add)(skill.identifier)
            ok, message = save_enabled(root, identifiers, skills)
            print(message)
            if not ok: print("A alteração foi desfeita.")
        elif choice == "2": invoke(skill, root)
        else: print("Opção inválida.")


def menu(skills: list[Skill], root: Path) -> int:
    while True:
        enabled = enabled_ids(root)
        print("\nOnmyōji — skills de integração")
        for index, skill in enumerate(skills, 1): print(f"  {index}. {skill.title} ({'habilitada' if skill.identifier in enabled else 'desabilitada'})")
        print("  X. Sair")
        choice = input("Selecione uma skill: ").strip().casefold()
        if choice == "x": return 0
        if choice.isdigit() and 1 <= int(choice) <= len(skills): skill_menu(skills[int(choice) - 1], root, skills)
        else: print("Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", choices=["menu", "list", "enable", "disable", "configure"], default="menu"); parser.add_argument("--skill"); args = parser.parse_args()
    skills = discover(); by_id = {skill.identifier: skill for skill in skills}
    if args.action == "menu": return menu(skills, ROOT)
    if args.action == "list":
        active = enabled_ids(ROOT)
        for skill in skills: print(f"{skill.identifier}\t{'enabled' if skill.identifier in active else 'disabled'}\t{skill.description}")
        return 0
    if args.skill not in by_id: parser.error("--skill deve identificar uma skill descoberta")
    if args.action == "configure": invoke(by_id[args.skill], ROOT); return 0
    active = enabled_ids(ROOT)
    (active.add if args.action == "enable" else active.discard)(args.skill)
    ok, message = save_enabled(ROOT, active, skills); print(message)
    return 0 if ok else 2


if __name__ == "__main__": raise SystemExit(main())
