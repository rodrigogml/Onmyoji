"""Corpus real opt-in de edição Excel com efeitos observados e fonte preservada."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Exige Windows/Excel e ONMYOJI_TEST_EXCEL=1.")


PRELUDE = r'''
from pathlib import Path
from decimal import Decimal
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import hashlib,json,math,shutil,sys,time
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.excel.component.native import ExcelSession
from onmyoji_daemon.mcp.excel.component.ooxml import SpreadsheetPackage
from onmyoji_daemon.mcp.excel.component.process import process_identity
from onmyoji_daemon.mcp.excel.service.native import execute_native
from onmyoji_daemon.mcp.excel.service.inspection import execute_direct
from onmyoji_daemon.mcp.excel.service.operations import parameterized_property,range_address

base=Path(sys.argv[1]);root=base/'instance';work=base/'workspace'
(root/'configs').mkdir(parents=True);work.mkdir()
(root/'configs/onmyoji-system.toml').write_text('[codex]\nproject_directory='+json.dumps(work.as_posix())+'\n',encoding='utf-8')
profile={'enabled':True,'description':'Corpus sintético de operações','readable_roots':[str(work)],'writable_roots':[str(work)],'work_directory':str(work/'jobs'),'allow_write':True}
defaults={'max_cells':10000,'max_operations':1000,'timeout_seconds':90}
counter=0
def context():
 global counter
 counter+=1
 result=DocumentContext(root,'excel','default',profile,defaults,work/'jobs'/str(counter))
 result.resolve_password=lambda reference:'SenhaSomenteSintetica42' if reference else None
 return result
def stopped(ctx):
 owner=json.loads((ctx.job_dir/'ownership.json').read_text())
 deadline=time.monotonic()+5
 while process_identity(owner['pid']) is not None and time.monotonic()<deadline:time.sleep(.025)
 assert process_identity(owner['pid']) is None,'Excel próprio sobreviveu ao encerramento'
def run(name,operations,source=None,calculate=True):
 ctx=context();output=work/(name+'.xlsx');original=source.read_bytes() if source else None
 args={'output_path':str(output),'operations':operations,'calculation':{'mode':'full_rebuild' if calculate else 'none'}}
 if source:args['input_path']=str(source)
 print('RUN',name,[item['type'] for item in operations],flush=True)
 result=execute_native('apply_changes' if source else 'create',args,ctx)
 artifacts=ctx.publish();assert len(artifacts)==1 and output.is_file(),artifacts
 if source:assert source.read_bytes()==original,'O engine alterou a fonte original'
 stopped(ctx)
 return output
def verify(path,assertions):
 ctx=context();copied=ctx.temporary_path('.xlsx');shutil.copyfile(path,copied);original=path.read_bytes()
 with ExcelSession(ctx) as session:
  workbook=session.open(copied)
  try:
   assertions(workbook,session)
   session.assert_hidden()
   assert session.application.Visible is False and not session.application.CutCopyMode
  finally:workbook.Close(SaveChanges=False)
 stopped(ctx);assert path.read_bytes()==original
def sheet_xml(path,name='Dados'):
 package=SpreadsheetPackage(path)
 with ZipFile(path) as archive:return ET.fromstring(archive.read(package.sheet(name)['part']))
def local_elements(document,name):return [element for element in document.iter() if element.tag.rsplit('}',1)[-1]==name]

def invariant_number_format(cell):
 return cell._oleobj_.Invoke(cell._oleobj_.GetIDsOfNames('NumberFormat'),1033,2,True)
def source(operations=()):
 return run('source',[{'type':'sheet','action':'add','name':'Dados'},*operations])

def dates(path,expected):
 original=path.read_bytes()
 arguments={'input_path':str(path),'sheet':'Dados','range':'I1:I2','fields':['value','date_value','number_format']}
 direct=execute_direct('read_range',arguments,context())
 native_context=context();native=execute_native('read_range',arguments,native_context);stopped(native_context)
 for result in (direct,native):
  assert [cell['value'] for cell in result['cells']]==[60,61],result
  assert [cell['date_value'] for cell in result['cells']]==expected,result
  assert all(cell['number_format'] in {'yyyy-mm-dd',r'yyyy\-mm\-dd'} for cell in result['cells']),result
 assert path.read_bytes()==original
'''


SCENARIOS = {
    "format_resize_clear_merge": r'''
original=source([
 {'type':'set_values','sheet':'Dados','range':'A1','values':[[1,2],[3,4]]},
 {'type':'format_range','sheet':'Dados','range':'A1:B2','number_format':'0.000','font':{'name':'Calibri','size':13.5,'bold':True,'italic':True,'color':255},'fill_color':65535,'horizontal_alignment':-4108,'vertical_alignment':-4108,'wrap_text':True,'borders':{'color':65280,'weight':2,'line_style':1}},
 {'type':'resize','sheet':'Dados','range':'A1:B2','row_height':32,'column_width':18},
 {'type':'set_values','sheet':'Dados','range':'D1','values':[['Cabecalho']]},
 {'type':'merge','sheet':'Dados','range':'D1:E1'},
 {'type':'set_values','sheet':'Dados','range':'G5','values':[['Texto suficientemente longo para ajustar a coluna']]},
 {'type':'resize','sheet':'Dados','range':'G5','row_height':80,'column_width':2},
 {'type':'resize','sheet':'Dados','range':'G5','autofit':'both'},
 {'type':'set_values','sheet':'Dados','range':'I1','values':[[60],[61]]},
 {'type':'format_range','sheet':'Dados','range':'I1:I2','number_format':'yyyy-mm-dd'}
])
def styled(workbook,session):
 sheet=workbook.Worksheets.Item('Dados');cell=sheet.Range('A1')
 assert cell.Value2==1 and invariant_number_format(cell)=='0.000'
 assert cell.Font.Name=='Calibri' and cell.Font.Size==13.5 and cell.Font.Bold and cell.Font.Italic and cell.Font.Color==255
 assert cell.Interior.Color==65535 and cell.Borders(7).Color==65280 and cell.Borders(7).LineStyle==1
 assert cell.HorizontalAlignment==-4108 and cell.VerticalAlignment==-4108 and cell.WrapText
 assert abs(cell.RowHeight-32)<=.375 and abs(cell.ColumnWidth-18)<.2
 assert sheet.Range('D1:E1').MergeCells is True
 assert sheet.Range('G5').ColumnWidth>20 and sheet.Range('G5').RowHeight<80
 assert sheet.Range('I1').Text=='1900-02-29' and sheet.Range('I2').Text=='1900-03-01'
verify(original,styled)
dates(original,['1900-02-29','1900-03-01'])
date_context=context();date_context.profile={**date_context.profile,'allow_object_api':True}
date_output=work/'dates-1904.xlsx';date_source=original.read_bytes()
execute_native('set_object',{'input_path':str(original),'output_path':str(date_output),'selector':[],'member':'Date1904','value':True},date_context)
assert len(date_context.publish())==1;stopped(date_context);assert original.read_bytes()==date_source
assert execute_direct('inspect',{'input_path':str(date_output)},context())['date_system']=='1904'
dates(date_output,['1904-03-01','1904-03-02'])
changed=run('cleared',[
 {'type':'clear','sheet':'Dados','range':'A1','what':'contents'},
 {'type':'clear','sheet':'Dados','range':'A2','what':'formats'},
 {'type':'clear','sheet':'Dados','range':'B2','what':'all'},
 {'type':'unmerge','sheet':'Dados','range':'D1:E1'}
],original)
def cleared(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.Range('A1').Value2 is None and sheet.Range('A1').Font.Bold
 assert sheet.Range('A2').Value2==3 and invariant_number_format(sheet.Range('A2'))=='General' and not sheet.Range('A2').Font.Bold
 assert sheet.Range('B2').Value2 is None and invariant_number_format(sheet.Range('B2'))=='General'
 assert sheet.Range('D1:E1').MergeCells is False and sheet.Range('D1').Value2=='Cabecalho'
verify(changed,cleared)
xml=sheet_xml(changed)
assert not local_elements(xml,'mergeCell')
with ZipFile(changed) as archive:
 styles=ET.fromstring(archive.read('xl/styles.xml'))
 assert any(item.get('formatCode')=='0.000' for item in local_elements(styles,'numFmt'))
''',
    "copy_transpose_insert_delete": r'''
original=source([
 {'type':'set_values','sheet':'Dados','range':'A1','values':[[10],[20]]},
 {'type':'set_formula','sheet':'Dados','range':'B1','formula':'=A1+1'},
 {'type':'set_formula','sheet':'Dados','range':'B2','formula':'=A2+2'},
 {'type':'format_range','sheet':'Dados','range':'B1:B2','number_format':'0.00','font':{'bold':True},'fill_color':65535},
 {'type':'set_values','sheet':'Dados','range':'H1','values':[[100],[200]]},
 {'type':'set_values','sheet':'Dados','range':'J1','values':[[95],[96]]}
])
normal=run('normal-copy',[
 {'type':'copy_range','sheet':'Dados','range':'A1:B2','target_sheet':'Dados','target_range':'D1','mode':'all'},
 {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_sheet':'Dados','target_range':'G1','mode':'values'},
 {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_sheet':'Dados','target_range':'I1','mode':'formulas'},
 {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_sheet':'Dados','target_range':'J1','mode':'formats'}
],original)
def copied(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.Range('D1').Value2==10 and sheet.Range('D2').Value2==20
 assert sheet.Range('E1').Formula2=='=D1+1' and sheet.Range('E1').Value2==11
 assert sheet.Range('E2').Formula2=='=D2+2' and sheet.Range('E2').Value2==22
 assert sheet.Range('G1:G2').Value2==((11.0,),(22.0,)) and not sheet.Range('G1:G2').HasFormula
 assert sheet.Range('I1').Formula2=='=H1+1' and sheet.Range('I1').Value2==101
 assert sheet.Range('I2').Value2==202
 assert sheet.Range('J1:J2').Value2==((95.0,),(96.0,)) and sheet.Range('J1').Font.Bold and invariant_number_format(sheet.Range('J1'))=='0.00'
verify(normal,copied)
transposed=run('transposed',[
 {'type':'set_formula','sheet':'Dados','range':'B1','formula':'=$A$1+1'},
 {'type':'set_formula','sheet':'Dados','range':'B2','formula':'=$A$2+2'},
 {'type':'set_values','sheet':'Dados','range':'L14','values':[[55,66]]},
 {'type':'copy_range','sheet':'Dados','range':'A1:B2','target_sheet':'Dados','target_range':'L5','mode':'all','transpose':True},
 {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_sheet':'Dados','target_range':'L10','mode':'values','transpose':True},
 {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_sheet':'Dados','target_range':'L12','mode':'formulas','transpose':True},
 {'type':'copy_range','sheet':'Dados','range':'B1:B2','target_sheet':'Dados','target_range':'L14','mode':'formats','transpose':True},
 {'type':'set_values','sheet':'Dados','range':'E20','values':[[1],[2],[3]]},
 {'type':'insert_range','sheet':'Dados','range':'E21','shift':'down'},
 {'type':'set_values','sheet':'Dados','range':'V1','values':[['a','b','c']]},
 {'type':'insert_range','sheet':'Dados','range':'W1','shift':'right'},
 {'type':'set_values','sheet':'Dados','range':'A30','values':[['Incluir','Valor'],[0,10],[1,20],[1,30]]},
 {'type':'set_values','sheet':'Dados','range':'E31','values':[[111,'oculta'],[222,'visivel2'],[333,'visivel3']]},
 {'type':'format_range','sheet':'Dados','range':'E31:F31','font':{'bold':True},'fill_color':65535},
 {'type':'filter','sheet':'Dados','range':'A30:B33','action':'apply','field':1,'criteria1':'>0'},
 {'type':'set_formula','sheet':'Dados','range':'D31:D33','formula':'=ROW()'},
 {'type':'set_formula','sheet':'Dados','range':'J31:J33','formula':'=A31'},
 {'type':'set_formula','sheet':'Dados','range':'K31:K33','formula':'=ROW(A31:A33)','array_mode':'legacy'},
 {'type':'set_values','sheet':'Dados','range':'M31','values':[[1000000000000001,'1000000000000001']]},
 {'type':'copy_range','sheet':'Dados','range':'E31:F33','target_sheet':'Dados','target_range':'H31','mode':'all'}
],normal)
def transpose(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.Range('L5:M6').Value2==((10.0,20.0),(11.0,22.0))
 assert sheet.Range('L6').Formula2=='=$A$1+1' and sheet.Range('M6').Formula2=='=$A$2+2'
 assert sheet.Range('L10:M10').Value2==((11.0,22.0),) and not sheet.Range('L10:M10').HasFormula
 assert sheet.Range('L12:M12').Value2==((11.0,22.0),) and sheet.Range('L12:M12').HasFormula
 assert sheet.Range('L14:M14').Value2==((55.0,66.0),) and sheet.Range('L14:M14').Font.Bold
 assert sheet.Range('E20:E23').Value2==((1.0,),(None,),(2.0,),(3.0,))
 assert sheet.Range('V1:Y1').Value2==(('a',None,'b','c'),)
 assert sheet.Rows(31).Hidden is True and not sheet.Rows(32).Hidden and not sheet.Rows(33).Hidden
 assert sheet.Range('D31:D33').Formula2==(('=ROW()',),('=ROW()',),('=ROW()',)) and sheet.Range('D31:D33').Value2==((31.0,),(32.0,),(33.0,))
 assert sheet.Range('J31:J33').Formula2==(('=A31',),('=A32',),('=A33',)) and sheet.Range('J31:J33').Value2==((0.0,),(1.0,),(1.0,))
 assert sheet.Range('K31:K33').HasArray and sheet.Range('K31').CurrentArray.Address=='$K$31:$K$33'
 assert sheet.Range('K31:K33').Value2==((31.0,),(32.0,),(33.0,))
 assert sheet.Range('M31').Value2==1000000000000001 and sheet.Range('N31').Value2=='1000000000000001'
 assert sheet.Range('H31:I33').Value2==((111.0,'oculta'),(222.0,'visivel2'),(333.0,'visivel3'))
 assert sheet.Range('H31:I31').Font.Bold and sheet.Range('H31:I31').Interior.Color==65535
verify(transposed,transpose)
with SpreadsheetPackage(transposed) as package:
 integer=package.cells('Dados')['M31']
 assert integer['value']==1000000000000001 and Decimal(integer['raw_value'])==Decimal(1000000000000001)
restored=run('deleted',[
 {'type':'delete_range','sheet':'Dados','range':'E21','shift':'up'},
 {'type':'delete_range','sheet':'Dados','range':'W1','shift':'left'}
],transposed)
def deleted(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.Range('E20:E22').Value2==((1.0,),(2.0,),(3.0,))
 assert sheet.Range('V1:X1').Value2==(('a','b','c'),)
verify(restored,deleted)
''',
    "sheets_names_comments_protection_goal_seek": r'''
reference={'credential_profile':'synthetic','credential_entry_path':'tests/protection'}
original=source([
 {'type':'sheet','action':'add','name':'Aux'},
 {'type':'set_values','sheet':'Dados','range':'A1','values':[[42],[3],[2]]},
 {'type':'set_formula','sheet':'Dados','range':'B3','formula':'=A3^2'},
 {'type':'name','action':'set','name':'GlobalTarget','refers_to':'=Dados!$A$1'},
 {'type':'name','action':'set','sheet':'Dados','name':'LocalTarget','refers_to':'=Dados!$A$2'},
 {'type':'comment','sheet':'Dados','range':'D1','action':'set','text':'Nota sintetica'},
 {'type':'hyperlink','sheet':'Dados','range':'D2','action':'set','sub_address':"'Dados'!$A$1",'text':'Ir para entrada'},
 {'type':'sheet','action':'rename','sheet':'Aux','name':'Alterada'},
 {'type':'sheet','action':'copy','sheet':'Dados','name':'Copia','index':1},
 {'type':'sheet','action':'move','sheet':'Alterada','index':1},
 {'type':'sheet','action':'visibility','sheet':'Copia','visibility':'hidden'}
])
def initial(workbook,session):
 assert workbook.Sheets.Item(1).Name=='Alterada'
 assert workbook.Worksheets.Item('Copia').Visible==0 and workbook.Worksheets.Item('Copia').Range('A1').Value2==42
 assert workbook.Names.Item('GlobalTarget').RefersToRange.Value2==42
 sheet=workbook.Worksheets.Item('Dados')
 assert any(item.Name.endswith('!LocalTarget') and item.RefersToRange.Value2==3 for item in sheet.Names)
 assert sheet.Range('D1').Comment.Text()=='Nota sintetica'
 assert sheet.Range('D2').Hyperlinks.Count==1 and sheet.Range('D2').Hyperlinks.Item(1).SubAddress=="'Dados'!$A$1"
 assert sheet.Range('D2').Value2=='Ir para entrada'
verify(original,initial)
hidden=run('very-hidden',[{'type':'sheet','action':'visibility','sheet':'Copia','visibility':'very_hidden'},
 {'type':'protect','sheet':'Dados','action':'protect','password_ref':reference}],original,calculate=False)
def protected_sheet(workbook,session):
 assert workbook.Worksheets.Item('Copia').Visible==2
 assert workbook.Worksheets.Item('Dados').ProtectContents is True
verify(hidden,protected_sheet)
workbook_protected=run('protected-workbook',[
 {'type':'protect','sheet':'Dados','action':'unprotect','password_ref':reference},
 {'type':'protect','action':'protect','password_ref':reference}
],hidden,calculate=False)
def protected_structure(workbook,session):
 assert not workbook.Worksheets.Item('Dados').ProtectContents and workbook.ProtectStructure
verify(workbook_protected,protected_structure)
finished=run('cleaned-goal',[
 {'type':'protect','action':'unprotect','password_ref':reference},
 {'type':'sheet','action':'visibility','sheet':'Copia','visibility':'visible'},
 {'type':'sheet','action':'delete','sheet':'Copia'},
 {'type':'name','action':'delete','name':'GlobalTarget'},
 {'type':'name','action':'delete','sheet':'Dados','name':'LocalTarget'},
 {'type':'comment','sheet':'Dados','range':'D1','action':'delete'},
 {'type':'hyperlink','sheet':'Dados','range':'D2','action':'delete'},
 {'type':'goal_seek','sheet':'Dados','range':'B3','goal':49,'changing_range':'A3'}
],workbook_protected)
def complete(workbook,session):
 names=[item.Name for item in workbook.Names]
 assert not workbook.ProtectStructure and 'Copia' not in [item.Name for item in workbook.Sheets]
 assert not any('LocalTarget' in name or 'GlobalTarget' in name for name in names),names
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.Range('D1').Comment is None and sheet.Range('D2').Hyperlinks.Count==0
 assert abs(sheet.Range('A3').Value2-7)<.001 and abs(sheet.Range('B3').Value2-49)<.001
verify(finished,complete)
assert not local_elements(sheet_xml(finished),'sheetProtection')
''',
    "sort_filter_validation_conditional_format": r'''
locale_context=context()
with ExcelSession(locale_context) as locale_session:list_separator=str(parameterized_property(locale_session.application,'International',5))
stopped(locale_context)
list_options=['Sim','Nao']+(['Texto,com,virgula'] if list_separator!=',' else [])
original=source([
 {'type':'set_values','sheet':'Dados','range':'A1','values':[['Nome','Valor'],['C',30],['A',10],['B',20]]},
 {'type':'sort','sheet':'Dados','range':'A1:B4','keys':[{'column':1,'order':'ascending'}],'header':True},
 {'type':'filter','sheet':'Dados','range':'A1:B4','action':'apply','field':2,'criteria1':'>15'},
 {'type':'set_values','sheet':'Dados','range':'C2','values':[[50,'Sim',1,5,10,30],[-1,'Talvez',2,10,20,20],[99,'Nao',3,15,30,10]]},
 {'type':'set_values','sheet':'Dados','range':'D5','values':[['Texto,com,virgula']]},
 {'type':'validation','sheet':'Dados','range':'C2:C4','action':'set','validation_type':'custom','formula1':'=AND(C2>0,C2<100)','ignore_blank':False,'input_title':'Valor','input_message':'Entre 0 e 100','error_title':'Invalido','error_message':'Corrija o valor'},
 {'type':'validation','sheet':'Dados','range':'D2:D5','action':'set','validation_type':'list','list_values':list_options,'ignore_blank':False,'in_cell_dropdown':True},
 {'type':'conditional_format','sheet':'Dados','range':'F2:F4','action':'add','rule_type':'data_bar','color':255},
 {'type':'conditional_format','sheet':'Dados','range':'G2:G4','action':'add','rule_type':'color_scale'},
 {'type':'conditional_format','sheet':'Dados','range':'H2:H4','action':'add','rule_type':'icon_set'},
 {'type':'conditional_format','sheet':'Dados','range':'E2:E4','action':'add','rule_type':'expression','formula1':'=AND($E2>1,$E2<4)','color':65535,'priority':1,'stop_if_true':True}
])
def rules(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.Range('A2:B4').Value2==(('A',10.0),('B',20.0),('C',30.0))
 assert sheet.Rows(2).Hidden is True and not sheet.Rows(3).Hidden and not sheet.Rows(4).Hidden
 assert sheet.AutoFilter.Filters.Item(2).On and sheet.AutoFilter.Filters.Item(2).Criteria1=='>15'
 assert sheet.Range('C2').Validation.Type==7 and sheet.Range('C2').Validation.Value is True
 assert sheet.Range('C3').Validation.Value is False and sheet.Range('C4').Validation.Value is True
 assert sheet.Range('C2').Validation.InputTitle=='Valor' and sheet.Range('C2').Validation.ErrorMessage=='Corrija o valor'
 assert sheet.Range('D2').Validation.Type==3 and sheet.Range('D2').Validation.InCellDropdown
 assert sheet.Range('D2').Validation.Value is True and sheet.Range('D3').Validation.Value is False
 assert sheet.Range('D5').Validation.Value is (list_separator!=',')
 assert sheet.Range('E2:E4').FormatConditions.Count==1
 rule=sheet.Range('E2:E4').FormatConditions.Item(1)
 assert rule.Type==2 and rule.Interior.Color==65535 and rule.Priority==1 and rule.StopIfTrue
 assert sheet.Range('E3').DisplayFormat.Interior.Color==65535 and sheet.Range('E2').DisplayFormat.Interior.Color!=65535
 bar=sheet.Range('F2:F4').FormatConditions.Item(1);assert bar.Type==4 and bar.BarColor.Color==255
 scale=sheet.Range('G2:G4').FormatConditions.Item(1);assert scale.Type==3 and scale.ColorScaleCriteria.Count==3
 icons=sheet.Range('H2:H4').FormatConditions.Item(1);assert icons.Type==6
verify(original,rules)
xml=sheet_xml(original)
assert len(local_elements(xml,'dataValidation'))==2
assert {'expression','dataBar','colorScale','iconSet'}<={item.get('type') for item in local_elements(xml,'cfRule')}
assert any((item.text or '').lstrip('=')=='AND($E2>1,$E2<4)' for item in local_elements(xml,'formula'))
cleared=run('cleared-rules',[
 {'type':'filter','sheet':'Dados','range':'A1:B4','action':'clear'},
 {'type':'validation','sheet':'Dados','range':'C2:C4','action':'delete'},
 {'type':'validation','sheet':'Dados','range':'D2:D5','action':'delete'},
 {'type':'conditional_format','sheet':'Dados','range':'E2:H4','action':'delete'}
],original)
def no_rules(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert not sheet.FilterMode and all(not sheet.Rows(index).Hidden for index in (2,3,4))
 assert sheet.Range('E2:H4').FormatConditions.Count==0
 assert sheet.Range('C3').Value2==-1 and sheet.Range('D3').Value2=='Talvez'
verify(cleared,no_rules)
assert not local_elements(sheet_xml(cleared),'dataValidation') and not local_elements(sheet_xml(cleared),'cfRule')
''',
    "table_append_calculated_column_resize_convert_delete": r'''
original=source([
 {'type':'set_values','sheet':'Dados','range':'A1','values':[['Qty','Price','Total'],[2,10,None],[3,5,None]]},
 {'type':'table','action':'create','sheet':'Dados','name':'Itens','range':'A1:C3','style':'TableStyleMedium2','show_totals':True},
 {'type':'table','action':'column_formula','sheet':'Dados','name':'Itens','column':'Total','formula':'=[@Qty]*[@Price]'}
])
def initial(workbook,session):
 table=workbook.Worksheets.Item('Dados').ListObjects.Item('Itens')
 assert table.ListRows.Count==2 and table.ListColumns.Count==3 and table.ShowTotals
 assert table.ListColumns.Item('Total').DataBodyRange.Value2==((20.0,),(15.0,))
verify(original,initial)
with ZipFile(original) as archive:
 table_xml=ET.fromstring(archive.read(next(name for name in archive.namelist() if name.startswith('xl/tables/') and name.endswith('.xml'))))
 assert local_elements(table_xml,'tableStyleInfo')[0].get('name')=='TableStyleMedium2'
appended=run('appended',[{'type':'table','action':'append','sheet':'Dados','name':'Itens','values':[[4,7,None]]}],original)
def appended_rows(workbook,session):
 table=workbook.Worksheets.Item('Dados').ListObjects.Item('Itens')
 assert table.ListRows.Count==3 and table.ShowTotals
 assert table.ListColumns.Item('Qty').DataBodyRange.Value2==((2.0,),(3.0,),(4.0,))
 total=table.ListColumns.Item('Total').DataBodyRange
 assert total.HasFormula is True and total.Value2==((20.0,),(15.0,),(28.0,))
verify(appended,appended_rows)
overridden=run('appended-explicit-blank',[{'type':'table','action':'append','sheet':'Dados','name':'Itens','values':[[4,7,None]],'preserve_calculated_columns':False}],original)

def explicit_blank(workbook,session):
 table=workbook.Worksheets.Item('Dados').ListObjects.Item('Itens')
 assert table.ListRows.Count==3 and table.ListColumns.Item('Total').DataBodyRange.Cells.Item(3).Value2 is None
 assert not table.ListColumns.Item('Total').DataBodyRange.Cells.Item(3).HasFormula
verify(overridden,explicit_blank)
expanded=run('expanded',[{'type':'table','action':'resize','sheet':'Dados','name':'Itens','range':'A1:C6'}],appended)
def resized(workbook,session):
 table=workbook.Worksheets.Item('Dados').ListObjects.Item('Itens')
 assert table.ListRows.Count==4 and table.Range.Rows.Count==6 and table.TotalsRowRange.Row==6
 assert table.ListColumns.Item('Qty').DataBodyRange.Cells.Item(1).Value2==2
verify(expanded,resized)
converted=run('converted',[{'type':'table','action':'convert','sheet':'Dados','name':'Itens'}],expanded)
def normal_range(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.ListObjects.Count==0 and sheet.Range('A1:C1').Value2==(('Qty','Price','Total'),)
 assert sheet.Range('C2:C4').Value2==((20.0,),(15.0,),(28.0,))
verify(converted,normal_range)
deleted=run('deleted-table',[{'type':'table','action':'delete','sheet':'Dados','name':'Itens'}],expanded)
def removed(workbook,session):
 sheet=workbook.Worksheets.Item('Dados')
 assert sheet.ListObjects.Count==0 and all(value is None for row in sheet.Range('A1:C6').Value2 for value in row)
verify(deleted,removed)
''',
}


@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_real_excel_operation_contracts(tmp_path, scenario):
    """Valida o efeito solicitado no Excel salvo/reaberto e os artefatos OOXML."""
    temporary = tmp_path / "system"
    temporary.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONUTF8": "1", "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"), "TMP": str(temporary), "TEMP": str(temporary), "TMPDIR": str(temporary)}
    result = subprocess.run([sys.executable, "-B", "-c", PRELUDE + SCENARIOS[scenario], str(tmp_path)], env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
