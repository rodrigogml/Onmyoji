"""OCR preserva origem, coordenadas, limites e falha sem falsos sucessos."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from onmyoji_daemon.mcp.document_processing.contracts import DocumentError
from onmyoji_daemon.mcp.ocr import service
from onmyoji_daemon.mcp.ocr.advanced_worker import _normalize_result


class Context:
    def __init__(self, root):
        self.root = root; self.namespace = "ocr"; self.profile_name = "test"
        self.profile = {"engine": "tesseract", "languages": ["por", "eng"], "dpi": 300}
        self.defaults = {"max_pixels": 50000000, "max_pages": 2000, "timeout_seconds": 30}
        self.job_dir = root; self.sequence = 0

    def temporary_path(self, suffix):
        self.sequence += 1
        return self.job_dir / f"temporary-{self.sequence}{suffix}"

    def input_path(self, value):
        return Path(value)

    def stage_output(self, value):
        return Path(value)


def image_file(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "scan.png"
    Image.new("RGB", (120, 80), "white").save(path)
    return path


def tsv():
    return "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t98.5\tOlá\n5\t1\t1\t1\t1\t2\t45\t20\t25\t12\t45\tmundo\n"


def configure_engine(monkeypatch):
    monkeypatch.setattr(service, "command_path", lambda _name: "tesseract")
    monkeypatch.setattr(service, "installed_languages", lambda _profile: ["por", "eng", "osd"])


def test_tesseract_preserves_text_boxes_confidence_and_source(tmp_path, monkeypatch):
    path = image_file(tmp_path); configure_engine(monkeypatch)
    calls = []
    def run(command, _context, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=tsv(), stderr="")
    monkeypatch.setattr(service, "_run", run)

    result = service.recognize_image(path, {}, Context(tmp_path))

    assert result["text"] == "Olá mundo"
    assert result["blocks"][0] == {"page": 1, "text": "Olá", "bbox": [10, 20, 40, 32], "source": "ocr", "confidence": 0.985}
    assert result["warnings"] and "baixa confiança" in result["warnings"][0]
    assert "por+eng" in calls[0] and calls[0][-1] == "tsv"


def test_native_ocr_reference_corpus_reports_character_error_rate(tmp_path, record_property):
    """Mede regressão em texto impresso limpo quando os modelos nativos estão instalados."""
    from onmyoji_daemon.mcp.ocr.runtime import installed_languages, command_path
    if not command_path("tesseract") or not {"por", "eng"} <= set(installed_languages()):
        pytest.skip("Tesseract e idiomas por/eng necessários para homologação nativa.")
    Image = pytest.importorskip("PIL.Image")
    from PIL import ImageDraw, ImageFont
    expected = "Documento de teste. Extração de texto em português. Página 1."
    image = Image.new("RGB", (1800, 150), "white")
    ImageDraw.Draw(image).text((30, 45), expected, fill="black", font=ImageFont.load_default(size=36))
    source = tmp_path / "reference.png"; image.save(source, dpi=(300, 300))
    recognized = service.recognize_image(source, {}, Context(tmp_path))["text"]
    actual = " ".join(recognized.split())
    previous = list(range(len(actual) + 1))
    for row, character in enumerate(expected, 1):
        current = [row]
        for column, candidate in enumerate(actual, 1):
            current.append(min(current[-1] + 1, previous[column] + 1, previous[column - 1] + (character != candidate)))
        previous = current
    rate = previous[-1] / len(expected)
    record_property("ocr_engine", "tesseract"); record_property("character_error_rate", rate)
    assert rate <= 0.05, {"expected": expected, "actual": actual, "cer": rate}


def test_uninstalled_language_rejects_before_ocr_execution(tmp_path, monkeypatch):
    path = image_file(tmp_path); configure_engine(monkeypatch)
    monkeypatch.setattr(service, "_run", lambda *_args, **_kwargs: pytest.fail("OCR must not start"))
    with pytest.raises(DocumentError) as failure:
        service.recognize_image(path, {"languages": ["fra"]}, Context(tmp_path))
    assert failure.value.code == "language_unavailable"


def test_oversized_image_rejects_before_engine_execution(tmp_path, monkeypatch):
    path = image_file(tmp_path); context = Context(tmp_path); context.defaults["max_pixels"] = 10
    monkeypatch.setattr(service, "_tesseract_frame", lambda *_args: pytest.fail("Engine must not start"))
    with pytest.raises(DocumentError) as failure:
        service.recognize_image(path, {}, context)
    assert failure.value.code == "resource_limit"


def test_multipage_tiff_returns_each_frame(tmp_path, monkeypatch):
    Image = pytest.importorskip("PIL.Image")
    first = Image.new("RGB", (120, 80), "white"); second = Image.new("RGB", (120, 80), "black")
    path = tmp_path / "pages.tiff"; first.save(path, save_all=True, append_images=[second])
    configure_engine(monkeypatch)
    monkeypatch.setattr(service, "_run", lambda command, _context, **_kwargs: subprocess.CompletedProcess(command, 0, stdout=tsv(), stderr=""))

    result = service.recognize_image(path, {}, Context(tmp_path))

    assert [page["page"] for page in result["pages"]] == [1, 2]
    assert [block["page"] for block in result["blocks"]] == [1, 1, 2, 2]


def test_rotated_coordinates_map_back_to_source_image():
    # A 90-degree clockwise rotation maps this source rectangle to the right.
    assert service._source_box([40, 10, 60, 30], [(-90, (100, 80), (80, 100))]) == [10, 20, 30, 40]


def test_pdf_internal_ocr_uses_enabled_ocr_profile_and_does_not_widen_roots(tmp_path):
    config = tmp_path / "configs" / "mcp"; config.mkdir(parents=True)
    (config / "ocr.toml").write_text('[profiles.text]\nenabled = true\nengine = "tesseract"\n', encoding="utf-8")
    context = Context(tmp_path); context.namespace = "pdf"; context.profile = {"ocr_profile": "text", "readable_roots": ["source"]}
    assert service._ocr_profile(context)["engine"] == "tesseract"
    assert context.profile["readable_roots"] == ["source"]
    context.profile["ocr_profile"] = "missing"
    with pytest.raises(DocumentError) as failure:
        service._ocr_profile(context)
    assert failure.value.code == "ocr_profile_unavailable"


def test_advanced_ocr_never_downloads_missing_models(tmp_path, monkeypatch):
    path = image_file(tmp_path)
    monkeypatch.setattr(service, "_run", lambda *_args, **_kwargs: pytest.fail("No model inference/download should start"))
    with pytest.raises(DocumentError) as failure:
        service.analyze_image_layout(path, {}, Context(tmp_path))
    assert failure.value.code == "models_unavailable"


def test_advanced_result_normalizes_cells_and_engine_confidence():
    raw = {"res": {"overall_ocr_res": {"rec_texts": ["Descrição"], "rec_scores": [0.9], "rec_boxes": [[1, 2, 30, 20]]}, "table_res_list": [{"pred_html": "<table><tr><th>Nome</th><th>Valor</th></tr><tr><td>Água</td><td>10</td></tr></table>", "table_region_bbox": [0, 0, 200, 100]}]}}
    result = _normalize_result(SimpleNamespace(json=raw), 1)
    assert result["blocks"][0]["confidence"] == 0.9
    assert result["tables"][0]["cells"] == [["Nome", "Valor"], ["Água", "10"]]
    assert result["tables"][0]["uncertain"] is True


def test_pdfa_claim_requires_successful_verapdf_report(tmp_path, monkeypatch):
    configure_engine(monkeypatch)
    monkeypatch.setattr(service, "_run", lambda command, context, **kwargs: subprocess.CompletedProcess(command, 0, stdout='<report><validationReport isCompliant="false" profileName="PDF/A-2B" /></report>', stderr=""))
    assert service.validate_pdfa(tmp_path / "input.pdf", Context(tmp_path))["compliant"] is False
    monkeypatch.setattr(service, "_run", lambda command, context, **kwargs: subprocess.CompletedProcess(command, 0, stdout="not xml", stderr=""))
    with pytest.raises(DocumentError) as failure:
        service.validate_pdfa(tmp_path / "input.pdf", Context(tmp_path))
    assert failure.value.code == "invalid_engine_result"


def test_missing_native_executable_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(service.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("private-path")))
    with pytest.raises(DocumentError) as failure:
        service._run(["not-found"], Context(tmp_path))
    assert failure.value.code == "dependency_unavailable" and "private-path" not in str(failure.value)


def test_capabilities_probe_the_private_runtime_not_the_setup_interpreter(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.ocr import runtime
    executable = tmp_path / "private-python.exe"; executable.touch()
    monkeypatch.setattr("onmyoji_daemon.mcp.runtime.mcp_runtime_python", lambda _root: executable)
    monkeypatch.setattr(runtime, "installed_languages", lambda _profile: ["por", "eng"])
    monkeypatch.setattr(runtime, "command_path", lambda _name: None)
    versions = {name.split("==")[0]: name.split("==")[1] for name in (*runtime.BASE_REQUIREMENTS, *runtime.OCR_REQUIREMENTS)}
    calls = []
    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(versions), stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", run)

    result = runtime.get_capabilities(tmp_path)

    assert result["pdf"] is True and result["packages"]["PyMuPDF"]["installed"] == "1.26.7"
    assert calls[0][0] == str(executable)


def test_model_directory_rejects_resources_outside_the_instance(tmp_path):
    from onmyoji_daemon.mcp.ocr.runtime import model_directory
    expected = tmp_path / "configs" / "mcp" / "ocr" / "models"
    assert model_directory(tmp_path) == expected
    assert model_directory(tmp_path, {"advanced_model_directory": str(expected / "latin")}) == expected / "latin"
    with pytest.raises(DocumentError, match="própria instância"):
        model_directory(tmp_path, {"advanced_model_directory": str(tmp_path / "external-models")})


def test_capabilities_do_not_use_host_libraries_when_instance_runtime_is_missing(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.ocr import runtime
    monkeypatch.setattr(runtime.importlib.metadata, "version", lambda _name: pytest.fail("Host libraries are not instance dependencies"))
    monkeypatch.setattr(runtime, "installed_languages", lambda _profile: [])
    monkeypatch.setattr(runtime, "command_path", lambda _name: None)

    capabilities = runtime.get_capabilities(tmp_path)

    assert capabilities["runtime_ready"] is False and capabilities["pdf"] is False
    assert all(package["installed"] is None for package in capabilities["packages"].values())


def test_ocr_subprocess_uses_job_directory_for_temporary_files_and_cache(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setenv("PYTHONPATH", "host-python-libraries")
    monkeypatch.setenv("TEMP", "host-temp")
    def run(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    monkeypatch.setattr(service.subprocess, "run", run)

    service._run(["tesseract", "input.png", "stdout"], Context(tmp_path))

    assert captured["cwd"] == str(tmp_path)
    assert {captured["env"][key] for key in ("TMP", "TEMP", "TMPDIR")} == {str(tmp_path)}
    assert captured["env"]["XDG_CACHE_HOME"] == str(tmp_path / "cache")
    assert "PYTHONPATH" not in captured["env"]


def test_model_cache_copy_does_not_share_writable_model_files(tmp_path):
    from onmyoji_daemon.mcp.ocr.runtime import copy_model_cache
    models = tmp_path / "models"; models.mkdir()
    resource = models / "weights.bin"; resource.write_bytes(b"original")
    cache = copy_model_cache(models, tmp_path / "work" / "model-cache")
    (cache / "weights.bin").write_bytes(b"activity")
    assert resource.read_bytes() == b"original"


def test_advanced_inference_uses_a_working_copy_of_prepared_resources(tmp_path, monkeypatch):
    source = image_file(tmp_path)
    executable = tmp_path / "advanced-python.exe"; executable.touch()
    models = tmp_path / "configs" / "mcp" / "ocr" / "models"; models.mkdir(parents=True)
    (models / "prepared.json").write_text('{"device":"cpu"}', encoding="utf-8")
    (models / "weights.bin").write_bytes(b"resource")
    monkeypatch.setattr(service, "advanced_python", lambda _root: executable)
    def run(command, context, **kwargs):
        request = json.loads(Path(command[-2]).read_text(encoding="utf-8"))
        cache = Path(request["models"])
        assert cache != models and cache.is_relative_to(context.job_dir)
        assert kwargs["env"]["PADDLE_PDX_CACHE_HOME"] == str(cache)
        (cache / "weights.bin").write_bytes(b"working-copy")
        Path(command[-1]).write_text('{"text":"reconhecido"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    monkeypatch.setattr(service, "_run", run)

    assert service.analyze_image_layout(source, {}, Context(tmp_path))["text"] == "reconhecido"
    assert (models / "weights.bin").read_bytes() == b"resource"


def test_installation_environment_and_target_are_private_to_instance(tmp_path, monkeypatch):
    from onmyoji_daemon.mcp.ocr import runtime
    root = tmp_path / "instance"; root.mkdir()
    executable = root / "configs" / "mcp" / "venv" / "Scripts" / "python.exe"
    calls = []
    monkeypatch.setattr("onmyoji_daemon.mcp.runtime.ensure_mcp_runtime", lambda _root: None)
    monkeypatch.setattr("onmyoji_daemon.mcp.runtime.mcp_runtime_python", lambda _root: executable)
    monkeypatch.setattr(runtime, "pip_command", lambda: ["installer-python", "-I", "pip-main.py"])
    monkeypatch.setattr(runtime, "_run_install", lambda command, **kwargs: calls.append((command, kwargs)))
    monkeypatch.setattr(runtime, "get_capabilities", lambda _root: {})
    monkeypatch.setenv("PIP_TARGET", "host-target")
    monkeypatch.setenv("PYTHONPATH", "host-libraries")

    runtime.install_runtime(root)

    command, arguments = calls[0]
    assert command[command.index("--python") + 1] == str(executable)
    assert arguments["cwd"] == root / "tmp" / "document-install"
    assert arguments["environment"]["TEMP"] == str(root / "tmp" / "document-install")
    assert "PIP_TARGET" not in arguments["environment"] and "PYTHONPATH" not in arguments["environment"]


def test_searchable_pdf_auto_uses_redo_to_recognize_hybrid_pages(tmp_path, monkeypatch):
    configure_engine(monkeypatch)
    context = Context(tmp_path); source = tmp_path / "source.pdf"; source.write_bytes(b"PDF")
    page = SimpleNamespace(rect=SimpleNamespace(width=595, height=842), get_text=lambda: "Native text")
    class FakeDocument:
        page_count = 1; is_encrypted = False; needs_pass = False
        def __iter__(self): return iter([page])
        def close(self): pass
    monkeypatch.setattr(service, "_open_pdf", lambda *_args: FakeDocument())
    monkeypatch.setattr(service.importlib.util, "find_spec", lambda _name: object())
    calls = []
    def run(command, *_args, **_kwargs):
        calls.append(command)
        Path(command[-1]).write_bytes(b"searchable")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
    monkeypatch.setattr(service, "_run", run)

    result = service.make_searchable({"input_path": str(source), "output_path": str(tmp_path / "output.pdf"), "mode": "auto", "rotate": True}, context)

    assert result["pages_with_text"] == 1
    assert "--redo-ocr" in calls[0] and "--skip-text" not in calls[0] and "--rotate-pages" in calls[0]


def test_searchable_pdf_native_does_not_require_ocr_engine(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    source = tmp_path / "source.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 70), "Native text")
        document.save(source)
    monkeypatch.setattr(service, "installed_languages", lambda _profile: pytest.fail("native mode must not query OCR languages"))
    result = service.make_searchable({"input_path": str(source), "output_path": str(tmp_path / "output.pdf"), "mode": "native"}, Context(tmp_path))
    assert result["engine"] == "pymupdf" and result["pages_with_text"] == 1
    with pymupdf.open(tmp_path / "output.pdf") as document:
        assert "Native text" in document[0].get_text()
