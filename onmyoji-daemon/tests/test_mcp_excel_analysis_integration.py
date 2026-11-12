"""Homologação real opt-in de hipóteses, fontes locais e cópia entre pastas."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Exige Windows/Excel e ONMYOJI_TEST_EXCEL=1.")


SCRIPT = r'''
from pathlib import Path
import json
import shutil
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.excel.service.native import execute_native

base=Path(sys.argv[1]); root=base/'instance'; work=base/'work'
(root/'configs').mkdir(parents=True); work.mkdir()
(root/'configs/onmyoji-system.toml').write_text('[codex]\nproject_directory='+json.dumps(work.as_posix())+'\n',encoding='utf-8')
profile={'enabled':True,'description':'Teste sintético','readable_roots':[str(work)],'writable_roots':[str(work)],'work_directory':str(work/'jobs'),'allow_write':True,'allow_refresh':True,'trusted_content':True}
defaults={'max_cells':10000,'timeout_seconds':60}
sequence=0
def run(operation,args):
 global sequence
 sequence+=1
 print('stage',sequence,operation,flush=True)
 context=DocumentContext(root,'excel','default',profile,defaults,work/'jobs'/str(sequence))
 result=execute_native(operation,args,context)
 context.publish()
 return result

source=work/'source.xlsx'
run('create',{'output_path':str(source),'operations':[
 {'type':'sheet','action':'add','name':'Dados'},
 {'type':'set_values','sheet':'Dados','range':'A1','values':[[2],[4]]},
 {'type':'set_formula','sheet':'Dados','range':'C1','formula':'=A1*A2'},
 {'type':'set_values','sheet':'Dados','range':'E1','values':[[1],[2]]},
 {'type':'set_formula','sheet':'Dados','range':'H1','formula':'=A1*A2'},
 {'type':'set_values','sheet':'Dados','range':'I1','values':[[10,20]]},
 {'type':'set_values','sheet':'Dados','range':'H2','values':[[2],[3]]},
 {'type':'sheet','action':'add','name':'Totais'},
 {'type':'set_values','sheet':'Totais','range':'A1','values':[['Grupo','Valor'],['A',1],['A',2],['B',3]]}
],'calculation':{'mode':'full_rebuild'}})
original=source.read_bytes()
result=work/'analysis.xlsx'
run('apply_changes',{'input_path':str(source),'output_path':str(result),'operations':[
 {'type':'autofill','sheet':'Dados','range':'E1:E2','destination':'E1:E5','fill_type':2},
 {'type':'outline','sheet':'Dados','range':'E2:E5','orientation':'rows','action':'group'},
 {'type':'outline','sheet':'Dados','range':'E6','orientation':'rows','action':'show_detail','show':False},
 {'type':'scenario','sheet':'Dados','action':'add','name':'Base','changing_range':'A1:A2','values':[3,5]},
 {'type':'scenario','sheet':'Dados','action':'show','name':'Base'},
 {'type':'data_table','sheet':'Dados','range':'H1:J3','row_input':'A1','column_input':'A2'},
 {'type':'subtotal','sheet':'Totais','range':'A1:B4','group_by':1,'function':'sum','total_columns':[2]}
],'calculation':{'mode':'full_rebuild'}})
assert source.read_bytes()==original
read=run('read_range',{'input_path':str(result),'sheet':'Dados','range':'A1:J5','limit':100,'fields':['value','formula','cached_value']})
cells={cell['address']:cell for cell in read['cells']}
assert cells['A1']['value']==3 and cells['A2']['value']==5,cells
assert cells['C1']['cached_value']==15,cells
assert cells['E5']['value']==5,cells
assert cells['I2']['cached_value']==20 and cells['J3']['cached_value']==60,cells
copied=work/'copied.xlsx'
run('create',{'output_path':str(copied),'operations':[{'type':'copy_sheet_from','input_path':str(source),'sheet':'Totais','name':'Copiada'}]})
inventory=run('inspect',{'input_path':str(copied)})
assert any(sheet['name']=='Copiada' for sheet in inventory['sheets']),inventory
query=work/'query.xlsx'
run('create',{'output_path':str(query),'operations':[
 {'type':'sheet','action':'add','name':'Dados'},
 {'type':'set_values','sheet':'Dados','range':'D1','values':[['Numero'],[1],[2]]},
 {'type':'table','action':'create','sheet':'Dados','name':'DimTable','range':'D1:D3'},
 {'type':'query','action':'set','name':'Local','formula':'let Source = #table(type table [Numero=number], {{1},{2}}) in Source'},
 {'type':'query_load','sheet':'Dados','name':'Local','destination':'A1','table_name':'LocalTable','refresh':True},
 {'type':'connection','action':'add','name':'ExtraLocal','connection_string':'OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;Location=Local;Extended Properties=""','command_text':'SELECT * FROM [Local]'},
 {'type':'connection','action':'update','name':'ExtraLocal','description':'Somente dados sintéticos','refresh_with_refresh_all':False},
 {'type':'connection','action':'delete','name':'ExtraLocal'}
],'calculation':{'mode':'full_rebuild'}})
read=run('read_range',{'input_path':str(query),'sheet':'Dados','range':'A1:A3','fields':['value','cached_value']})
assert [item['value'] for item in read['cells']]==['Numero',1,2],read
model=work/'model.xlsx'
profile['allow_object_api']=True
run('apply_changes',{'input_path':str(query),'output_path':str(model),'operations':[
 {'type':'connection','action':'add','name':'ModelLocal','connection_string':'WORKSHEET;'+str(query),'command_text':'LocalTable','command_type':7,'create_model_connection':True},
 {'type':'connection','action':'add','name':'ModelDim','connection_string':'WORKSHEET;'+str(query),'command_text':'DimTable','command_type':7,'create_model_connection':True}
],'validation':{'require_preservation':False}})
name=run('get_object',{'input_path':str(model),'selector':[{'collection':'Model'},{'collection':'ModelTables','index':1}],'member':'Name'})['value']
second=run('get_object',{'input_path':str(model),'selector':[{'collection':'Model'},{'collection':'ModelTables','index':2}],'member':'Name'})['value']
assert {name,second}=={'LocalTable','DimTable'},(name,second)
measured=work/'measure.xlsx'
run('apply_changes',{'input_path':str(model),'output_path':str(measured),'operations':[
 {'type':'model_measure','action':'set','table':'LocalTable','name':'Total','formula':"SUM('LocalTable'[Numero])"},
 {'type':'model_relationship','action':'add','table':'LocalTable','column':'Numero','related_table':'DimTable','related_column':'Numero'}
],'validation':{'require_preservation':False}})
formula=run('get_object',{'input_path':str(measured),'selector':[{'collection':'Model'},{'collection':'ModelMeasures','name':'Total'}],'member':'Formula'})['value']
assert 'SUM' in formula,formula
relationship=run('get_object',{'input_path':str(measured),'selector':[{'collection':'Model'},{'collection':'ModelRelationships','index':1}],'member':'Active'})['value']
assert relationship is True,relationship
with ZipFile(measured) as archive:
 for part in archive.namelist():
  if part.endswith('.xml'):
   content=archive.read(part)
   document=ET.fromstring(content)
   for parent in document.iter():
    for child in list(parent):
     if child.tag=='{http://schemas.microsoft.com/office/spreadsheetml/2010/11/ac}absPath':parent.remove(child)
   content=ET.tostring(document,encoding='utf-8')
   assert str(work/'jobs').encode('utf-8').lower() not in content.lower(),part
staging=(work/'jobs').resolve()
assert staging.is_relative_to(work.resolve()) and staging!=work.resolve()
shutil.rmtree(staging)
persisted=run('get_object',{'input_path':str(measured),'selector':[{'collection':'Model'},{'collection':'ModelMeasures','name':'Total'}],'member':'Formula'})['value']
assert persisted==formula,persisted
print('MODEL_VERIFIED',flush=True)
print('ANALYSIS_VERIFIED',flush=True)
'''


def test_real_excel_analysis_query_load_and_cross_workbook_copy(tmp_path):
    """Verifica cálculos de hipóteses e Power Query local, sem fontes externas reais."""
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    system = tmp_path / "system"
    system.mkdir()
    environment.update(TMP=str(system), TEMP=str(system), TMPDIR=str(system))
    result = subprocess.run([sys.executable, "-B", "-c", SCRIPT, str(tmp_path)], env=environment, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=150, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    assert "ANALYSIS_VERIFIED" in result.stdout and "MODEL_VERIFIED" in result.stdout
