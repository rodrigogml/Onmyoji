#!/usr/bin/env python3
"""Configuração local da skill Omie."""
from __future__ import annotations
import argparse, json, sys, tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from setup_ui import choose_keepass_profile, item, prompt, result, screen, suggested_vault_entry
def path(root: Path) -> Path: return root / "configs" / "omie.toml"
def empty() -> dict: return {"schema_version": 1, "defaults": {"timeout_seconds": 30, "max_retries": 2}, "profiles": {}}
def render(data: dict) -> str:
    lines = ["schema_version = 1", "", "[defaults]", f"timeout_seconds = {data['defaults']['timeout_seconds']}", f"max_retries = {data['defaults']['max_retries']}"]
    for name, profile in sorted(data['profiles'].items()): lines += ["", f"[profiles.{name}]", *[f"{key} = {json.dumps(value, ensure_ascii=False)}" for key, value in profile.items()]]
    return "\n".join(lines) + "\n"
def load(file: Path) -> dict:
    if not file.exists(): return empty()
    return tomllib.loads(file.read_text(encoding="utf-8"))
def valid(data: dict) -> tuple[bool, str]:
    for name, profile in data.get('profiles', {}).items():
        if not all(isinstance(profile.get(k), str) and profile[k] for k in ('vault_profile','vault_entry_path')): return False, f"Perfil {name}: KeePass incompleto."
        if profile.get('app_key_field') not in {'username','password','url','notes'} or profile.get('app_secret_field') not in {'username','password','url','notes'}: return False, f"Perfil {name}: campos KeePass inválidos."
    return True, "Configuração Omie válida."
def save(file: Path, data: dict) -> tuple[bool, str]:
    ok, message = valid(data)
    if not ok: return ok, message
    file.parent.mkdir(parents=True, exist_ok=True); file.write_text(render(data), encoding="utf-8", newline="\n"); tomllib.loads(file.read_text(encoding="utf-8")); return True, message
def configure(root: Path) -> None:
    file, data = path(root), load(path(root))
    while True:
        screen("Omie", "Configuração", "Perfis de acesso ao ERP")
        item("1.", "Criar perfil"); item("2.", "Remover perfil"); item("X.", "Voltar")
        choice=prompt("Opção: ").strip().casefold()
        if choice in {'x','\x1b'}: return
        if choice == '1':
            name=prompt("Nome do perfil [X cancela]: ").strip()
            if not name or name.casefold() in {'x','\x1b'} or name in data['profiles']: result(False, "Nome inválido ou existente."); continue
            vault_profile=choose_keepass_profile(root)
            if vault_profile is None: continue
            suggested_entry=suggested_vault_entry('Omie', name)
            profile={'vault_profile': vault_profile, 'vault_entry_path': prompt(f"Entrada KeePass [{suggested_entry}]: ").strip() or suggested_entry, 'app_key_field': prompt("Campo app_key [username]: ").strip() or 'username', 'app_secret_field': prompt("Campo app_secret [password]: ").strip() or 'password'}
            candidate={**data, 'profiles': {**data['profiles'], name: profile}}; ok,message=save(file,candidate); result(ok, message if ok else f"Não salvo: {message}"); data=candidate if ok else data
        elif choice == '2':
            names=sorted(data['profiles']); screen("Omie", "Remover perfil", "Escolha um perfil ou pressione X para voltar"); [item(f"{i}.", name) for i,name in enumerate(names,1)]; item("X.", "Voltar")
            selected=prompt("Número [X cancela]: ").strip().casefold()
            if selected.isdigit() and 1 <= int(selected) <= len(names) and prompt(f"Digite REMOVER para excluir {names[int(selected)-1]}: ").strip()=='REMOVER':
                candidate={**data, 'profiles': dict(data['profiles'])}; del candidate['profiles'][names[int(selected)-1]]; ok,message=save(file,candidate); result(ok, message); data=candidate if ok else data
        else: result(False, "Opção inválida.")
def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument('--onmyoji-root',type=Path,default=ROOT); parser.add_argument('--action',choices=['describe','status','configure'],default='configure'); parser.add_argument('--json',action='store_true'); args=parser.parse_args()
    if args.action=='describe':
        data={'id':'omie','title':'Omie','description':'ERP, financeiro e NF-e com credenciais no KeePass Vault.'}; print(json.dumps(data,ensure_ascii=False) if args.json else data['title']); return 0
    if args.action=='status':
        data=load(path(args.onmyoji_root)); ok,message=valid(data); print(json.dumps({'configured':bool(data['profiles']),'valid':ok,'message':message},ensure_ascii=False)); return 0 if ok else 2
    configure(args.onmyoji_root); return 0
if __name__=='__main__': raise SystemExit(main())
