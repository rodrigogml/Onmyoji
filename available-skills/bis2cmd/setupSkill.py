#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
SKILL=Path(__file__).resolve().parent
sys.path.insert(0,str(SKILL.parent))
from setup_ui import note,screen
from setup_profile_api import Field,handle as handle_profile,simple_load,simple_save
DEFAULTS={'java_path':'java','timeout_seconds':180,'encoding':'utf-8'}
PROFILE_FIELDS=(Field('jar_path','Arquivo JAR',required=True),Field('working_dir','Diretório de trabalho',required=True),Field('host','Host','127.0.0.1',True),Field('port','Porta',8080),Field('vault_profile','Perfil KeePass',required=True),Field('vault_entry_path','Entrada KeePass',required=True),Field('username_field','Campo do usuário','username'),Field('password_field','Campo da senha','password'))
def config_path(root):return root/'configs'/'bis2cmd.toml'
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);p.add_argument('--profile');p.add_argument('--set',action='append');p.add_argument('--confirm-delete');a=p.parse_args();root=a.onmyoji_root.resolve() if a.onmyoji_root else SKILL.parents[1];info={'id':'bis2cmd','title':'BISCMD','description':'Cliente BIS2 via Java, com credenciais no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(info,ensure_ascii=False) if a.json else info['title']);return
 if a.action=='status':print(json.dumps({'configured':(root/'configs/bis2cmd.toml').exists(),'valid':True}));return
 if a.action.startswith('profile-'):
  code,value=handle_profile(action=a.action,profile_name=a.profile,values=a.set,confirm_delete=a.confirm_delete,path=config_path(root),load=lambda p:simple_load(p,DEFAULTS),save=simple_save,fields=PROFILE_FIELDS);print(json.dumps(value,ensure_ascii=False));return code
 screen('BISCMD','Configuração','Perfil local da integração');note(f'Crie {root/"configs/bis2cmd.toml"} a partir de {SKILL/"configs/bis2cmd.toml.model"}.')
if __name__=='__main__':main()
