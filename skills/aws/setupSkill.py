#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def cfg(root): return root/'configs'/'aws.toml'
def empty(): return {'schema_version':1,'defaults':{'timeout_seconds':30,'max_attempts':3},'profiles':{}}
def load(p): return tomllib.loads(p.read_text(encoding='utf-8')) if p.exists() else empty()
def render(d):
 l=['schema_version = 1','','[defaults]',f"timeout_seconds = {d['defaults']['timeout_seconds']}",f"max_attempts = {d['defaults']['max_attempts']}"]
 for n,v in sorted(d['profiles'].items()): l += ['',f'[profiles.{n}]',*[f'{k} = {json.dumps(x)}' for k,x in v.items()]]
 return '\n'.join(l)+'\n'
def save(p,d): p.parent.mkdir(parents=True,exist_ok=True);p.write_text(render(d),encoding='utf-8',newline='\n');tomllib.loads(p.read_text(encoding='utf-8'))
def main():
 a=argparse.ArgumentParser();a.add_argument('--onmyoji-root',type=Path,default=ROOT);a.add_argument('--action',default='configure',choices=['describe','status','configure']);a.add_argument('--json',action='store_true');x=a.parse_args()
 if x.action=='describe': print(json.dumps({'id':'aws','title':'AWS','description':'S3 e IAM com credenciais no KeePass Vault.'}) if x.json else 'AWS');return
 d=load(cfg(x.onmyoji_root))
 if x.action=='status': print(json.dumps({'configured':bool(d['profiles']),'valid':True}));return
 while True:
  print('\n══ ONMYŌJI · AWS ══\n  1. Criar perfil\n  X. Voltar');c=input('Opção: ').strip().casefold()
  if c in {'x','\x1b'}:return
  if c=='1':
   n=input('Nome [X cancela]: ').strip()
   if n and n.casefold() not in {'x','\x1b'} and n not in d['profiles']:
    d['profiles'][n]={'cli_path':input('AWS CLI [aws]: ').strip() or 'aws','region':input('Região [sa-east-1]: ').strip() or 'sa-east-1','expected_account_id':input('Conta esperada (opcional): ').strip(),'vault_profile':input('Perfil KeePass: ').strip(),'vault_entry_path':input('Entrada KeePass: ').strip()};save(cfg(x.onmyoji_root),d);print('Configuração salva e validada.')
if __name__=='__main__':main()
