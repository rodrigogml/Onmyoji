#!/usr/bin/env python3
"""Configurador interativo da skill EccoVox."""
from __future__ import annotations

import argparse
import json
import secrets
import shutil
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_ui import item, prompt, result, screen

SKILL = Path(__file__).resolve().parent
sys.path.insert(0, str(SKILL))


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


def select_profile(profiles: dict, action: str) -> str | None:
    names = sorted(profiles)
    if not names:
        result(False, "Não há perfis configurados.")
        return None
    screen("EccoVox", "Selecionar perfil", action)
    for index, name in enumerate(names, 1): item(f"{index}.", name)
    item("X.", "Voltar")
    selected = ask("Número do perfil")
    if selected is None: return None
    if not selected.isdigit() or not 1 <= int(selected) <= len(names):
        result(False, "Seleção inválida.")
        return None
    return names[int(selected) - 1]


def test_profile(path: Path, name: str, audio_path: str = "") -> None:
    """Exercita o wrapper, o runtime e as raízes permitidas sem reter áudio de teste."""
    from scripts.eccovox import SafeError, execute, load_config
    generated: Path | None = None
    try:
        config = load_config(str(path), name)
        health = execute(config, {"version": 1, "operation": "health.get"})
        status = str(health.get("data", {}).get("status") or "desconhecido")
        result(status == "ready", f"Health do EccoVox: {status}.")
        if status != "ready": return
        writable = config["writable"]
        if writable:
            generated = writable[0] / f".onmyoji-eccovox-test-{secrets.token_hex(8)}.wav"
            response = execute(config, {"version": 1, "operation": "tts.synthesize", "text": "Teste de áudio do Onmyoji.", "output_path": str(generated), "response_format": "wav", "confirm": True})
            result(generated.is_file(), f"TTS aceitou a chamada e gerou {int(response.get('data', {}).get('bytes') or 0)} bytes.")
        else:
            result(False, "TTS não testado: configure ao menos uma raiz de escrita no perfil.")
        candidate = Path(audio_path).resolve() if audio_path else generated
        if candidate and candidate.is_file() and any(candidate.is_relative_to(root) for root in config["readable"]):
            execute(config, {"version": 1, "operation": "stt.transcribe", "audio_path": str(candidate)})
            result(True, "STT aceitou o áudio e retornou uma transcrição.")
        elif candidate and candidate.is_file():
            result(False, "STT não testado: o áudio não está em uma raiz de leitura autorizada.")
        else:
            result(False, "STT não testado: informe um áudio autorizado ou configure uma raiz comum de leitura e escrita.")
    except SafeError as error:
        result(False, f"Teste recusado: {error.message}")
    except (OSError, ValueError) as error:
        result(False, f"Teste falhou: {error}")
    finally:
        if generated: generated.unlink(missing_ok=True)


def configure(root: Path) -> None:
    path = config_path(root)
    while True:
        data = load(path); profiles = data.setdefault("profiles", {})
        screen("EccoVox", "Configuração", "Perfis do serviço local de voz")
        if profiles:
            print("  PERFIS CONFIGURADOS")
            for name in sorted(profiles): print(f"    • {name}")
            print()
        print("  AÇÕES")
        item("1.", "Criar novo perfil"); item("2.", "Editar perfil existente"); item("3.", "Excluir perfil existente"); item("4.", "Testar perfil", "Health, TTS e STT"); item("X.", "Voltar")
        choice = prompt("Opção: ").strip().casefold()
        if choice in {"x", "\x1b"}: return
        if choice == "1":
            name = ask("Nome do perfil", "eccovox")
            if not name or name in profiles or not name.replace("-", "").replace("_", "").isalnum(): result(False, "Nome inválido ou já existente."); continue
            profile = {"base_url": "http://127.0.0.1:8870", "readable_roots": [], "writable_roots": []}
            for key, label in (("base_url", "URL local"),):
                value = ask(label, profile[key])
                if value is None: break
                profile[key] = value
            else:
                profiles[name] = profile; ok, message = save(path, data); result(ok, message)
        elif choice in {"2", "3"}:
            name = select_profile(profiles, "Escolha o perfil para editar" if choice == "2" else "Escolha o perfil para excluir")
            if not name: continue
            if choice == "3":
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
        elif choice == "4":
            name = select_profile(profiles, "Escolha o perfil para testar")
            if not name: continue
            audio = ask("Áudio local para teste STT (vazio = usar o áudio TTS, se autorizado)")
            if audio is not None: test_profile(path, name, audio)
        else: result(False, "Opção inválida.")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--action", default="configure"); parser.add_argument("--json", action="store_true"); parser.add_argument("--onmyoji-root", type=Path); args = parser.parse_args(); root = root_from(args)
    description = {"id": "eccovox", "title": "EccoVox", "description": "STT e TTS locais, com perfis e diretórios autorizados."}
    if args.action == "describe": print(json.dumps(description, ensure_ascii=False) if args.json else description["title"]); return 0
    if args.action == "status": print(json.dumps({"configured": config_path(root).exists(), "valid": True})); return 0
    configure(root); return 0


if __name__ == "__main__": raise SystemExit(main())
