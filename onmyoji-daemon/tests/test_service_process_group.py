"""Encerramento de descendentes depois da morte inesperada do serviço."""
import os
from pathlib import Path
import subprocess
import sys
import time

from onmyoji_daemon.supervision.component.process_group import ServiceProcessGroup


def alive(pid):
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        stat = Path(f"/proc/{pid}/stat")
        return not stat.exists() or stat.read_text().split(") ", 1)[1].split()[0] != "Z"
    except ProcessLookupError:
        return False


def test_containment_removes_child_after_wrapper_crash(tmp_path):
    """Um wrapper morto nunca deixa um conector vivo para a próxima geração."""
    script = tmp_path / "wrapper.py"
    activate, child_pid = tmp_path / "activate", tmp_path / "child.pid"
    script.write_text('''import pathlib,subprocess,sys,time
activate,pidfile=map(pathlib.Path,sys.argv[1:])
while not activate.exists(): time.sleep(0.01)
child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(60)"])
pidfile.write_text(str(child.pid))
time.sleep(60)
''', encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-B", str(script), str(activate), str(child_pid)], start_new_session=os.name != "nt", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    group = None
    try:
        group = ServiceProcessGroup(process)
        activate.touch()
        deadline = time.monotonic() + 5
        while not child_pid.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert child_pid.exists()
        pid = int(child_pid.read_text())
        assert alive(pid)
        process.kill()
        process.wait(5)
        group.close()
        group = None
        assert not alive(pid)
    finally:
        if group:
            group.close()
        if process.poll() is None:
            process.kill()
            process.wait(5)
