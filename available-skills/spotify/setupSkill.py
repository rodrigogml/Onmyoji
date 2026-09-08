#!/usr/bin/env python3
import argparse,json,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def path(r):return r/'configs/spotify.toml'
def empty():return {'schema_version':1,'defaults':{'timeout_seconds':30,'max_retries':2},'profiles':{}}
def load(p):return tomllib.loads(p.read_text(encoding='utf8')) if p.exists() else empty()
def valid(d):
 try:
  for q in d['profiles'].values():
   if not q['vault_profile'] or not q['vault_entry_path'] or q.get('access','read_only') not in ('read_only','read_write'):return False
  return True
 except (KeyError,TypeError):return False
def render(d):
 x=['schema_version = 1','','[defaults]',f"timeout_seconds = {d['defaults']['timeout_seconds']}",f"max_retries = {d['defaults']['max_retries']}"]
 for n,q in sorted(d['profiles'].items()):x+=['',f'[profiles.{n}]',*[f'{k} = {json.dumps(v,ensure_ascii=False)}' for k,v in q.items()]]
 return '\n'.join(x)+'\n'
def main():
 p=argparse.ArgumentParser();p.add_argument('--onmyoji-root',type=Path,default=ROOT);p.add_argument('--action',default='configure',choices=['describe','status','configure','profile-schema','profile-list','profile-create','profile-update','profile-delete','profile-test']);p.add_argument('--json',action='store_true');p.add_argument('--profile');p.add_argument('--set',action='append');p.add_argument('--confirm-delete');a=p.parse_args();f=path(a.onmyoji_root);d=load(f)
 if a.action=='describe':print(json.dumps({'id':'spotify','title':'Spotify','description':'Spotify OAuth PKCE com credenciais no KeePass Vault.'},ensure_ascii=False));return 0
 if a.action=='status':print(json.dumps({'configured':bool(d['profiles']),'valid':valid(d),'profiles':sorted(d['profiles'])},ensure_ascii=False));return 0
 if a.action=='profile-schema':print(json.dumps({'ok':True,'fields':['vault_profile','vault_entry_path','client_id_field','refresh_token_field','access','allowed_operations']},ensure_ascii=False));return 0
 if a.action=='profile-list':print(json.dumps({'ok':True,'profiles':[{'name':n,**q} for n,q in d['profiles'].items()]},ensure_ascii=False));return 0
 if a.action in ('profile-create','profile-update'):
  if not a.profile:print(json.dumps({'ok':False,'error':{'code':'missing_profile'}}));return 2
  q=dict(d['profiles'].get(a.profile,{}));
  for item in a.set or []:
   k,_,v=item.partition('=');q[k]=json.loads(v) if v[:1] in '[{"' else v
  d['profiles'][a.profile]=q
  if not valid(d):print(json.dumps({'ok':False,'error':{'code':'invalid_profile'}}));return 2
  f.parent.mkdir(parents=True,exist_ok=True);f.write_text(render(d),encoding='utf8');print(json.dumps({'ok':True,'profile':a.profile}));return 0
 if a.action=='profile-delete' and a.profile in d['profiles'] and a.confirm_delete=='DELETE':del d['profiles'][a.profile];f.parent.mkdir(parents=True,exist_ok=True);f.write_text(render(d),encoding='utf8');print(json.dumps({'ok':True}));return 0
 print('Configure um perfil pelo menu do Onmyoji; crie uma entrada KeePass e use profile-create para configuração não interativa.');return 0
if __name__=='__main__':raise SystemExit(main())
