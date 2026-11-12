"""Regressão de workers reais com launchers de ambientes virtuais Windows.

Os testes criam venv e documentos sintéticos em ``tmp_path``. A leitura e a
supervisão independem de Excel ou MCP instalados; o teste nativo é opt-in.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "src"


RUNNER = r'''
from pathlib import Path
from zipfile import ZipFile
import json,os,sys,time,subprocess
from onmyoji_daemon.mcp.document_processing.jobs import _write_json
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.jobs import ExcelJobs
from onmyoji_daemon.mcp.excel.config.configuration import DEFAULTS,profile_defaults
from onmyoji_daemon.mcp.excel.component.process import process_identity,same_process

base=Path(sys.argv[1]); mode=sys.argv[2]
root=base/'instance'; work=base/'workspace'
(root/'configs').mkdir(parents=True); work.mkdir()
(root/'configs/onmyoji-system.toml').write_text('[codex]\nproject_directory='+json.dumps(work.as_posix())+'\n',encoding='utf-8')
profile=profile_defaults(root); Path(profile['work_directory']).mkdir(parents=True)
defaults={**DEFAULTS,'timeout_seconds':30}
jobs=ExcelJobs(root)
observed=[]

def terminal(identifier):
 deadline=time.monotonic()+25
 while time.monotonic()<deadline:
  state=jobs.get('default',identifier)
  if state['status'] not in {'queued','running'}:return state
  time.sleep(.01)
 raise AssertionError('Worker venv não concluiu no prazo.')

def handshake(identifier):
 directory=jobs._directories[identifier]
 activation=json.loads((directory/'activation.json').read_text())
 ready=json.loads((directory/'worker-ready.json').read_text())
 accepted=json.loads((directory/'worker-accepted.json').read_text())
 assert same_process(activation['launcher_identity'],ready['launcher_identity'])
 assert same_process(ready['worker_identity'],accepted['worker_identity'])
 assert jobs._records[identifier]['worker_pid']==ready['worker_identity']['pid']
 if os.name=='nt':assert activation['launcher_identity']['pid']!=ready['worker_identity']['pid']
 assert not (directory/'ownership.json').exists(), 'Leitura direta ativou Excel.'
 observed.extend([activation['launcher_identity'],ready['worker_identity']])
 return ready

try:
 if mode=='read':
  xlsx=work/'source.xlsx'
  with ZipFile(xlsx,'w') as package:
   package.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>')
   package.writestr('_rels/.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
   package.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Dados" sheetId="1" r:id="rId1"/></sheets></workbook>')
   package.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
   package.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1"><v>12</v></c><c r="B1"><f>A1*2</f><v>24</v></c></row></sheetData></worksheet>')
  csv=work/'source.csv'; csv.write_bytes(b'Codigo,Texto\r\n00123,=literal\r\n')
  originals={path:path.read_bytes() for path in (xlsx,csv)}
  for source in (xlsx,csv):
   started=jobs.submit('default',profile,defaults,'read_range',{'input_path':str(source),'backend':'direct','sheet':'Dados','range':'A1:B2' if source==csv else 'A1:B1'},source.suffix)
   state=terminal(started['job_id']); assert state['status']=='succeeded',state
   result=jobs.result('default',started['job_id'])['result']
   handshake(started['job_id'])
   if source==xlsx:
    assert result['cells'][0]['value']==12
    assert result['cells'][1]['value'] is None and result['cells'][1]['cached_value']==24
   else:
    assert result['cells'][2]['value']=='00123' and result['cells'][3]['value']=='=literal'
   assert source.read_bytes()==originals[source] and result['artifacts']==[]
 elif mode.startswith(('cancel-','close-')):
  action,phase=mode.split('-',1)
  source=work/'large.csv'
  with source.open('wb') as stream:
   stream.write(b'Codigo\r\n')
   for _ in range(750):stream.write(b'00123\r\n'*1000)
  if phase=='before':jobs._accept_worker=lambda identifier:None
  started=jobs.submit('default',profile,defaults,'inspect',{'input_path':str(source),'backend':'direct'},mode)
  identifier=started['job_id']; directory=jobs._directories[identifier]
  expected=directory/('worker-ready.json' if phase=='before' else 'worker-accepted.json')
  deadline=time.monotonic()+10
  while not expected.exists() and time.monotonic()<deadline:
   assert jobs.get('default',identifier)['status'] in {'queued','running'},jobs.get('default',identifier)
   time.sleep(.005)
  assert expected.is_file(),'O worker real não chegou à etapa esperada.'
  ready=json.loads((directory/'worker-ready.json').read_text())
  launcher=json.loads((directory/'activation.json').read_text())['launcher_identity']
  worker=ready['worker_identity']
  assert same_process(worker,process_identity(worker['pid'])),'O teste precisa interromper um worker real ainda vivo.'
  if phase=='before':assert not (directory/'worker-accepted.json').exists()
  else:handshake(identifier)
  observed.extend([launcher,worker])
  if action=='cancel':
   state=jobs.cancel('default',identifier); assert state['status']=='cancelled',state
  else:
   jobs.close()
   state=json.loads((directory/'state.json').read_text()); assert state['status']=='interrupted',state
  deadline=time.monotonic()+5
  while any(same_process(identity,process_identity(identity['pid'])) for identity in (launcher,worker)) and time.monotonic()<deadline:time.sleep(.025)
  assert not any(same_process(identity,process_identity(identity['pid'])) for identity in (launcher,worker)),'Launcher/worker sobreviveram ao cancelamento.'
  assert not (directory/'ownership.json').exists() and not (directory/'result.json').exists()
 elif mode=='forged':
  launcher=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),'-B','-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
  try:
   with jobs._lock:
    started=jobs.submit('default',profile,defaults,'inspect',{},'forged-ready')
    identifier=started['job_id'];directory=jobs._directories[identifier]
    launcher_identity=process_identity(launcher.pid)
    jobs._records[identifier]['launcher_identity']=launcher_identity
    _write_json(directory/'worker-ready.json',{'launcher_identity':launcher_identity,'worker_identity':process_identity(os.getpid())})
    try:jobs._accept_worker(identifier)
    except ExcelExceptions as error:assert error.code=='process_ownership_unverified',error.code
    else:raise AssertionError('Um processo não descendente foi aceito como worker.')
    assert not (directory/'worker-accepted.json').exists()
    assert same_process(launcher_identity,process_identity(launcher.pid)),'A rejeição não pode encerrar um processo só por uma alegação em arquivo.'
    jobs.cancel('default',identifier)
  finally:
   if launcher.poll() is None:launcher.kill()
   launcher.wait(timeout=5)
 elif mode=='native':
  import importlib.util
  assert importlib.util.find_spec('win32com') is not None,'Pywin32 ausente do venv system-site-packages.'
  output=work/'native.xlsx'
  started=jobs.submit('default',profile,defaults,'create',{'output_path':str(output),'operations':[
   {'type':'sheet','action':'add','name':'Dados'},
   {'type':'set_values','sheet':'Dados','range':'A1','values':[[2],[3]]},
   {'type':'set_formula','sheet':'Dados','range':'B1','formula':'=SUM(A1:A2)'}
  ],'calculation':{'mode':'full_rebuild'}},'native-venv')
  state=terminal(started['job_id']); assert state['status']=='succeeded',state
  result=jobs.result('default',started['job_id'])['result']
  assert result['calculation']['status']=='completed' and output.is_file()
  owner=json.loads((jobs._directories[started['job_id']]/'ownership.json').read_text())
  assert owner['worker_pid']==jobs._records[started['job_id']]['worker_pid']
  observed.append(owner)
  read=jobs.submit('default',profile,defaults,'read_range',{'input_path':str(output),'backend':'direct','sheet':'Dados','range':'B1'},'native-venv-cache')
  state=terminal(read['job_id']);assert state['status']=='succeeded',state
  cell=jobs.result('default',read['job_id'])['result']['cells'][0]
  assert cell['formula']=='=SUM(A1:A2)' and cell['cached_value']==5 and cell['value'] is None,cell
  handshake(read['job_id'])
 else:raise AssertionError(mode)
finally:
 jobs.close()
deadline=time.monotonic()+5
while any(same_process(identity,process_identity(identity['pid'])) for identity in observed) and time.monotonic()<deadline:time.sleep(.025)
assert not any(same_process(identity,process_identity(identity['pid'])) for identity in observed),'Processo próprio sobreviveu ao encerramento.'
print(json.dumps({'mode':mode,'venv':sys.prefix!=sys.base_prefix,'executable':sys.executable,'verified':True}))
'''


@pytest.fixture
def venv_runner(tmp_path):
    environment_directory = tmp_path / "venv"
    venv.EnvBuilder(system_site_packages=True, with_pip=False, symlinks=False).create(environment_directory)
    executable = environment_directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runner = tmp_path / "worker_regression.py"
    runner.write_text(RUNNER, encoding="utf-8")
    temporary = tmp_path / "system"
    temporary.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(SOURCE), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
                   "PYTHONPYCACHEPREFIX": str(tmp_path / "pycache"), "TMP": str(temporary), "TEMP": str(temporary), "TMPDIR": str(temporary)}
    for key in ("PYTHONHOME", "PYTHONUSERBASE", "PYTHONNOUSERSITE"):
        environment.pop(key, None)
    return executable, runner, environment


def run_fixture(tmp_path, venv_runner, mode):
    executable, runner, environment = venv_runner
    base = tmp_path / "execution"
    base.mkdir()
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
    result = subprocess.run([str(executable), "-B", str(runner), str(base), mode], cwd=tmp_path,
                            env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=50, **options)
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["venv"] is True and evidence["verified"] is True
    return evidence


def test_real_venv_worker_handshake_reads_ooxml_and_csv(tmp_path, venv_runner):
    run_fixture(tmp_path, venv_runner, "read")


def test_venv_supervisor_rejects_ready_identity_from_unrelated_python_process(tmp_path, venv_runner):
    run_fixture(tmp_path, venv_runner, "forged")


@pytest.mark.parametrize("action,phase", [("cancel", "before"), ("close", "before"), ("cancel", "after"), ("close", "after")])
def test_real_venv_worker_and_launcher_are_stopped_on_cancel_or_close(tmp_path, venv_runner, action, phase):
    run_fixture(tmp_path, venv_runner, action + "-" + phase)


@pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Exige Windows/Excel e ONMYOJI_TEST_EXCEL=1.")
def test_real_venv_worker_creates_and_calculates_hidden_excel(tmp_path, venv_runner):
    run_fixture(tmp_path, venv_runner, "native")
