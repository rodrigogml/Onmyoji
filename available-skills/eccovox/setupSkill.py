#!/usr/bin/env python3
"""Configurador interativo da skill EccoVox."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_ui import item, prompt, result, screen

SKILL = Path(__file__).resolve().parent


def root_from(args: argparse.Namespace) -> Path:
    return args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1]


def config_path(root: Path) -> Path:
    return root / "configs" / "eccovox.toml"


def load(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "defaults": {"request_timeout_seconds": 120, "max_audio_bytes": 10485760, "max_text_characters": 4000}, "profiles": {}}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def quote(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def render(data: dict) -> str:
    defaults = data.get("defaults", {})
    lines = ["schema_version = 1", "", "[defaults]"]
    for key in ("request_timeout_seconds", "max_audio_bytes", "max_text_characters"):
        lines.append(f"{key} = {int(defaults[key])}")
    for name, profile in sorted(data.get("profiles", {}).items()):
        lines.extend(["", f"[profiles.{name}]"])
        for key in ("base_url", "readable_roots", "writable_roots"):
            lines.append(f"{key} = {quote(profile[key])}")
    return "\n".join(lines) + "\n"


def valid(path: Path, data: dict) -> tuple[bool, str]:
    try:
        from scripts.eccovox import load_config
        for name in data.get("profiles", {}):
            load_config(str(path), name)
    except Exception as exc:
        return False, str(exc)
    return True, "Configuração validada."


def save(path: Path, data: dict) -> tuple[bool, str]:
    old = path.read_text(encoding="utf-8") if path.exists() else None
    try:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_text(render(data), encoding="utf-8", newline="\n")
        tomllib.loads(path.read_text(encoding="utf-8")); ok, message = valid(path, data)
        if not ok: raise ValueError(message)
    except Exception as exc:
        if old is None: path.unlink(missing_ok=True)
        else: path.write_text(old, encoding="utf-8", newline="\n")
        return False, f"Não salvo: {exc}"
    return True, "Configuração salva e validada."


def ask(label: str, current: str = "") -> str | None:
    value = prompt(f"{label}" + (f" [{current}]" if current else "") + " (X cancela): ").strip()
    return None if value.casefold() in {"x", "\x1b"} else (value or current)


def configure(root: Path) -> None:
    path = config_path(root)
    while True:
        data = load(path); profiles = data.setdefault("profiles", {})
        screen("EccoVox", "Configuração", "Perfis do serviço local de voz")
        if profiles:
            print("  PERFIS CONFIGURADOS")
            for index, name in enumerate(sorted(profiles), 1): item(f"{index}.", name)
            print()
        print("  AÇÕES")
        item("N.", "Criar novo perfil"); item("E.", "Editar perfil existente"); item("R.", "Excluir perfil existente"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "n":
            name = ask("Nome do perfil", "eccovox")
            if not name or name in profiles or not name.replace("-", "").replace("_", "").isalnum(): result(False, "Nome inválido ou já existente."); continue
            profile = {"base_url": "http://127.0.0.1:8870", "readable_roots": [], "writable_roots": []}
            for key, label in (("base_url", "URL local"),):
                value = ask(label, profile[key])
                if value is None: break
                profile[key] = value
            else:
                profiles[name] = profile; ok, message = save(path, data); result(ok, message)
        elif choice in {"e", "r"}:
            names = sorted(profiles)
            if not names: result(False, "Não há perfis configurados."); continue
            selected = ask("Número do perfil")
            if selected is None or not selected.isdigit() or not 1 <= int(selected) <= len(names): result(False, "Seleção inválida."); continue
            name = names[int(selected) - 1]
            if choice == "r":
                if prompt(f"Digite EXCLUIR para remover {name}: ").strip() == "EXCLUIR": del profiles[name]; ok, message = save(path, data); result(ok, message)
                else: result(False, "Operação cancelada.")
            else:
                profile = profiles[name]
                screen("EccoVox", "Editar perfil", name)
                item("1.", "URL local", profile['base_url']); item("2.", "Raízes de leitura", str(profile['readable_roots'])); item("3.", "Raízes de escrita", str(profile['writable_roots'])); item("X.", "Voltar")
                field = prompt("Editar: ").strip().casefold()
                keys = {"1": "base_url", "2": "readable_roots", "3": "writable_roots"}
                if field in keys:
                    raw = ask("Novo valor" if field == "1" else "Diretórios separados por ;", profile[keys[field]] if field == "1" else ";".join(profile[keys[field]]))
                    if raw is not None: profile[keys[field]] = raw if field == "1" else [part.strip() for part in raw.split(";") if part.strip()]; ok, message = save(path, data); result(ok, message)
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", default="configure"); parser.add_argument("--json", action="store_true"); parser.add_argument("--onmyoji-root", type=Path); args = parser.parse_args(); root = root_from(args)
    description = {"id": "eccovox", "title": "EccoVox", "description": "STT e TTS locais, com perfis e diretórios autorizados."}
    if args.action == "describe": print(json.dumps(description, ensure_ascii=False) if args.json else description["title"]); return 0
    if args.action == "status": print(json.dumps({"configured": config_path(root).exists(), "valid": True})); return 0
    configure(root); return 0


if __name__ == "__main__": raise SystemExit(main())
