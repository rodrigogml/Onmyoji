"""Publicação local de configuração com tolerância limitada a compartilhamento Windows."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile
import time


def operational_file_mode(root: Path) -> int | None:
    """Retorna o modo POSIX exigido pela identidade configurada da instância.

    Somente uma instância Linux dedicada que compartilha acesso com o
    instalador precisa liberar escrita ao grupo privado do Shikigami. Nas
    demais plataformas e perfis, a ACL herdada do diretório continua sendo a
    autoridade de acesso.
    """
    if os.name != "posix":
        return None
    try:
        from .linux_service import load_execution_profile
        profile = load_execution_profile(root)
    except (OSError, ValueError):
        return None
    if profile.get("mode") == "dedicated" and profile.get("installer_access") is True:
        return 0o660
    return None


def apply_shared_file_mode(path: Path) -> None:
    """Compatibilidade para criadores externos, usando a política da instância dona."""
    root = next((parent.parent for parent in (path.parent, *path.parents) if parent.name == "configs"), None)
    if root is not None:
        normalize_instance_file(root, path)


def normalize_instance_file(root: Path, path: Path) -> None:
    """Aplica a política operacional após uma biblioteca ou processo externo gravar.

    A função não amplia permissões de instalações que usam o usuário atual ou
    uma identidade dedicada sem compartilhamento.
    """
    mode = operational_file_mode(root)
    if mode is not None and path.is_file() and stat.S_IMODE(path.stat().st_mode) != mode:
        path.chmod(mode)


def publish_instance_file(root: Path, source: Path, destination: Path) -> None:
    """Publica bytes operacionais com staging no diretório de destino.

    O staging local faz arquivos novos herdarem a ACL da pasta também no
    Windows; no Linux a política compartilhada é aplicada antes da troca.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=destination.name + ".", suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
            shutil.copyfileobj(input_file, output)
            output.flush()
            os.fsync(output.fileno())
        publish_file(temporary, destination, mode=operational_file_mode(root))
        source.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)


def write_instance_bytes(root: Path, path: Path, content: bytes) -> None:
    """Grava um arquivo operacional aplicando a única política de acesso da instância."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        publish_file(temporary, path, mode=operational_file_mode(root))
    finally:
        temporary.unlink(missing_ok=True)


def write_instance_text(root: Path, path: Path, content: str, *, encoding: str = "utf-8") -> None:
    """Grava texto UTF-8 operacional sem BOM e aplica a política central."""
    write_instance_bytes(root, path, content.encode(encoding))


def copy_instance_file(root: Path, source: Path, destination: Path) -> None:
    """Copia um modelo sem transportar suas permissões para a instância."""
    write_instance_bytes(root, destination, source.read_bytes())


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
