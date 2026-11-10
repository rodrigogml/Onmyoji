"""Publicação local de configuração com tolerância limitada a compartilhamento Windows."""
from __future__ import annotations

from pathlib import Path
import time


def publish_file(source: Path, destination: Path) -> None:
    """Substitui o destino; só repete bloqueios Windows de acesso/compartilhamento.

    No máximo cinco tentativas aguardam 10, 20, 30 e 40 ms. A última falha
    original é propagada; outros erros não são repetidos. A publicação usa
    somente a substituição do filesystem, sem copiar ou truncar o destino.
    """
    failures = 0
    while True:
        try:
            source.replace(destination)
            return
        except PermissionError as error:
            failures += 1
            if getattr(error, "winerror", None) not in {5, 32, 33} or failures >= 5:
                raise
            time.sleep(failures / 100)
