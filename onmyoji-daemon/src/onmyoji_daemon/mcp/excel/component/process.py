"""Identidade e exclusão mútua para processos pertencentes ao MCP Excel."""
from __future__ import annotations

import os
from pathlib import Path

from ..exception import ExcelExceptions, require
from ...document_processing.paths import reject_links


class FileLease:
    """Reserva um diretório de jobs entre processos, sem depender de PID em arquivo.

    A reserva é liberada pelo sistema operacional se o proprietário morrer.
    Um segundo supervisor recebe ``profile_busy`` sem reconciliar jobs ativos
    do primeiro. O arquivo de lock é persistente; seu conteúdo não indica posse.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.stream = None

    def acquire(self) -> None:
        reject_links(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        reject_links(self.path)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if stream.seek(0, os.SEEK_END) == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            stream.close()
            raise ExcelExceptions("profile_busy", "Outro servidor MCP já gerencia este diretório de jobs Excel.") from error
        self.stream = stream

    def close(self) -> None:
        if self.stream is not None:
            stream, self.stream = self.stream, None
            try:
                if os.name == "nt":
                    import msvcrt
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.close()


def _windows_api():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    definitions = {
        "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "WaitForSingleObject": ([wintypes.HANDLE, wintypes.DWORD], wintypes.DWORD),
        "GetProcessTimes": ([wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4, wintypes.BOOL),
        "QueryFullProcessImageNameW": ([wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "ProcessIdToSessionId": ([wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        "TerminateProcess": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
    }
    for name, (arguments, result) in definitions.items():
        function = getattr(kernel, name)
        function.argtypes, function.restype = arguments, result
    return kernel


def _windows_identity(kernel, handle, pid: int) -> dict | None:
    import ctypes
    from ctypes import wintypes
    if kernel.WaitForSingleObject(handle, 0) == 0:
        return None
    created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
    name = ctypes.create_unicode_buffer(32768)
    length, session = wintypes.DWORD(len(name)), wintypes.DWORD()
    if not (kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user))
            and kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length))
            and kernel.ProcessIdToSessionId(pid, ctypes.byref(session))):
        if kernel.WaitForSingleObject(handle, 200) == 0:
            return None
        raise ExcelExceptions("process_ownership_unverified", "Não foi possível verificar a identidade do processo Excel.")
    return {"pid": pid, "creation_time": (created.dwHighDateTime << 32) | created.dwLowDateTime,
            "executable": name.value, "session_id": session.value}


def process_identity(pid: int) -> dict | None:
    """Obtém identidade verificável; processo encerrado retorna ``None``.

    No Windows inclui instante de criação FILETIME, executável e sessão.
    Falha de acesso retorna ``process_ownership_unverified``, nunca uma
    identidade incompleta que autorize encerrar outro processo.
    """
    require(type(pid) is int and pid > 0, "process_ownership_unverified", "Identificador de processo inválido.")
    if os.name == "nt":
        import ctypes
        kernel = _windows_api()
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() in {87, 1168}:
                return None
            raise ExcelExceptions("process_ownership_unverified", "Não foi possível consultar o processo Excel.")
        try:
            return _windows_identity(kernel, handle, pid)
        finally:
            kernel.CloseHandle(handle)
    proc = Path("/proc") / str(pid)
    try:
        fields = (proc / "stat").read_text().rsplit(") ", 1)[1].split()
        if fields[0] == "Z":
            return None
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        return {"pid": pid, "creation_time": boot + ":" + fields[19],
                "executable": os.readlink(proc / "exe"), "session_id": int(fields[3])}
    except FileNotFoundError:
        return None
    except (OSError, ValueError, IndexError) as error:
        raise ExcelExceptions("process_ownership_unverified", "Não foi possível consultar a identidade do processo.") from error


def same_process(expected: dict, actual: dict | None) -> bool:
    """Compara identidade inteira; PID sozinho nunca prova propriedade."""
    return bool(isinstance(expected, dict) and isinstance(actual, dict)
                and all(type(item.get("pid")) is int and item["pid"] > 0
                        and type(item.get("session_id")) is int and item["session_id"] >= 0
                        and type(item.get("creation_time")) in {str, int} and bool(item["creation_time"])
                        and isinstance(item.get("executable"), str) and bool(item["executable"])
                        for item in (expected, actual))
                and all(expected.get(key) == actual.get(key) for key in ("pid", "creation_time", "session_id"))
                and os.path.normcase(expected["executable"]) == os.path.normcase(actual["executable"]))


def terminate_owned(identity: dict) -> bool:
    """Encerra somente Excel cuja identidade foi comprovada no mesmo handle.

    Retorna ``False`` se o processo morreu ou o PID foi reutilizado. Registros
    incompletos ou executáveis diferentes de Excel geram
    ``process_ownership_unverified`` antes de qualquer encerramento.
    """
    require(isinstance(identity, dict) and type(identity.get("pid")) is int
            and Path(identity.get("executable", "")).name.casefold() == "excel.exe"
            and identity.get("creation_time") is not None and type(identity.get("session_id")) is int,
            "process_ownership_unverified", "A propriedade do processo Excel não está comprovada.")
    require(os.name == "nt", "process_ownership_unverified", "O encerramento nativo Excel requer Windows.")
    import ctypes
    kernel = _windows_api()
    handle = kernel.OpenProcess(0x1000 | 0x100000 | 0x0001, False, identity["pid"])
    if not handle:
        if ctypes.get_last_error() in {87, 1168}:
            return False
        raise ExcelExceptions("process_ownership_unverified", "Não foi possível abrir o processo Excel para recuperação.")
    try:
        actual = _windows_identity(kernel, handle, identity["pid"])
        if not same_process(identity, actual):
            return False
        require(bool(kernel.TerminateProcess(handle, 1)), "process_cleanup_failed", "Não foi possível encerrar a instância Excel do MCP.")
        require(kernel.WaitForSingleObject(handle, 5000) == 0, "process_cleanup_failed", "A instância Excel do MCP ainda não encerrou.")
        return True
    finally:
        kernel.CloseHandle(handle)


def visible_windows(pid: int) -> list[int]:
    """Detecta janelas visíveis do processo sem interagir com a interface."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    result = []
    @callback_type
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(hwnd):
            result.append(int(hwnd))
        return True
    user.EnumWindows(collect, 0)
    return result


def private_memory_bytes(pid: int) -> int:
    """Consulta memória do processo para o orçamento combinado do job.

    Windows usa PrivateUsage; Linux usa RSS como aproximação conservadora
    para o worker de leitura direta. Processo já encerrado retorna zero.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
        kernel = _windows_api()
        kernel.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        kernel.K32GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000 | 0x0010 | 0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() in {87, 1168}:
                return 0
            raise ExcelExceptions("resource_monitor_unavailable", "Não foi possível consultar o consumo de memória do job Excel.")
        try:
            if kernel.WaitForSingleObject(handle, 0) == 0:
                return 0
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not kernel.K32GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                if kernel.WaitForSingleObject(handle, 200) == 0:
                    return 0
                raise ExcelExceptions("resource_monitor_unavailable", "Não foi possível verificar o consumo de memória do job Excel.")
            return counters.PrivateUsage
        finally:
            kernel.CloseHandle(handle)
    try:
        for line in (Path("/proc") / str(pid) / "status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except FileNotFoundError:
        return 0
    except (OSError, ValueError, IndexError) as error:
        raise ExcelExceptions("resource_monitor_unavailable", "Não foi possível verificar a memória do worker Excel.") from error
    return 0


def is_process_descendant(identity: dict, ancestor: dict) -> bool:
    """Confere a cadeia de pais e as identidades, incluindo launchers de venv.

    O executável Python de um venv Windows pode ser um launcher que mantém
    outro Python como filho. PID retornado por Popen não identifica sempre
    o processo que executará o worker.
    """
    if not same_process(identity, identity) or not same_process(ancestor, ancestor):
        return False
    if not same_process(identity, process_identity(identity["pid"])) or not same_process(ancestor, process_identity(ancestor["pid"])):
        return False
    if identity["pid"] == ancestor["pid"]:
        return True
    if identity["session_id"] != ancestor["session_id"]:
        return False
    if os.name == "nt":
        if type(identity["creation_time"]) is not int or type(ancestor["creation_time"]) is not int or identity["creation_time"] < ancestor["creation_time"]:
            return False
    else:
        try:
            worker_boot, worker_tick = identity["creation_time"].rsplit(":", 1)
            parent_boot, parent_tick = ancestor["creation_time"].rsplit(":", 1)
            if worker_boot != parent_boot or int(worker_tick) < int(parent_tick):
                return False
        except (AttributeError, TypeError, ValueError):
            return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        class ProcessEntry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]
        kernel = _windows_api()
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        require(snapshot and snapshot != ctypes.c_void_p(-1).value, "process_ownership_unverified", "Não foi possível verificar a cadeia do worker Python.")
        parents = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel.CloseHandle(snapshot)
        current = identity["pid"]
        for _ in range(64):
            current = parents.get(current)
            if current == ancestor["pid"]:
                return True
            if current is None:
                return False
        return False
    current = identity["pid"]
    for _ in range(64):
        try:
            fields = (Path("/proc") / str(current) / "stat").read_text().rsplit(") ", 1)[1].split()
            current = int(fields[1])
        except (OSError, ValueError, IndexError):
            return False
        if current == ancestor["pid"]:
            return True
        if current <= 1:
            return False
    return False


def attach_verified_process(job_handle, identity: dict) -> None:
    """Anexa o worker real ao Job Object após conferir identidade no mesmo handle."""
    require(os.name == "nt", "process_ownership_unverified", "Contenção por Job Object requer Windows.")
    require(bool(job_handle) and same_process(identity, identity), "process_ownership_unverified", "Identidade ou contenção do worker inválida.")
    import ctypes
    from ctypes import wintypes
    kernel = _windows_api()
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
    handle = kernel.OpenProcess(0x1000 | 0x100000 | 0x0100 | 0x0001, False, identity["pid"])
    require(bool(handle), "process_ownership_unverified", "Não foi possível conter o worker Python real.")
    try:
        require(same_process(identity, _windows_identity(kernel, handle, identity["pid"])),
                "process_ownership_unverified", "O worker Python mudou antes da contenção.")
        already = wintypes.BOOL()
        require(bool(kernel.IsProcessInJob(handle, job_handle, ctypes.byref(already))),
                "process_ownership_unverified", "Não foi possível consultar a contenção do worker.")
        require(already.value or bool(kernel.AssignProcessToJobObject(job_handle, handle)),
                "process_ownership_unverified", "Não foi possível anexar o worker real ao Job Object.")
    finally:
        kernel.CloseHandle(handle)
