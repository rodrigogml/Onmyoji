"""Instância Excel exclusiva em STA, oculta e limitada ao processo executor."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import gc
import json
import os
from pathlib import Path
import threading
import time

from ..exception import ExcelExceptions, require


LOCAL_FUNCTION_PROBES = {
    "LET": "=LET(x,2,x+1)",
    "LAMBDA": "=LAMBDA(x,x+1)(2)",
    "XLOOKUP": "=XLOOKUP(2,A10:A13,B10:B13)",
    "FILTER": "=FILTER(B10:B13,A10:A13=2)",
    "UNIQUE": "=UNIQUE(A10:A13)",
    "SEQUENCE": "=SEQUENCE(2,2)",
    "TEXTSPLIT": '=TEXTSPLIT("a,b",",")',
    "VSTACK": "=VSTACK(1,2)",
    "HSTACK": "=HSTACK(1,2)",
    "GROUPBY": "=GROUPBY(C10:C13,B10:B13,SUM)",
    "PIVOTBY": "=PIVOTBY(C10:C13,A10:A13,B10:B13,SUM)",
    "REGEXTEST": '=REGEXTEST("abc","^a")',
    "TRIMRANGE": "=TRIMRANGE(A10:C14)",
}


class ExcelSession:
    """Mantém o Excel nativo isolado do Excel já aberto pelo usuário.

    A criação exige sessão Windows interativa e PID novo comprovado. Conteúdo
    não confiável usa macros/eventos desabilitados. Um Job Object encerra o
    Excel próprio se o worker morrer; detectar janela visível aborta o job.
    A classe não é sandbox para macros nem garante APIs sem diálogo.
    """

    def __init__(self, context, *, allow_macros: bool = False):
        self.context = context
        self.allow_macros = allow_macros
        self.application = None
        self.bootstrap = None
        self.identity = None
        self._mutex = None
        self._job = None
        self._com_initialized = False
        self._stop = threading.Event()
        self._visible = threading.Event()
        self._monitor = None
        self.deadline = time.monotonic() + context.defaults.get("timeout_seconds", 1800)

    def __enter__(self):
        require(os.name == "nt", "excel_session_unavailable", "O backend nativo exige Windows com Excel desktop.")
        require(not self.allow_macros or self.context.profile.get("allow_macros") is True and self.context.profile.get("trusted_content") is True,
                "macro_execution_denied", "O perfil não autoriza executar VBA de conteúdo confiável.")
        try:
            import pythoncom
            import win32api
            import win32con
            import win32process
            import win32security
            import win32com
            generated = self.context.job_dir / "cache" / "gen_py"
            generated.mkdir(parents=True, exist_ok=True)
            win32com.__gen_path__ = str(generated)
            import win32com.client.dynamic

            self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            session = wintypes.DWORD()
            require(bool(self._kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))) and session.value != 0,
                    "excel_session_unavailable", "Execute o MCP na sessão Windows do usuário, fora de Session 0.")
            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            try:
                sid = win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
            finally:
                token.Close()
            self._kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            self._kernel.CreateMutexW.restype = wintypes.HANDLE
            self._kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self._kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            self._kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self._mutex = self._kernel.CreateMutexW(None, False, "Local\\Onmyoji.Excel." + sid + "." + str(session.value))
            require(bool(self._mutex), "excel_session_unavailable", "Não foi possível reservar o executor Excel.")
            while True:
                waited = self._kernel.WaitForSingleObject(self._mutex, 100)
                if waited in (0, 0x80):
                    break
                require(waited == 0x102 and time.monotonic() < self.deadline, "deadline_exceeded", "O executor Excel permaneceu ocupado além do prazo.")
            before = set(win32process.EnumProcesses())
            pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
            self._com_initialized = True
            try:
                dispatch = pythoncom.CoCreateInstanceEx("Excel.Application", None,
                    pythoncom.CLSCTX_LOCAL_SERVER, None, (pythoncom.IID_IDispatch,))[0]
                self.application = win32com.client.dynamic.Dispatch(dispatch)
            except Exception as error:
                raise ExcelExceptions("excel_not_installed", "Não foi possível iniciar o Excel desktop; confira instalação e ativação.") from error
            hwnd = int(self.application.Hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            require(pid not in before, "process_ownership_unverified", "O Excel retornou uma instância preexistente; a operação foi recusada.")
            from .process import process_identity
            identity = process_identity(pid)
            require(identity is not None and Path(identity["executable"]).name.lower() == "excel.exe" and identity["session_id"] == session.value,
                    "process_ownership_unverified", "Não foi possível comprovar a identidade da instância Excel.")
            self.identity = {**identity, "hwnd": hwnd, "worker_pid": os.getpid()}
            temporary = self.context.job_dir / "ownership.json.tmp"
            temporary.write_text(json.dumps(self.identity), encoding="utf-8")
            temporary.replace(self.context.job_dir / "ownership.json")
            self._attach_job(pid)
            app = self.application
            app.Visible = False
            app.DisplayAlerts = False
            app.ScreenUpdating = False
            app.EnableEvents = False
            app.AskToUpdateLinks = False
            app.AutomationSecurity = 1 if self.allow_macros else 3
            self._monitor = threading.Thread(target=self._watch_visibility, name="excel-visibility", daemon=True)
            self._monitor.start()
            self.bootstrap = app.Workbooks.Add(-4167)
            app.Calculation = -4135
            app.CalculateBeforeSave = False
            self.assert_hidden()
            return self
        except ImportError as error:
            self.close()
            raise ExcelExceptions("dependency_missing", "Instale pywin32 no runtime Python do MCP.") from error
        except Exception:
            self.close()
            raise

    def _attach_job(self, pid: int) -> None:
        """Contém somente o PID cuja criação e identidade já foram comprovadas."""
        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class Limits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", Counters), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        kernel = self._kernel
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._job = kernel.CreateJobObjectW(None, None)
        require(bool(self._job), "process_ownership_unverified", "Não foi possível conter o processo Excel.")
        limits = Limits()
        limits.BasicLimitInformation.LimitFlags = 0x2000 | 0x200
        limits.JobMemoryLimit = self.context.defaults.get("max_memory_bytes", 2147483648)
        process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)
        try:
            require(bool(process) and bool(kernel.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
                    and bool(kernel.AssignProcessToJobObject(self._job, process)),
                    "process_ownership_unverified", "Não foi possível anexar o Excel à contenção do worker.")
        finally:
            if process:
                kernel.CloseHandle(process)

    def _watch_visibility(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes = [wintypes.HWND]

        def inspect(hwnd, _):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if self.identity and pid.value == self.identity["pid"] and user.IsWindowVisible(hwnd):
                self._visible.set()
            return True

        callback = callback_type(inspect)
        while not self._stop.wait(0.05):
            user.EnumWindows(callback, 0)
            if self._visible.is_set():
                from .process import terminate_owned
                terminate_owned(self.identity)
                return

    def assert_hidden(self) -> None:
        require(not self._visible.is_set(), "ui_required", "O Excel solicitou interface visual; o processamento oculto foi interrompido.")
        require(time.monotonic() < self.deadline, "deadline_exceeded", "O processamento Excel excedeu o prazo configurado.")
        if self.application is not None:
            require(not self.application.Visible, "ui_required", "A operação tentou tornar o Excel visível.")

    def open(self, path: Path, password: str | None = None, *, template_mode: str = "edit"):
        """Abre cópia sem eventos, links, prompts de senha ou inclusão nos recentes."""
        self.assert_hidden()
        self.application.AutomationSecurity = 1 if self.allow_macros else 3
        self.application.EnableEvents = False
        self.application.DisplayAlerts = False
        self.application.AskToUpdateLinks = False
        self.application.Calculation = -4135
        self.application.CalculateBeforeSave = False
        import pythoncom
        missing = pythoncom.Missing
        editable = template_mode != "instantiate" if path.suffix.lower() in {".xltx", ".xltm", ".xlt"} else missing
        workbook = self.application.Workbooks.Open(str(path), 0, False, missing, password or "", "", True,
            missing, missing, editable, False, missing, False, False, 0)
        self.application.Calculation = -4135
        self.application.CalculateBeforeSave = False
        self.assert_hidden()
        return workbook

    def wait_calculation(self) -> None:
        import pythoncom
        while int(self.application.CalculationState) != 0:
            self.assert_hidden()
            pythoncom.PumpWaitingMessages()
            time.sleep(0.02)
        self.assert_hidden()

    def probe(self) -> dict:
        app = self.application
        result = {"backend": "excel_com", "version": str(app.Version), "build": str(app.Build),
                  "operating_system": str(app.OperatingSystem), "visible": bool(app.Visible), "isolated": True,
                  "formula2": False, "vba_project_access": False}
        sheet = self.bootstrap.Worksheets(1)
        sheet.Range("A1").Formula2 = "=SUM(1,2)"
        app.CalculateFullRebuild()
        self.wait_calculation()
        result["formula2"] = sheet.Range("A1").Value2 == 3
        sheet.Range("A10:C13").Value2 = ((1, 10, "A"), (2, 20, "B"), (2, 30, "A"), (3, 40, "B"))
        result["functions"] = {}
        errors = {2000: "#NULL!", 2007: "#DIV/0!", 2015: "#VALUE!", 2023: "#REF!", 2029: "#NAME?", 2036: "#NUM!", 2042: "#N/A", 2045: "#SPILL!"}
        for index, (name, formula) in enumerate(LOCAL_FUNCTION_PROBES.items()):
            cell = sheet.Range("E" + str(10 + index * 6))
            try:
                cell.Formula2 = formula
                app.CalculateFullRebuild()
                self.wait_calculation()
                error = bool(app.WorksheetFunction.IsError(cell))
                value = cell.Value2
                supported = not error and bool(cell.HasFormula)
                result["functions"][name] = {"supported": supported, "available": supported, "verified": True,
                                              "error": errors.get(value & 0xffff, str(cell.Text)) if error and isinstance(value, int) else None,
                                              "result": None if error else value}
            except ExcelExceptions:
                raise
            except Exception:
                result["functions"][name] = {"supported": False, "available": False, "verified": True, "error": "formula_not_accepted", "result": None}
        try:
            result["vba_project_access"] = self.bootstrap.VBProject.VBComponents.Count >= 1
        except Exception:
            pass
        self.assert_hidden()
        return result

    def close(self) -> None:
        """Libera apenas objetos/processos desta sessão, inclusive após erro."""
        self._stop.set()
        if self._monitor is not None:
            self._monitor.join(timeout=1)
        if self.application is not None and self.identity is not None:
            try:
                self.application.EnableEvents = False
                self.application.DisplayAlerts = False
                for index in range(self.application.Workbooks.Count, 0, -1):
                    self.application.Workbooks(index).Close(SaveChanges=False)
                self.application.Quit()
            except Exception:
                pass
        self.bootstrap = None
        self.application = None
        gc.collect()
        if self._job is not None:
            self._kernel.CloseHandle(self._job)
            self._job = None
        if self._com_initialized:
            import pythoncom
            pythoncom.CoUninitialize()
            self._com_initialized = False
        if self._mutex is not None:
            self._kernel.ReleaseMutex(self._mutex)
            self._kernel.CloseHandle(self._mutex)
            self._mutex = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
