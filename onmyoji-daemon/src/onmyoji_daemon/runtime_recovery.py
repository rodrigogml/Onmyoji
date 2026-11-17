"""Recupera sobreposições globais sem remover distribuições de outro gerenciador."""
from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit, urlunsplit


def _normalized_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _blocked_distribution(detail: str) -> tuple[str, str] | None:
    if not re.search(r"uninstall-no-record-file|RECORD file not found|no RECORD file was found", detail, re.IGNORECASE):
        return None
    match = re.search(r"Cannot uninstall\s+([A-Za-z0-9][A-Za-z0-9_.-]*)\s+([^\s,]+)", detail, re.IGNORECASE)
    return (_normalized_name(match[1]), match[2]) if match else None


def _candidate_reference(report: dict, name: str) -> str | None:
    for item in report.get("install", []):
        metadata = item.get("metadata", {})
        if _normalized_name(str(metadata.get("name", ""))) != name:
            continue
        download = item.get("download_info", {})
        url = download.get("url")
        if not isinstance(url, str) or not metadata.get("version"):
            return None
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http", "file"} or not parsed.path:
            return None
        archive = download.get("archive_info", {})
        hashes = archive.get("hashes", {})
        if not hashes and isinstance(archive.get("hash"), str):
            algorithm, separator, digest = archive["hash"].partition("=")
            if separator:
                hashes = {algorithm: digest}
        if hashes:
            algorithm = "sha256" if "sha256" in hashes else sorted(hashes)[0]
            digest = hashes[algorithm]
            if not re.fullmatch(r"[a-z0-9]+", algorithm) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]+", digest):
                return None
            fragment = parsed.fragment + ("&" if parsed.fragment else "") + algorithm + "=" + digest
            url = urlunsplit(parsed._replace(fragment=fragment))
        return f"{name} @ {url}"
    return None


def recover_distribution(root: Path, executable: Path, requirements: list[str], detail: str, progress: Callable[[str], None] | None, install: Callable[[list[str], str], tuple[int, str]], recovered: set[str]) -> bool:
    """Sobrepõe um pacote externo sem RECORD e permite repetir a instalação normal.

    Só recupera um pacote ainda não tratado quando o destino global é separado
    da instalação protegida e possui precedência no Python selecionado. Resolve
    o candidato exato com o pip, instala somente ele e preserva a distribuição
    externa. ``install`` executa os argumentos do pip com a autorização já obtida.

    Raises:
        RuntimeError: Se os metadados não permitirem uma sobreposição segura,
            o candidato não puder ser resolvido ou a instalação exata falhar.
    """
    from .runtime import RuntimeError, _environment, _python_code, _safe_output

    blocked = _blocked_distribution(detail)
    if blocked is None or blocked[0] in recovered:
        return False
    name, version = blocked
    guidance = " Preserve os pacotes do sistema; selecione isolated em Python e bibliotecas ou solicite a preparação do Python global ao administrador."
    code = """import json, sys, sysconfig
from importlib.metadata import distribution
from pathlib import Path
name = NAME
dist = distribution(name)
source = Path(dist.locate_file('')).resolve()
targets = list(dict.fromkeys(str(Path(sysconfig.get_path(key)).resolve()) for key in ('purelib', 'platlib')))
paths = list(dict.fromkeys(str(Path(value).resolve()) for value in sys.path if value))
def separate(target):
    target = Path(target)
    return not target.is_relative_to(source) and not source.is_relative_to(target)
safe = str(source) in paths and all(separate(target) and target in paths and paths.index(target) < paths.index(str(source)) for target in targets)
print(json.dumps({'name': dist.metadata['Name'], 'version': dist.version, 'installer': (dist.read_text('INSTALLER') or '').strip(), 'missing_record': dist.read_text('RECORD') is None, 'safe_target': safe}))
""".replace("NAME", repr(name))
    try:
        completed = subprocess.run(_python_code(executable, code), env=_environment(), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=15, check=False)
        metadata = json.loads(completed.stdout) if completed.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError) as error:
        raise RuntimeError("Não foi possível verificar a distribuição protegida pelo gerenciador do sistema." + guidance) from error
    if not isinstance(metadata, dict) or _normalized_name(str(metadata.get("name", ""))) != name or metadata.get("version") != version or not metadata.get("missing_record") or not metadata.get("safe_target") or not metadata.get("installer") or str(metadata["installer"]).casefold() == "pip":
        raise RuntimeError(f"Não é seguro sobrepor a distribuição {name} sem RECORD no destino global." + guidance)
    if progress:
        progress(f"A distribuição {name} pertence a outro gerenciador. Preparando uma cópia no destino global separado, sem remover a original.")
    temporary_root = root.resolve() / "tmp"
    try:
        temporary_root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="runtime-recovery-", dir=temporary_root) as temporary:
            report_path = Path(temporary) / "report.json"
            report_path.touch()
            result, report_detail = install(["install", "--disable-pip-version-check", "--no-input", "--upgrade", "--dry-run", "--report", str(report_path), *requirements], f"Resolvendo a atualização da distribuição {name}.")
            if result:
                raise RuntimeError(f"Não foi possível resolver a atualização da distribuição {name}." + (" Detalhe: " + _safe_output(report_detail) if report_detail else "") + guidance)
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                reference = _candidate_reference(report, name)
            except (OSError, ValueError, TypeError, AttributeError):
                reference = None
            if reference is None:
                raise RuntimeError(f"O pip não identificou um candidato exato para a distribuição {name}." + guidance)
            result, install_detail = install(["install", "--disable-pip-version-check", "--no-input", "--ignore-installed", "--no-deps", reference], f"Instalando a cópia global da distribuição {name}.")
            if result:
                raise RuntimeError(f"Não foi possível instalar a cópia global da distribuição {name}." + (" Detalhe: " + _safe_output(install_detail) if install_detail else "") + guidance)
    except OSError as error:
        raise RuntimeError("Não foi possível preparar o relatório temporário da recuperação global." + guidance) from error
    recovered.add(name)
    return True
