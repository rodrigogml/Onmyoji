"""Instalação explícita e diagnóstico dos motores locais de documentos."""
from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ..document_processing.contracts import DocumentError


BASE_REQUIREMENTS = ("PyMuPDF==1.26.7", "pypdf==6.1.1", "reportlab==4.4.4", "Pillow==12.1.0", "openpyxl==3.1.5", "pyHanko==0.31.0")
OCR_REQUIREMENTS = ("ocrmypdf==17.13.0",)
ADVANCED_REQUIREMENTS = ("paddleocr[doc-parser]==3.7.0", "paddlepaddle==3.2.0")


def advanced_directory(root: Path) -> Path:
    return root.resolve() / "configs" / "mcp" / "ocr" / "advanced"


def advanced_python(root: Path) -> Path:
    return advanced_directory(root) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def model_directory(root: Path, profile: dict | None = None) -> Path:
    configured = (profile or {}).get("advanced_model_directory")
    return Path(configured).expanduser().resolve() if configured else root.resolve() / "configs" / "mcp" / "ocr" / "models"


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
            checked = subprocess.run([str(executable), "-B", "-c", probe], text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=10, check=False)
            if checked.returncode == 0:
                versions = json.loads(checked.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    packages = {}
    for requirement in (*BASE_REQUIREMENTS, *OCR_REQUIREMENTS):
        name = requirement.split("==")[0]
        if executable.is_file() and executable.resolve() != Path(sys.executable).resolve():
            version = versions.get(name)
        else:
            try:
                version = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                version = None
        packages[name] = {"installed": version, "supported": requirement}
    languages = installed_languages(profile)
    models = model_directory(root, profile)
    advanced_ready = advanced_python(root).is_file() and (models / "prepared.json").is_file()
    return {"packages": packages, "pdf": packages["PyMuPDF"]["installed"] is not None,
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


def _run_install(command: list[str], *, timeout: int = 1200, environment: dict | None = None) -> None:
    try:
        completed = subprocess.run(command, env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DocumentError("installation_failed", "Não foi possível preparar o runtime de documentos.") from error
    if completed.returncode:
        raise DocumentError("installation_failed", "A instalação dos componentes não foi concluída; confira a compatibilidade do Python e os diagnósticos do setup.")


def install_runtime(root: Path, tier: str = "base", gpu: bool = False) -> dict:
    """Instala versões explícitas no runtime privado, somente por ação do setup.

    Tesseract, Ghostscript e veraPDF são dependências nativas diagnosticadas;
    não alteramos pacotes globais nem executamos instaladores administrativos.
    """
    if tier not in {"base", "advanced", "pdfa"}:
        raise DocumentError("invalid_argument", "Nível de instalação inválido.")
    from ..runtime import ensure_mcp_runtime, mcp_runtime_python
    ensure_mcp_runtime(root)
    executable = mcp_runtime_python(root)
    if tier == "advanced":
        executable = advanced_python(root)
        if not executable.is_file():
            _run_install([sys.executable, "-B", "-m", "venv", "--without-pip", str(advanced_directory(root))], timeout=120)
        requirements = list(ADVANCED_REQUIREMENTS)
        # CPU e GPU compartilham o pacote importável paddle; não manter ambos.
        _run_install([sys.executable, "-m", "pip", "--python", str(executable), "uninstall", "--yes", "paddlepaddle", "paddlepaddle-gpu"])
        if gpu:
            # Official CUDA 12.6 wheels are not published on PyPI.
            _run_install([sys.executable, "-m", "pip", "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", "paddlepaddle-gpu==3.2.0", "--index-url", "https://www.paddlepaddle.org.cn/packages/stable/cu126/"])
            requirements.pop()
            _run_install([str(executable), "-B", "-c", "import paddle; assert paddle.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0"], timeout=60)
        _run_install([sys.executable, "-m", "pip", "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", *requirements])
        prepare_advanced_models(root, device="gpu:0" if gpu else "cpu")
    else:
        _run_install([sys.executable, "-m", "pip", "--python", str(executable), "install", "--disable-pip-version-check", "--no-input", *BASE_REQUIREMENTS, *OCR_REQUIREMENTS])
    return {"tier": tier, "installed": True, "capabilities": get_capabilities(root)}


def prepare_advanced_models(root: Path, device: str = "cpu", profile: dict | None = None) -> None:
    """Permite download dos modelos exclusivamente durante o setup explícito."""
    models = model_directory(root, profile)
    models.mkdir(parents=True, exist_ok=True)
    source = str(Path(__file__).resolve().parents[3])
    environment = {**os.environ, "PYTHONPATH": source, "PYTHONDONTWRITEBYTECODE": "1", "PADDLE_PDX_CACHE_HOME": str(models), "HF_HOME": str(models / "huggingface"), "PADDLE_HOME": str(models / "paddle")}
    _run_install([str(advanced_python(root)), "-B", "-m", "onmyoji_daemon.mcp.ocr.advanced_worker", "--prepare", str(models), "--device", device], environment=environment)
