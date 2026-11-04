"""Processo cloudflared contido pelo Supervisor e autenticado somente por ambiente."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time
from typing import Any
import urllib.request

from .exception import TunnelError


class TunnelProcess:
    """Executa um conector; a recuperação do processo pertence ao Supervisor."""

    def __init__(self, executable: str, token: str):
        if not shutil.which(executable) and not Path(executable).is_file():
            raise TunnelError("executable_missing", "cloudflared não encontrado.")
        self.executable, self.token = executable, token
        self.process: subprocess.Popen[str] | None = None
        self.last_error: str | None = None
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.metrics_port = int(listener.getsockname()[1])

    def _safe_output(self, value: str) -> str:
        if self.token:
            value = value.replace(self.token, "[redacted]")
        value = re.sub(r"https?://[^\s?]+\?\S+", "[url redacted]", value)
        value = re.sub(r"(?i)(authorization|cookie|set-cookie)\s*[:=].*", r"\1=[redacted]", value)
        return re.sub(r"(?i)(token|password|secret|code|state)\s*[=:]\s*\S+", r"\1=[redacted]", value).strip()[-800:]

    def _capture_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        try:
            for raw in process.stdout:
                if any(level in raw.upper() for level in (" ERR ", "ERROR", "FATAL")):
                    # A saída pode conter URLs de callbacks e headers do cliente.
                    # O status conserva o motivo operacional sem persistir a linha.
                    self.last_error = "cloudflared reportou uma falha de transporte."
        except OSError:
            pass

    def start(self) -> None:
        """Inicia sem token na linha de comando; falha imediata gera executable_failed."""
        if self.process and self.process.poll() is None:
            return
        permitted = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "PATH", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in permitted}
        environment["TUNNEL_TOKEN"] = self.token
        self.last_error = None
        try:
            self.process = subprocess.Popen([self.executable, "tunnel", "--no-autoupdate", "--metrics", f"127.0.0.1:{self.metrics_port}", "run"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as error:
            raise TunnelError("executable_failed", "Não foi possível iniciar cloudflared.") from error
        threading.Thread(target=self._capture_output, daemon=True).start()
        time.sleep(0.1)
        if self.process.poll() is not None:
            raise TunnelError("executable_failed", self.last_error or f"cloudflared encerrou com código {self.process.returncode}.")

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)

    def status(self) -> dict[str, Any]:
        running = bool(self.process and self.process.poll() is None)
        connected = False
        if running:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.metrics_port}/ready", timeout=0.2) as response:
                    connected = response.status == 200
            except OSError:
                pass
        elif self.process and not self.last_error:
            self.last_error = f"cloudflared encerrou com código {self.process.returncode}."
        return {"running": running, "connected": connected, "pid": self.process.pid if running and self.process else None, "last_error": self.last_error}
