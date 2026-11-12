"""Aceite opt-in de pivôs avançados/filtro com Excel real e arquivos sintéticos."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Requer Excel desktop real e execução nativa explicitamente habilitada.")


SCRIPT = r'''
import json, sys
from pathlib import Path
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.excel.service.native import execute_native
from onmyoji_daemon.mcp.excel.component.ooxml import SpreadsheetPackage
base=Path(sys.argv[1])
root,work=base/'instance',base/'workspace'
(root/'configs').mkdir(parents=True)
work.mkdir()
(root/'configs/onmyoji-system.toml').write_text('[codex]\nproject_directory="'+work.as_posix()+'"\n'.replace('\\n','\n'),encoding='utf-8')
profile={'readable_roots':[str(work)],'writable_roots':[str(work)],'work_directory':str(work/'jobs'),'allow_write':True,'allow_refresh':False}
defaults={'timeout_seconds':120,'max_cells':10000,'max_operations':1000,'max_memory_bytes':2147483648}
def run(operation,args,name):
    context=DocumentContext(root,'excel','default',profile,defaults,work/'jobs'/name)
    try:
        result=execute_native(operation,args,context)
    except Exception:
        from onmyoji_daemon.mcp.excel.component.preservation import capture_preservation,validate_preservation
        if args.get('input_path'):
            for staged in context._outputs.values():
                if staged.is_file() and staged.suffix=='.xlsx':
                    evidence=validate_preservation(capture_preservation(Path(args['input_path'])),staged,operation,args)
                    print(json.dumps({'stage':name,'preservation':evidence},ensure_ascii=False),file=sys.stderr)
        raise
    context.publish()
    return result
source=work/'source.xlsx'
operations=[{'type':'sheet','action':'add','name':name} for name in ('Resumo','Agrupar','Dados')]
operations += [
 {'type':'set_values','sheet':'Dados','range':'A1','values':[['Categoria','Numero','Valor'],['A',1,10],['A',2,20],['B',10,30],['C',20,40]]},
 {'type':'set_values','sheet':'Dados','range':'H1','values':[['Categoria'],['A']]},
 {'type':'pivot','action':'create','sheet':'Resumo','name':'Resumo','source_sheet':'Dados','source_range':'A1:C5','destination':'A1','fields':[{'name':'Categoria','orientation':'row'},{'name':'Valor','orientation':'data','function':'sum'}]},
 {'type':'pivot','action':'create','sheet':'Agrupar','name':'Agrupar','source_sheet':'Dados','source_range':'A1:C5','destination':'A1','fields':[{'name':'Numero','orientation':'row'},{'name':'Valor','orientation':'data','function':'sum'}]},
 {'type':'advanced_filter','sheet':'Dados','range':'A1:C5','action':'copy','criteria_range':'H1:H2','copy_sheet':'Dados','copy_range':'J1','unique':False},
]
created=run('create',{'output_path':str(source),'operations':operations,'calculation':{'mode':'full_rebuild'}},'create')
with SpreadsheetPackage(source) as package:
    assert package.cells('Dados')['J2']['value']=='A'
    assert package.cells('Dados')['L3']['value']==20
output=work/'filtered.xlsx'
result=run('apply_changes',{'input_path':str(source),'output_path':str(output),'operations':[
 {'type':'pivot_field','sheet':'Resumo','name':'Resumo','field':'Categoria','action':'items','items':['A']},
 {'type':'pivot_calculated_field','sheet':'Resumo','name':'Resumo','field':'Dobro','action':'set','formula':'=Valor*2'},
 {'type':'pivot_group','sheet':'Agrupar','name':'Agrupar','field':'Numero','action':'group','start':0,'end':30,'interval':10},
], 'calculation':{'mode':'full_rebuild'}},'filter-group')
assert result['preservation']['status']=='completed',result.get('preservation')
second=work/'changed.xlsx'
result=run('apply_changes',{'input_path':str(output),'output_path':str(second),'operations':[
 {'type':'pivot_field','sheet':'Resumo','name':'Resumo','field':'Categoria','action':'clear'},
 {'type':'pivot_source','sheet':'Resumo','name':'Resumo','source_sheet':'Dados','source_range':'A1:C4'},
 {'type':'pivot_drilldown','sheet':'Resumo','name':'Resumo','range':'B2','destination_sheet':'Detalhes'},
], 'calculation':{'mode':'full_rebuild'}},'source-drilldown')
with SpreadsheetPackage(second) as package:
    assert any(sheet['name']=='Detalhes' for sheet in package.sheets)
assert result['preservation']['status']=='completed',result.get('preservation')
print(json.dumps({'status':'verified','source':str(source),'filtered':str(output),'output':str(second)},ensure_ascii=False))
'''


def test_real_excel_pivots_filters_calculated_fields_grouping_source_and_drilldown(tmp_path):
    temporary = tmp_path / "temporary"; temporary.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "TMP": str(temporary), "TEMP": str(temporary), "TMPDIR": str(temporary)}
    result = subprocess.run([sys.executable, "-B", "-c", SCRIPT, str(tmp_path)], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=180, env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "verified"
