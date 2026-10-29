"""Bootstrap de importação para a suíte integrada do Onmyōji.

As skills são scripts independentes e seus testes importam os wrappers locais
diretamente. A coleta por importlib evita colisões entre diretórios repetidos
como ``scripts`` e ``tests``; estes caminhos preservam o modo de importação
usado por cada teste isolado.
"""
from __future__ import annotations

import sys
import tempfile
import shutil
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent
TEST_TEMPORARY_ROOT = ROOT / "tmp" / "pytest-system"
TEST_PYCACHE_ROOT = ROOT / "tmp" / "pycache"
TEST_PYTEST_CACHE_ROOT = ROOT / "tmp" / "pytest-cache"


def pytest_configure() -> None:
    TEST_TEMPORARY_ROOT.mkdir(parents=True, exist_ok=True)
    TEST_PYCACHE_ROOT.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(TEST_TEMPORARY_ROOT)
    sys.pycache_prefix = str(TEST_PYCACHE_ROOT)
    paths = [ROOT / "onmyoji-daemon" / "src"]
    skill_root = ROOT / "shikigami" / "skills"
    if skill_root.is_dir():
        paths.extend(skill / "scripts" for skill in skill_root.iterdir() if (skill / "scripts").is_dir())
    for path in reversed(paths):
        value = str(path)
        if value not in sys.path:
            sys.path.insert(0, value)


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus: int) -> None:
    """Remove os artefatos da suíte, inclusive quando algum teste falhar."""
    del session, exitstatus
    for path in (ROOT / ".pytest_cache", ROOT / "__pycache__", ROOT / "tmp" / "pytest", TEST_TEMPORARY_ROOT, TEST_PYCACHE_ROOT, TEST_PYTEST_CACHE_ROOT):
        shutil.rmtree(path, ignore_errors=True)
