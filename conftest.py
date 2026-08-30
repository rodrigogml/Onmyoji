"""Bootstrap de importação para a suíte integrada do Onmyōji.

As skills são scripts independentes e seus testes importam os wrappers locais
diretamente. A coleta por importlib evita colisões entre diretórios repetidos
como ``scripts`` e ``tests``; estes caminhos preservam o modo de importação
usado por cada teste isolado.
"""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def pytest_configure() -> None:
    paths = [ROOT / "onmyoji-daemon" / "src"]
    paths.extend(skill / "scripts" for skill in (ROOT / "available-skills").iterdir() if (skill / "scripts").is_dir())
    for path in reversed(paths):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)
