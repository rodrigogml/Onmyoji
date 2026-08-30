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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False); parser.add_argument("--service-name", required=True); parser.add_argument("--onmyoji-root", type=Path, required=True); args, remainder = parser.parse_known_args(argv)
    if not win32serviceutil: raise RuntimeError("O modo serviço Windows exige pywin32 instalado no Python do daemon.")
    root, name = args.onmyoji_root.resolve(), args.service_name

    class InstanceService(win32serviceutil.ServiceFramework):
        _svc_name_ = name; _svc_display_name_ = name; _svc_description_ = f"Shikigami {identity(root)} Daemon"
        def __init__(self, values): super().__init__(values); self.stop_event = threading.Event()
        def SvcStop(self): self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING); self.stop_event.set()
        def SvcDoRun(self):
            supervisor = Supervisor(root); supervisor.stop_event = self.stop_event; supervisor.run_forever()

    # pywin32 persiste este nome para que PythonService.exe recarregue a classe.
    InstanceService.__name__ = "OnmyojiManagedService"
    InstanceService.__qualname__ = "OnmyojiManagedService"
    globals()["OnmyojiManagedService"] = InstanceService
    previous, __import__("sys").argv = __import__("sys").argv, [__import__("sys").argv[0], *remainder]
    try: win32serviceutil.HandleCommandLine(InstanceService)
    finally: __import__("sys").argv = previous
    return 0


if __name__ == "__main__": raise SystemExit(main())
