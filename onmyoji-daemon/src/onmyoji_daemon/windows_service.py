"""Host pywin32 para um serviço pertencente a uma instância Onmyōji."""
from __future__ import annotations

import argparse
from pathlib import Path
import threading

from .instance import identity
from .supervisor import Supervisor

try:
    import win32service
    import win32serviceutil
except ImportError:
    win32service = win32serviceutil = None


def _service_root(name: str) -> Path:
    """Obtém a raiz registrada para a instância atendida pelo Windows Service."""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{name}")
    try: value, _kind = winreg.QueryValueEx(key, "OnmyojiRoot")
    finally: winreg.CloseKey(key)
    return Path(str(value)).resolve()


def _save_service_root(name: str, root: Path) -> None:
    import winreg
    key = winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, f"SYSTEM\\CurrentControlSet\\Services\\{name}")
    try: winreg.SetValueEx(key, "OnmyojiRoot", 0, winreg.REG_SZ, str(root.resolve()))
    finally: winreg.CloseKey(key)


if win32serviceutil:
    class OnmyojiManagedService(win32serviceutil.ServiceFramework):
        """Host importável pelo pythonservice.exe para uma instância Onmyōji."""
        _svc_name_ = "OnmyojiManagedService"; _svc_display_name_ = "Onmyoji Managed Service"; _svc_description_ = "Onmyōji daemon"

        def __init__(self, values):
            self._svc_name_ = values[0]; self.root = _service_root(values[0]); super().__init__(values); self.stop_event = threading.Event()
        def SvcStop(self): self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING); self.stop_event.set()
        def SvcDoRun(self):
            supervisor = Supervisor(self.root); supervisor.stop_event = self.stop_event; supervisor.run_forever()
else:
    class OnmyojiManagedService: pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False); parser.add_argument("--service-name", required=True); parser.add_argument("--onmyoji-root", type=Path, required=True); args, remainder = parser.parse_known_args(argv)
    if not win32serviceutil: raise RuntimeError("O modo serviço Windows exige pywin32 instalado no Python do daemon.")
    root, name = args.onmyoji_root.resolve(), args.service_name

    OnmyojiManagedService._svc_name_ = name; OnmyojiManagedService._svc_display_name_ = name; OnmyojiManagedService._svc_description_ = f"Shikigami {identity(root)} Daemon"
    previous, __import__("sys").argv = __import__("sys").argv, [__import__("sys").argv[0], *remainder]
    try:
        win32serviceutil.HandleCommandLine(OnmyojiManagedService)
        if any(action in remainder for action in ("install", "update")): _save_service_root(name, root)
    finally: __import__("sys").argv = previous
    return 0


if __name__ == "__main__": raise SystemExit(main())
