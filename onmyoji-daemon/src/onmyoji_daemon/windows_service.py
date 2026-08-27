"""Adaptador opcional do Serviço do Windows; o núcleo permanece multiplataforma."""
from __future__ import annotations

import os
import threading

from .supervisor import Supervisor

try:
    import win32service
    import win32serviceutil
    import servicemanager
except ImportError: win32service = win32serviceutil = servicemanager = None


if win32serviceutil:
    class OnmyojiDaemonService(win32serviceutil.ServiceFramework):
        _svc_name_ = "OnmyojiDaemon"; _svc_display_name_ = "Onmyōji Daemon"; _svc_description_ = "Serviços locais do Shikigami"
        def __init__(self, args): super().__init__(args); self.stop_event = threading.Event()
        def SvcStop(self): self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING); self.stop_event.set()
        def SvcDoRun(self):
            root = os.environ.get("ONMYOJI_ROOT")
            if not root: raise RuntimeError("ONMYOJI_ROOT is required for the Windows service")
            supervisor = Supervisor(__import__("pathlib").Path(root)); supervisor.stop_event = self.stop_event; supervisor.run_forever()


def main() -> None:
    if not win32serviceutil: raise RuntimeError("Windows service mode requires pywin32")
    win32serviceutil.HandleCommandLine(OnmyojiDaemonService)
