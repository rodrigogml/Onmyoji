#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,tomllib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def main():
 p=argparse.ArgumentParser();p.add_argument('--onmyoji-root',type=Path,default=ROOT);p.add_argument('--action',choices=['describe','status','configure'],default='configure');p.add_argument('--json',action='store_true');a=p.parse_args()
 if a.action=='describe': print(json.dumps({'id':'ssh','title':'SSH','description':'Execução e transferência SSH com credenciais no KeePass Vault.'}) if a.json else 'SSH');return
 if a.action=='status': print(json.dumps({'configured':(a.onmyoji_root/'configs'/'ssh.toml').exists(),'valid':True});return
 print('Configure os perfis SSH em configs/ssh.toml a partir de skills/ssh/configs/ssh.toml.model.')
if __name__=='__main__':main()
