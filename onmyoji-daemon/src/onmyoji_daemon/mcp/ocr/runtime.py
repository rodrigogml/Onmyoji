"""Instalação explícita e diagnóstico dos motores locais de documentos."""
from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from collections.abc import Callable
from uuid import uuid4

from ..document_processing.contracts import DocumentError


BASE_REQUIREMENTS = ("PyMuPDF==1.26.7", "pypdf==6.1.1", "reportlab==4.4.4", "Pillow==12.1.0", "openpyxl==3.1.5", "pyHanko==0.31.0")
OCR_REQUIREMENTS = ("ocrmypdf==17.13.0",)
ADVANCED_REQUIREMENTS = ("paddleocr[doc-parser]==3.7.0", "paddlepaddle==3.2.0")


def advanced_directory(root: Path) -> Path:
    """Compatibilidade: OCR avançado compartilha o runtime único da instância."""
    from ...runtime import venv_path
    return venv_path(root)


def advanced_python(root: Path) -> Path:
    from ..runtime import mcp_runtime_python
    return mcp_runtime_python(root)


def model_directory(root: Path, profile: dict | None = None) -> Path:
    """Restringe modelos persistentes à área de estado da própria instância."""
    from ..document_processing.paths import reject_links
    allowed = root.resolve() / "state" / "mcp" / "ocr" / "models"
    legacy = root.resolve() / "configs" / "mcp" / "ocr" / "models"
    configured = (profile or {}).get("advanced_model_directory")
    path = Path(configured).expanduser() if configured else allowed
    reject_links(path)
    if not path.is_absolute():
        raise DocumentError("invalid_config", "Modelos OCR devem ficar na área state/mcp/ocr/models da própria instância.")
    resolved = path.resolve()
    if resolved.is_relative_to(allowed):
        return resolved
    if resolved.is_relative_to(legacy):
        return allowed / resolved.relative_to(legacy)
    raise DocumentError("invalid_config", "Modelos OCR devem ficar na área state/mcp/ocr/models da própria instância.")


def migrate_legacy_models(root: Path, profile: dict | None = None, progress: Callable[[str], None] | None = None) -> Path:
    """Move modelos da localização legada somente durante a preparação explícita."""
    from ..document_processing.paths import reject_links
    destination = model_directory(root, profile)
    legacy_root = root.resolve() / "configs" / "mcp" / "ocr" / "models"
    configured = (profile or {}).get("advanced_model_directory")
    source = Path(configured).expanduser().resolve() if configured else legacy_root
    if not source.is_relative_to(legacy_root) or not source.exists() or destination.exists():
        return destination
    reject_links(source)
    if progress:
        progress("Movendo modelos OCR da área legada de configuração para o estado local.")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except OSError as error:
        raise DocumentError("model_migration_failed", "Não foi possível mover os modelos OCR legados para o estado local.") from error
    if progress:
        progress("Movendo modelos OCR da área legada de configuração para o estado local. Concluída.")
    return destination


def local_process_environment(directory: Path, *, base: dict | None = None, source: str | None = None) -> dict:
    """Direciona arquivos transitórios e caches de subprocessos ao local autorizado."""
    from ..document_processing.paths import reject_links
    reject_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ if base is None else base)
    for key in tuple(environment):
        if key.upper() in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE", "PIP_TARGET", "PIP_PREFIX", "PIP_USER"}:
            environment.pop(key, None)
    cache = directory / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    environment.update({"TEMP": str(directory), "TMP": str(directory), "TMPDIR": str(directory),
                        "XDG_CACHE_HOME": str(cache), "PIP_CACHE_DIR": str(cache / "pip"),
                        "MPLCONFIGDIR": str(cache / "matplotlib"), "NUMBA_CACHE_DIR": str(cache / "numba"),
                        "HF_HOME": str(cache / "huggingface"), "PADDLE_HOME": str(cache / "paddle"),
                        "PADDLE_PDX_CACHE_HOME": str(cache / "paddle-pdx"),
                        "TORCH_HOME": str(cache / "torch"), "CUDA_CACHE_PATH": str(cache / "cuda"),
                        "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PIP_CONFIG_FILE": os.devnull})
    if source is not None:
        environment["PYTHONPATH"] = source
    return environment


def installation_directory(root: Path) -> Path:
    """Temporários de preparação ficam em tmp, nunca entre configurações/modelos."""
    from ..document_processing.paths import reject_links
    directory = root.resolve() / "tmp" / "document-install"
    reject_links(directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def pip_command() -> list[str]:
    """Executa somente o pip selecionado, sem habilitar o restante do user-site."""
    specification = importlib.util.find_spec("pip")
    if specification is None or not specification.origin:
        raise DocumentError("installation_failed", "Instale pip no Python usado pelo setup.")
    entry = Path(specification.origin).with_name("__main__.py")
    if not entry.is_file():
        raise DocumentError("installation_failed", "O instalador pip deste Python está indisponível.")
    return [sys.executable, "-I", "-B", str(entry)]


def copy_model_cache(models: Path, destination: Path) -> Path:
    """Copia recursos preparados para um cache de inferência sem gravar nos originais.

    O destino precisa ser curto o bastante para acomodar toda a árvore de
    modelos no Windows. Isso evita que uma instalação válida seja anunciada
    como pronta e falhe apenas na primeira inferência.
    """
    from ..document_processing.paths import reject_links
    models = models.resolve()
    reject_links(destination)
    for candidate in models.rglob("*"):
        reject_links(candidate)
        if not candidate.resolve().is_relative_to(models):
            raise DocumentError("invalid_models", "O cache de modelos contém um caminho externo à área de recursos.")
        if os.name == "nt" and len(str(destination / candidate.relative_to(models))) >= 260:
            raise DocumentError("model_cache_path_too_long", "O diretório de trabalho OCR gera caminhos longos demais no Windows; configure um workspace com caminho menor.")
    try:
        shutil.copytree(models, destination, symlinks=False, dirs_exist_ok=False)
    except (OSError, shutil.Error) as error:
        raise DocumentError("model_cache_failed", "Não foi possível preparar o cache de inferência no workspace.") from error
    return destination


def command_path(name: str) -> str | None:
    """Resolve executável sem interpretar argumentos como comandos de shell."""
    candidate = Path(name).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    available = shutil.which(name)
    if available or os.name != "nt":
        return available
    program_files = Path(os.environ.get("ProgramFiles", "C:/Program Files"))
    if name == "tesseract":
        candidate = program_files / "Tesseract-OCR" / "tesseract.exe"
        return str(candidate) if candidate.is_file() else None
    if name == "gswin64c":
        candidates = sorted((program_files / "gs").glob("gs*/bin/gswin64c.exe"))
        return str(candidates[-1]) if candidates else None
    if name == "verapdf":
        candidate = program_files / "veraPDF" / "verapdf.bat"
        return str(candidate) if candidate.is_file() else None
    return None


def installed_languages(profile: dict | None = None) -> list[str]:
    profile = profile or {}
    executable = command_path(str(profile.get("tesseract_path", "tesseract")))
    if not executable:
        return []
    command = [executable, "--list-langs"]
    if profile.get("tessdata_dir"):
        command += ["--tessdata-dir", str(profile["tessdata_dir"])]
    try:
        completed = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode:
        return []
    return sorted(line.strip() for line in completed.stdout.splitlines() if line.strip() and not line.startswith("List of available"))


def get_capabilities(root: Path, profile: dict | None = None) -> dict:
    """Descreve disponibilidade real; não instala pacotes ou baixa modelos."""
    profile = profile or {}
    versions = {}
    from ..runtime import mcp_runtime_python
    executable = mcp_runtime_python(root)
    if executable.is_file() and executable.resolve() != Path(sys.executable).resolve():
        probe = "import importlib.metadata as m,json; names=" + repr([requirement.split("==")[0] for requirement in (*BASE_REQUIREMENTS, *OCR_REQUIREMENTS)]) + "; result={};\nfor name in names:\n try: result[name]=m.version(name)\n except m.PackageNotFoundError: result[name]=None\nprint(json.dumps(result))"
        try:
            environment = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"}}
            environment["PYTHONNOUSERSITE"] = "1"
            checked = subprocess.run([str(executable), "-I", "-B", "-c", probe], env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False)
            if checked.returncode == 0:
                versions = json.loads(checked.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    packages = {}
    for requirement in (*BASE_REQUIREMENTS, *OCR_REQUIREMENTS):
        name = requirement.split("==")[0]
        if executable.is_file() and executable.resolve() != Path(sys.executable).resolve():
            version = versions.get(name)
        elif executable.is_file():
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = None
        else:
            version = None
        packages[name] = {"installed": version, "supported": requirement}
    languages = installed_languages(profile)
    models = model_directory(root, profile)
    advanced_ready = advanced_python(root).is_file() and (models / "prepared.json").is_file()
    return {"packages": packages, "runtime_ready": executable.is_file(), "pdf": packages["PyMuPDF"]["installed"] is not None,
            "tesseract": {"available": bool(command_path(str(profile.get("tesseract_path", "tesseract")))), "languages": languages},
            "advanced": {"available": advanced_ready, "python": str(advanced_python(root)), "models": str(models)},
            "searchable_pdf": packages["ocrmypdf"]["installed"] is not None and bool(languages),
            "pdfa": {"ghostscript": bool(command_path(str(profile.get("ghostscript_path", "gswin64c" if os.name == "nt" else "gs")))), "verapdf": bool(command_path(str(profile.get("verapdf_path", "verapdf"))))},
            "network_during_processing": False}


def native_installation_guidance() -> dict:
    """Retorna comandos administrativos revisáveis, nunca executados pelo MCP."""
    if os.name == "nt":
        return {"platform": "windows", "commands": ["winget install -e --id tesseract-ocr.tesseract"],
                "steps": ["Instale os dados por.traineddata e eng.traineddata em tessdata.", "Instale Ghostscript 64-bit e veraPDF para PDF/A; configure seus executáveis no perfil."],
                "links": {"tesseract": "https://tesseract-ocr.github.io/tessdoc/Installation.html", "language_models": "https://github.com/tesseract-ocr/tessdata_fast", "ghostscript": "https://ghostscript.com/releases/gsdnld.html", "verapdf": "https://verapdf.org/software/"}}
    return {"platform": "linux", "commands": ["sudo apt install tesseract-ocr tesseract-ocr-por tesseract-ocr-eng ghostscript"],
            "steps": ["Instale veraPDF e um Java compatível pelo instalador oficial; configure o launcher no perfil."],
            "links": {"verapdf": "https://verapdf.org/software/", "tesseract": "https://tesseract-ocr.github.io/tessdoc/Installation.html"}}


def _run_install(command: list[str], *, timeout: int = 1200, environment: dict | None = None, cwd: Path | None = None, stage: str = "Preparando componentes documentais.", progress: Callable[[str], None] | None = None) -> None:
    """Executa uma etapa com aviso periódico e devolve o diagnóstico do instalador."""
    try:
        from ...runtime import RuntimeError as SharedRuntimeError, run_with_progress
        code, detail = run_with_progress(command, environment=dict(environment or {}), timeout=timeout, stage=stage, progress=progress, cwd=cwd)
    except SharedRuntimeError as error:
        raise DocumentError("installation_failed", f"{stage} Não foi possível concluir a etapa.") from error
    if code:
        suffix = f" Detalhe: {detail}" if detail else ""
        raise DocumentError("installation_failed", f"{stage} O instalador retornou código {code}.{suffix}")
    if progress:
        progress(f"{stage} Concluída.")


def install_runtime(root: Path, tier: str = "base", gpu: bool = False, progress: Callable[[str], None] | None = None) -> dict:
    """Instala versões explícitas no runtime privado, somente por ação do setup.

    Tesseract, Ghostscript e veraPDF são dependências nativas diagnosticadas;
    não alteramos pacotes globais nem executamos instaladores administrativos.
    """
    if tier not in {"base", "advanced", "pdfa"}:
        raise DocumentError("invalid_argument", "Nível de instalação inválido.")
    from ..runtime import ensure_mcp_runtime, mcp_runtime_python
    preparation = installation_directory(root)
    environment = local_process_environment(preparation)
    if progress:
        ensure_mcp_runtime(root, progress=progress)
    else:
        ensure_mcp_runtime(root)
    executable = mcp_runtime_python(root)
    if tier == "advanced":
        executable = advanced_python(root)
        if not executable.is_file():
            if progress:
                ensure_mcp_runtime(root, progress=progress)
            else:
                ensure_mcp_runtime(root)
            executable = advanced_python(root)
        requirements = list(ADVANCED_REQUIREMENTS)
        # CPU e GPU compartilham o pacote importável paddle; não manter ambos.
        _run_install([*pip_command(), "--python", str(executable), "uninstall", "--yes", "paddlepaddle", "paddlepaddle-gpu"], environment=environment, cwd=preparation, stage="Removendo a variante OCR avançada anterior.", progress=progress)
        if gpu:
            # Official CUDA 12.6 wheels are not published on PyPI.
            _run_install([*pip_command(), "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", "paddlepaddle-gpu==3.2.0", "--index-url", "https://www.paddlepaddle.org.cn/packages/stable/cu126/"], environment=environment, cwd=preparation, stage="Instalando PaddleOCR com suporte NVIDIA CUDA 12.6.", progress=progress)
            requirements.pop()
            _run_install([str(executable), "-I", "-B", "-c", "import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0"], timeout=60, environment=environment, cwd=preparation, stage="Validando a GPU NVIDIA para OCR avançado.", progress=progress)
        _run_install([*pip_command(), "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", *requirements], environment=environment, cwd=preparation, stage="Instalando bibliotecas do OCR avançado.", progress=progress)
        prepare_advanced_models(root, device="gpu:0" if gpu else "cpu", progress=progress)
    else:
        _run_install([*pip_command(), "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", *BASE_REQUIREMENTS, *OCR_REQUIREMENTS], environment=environment, cwd=preparation, stage="Instalando bibliotecas PDF e OCR básico.", progress=progress)
    return {"tier": tier, "installed": True, "capabilities": get_capabilities(root)}


def prepare_advanced_models(root: Path, device: str = "cpu", profile: dict | None = None, progress: Callable[[str], None] | None = None) -> None:
    """Permite download dos modelos exclusivamente durante o setup explícito."""
    models = migrate_legacy_models(root, profile, progress)
    models.mkdir(parents=True, exist_ok=True)
    source = str(Path(__file__).resolve().parents[3])
    preparation = installation_directory(root)
    environment = local_process_environment(preparation, source=source)
    environment.update({"PADDLE_PDX_CACHE_HOME": str(models), "HF_HOME": str(models / "huggingface"), "PADDLE_HOME": str(models / "paddle")})
    _run_install([str(advanced_python(root)), "-B", "-m", "onmyoji_daemon.mcp.ocr.advanced_worker", "--prepare", str(models), "--device", device], environment=environment, cwd=preparation, stage=f"Baixando e validando modelos locais do OCR avançado ({device}).", progress=progress)
    # Alguns caches de download usam links internos para os blobs. O conjunto
    # preparado contém arquivos comuns para poder ser copiado sem seguir links.
    for candidate in models.rglob("*"):
        if not candidate.is_symlink():
            continue
        target = candidate.resolve()
        if not target.is_relative_to(models) or not target.is_file():
            raise DocumentError("invalid_models", "Os modelos preparados contêm um link externo ou um diretório vinculado.")
        temporary = preparation / (uuid4().hex + ".model")
        try:
            shutil.copyfile(target, temporary)
            os.replace(temporary, candidate)
        finally:
            temporary.unlink(missing_ok=True)
