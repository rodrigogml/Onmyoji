"""Homologação opt-in em Excel real, com worker e timeout externos ao COM."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt" or os.environ.get("ONMYOJI_TEST_EXCEL") != "1", reason="Exige Windows/Excel e ONMYOJI_TEST_EXCEL=1.")


NATIVE_SCRIPT = r'''
from pathlib import Path
import json
import sys
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.excel.component.process import process_identity
from onmyoji_daemon.mcp.excel.exception import ExcelExceptions
from onmyoji_daemon.mcp.excel.service.native import execute_native

base = Path(sys.argv[1])
root, work = base / "instance", base / "workspace"
(root / "configs").mkdir(parents=True)
work.mkdir()
(root / "configs/onmyoji-system.toml").write_text('[codex]\nproject_directory = ' + json.dumps(work.as_posix()) + '\n', encoding="utf-8")
profile = {"enabled": True, "description": "Homologacao", "readable_roots": [str(work)], "writable_roots": [str(work)], "work_directory": str(work / "jobs"), "allow_write": True}
defaults = {"max_cells": 10000, "timeout_seconds": 45}

def run(operation, args, number):
    context = DocumentContext(root, "excel", "default", profile, defaults, work / "jobs" / str(number))
    result = execute_native(operation, args, context)
    result["artifacts"] = context.publish()
    identity = json.loads((context.job_dir / "ownership.json").read_text())
    import time
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            if process_identity(identity["pid"]) is None:
                break
        except ExcelExceptions:
            pass
        time.sleep(.05)
    assert process_identity(identity["pid"]) is None, "Excel proprio permaneceu ativo"
    return result

probe = run("native_probe", {}, 1)
path = work / "sintetica.xlsx"
created = run("create", {"output_path": str(path), "operations": [
    {"type": "sheet", "action": "add", "name": "Dados"},
    {"type": "sheet", "action": "add", "name": "Outra"},
    {"type": "name", "action": "set", "name": "EnderecoOutra", "refers_to": "=Outra!$A$1"},
    {"type": "set_values", "sheet": "Dados", "range": "A1", "values": [["Produto", "Valor", "Texto"], ["A", 10, "=1+1"], ["B", 20, "00123"]]},
    {"type": "table", "action": "create", "sheet": "Dados", "name": "Vendas", "range": "A1:B3", "style": "TableStyleMedium2"},
    {"type": "set_formula", "sheet": "Dados", "range": "E1", "formula": "=SUM(Vendas[Valor])"},
    {"type": "set_formula", "sheet": "Dados", "range": "E3", "formula": "=SEQUENCE(2,2)"},
    {"type": "set_values", "sheet": "Dados", "range": "A6", "values": [[-2146826281]]},
    {"type": "set_formula", "sheet": "Dados", "range": "C6", "formula": "=1/0"},
    {"type": "validation", "action": "set", "sheet": "Dados", "range": "B2:B3", "validation_type": "whole", "operator": 7, "formula1": 0},
    {"type": "chart", "action": "create", "sheet": "Dados", "name": "Grafico", "range": "A1:B3", "chart_type": 51, "title": "Vendas"},
    {"type": "pivot", "action": "create", "sheet": "Dados", "name": "ResumoVendas", "source_sheet": "Dados", "source_range": "A1:B3", "destination": "H1", "fields": [{"name": "Produto", "orientation": "row"}, {"name": "Valor", "orientation": "data", "function": "sum"}]}
], "calculation": {"mode": "full_rebuild"}, "validation": {"reject_new_formula_errors": False}}, 2)
read = run("read_range", {"input_path": str(path), "sheet": "Dados", "range": "A1:F6", "limit": 100, "fields": ["value", "cached_value", "formula", "type", "error"]}, 3)
cells = {cell["address"]: cell for cell in read["cells"]}
assert cells["E1"]["cached_value"] == 30 and cells["E1"]["value"] is None
assert cells["E3"]["cached_value"] == 1 and cells["F4"]["value"] == 4
assert cells["C2"]["value"] == "=1+1" and cells["C2"]["formula"] is None
assert cells["C3"]["value"] == "00123"
assert cells["A6"]["value"] == -2146826281 and cells["A6"]["type"] == "number"
assert cells["C6"]["cached_value"] == "#DIV/0!" and cells["C6"]["error"] == "#DIV/0!" and cells["C6"]["type"] == "formula"
try:
    run("read_range", {"input_path":str(path),"sheet":"Dados","range":"EnderecoOutra"}, "foreign-range")
    raise AssertionError("Nome definido em outra aba deveria ser recusado")
except ExcelExceptions as error:
    assert error.code in {"range_context_mismatch", "native_operation_failed"}
inventory = run("inspect", {"input_path": str(path)}, 4)
sheet = next(item for item in inventory["sheets"] if item["name"] == "Dados")
assert sheet["tables"] == ["Vendas"] and sheet["pivots"] == ["ResumoVendas"] and sheet["charts"] == ["Grafico"]
analysis = run("analyze_formulas", {"input_path": str(path), "sheet": "Dados", "range": "E1", "limit":100}, "analysis")
assert analysis["total"] == 1 and analysis["formulas"][0]["cached_value"] == 30
validation = run("validate", {"input_path": str(path), "checks": ["data"], "rules": [{"type": "range", "sheet": "Dados", "range": "B2:B3", "maximum":15}]}, "validation")
assert validation["status"] == "failed" and any(issue.get("address") == "B3" for issue in validation["issues"])
comparison = run("compare", {"input_path": str(path), "other_path":str(path)}, "comparison")
assert comparison["equal_in_scope"] is True
original = path.read_bytes()
try:
    run("apply_changes", {"input_path":str(path),"output_path":str(work / "invalid.xlsx"),"operations":[{"type":"set_formula","sheet":"Dados","range":"D6","formula":"=1/0"}],"calculation":{"mode":"full_rebuild"}}, "rejected")
    raise AssertionError("Erro novo deveria impedir publicacao")
except ExcelExceptions as error:
    assert error.code == "formula_validation_failed"
assert not (work / "invalid.xlsx").exists() and path.read_bytes() == original
cleared_path = work / "cleared.xlsx"
cleared = run("apply_changes", {"input_path":str(path),"output_path":str(cleared_path),"operations":[{"type":"clear","sheet":"Dados","range":"A6:C6","what":"contents"}]}, "clear-last")
assert cleared["persisted_cache"]["native_objects_preserved"] is True
cleared_cells = run("read_range", {"input_path":str(cleared_path),"sheet":"Dados","range":"A6:C6","fields":["value","formula"]}, "read-cleared")["cells"]
assert all(cell["value"] is None and cell["formula"] is None for cell in cleared_cells)
macro_path = work / "macros.xlsm"
run("create", {"output_path": str(macro_path)}, 5)
profile["allow_vba_source"] = True
if probe["vba_project_access"]:
    edited_path = work / "macros-editadas.xlsm"
    source = "Public Function Somar(ByVal a As Double, ByVal b As Double) As Double\r\nSomar = a + b\r\nEnd Function"
    edited = run("edit_vba", {"input_path": str(macro_path), "output_path": str(edited_path), "action": "set", "module": "Calculos", "source": source}, 6)
    obtained = run("get_vba_source", {"input_path": str(edited_path), "module": "Calculos"}, 7)
    assert "Somar = a + b" in obtained["modules"][0]["source"]
    profile.update(allow_macros=True, trusted_content=True)
    executed = run("run_macro", {"input_path": str(edited_path), "output_path": str(work / "macros-executadas.xlsm"), "procedure": "Calculos.Somar", "args": [2,3]}, 8)
    assert executed["macros"]["result"] == 5
    udf_path = work / "udf.xlsm"
    macro_sheet = run("inspect", {"input_path": str(edited_path)}, 9)["sheets"][0]["name"]
    run("apply_changes", {"input_path": str(edited_path), "output_path": str(udf_path), "operations": [{"type": "set_formula", "sheet": macro_sheet, "range": "A1", "formula": "=Somar(3,4)"}], "calculation": {"mode": "full_rebuild", "enable_vba_udfs": True}}, 10)
    udf = run("read_range", {"input_path":str(udf_path),"sheet":macro_sheet,"range":"A1","fields":["cached_value"]}, 11)
    assert udf["cells"][0]["cached_value"] == 7
    vba_status = "edit_and_run_verified"
else:
    try:
        run("get_vba_source", {"input_path": str(macro_path)}, 6)
        raise AssertionError("Trust access bloqueado deveria impedir leitura VBIDE")
    except ExcelExceptions as error:
        assert error.code == "vba_access_denied"
    vba_status = "trust_access_denied_verified"
print(json.dumps({"probe": probe, "created": created, "read": read, "inventory": inventory, "vba_status": vba_status}))
'''


def test_real_excel_hidden_calculation_tables_pivots_charts_and_literal_text(tmp_path):
    """Confere cache salvo, tipos e objetos usando somente documentos sintéticos."""
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    temporary = tmp_path / "system"
    temporary.mkdir()
    environment.update(TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    result = subprocess.run([sys.executable, "-B", "-c", NATIVE_SCRIPT, str(tmp_path)], env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence["probe"]["visible"] is False
    assert evidence["probe"]["formula2"] is True
    assert evidence["vba_status"] in {"edit_and_run_verified", "trust_access_denied_verified"}


def test_excel_job_object_closes_owned_excel_when_worker_is_killed(tmp_path):
    """A morte abrupta do worker não deve deixar sua instância Excel órfã."""
    import time
    from onmyoji_daemon.mcp.excel.component.process import process_identity
    from onmyoji_daemon.mcp.excel.exception import ExcelExceptions

    setup = NATIVE_SCRIPT[:NATIVE_SCRIPT.index("probe = run")]
    script = setup + '''
from onmyoji_daemon.mcp.excel.component.native import ExcelSession
import time
context = DocumentContext(root,"excel","default",profile,defaults,work / "jobs" / "blocked")
with ExcelSession(context):
    time.sleep(60)
'''
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    process = subprocess.Popen([sys.executable, "-B", "-c", script, str(tmp_path)], env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        ownership = tmp_path / "workspace/jobs/blocked/ownership.json"
        deadline = time.monotonic() + 15
        while not ownership.is_file() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(.05)
        assert ownership.is_file(), process.communicate(timeout=5)[1].decode(errors="replace") if process.poll() is not None else "Worker não iniciou a instância Excel"
        identity = json.loads(ownership.read_text(encoding="utf-8"))
        # Ownership é gravado antes de anexar ao Job Object; aguardar o probe
        # operacional evita matar o worker na janela anterior à contenção.
        time.sleep(.5)
        assert process_identity(identity["pid"]) is not None
        process.kill()
        process.communicate(timeout=5)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if process_identity(identity["pid"]) is None:
                    break
            except ExcelExceptions:
                pass
            time.sleep(.05)
        assert process_identity(identity["pid"]) is None
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)


def test_native_session_does_not_attach_to_preexisting_hidden_excel(tmp_path):
    """Confere isolamento com instância prévia sem mostrar janelas ao usuário."""
    setup = NATIVE_SCRIPT[:NATIVE_SCRIPT.index("probe = run")]
    baseline_code = r'''
from pathlib import Path
import sys
from onmyoji_daemon.mcp.document_processing.paths import DocumentContext
from onmyoji_daemon.mcp.excel.component.native import ExcelSession
root, work = Path(sys.argv[1]), Path(sys.argv[2])
profile = {"readable_roots": [str(work)], "writable_roots": [str(work)], "work_directory": str(work / "jobs")}
context = DocumentContext(root,"excel","default",profile,{"timeout_seconds":40},work / "jobs/baseline")
with ExcelSession(context) as baseline:
    original = baseline.identity.copy()
    baseline.bootstrap.Worksheets(1).Range("A1").Value2 = 12345
    baseline.application.Calculation = -4105
    # Esta instância representa Excel externo ao executor MCP e portanto
    # não participa da reserva do executor durante a operação sob teste.
    baseline._kernel.ReleaseMutex(baseline._mutex)
    print("ready", flush=True)
    sys.stdin.readline()
    assert baseline.identity == original
    assert baseline.bootstrap.Worksheets(1).Range("A1").Value2 == 12345
    assert baseline.application.Calculation == -4105
    assert baseline.application.Visible is False
    print("preserved", flush=True)
'''
    script = setup + '\nBASELINE_CODE = ' + repr(baseline_code) + r'''
import subprocess
baseline = subprocess.Popen([sys.executable,"-B","-c",BASELINE_CODE,str(root),str(work)],
    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
    creationflags=subprocess.CREATE_NO_WINDOW)
try:
    assert baseline.stdout.readline().strip() == "ready", "Baseline Excel não iniciou"
    run("native_probe", {}, "second")
    output, errors = baseline.communicate("inspect\n", timeout=10)
    assert baseline.returncode == 0, errors
    assert "preserved" in output
finally:
    if baseline.poll() is None:
        baseline.kill()
        baseline.communicate(timeout=5)
print("isolated")
'''
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"), "PYTHONDONTWRITEBYTECODE": "1",
                   "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], env=environment,
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert "isolated" in result.stdout


def test_native_refresh_calculates_pivot_sources_and_final_dependents(tmp_path):
    """Uma origem dirty não pode atualizar o pivô com cache anterior ao cálculo."""
    setup = NATIVE_SCRIPT[:NATIVE_SCRIPT.index("probe = run")]
    script = setup + r'''
profile["allow_refresh"] = True
data = work / "data.xlsx"
run("create", {"output_path":str(data),"operations":[
 {"type":"sheet","action":"add","name":"Dados"},
 {"type":"set_values","sheet":"Dados","range":"A1","values":[["Base","Valor"],[10,None]]},
 {"type":"set_formula","sheet":"Dados","range":"B2","formula":"=A2*2"}
],"calculation":{"mode":"full_rebuild"}}, "seed")
pivot_file = work / "pivot.xlsx"
run("apply_changes", {"input_path":str(data),"output_path":str(pivot_file),"operations":[
 {"type":"pivot","action":"create","sheet":"Dados","name":"Resumo","source_sheet":"Dados","source_range":"A1:B2","destination":"E1","fields":[{"name":"Base","orientation":"row"},{"name":"Valor","orientation":"data","function":"sum"}]},
 {"type":"set_formula","sheet":"Dados","range":"J1","formula":"=F2"}
],"calculation":{"mode":"full_rebuild"}}, "pivot")
dirty = work / "dirty.xlsx"
run("apply_changes", {"input_path":str(pivot_file),"output_path":str(dirty),"operations":[
 {"type":"set_values","sheet":"Dados","range":"A2","values":[[30]]}
],"calculation":{"mode":"none"}}, "dirty")
fresh = work / "fresh.xlsx"
refreshed = run("refresh", {"input_path":str(dirty),"output_path":str(fresh),"calculation":{"mode":"full_rebuild"}}, "refresh")
values = run("read_range", {"input_path":str(fresh),"sheet":"Dados","range":"A1:J3","fields":["value","cached_value"]}, "read")
cells = {item["address"]:item for item in values["cells"]}
assert cells["B2"]["cached_value"] == 60, cells
assert cells["F2"]["value"] == 60, cells
assert cells["J1"]["cached_value"] == 60, cells
assert refreshed["calculation"]["before_pivots"]["status"] == "completed"
assert refreshed["pivots"][0]["status"] == "completed"
blocked = work / "blocked.xlsx"
try:
 run("refresh", {"input_path":str(dirty),"output_path":str(blocked),"calculation":{"mode":"none"}}, "blocked")
 raise AssertionError("refresh sem calcular fonte com formula deveria recusar sucesso")
except ExcelExceptions as error:
 assert error.code == "refresh_unverified", error.code
assert not blocked.exists()
pending = run("refresh", {"input_path":str(dirty),"output_path":str(work / "pending.xlsx"),"require_verified":False,"calculation":{"mode":"none"}}, "pending")
assert pending["pivots"][0]["status"] == "unverified" and pending["pivots"][0]["attempted"] is False
print(json.dumps({"source":60,"pivot":60,"dependent":60,"pending":pending["pivots"]}))
'''
    environment = {**os.environ, "PYTHONPATH":str(Path(__file__).resolve().parents[1] / "src"),"PYTHONDONTWRITEBYTECODE":"1",
                   "TEMP":str(tmp_path),"TMP":str(tmp_path)}
    result = subprocess.run([sys.executable,"-B","-c",script,str(tmp_path)], env=environment,
        stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["pivot"] == 60
