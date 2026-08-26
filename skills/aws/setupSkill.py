#!/usr/bin/env python3
"""Configurador interativo de perfis AWS."""
from __future__ import annotations
import argparse,json,tomllib
from pathlib import Path
SKILL=Path(__file__).resolve().parent
DEFAULTS={"timeout_seconds":30,"max_attempts":3}
FIELDS=(("cli_path","Executável AWS CLI","aws"),("region","Região","sa-east-1"),("expected_account_id","Conta AWS esperada (opcional)",""),("vault_profile","Perfil KeePass","example"),("vault_entry_path","Entrada KeePass","AWS/example"))
def path(root):return root/'configs'/'aws.toml'
def load(file):return tomllib.loads(file.read_text(encoding='utf-8')) if file.exists() else {'schema_version':1,'defaults':dict(DEFAULTS),'profiles':{}}
def render(data):
 lines=['schema_version = 1','','[defaults]',*[f'{k} = {json.dumps(v)}' for k,v in data['defaults'].items()]]
 for name,profile in sorted(data['profiles'].items()):lines+=['',f'[profiles.{name}]',*[f'{k} = {json.dumps(v)}' for k,v in profile.items()]]
 return '\n'.join(lines)+'\n'
def save(file,data):
 old=file.read_text(encoding='utf-8') if file.exists() else None
 try:file.parent.mkdir(parents=True,exist_ok=True);file.write_text(render(data),encoding='utf-8',newline='\n');tomllib.loads(file.read_text(encoding='utf-8'))
 except Exception as e:
  if old is None:file.unlink(missing_ok=True)
  else:file.write_text(old,encoding='utf-8',newline='\n')
  print(f'Não salvo: {e}');return
 print('Configuração salva e validada.')
def choose(profiles):
 names=sorted(profiles)
 for i,name in enumerate(names,1):print(f'  {i}. {name}')
 raw=input('Perfil [X cancela]: ').strip().casefold()
 return names[int(raw)-1] if raw.isdigit() and 1<=int(raw)<=len(names) else None
def configure(root):
 file=path(root)
 while True:
  data=load(file);profiles=data['profiles'];print('\n══ ONMYŌJI · AWS ══\n  1. Criar perfil\n  2. Editar perfil\n  3. Excluir perfil\n  X. Voltar');action=input('Opção: ').strip().casefold()
  if action in {'x','\x1b'}:return
  if action=='1':
   name=input('Nome [X cancela]: ').strip()
   if not name or name.casefold()=='x' or name in profiles or not name.replace('-','').replace('_','').isalnum():print('Nome inválido ou existente.');continue
   profiles[name]={key:(input(f'{label} [{default}]: ').strip() or default) for key,label,default in FIELDS};save(file,data)
  elif action in {'2','3'}:
   if not profiles:print('Não há perfis configurados.');continue
   print('Perfis:');name=choose(profiles)
   if not name:print('Seleção cancelada.');continue
   if action=='3':
    if input(f'Digite EXCLUIR para remover {name}: ').strip()=='EXCLUIR':del profiles[name];save(file,data)
    else:print('Operação cancelada.')
   else:
    profile=profiles[name];print(f'\nPerfil {name}')
    for i,(key,label,_) in enumerate(FIELDS,1):print(f'  {i}. {label}: {profile.get(key, "")}')
    field=input('Editar [X volta]: ').strip().casefold()
    if field.isdigit() and 1<=int(field)<=len(FIELDS):
     key,label,_=FIELDS[int(field)-1];value=input(f'{label} [X cancela]: ').strip()
     if value and value.casefold() not in {'x','\x1b'}:profile[key]=value;save(file,data)
  else:print('Opção inválida.')
def main():
 parser=argparse.ArgumentParser();parser.add_argument('--onmyoji-root',type=Path);parser.add_argument('--action',default='configure');parser.add_argument('--json',action='store_true');args=parser.parse_args();root=args.onmyoji_root.resolve() if args.onmyoji_root else SKILL.parents[1];info={'id':'aws','title':'AWS','description':'S3 e IAM com credenciais no KeePass Vault.'}
 if args.action=='describe':print(json.dumps(info,ensure_ascii=False) if args.json else info['title']);return
 if args.action=='status':print(json.dumps({'configured':bool(load(path(root))['profiles']),'valid':True}));return
 configure(root)
if __name__=='__main__':main()
