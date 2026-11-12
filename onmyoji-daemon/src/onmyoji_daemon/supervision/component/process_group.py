"""Contenção do serviço e seus descendentes, inclusive após falha do wrapper."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import time


class ServiceProcessGroup:
    """Mantém um Job Object Windows ou uma sessão POSIX sob um único proprietário.

    No Windows, o serviço deve aguardar a ativação antes de criar filhos.
    Em POSIX, o chamador precisa iniciar o processo com ``start_new_session``.
    ``close`` encerra os descendentes antes de permitir uma nova geração.
    """

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.handle = None
        if os.name == "nt":
            self._attach_windows()

    def _attach_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class Counters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                              "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class Limits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", Counters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = self.kernel.CreateJobObjectW(None, None)
        if not handle:
            raise OSError("Não foi possível criar a contenção do serviço.")
        limits = Limits()
        limits.BasicLimitInformation.LimitFlags = 0x2000
        if not self.kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not self.kernel.AssignProcessToJobObject(handle, int(self.process._handle)):
            self.kernel.CloseHandle(handle)
            raise OSError("Não foi possível conter os descendentes do serviço.")
        self.handle = handle

    def close(self) -> None:
        """Encerra a árvore e aguarda sua saída; falhas impedem uma nova geração."""
        if os.name == "nt":
            if self.handle is None:
                return
            import ctypes
            from ctypes import wintypes

            class Accounting(ctypes.Structure):
                _fields_ = [(name, ctypes.c_longlong) for name in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
                    (name, wintypes.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses", "TotalTerminatedProcesses")]

            # ActiveProcesses pode chegar a zero antes da sinalização final do
            # processo. Conservar os handles evita recuperar o serviço cedo.
            members = ctypes.create_string_buffer(8192)
            if not self.kernel.QueryInformationJobObject(self.handle, 3, members, len(members), None):
                raise OSError("Não foi possível enumerar os descendentes do serviço.")
            count = ctypes.cast(members, ctypes.POINTER(wintypes.DWORD))[1]
            identifiers = (ctypes.c_size_t * count).from_buffer(members, 8)
            processes = [handle for pid in identifiers if (handle := self.kernel.OpenProcess(0x100000, False, pid))]
            try:
                if not self.kernel.TerminateJobObject(self.handle, 1):
                    raise OSError("Não foi possível encerrar a árvore do serviço.")
                deadline = time.monotonic() + 5
                while True:
                    accounting = Accounting()
                    if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None):
                        raise OSError("Não foi possível confirmar o encerramento dos descendentes.")
                    if accounting.ActiveProcesses == 0:
                        break
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Descendentes do serviço ainda estão ativos.")
                    time.sleep(0.02)
                for process_handle in processes:
                    if self.kernel.WaitForSingleObject(process_handle, max(0, int((deadline - time.monotonic()) * 1000))) != 0:
                        raise TimeoutError("Descendente do serviço ainda não encerrou.")
            finally:
                for process_handle in processes:
                    self.kernel.CloseHandle(process_handle)
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        else:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.killpg(self.process.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 5
                while self._posix_live_members():
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Descendentes do serviço ainda estão ativos.")
                    time.sleep(0.02)
        if self.process.poll() is None:
            self.process.wait(timeout=5)

    def _posix_live_members(self) -> bool:
        """Ignora zombies Linux, que já não executam nem mantêm o transporte."""
        proc = Path("/proc")
        if proc.is_dir():
            for entry in proc.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    fields = (entry / "stat").read_text().rsplit(") ", 1)[1].split()
                    if fields[2] == str(self.process.pid) and fields[0] != "Z":
                        return True
                except (OSError, IndexError):
                    continue
            return False
        try:
            os.killpg(self.process.pid, 0)
            return True
        except ProcessLookupError:
            return False
