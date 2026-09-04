#!/usr/bin/env python3
"""Configuração local da memória estruturada da skill Barinella."""
from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def config_path(root: Path) -> Path:
    return root / "configs" / "barinella.toml"


def load_config(root: Path) -> dict[str, str]:
    path = config_path(root)
    if not path.exists(): return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return {key: value for key, value in data.items() if key in {"memory_dir", "memory_namespace"} and isinstance(value, str)}


def valid(config: dict[str, str]) -> tuple[bool, str]:
    memory_dir, namespace = config.get("memory_dir"), config.get("memory_namespace", "barinella")
    if memory_dir is not None and not Path(memory_dir).is_absolute(): return False, "memory_dir deve ser um diretório absoluto."
    if memory_dir is not None and not Path(memory_dir).is_dir(): return False, "memory_dir não existe ou não é um diretório."
    if not namespace: return False, "memory_namespace é obrigatório."
    return True, "Memória estruturada configurada." if memory_dir else "Memória estruturada usará o diretório padrão do Onmyōji."


def save_config(root: Path, config: dict[str, str]) -> tuple[bool, str]:
    ok, message = valid(config)
    if not ok: return ok, message
    path = config_path(root); path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if config.get("memory_dir"): lines.append("memory_dir = " + json.dumps(str(Path(config["memory_dir"]).resolve()), ensure_ascii=False))
    lines.append("memory_namespace = " + json.dumps(config.get("memory_namespace", "barinella"), ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return True, message


def configure(root: Path) -> int:
    from setup_ui import item, prompt, result, screen
    config = load_config(root)
    while True:
        screen("Barinella", "Memória estruturada", "Configuração local da instância")
        item("1.", "Diretório compartilhado", config.get("memory_dir", "(padrão do Onmyōji)"))
        item("2.", "Namespace", config.get("memory_namespace", "barinella"))
        item("S.", "Salvar")
        item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return 0
        if choice == "1":
            value = prompt("Diretório absoluto [vazio usa o padrão; X cancela]: ").strip()
            if value.casefold() not in {"x", "\x1b"}:
                if value: config["memory_dir"] = str(Path(value).expanduser())
                else: config.pop("memory_dir", None)
        elif choice == "2":
            value = prompt("Namespace [barinella]: ").strip()
            if value.casefold() not in {"x", "\x1b"} and value: config["memory_namespace"] = value
        elif choice == "s":
            config.setdefault("memory_namespace", "barinella")
            ok, message = save_config(root, config); result(ok, message)
            if ok: return 0
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--onmyoji-root", type=Path, required=True)
    parser.add_argument("--action", choices=["describe", "status", "configure"], default="configure")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(); root = args.onmyoji_root.resolve()
    if args.action == "describe":
        data = {"id": "barinella", "title": "Barinella", "description": "Conhecimento operacional e memória estruturada da Barinella."}
        print(json.dumps(data, ensure_ascii=False) if args.json else data["title"]); return 0
    config = load_config(root); ok, message = valid(config)
    if args.action == "status":
        data = {"configured": config_path(root).exists(), "valid": ok, "message": message}
        print(json.dumps(data, ensure_ascii=False) if args.json else message); return 0 if ok else 2
    sys.path.insert(0, str(root / "available-skills"))
    return configure(root)


if __name__ == "__main__": raise SystemExit(main())
