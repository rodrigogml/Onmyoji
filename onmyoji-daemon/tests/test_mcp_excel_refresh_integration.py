"""Regressão opt-in da ordem cálculo de origem, refresh de pivô e dependentes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Requer Excel desktop real e execução nativa explicitamente habilitada.")


SCRIPT = r'''
import json,sys
from pathlib import Path
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.excel.component.ooxml import SpreadsheetPackage
from onmyoji_daemon.mcp.excel.service.native import execute_native

base=Path(sys.argv[1])
root,work=base/'instance',base/'workspace'
(root/'configs').mkdir(parents=True)
work.mkdir()
(root/'configs/onmyoji-system.toml').write_text('[codex]\nproject_directory="'+work.as_posix()+'"\n'.replace('\\n','\n'),encoding='utf-8')
profile={'readable_roots':[str(work)],'writable_roots':[str(work)],'work_directory':str(work/'jobs'),
         'allow_write':True,'allow_refresh':True}
defaults={'timeout_seconds':120,'max_cells':10000,'max_operations':1000,'max_memory_bytes':2147483648}

def run(operation,args,name):
    context=DocumentContext(root,'excel','default',profile,defaults,work/'jobs'/name)
    result=execute_native(operation,args,context)
    context.publish()
    return result

source,pivot_source,edited,refreshed=(work/name for name in ('source.xlsx','pivot.xlsx','edited.xlsx','refreshed.xlsx'))
run('create',{'output_path':str(source),'operations':[
    {'type':'sheet','action':'add','name':'Dados'},
    {'type':'set_values','sheet':'Dados','range':'A1','values':[['Base','Dobro'],[10,None]]},
    {'type':'set_formula','sheet':'Dados','range':'B2','formula':'=A2*2'}
], 'calculation':{'mode':'full_rebuild'}},'create-formula')
run('apply_changes',{'input_path':str(source),'output_path':str(pivot_source),'operations':[
    {'type':'sheet','action':'add','name':'Resumo'},
    {'type':'pivot','action':'create','sheet':'Resumo','name':'SomaDobro','source_sheet':'Dados',
     'source_range':'A1:B2','destination':'A1','fields':[{'name':'Dobro','orientation':'data','function':'sum'}]}
], 'calculation':{'mode':'full_rebuild'}},'create-pivot')
with SpreadsheetPackage(pivot_source) as package:
    assert package.cells('Dados')['B2']['cached_value']==20
    assert [cell['value'] for cell in package.cells('Resumo').values() if cell['type']=='number']==[20]

# Persist an intentionally stale source cache using the supported explicit
# "none" calculation mode. The following refresh must not reuse this 20.
run('apply_changes',{'input_path':str(pivot_source),'output_path':str(edited),'operations':[
    {'type':'set_values','sheet':'Dados','range':'A2','values':[[30]]}
], 'calculation':{'mode':'none'}},'change-without-calculation')
with SpreadsheetPackage(edited) as package:
    assert package.cells('Dados')['A2']['value']==30
    assert package.cells('Dados')['B2']['cached_value']==20
    assert [cell['value'] for cell in package.cells('Resumo').values() if cell['type']=='number']==[20]

result=run('refresh',{'input_path':str(edited),'output_path':str(refreshed),'pivots':True,
                      'calculation':{'mode':'full_rebuild'}},'refresh-in-dependency-order')
with SpreadsheetPackage(refreshed) as package:
    cached=package.cells('Dados')['B2']['cached_value']
    total=[cell['value'] for cell in package.cells('Resumo').values() if cell['type']=='number']
assert cached==60,{'source_cached_value':cached}
assert total==[60],{'source_cached_value':cached,'pivot_values':total}
assert result['calculation']['status']=='completed'
assert all(item['status']=='completed' for item in result['pivots'])
assert result['preservation']['status']=='completed',result['preservation']
print(json.dumps({'status':'verified','source_cached_value':cached,'pivot_values':total}))
'''


def test_refresh_calculates_formula_source_before_pivot_cache(tmp_path):
    """B2 e o pivô devem persistir 60, mesmo quando ambos começaram em cache 20."""
    temporary=tmp_path/'temporary'; temporary.mkdir()
    environment={**os.environ,'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'src'),'PYTHONDONTWRITEBYTECODE':'1',
                 'TMP':str(temporary),'TEMP':str(temporary),'TMPDIR':str(temporary)}
    process=subprocess.run([sys.executable,'-B','-c',SCRIPT,str(tmp_path)],stdin=subprocess.DEVNULL,capture_output=True,
                           text=True,timeout=180,env=environment,creationflags=subprocess.CREATE_NO_WINDOW)
    assert process.returncode==0,process.stderr
    result=json.loads(process.stdout)
    assert result['source_cached_value']==60 and result['pivot_values']==[60]
