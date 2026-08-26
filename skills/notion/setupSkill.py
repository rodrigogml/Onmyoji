#!/usr/bin/env python3
"""Configurador interativo de perfis Notion."""
from __future__ import annotations
import argparse, json, tomllib
from pathlib import Path

SKILL = Path(__file__).resolve().parent
DEFAULTS = {"api_base":"https://api.notion.com","notion_version":"2026-03-11","timeout_seconds":30,"max_retries":2,"page_size":100}
def path(root): return root / "configs" / "notion.toml"
def load(file): return tomllib.loads(file.read_text(encoding="utf-8")) if file.exists() else {"schema_version":1,"defaults":dict(DEFAULTS),"profiles":{}}
def render(data):
    lines=["schema_version = 1","","[defaults]"]
    for k,v in data["defaults"].items(): lines.append(f"{k} = {json.dumps(v,ensure_ascii=False)}")
    for n,p in sorted(data["profiles"].items()):
        lines += ["",f"[profiles.{n}]"]
        for k in ("vault_profile","vault_entry_path","vault_field"): lines.append(f"{k} = {json.dumps(p[k],ensure_ascii=False)}")
    return "\n".join(lines)+"\n"
def save(file,data):
    old=file.read_text(encoding="utf-8") if file.exists() else None
    try:
        file.parent.mkdir(parents=True,exist_ok=True);file.write_text(render(data),encoding="utf-8",newline="\n");tomllib.loads(file.read_text(encoding="utf-8"))
    except Exception as e:
        if old is None:file.unlink(missing_ok=True)
        else:file.write_text(old,encoding="utf-8",newline="\n")
        print(f"Não salvo: {e}");return
    print("Configuração salva e validada.")
def select(profiles):
    names=sorted(profiles)
    for i,name in enumerate(names,1):print(f"  {i}. {name}")
    c=input("Perfil [X cancela]: ").strip().casefold()
    return names[int(c)-1] if c.isdigit() and 1<=int(c)<=len(names) else None
def configure(root):
    file=path(root)
    while True:
        data=load(file);profiles=data["profiles"]
        print("\n══ Notion · configuração ══\n  1. Criar perfil\n  2. Editar perfil\n  3. Excluir perfil\n  X. Voltar")
        c=input("Opção: ").strip().casefold()
        if c in {"x","\x1b"}:return
        if c=="1":
            name=input("Nome do perfil [X cancela]: ").strip()
            if not name or name.casefold()=="x" or name in profiles or not name.replace("-","").replace("_","").isalnum():print("Nome inválido ou existente.");continue
            p={}
            for key,label,default in (("vault_profile","Perfil KeePass","example"),("vault_entry_path","Entrada KeePass","APIs/Notion"),("vault_field","Campo do token","password")):
                value=input(f"{label} [{default}]: ").strip();p[key]=value or default
            profiles[name]=p;save(file,data)
        elif c in {"2","3"}:
            if not profiles:print("Não há perfis configurados.");continue
            print("Perfis:");name=select(profiles)
            if not name:print("Seleção cancelada.");continue
            if c=="3":
                if input(f"Digite EXCLUIR para remover {name}: ").strip()=="EXCLUIR":del profiles[name];save(file,data)
                else:print("Operação cancelada.")
            else:
                p=profiles[name];print(f"\n{name}\n  1. Perfil KeePass: {p['vault_profile']}\n  2. Entrada KeePass: {p['vault_entry_path']}\n  3. Campo: {p['vault_field']}\n  X. Voltar")
                key={"1":"vault_profile","2":"vault_entry_path","3":"vault_field"}.get(input("Editar: ").strip().casefold())
                if key:
                    value=input("Novo valor [X cancela]: ").strip()
                    if value and value.casefold() not in {"x","\x1b"}:p[key]=value;save(file,data)
        else:print("Opção inválida.")
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--action",default="configure");parser.add_argument("--json",action="store_true");parser.add_argument("--onmyoji-root",type=Path);args=parser.parse_args();root=args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1];info={"id":"notion","title":"Notion","description":"Páginas, bases e arquivos com token no KeePass Vault."}
    if args.action=="describe":print(json.dumps(info,ensure_ascii=False) if args.json else info["title"]);return
    if args.action=="status":print(json.dumps({"configured":path(root).exists(),"valid":True}));return
    configure(root)
if __name__=="__main__":main()
