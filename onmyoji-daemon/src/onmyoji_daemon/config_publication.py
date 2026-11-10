"""Publicação local de configuração com tolerância limitada a compartilhamento Windows."""
from __future__ import annotations

import os
from pathlib import Path
import time


def apply_shared_file_mode(path: Path) -> None:
    """Mantém arquivos operacionais graváveis pelas duas contas da instância."""
    if os.name != "nt" and path.is_file():
        path.chmod(0o660)


def publish_file(source: Path, destination: Path, *, mode: int | None = None) -> None:
    """Substitui o destino; só repete bloqueios Windows de acesso/compartilhamento.

    No máximo cinco tentativas aguardam 10, 20, 30 e 40 ms. A última falha
    original é propagada; outros erros não são repetidos. Quando informado,
    ``mode`` é aplicado ao arquivo de staging antes da substituição para que a
    publicação preserve a política de acesso do destino.
    """
    if mode is not None and source.is_file():
        source.chmod(mode)
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
