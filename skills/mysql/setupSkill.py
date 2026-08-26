#!/usr/bin/env python3
from __future__ import annotations
import argparse,json
from pathlib import Path
SKILL=Path(__file__).resolve().parent
def main():
 p=argparse.ArgumentParser();p.add_argument('--action',default='configure');p.add_argument('--json',action='store_true');p.add_argument('--onmyoji-root',type=Path);a=p.parse_args();root=a.onmyoji_root.resolve() if a.onmyoji_root else SKILL.parents[1];info={'id':'mysql','title':'MySQL','description':'Consultas e administração MySQL com credenciais no KeePass Vault.'}
 if a.action=='describe':print(json.dumps(info,ensure_ascii=False) if a.json else info['title']);return
 if a.action=='status':print(json.dumps({'configured':(root/'configs/mysql.toml').exists(),'valid':True}));return
 print(f"Use {root/'configs/mysql.toml'} a partir do modelo {SKILL/'configs/mysql.toml.model'}.")
if __name__=='__main__':main()
