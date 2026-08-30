#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from setup_ui import note,screen
from setup_profile_api import Field,handle as handle_profile,simple_load,simple_save
DEFAULTS={'timeout_seconds':30,'temp_dir':''}
PROFILE_FIELDS=(Field('host','Host',required=True),Field('port','Porta',22),Field('username','Usuário',required=True),Field('auth_mode','Autenticação','key'),Field('vault_profile','Perfil KeePass',required=True),Field('vault_entry_path','Entrada KeePass',required=True),Field('keepass_password_field','Campo da senha','password'),Field('keepass_key_attachment','Anexo da chave','id_ed25519'),Field('keepass_key_passphrase_field','Campo da frase secreta','password'),Field('known_hosts','Known hosts',''))
def config_path(root):return root/'configs'/'ssh.toml'
def main():
 p=argparse.ArgumentParser();p.add_argument('--onmyoji-root',type=Path,default=ROOT);p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--profile');p.add_argument('--set',action='append');p.add_argument('--confirm-delete');a=p.parse_args()
 if a.action=='describe': print(json.dumps({'id':'ssh','title':'SSH','description':'Execução e transferência SSH com credenciais no KeePass Vault.'}) if a.json else 'SSH');return
 if a.action=='status':
  print(json.dumps({'configured':(a.onmyoji_root/'configs'/'ssh.toml').exists(),'valid':True}))
  return
 if a.action.startswith('profile-'):
  code,value=handle_profile(action=a.action,profile_name=a.profile,values=a.set,confirm_delete=a.confirm_delete,path=config_path(a.onmyoji_root),load=lambda p:simple_load(p,DEFAULTS),save=simple_save,fields=PROFILE_FIELDS);print(json.dumps(value,ensure_ascii=False));return code
 screen('SSH','Configuração','Perfil local da integração');note('Configure os perfis SSH em configs/ssh.toml a partir de skills/ssh/configs/ssh.toml.model.')
if __name__=='__main__':main()
