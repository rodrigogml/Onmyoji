"""OCR local de imagens e PDFs, com proveniência e dependências opcionais."""
from __future__ import annotations

import csv
from html.parser import HTMLParser
import importlib.util
import io
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
import xml.etree.ElementTree as ET

from ..document_processing.contracts import DocumentError
from .runtime import advanced_python, command_path, get_capabilities, installed_languages, model_directory, local_process_environment, copy_model_cache


def _ocr_profile(context) -> dict:
    if context.namespace == "ocr":
        return context.profile
    name = context.profile.get("ocr_profile")
    if not name:
        raise DocumentError("ocr_profile_required", "Selecione um perfil OCR na configuração PDF.")
    try:
        data = tomllib.loads((context.root / "configs" / "mcp" / "ocr.toml").read_text(encoding="utf-8"))
        profile = data["profiles"][name]
    except (OSError, ValueError, KeyError) as error:
        raise DocumentError("ocr_profile_unavailable", "O perfil OCR configurado está indisponível.") from error
    if profile.get("enabled") is not True:
        raise DocumentError("ocr_profile_unavailable", "O perfil OCR configurado está desabilitado.")
    return profile


def _run(command: list[str], context, *, env: dict | None = None, allowed_codes=(0,)) -> subprocess.CompletedProcess:
    if os.name == "nt" and Path(command[0]).suffix.lower() in {".bat", ".cmd"} and any(re.search(r"[&|<>^%!\r\n]", argument) for argument in command):
        raise DocumentError("invalid_path", "O launcher de validação não aceita metacaracteres de shell nos caminhos; escolha um nome simples para o arquivo.")
    try:
        environment = local_process_environment(context.job_dir, base=env, source=env.get("PYTHONPATH") if env else None)
        completed = subprocess.run(command, env=environment, cwd=str(context.job_dir), text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=int(context.defaults.get("timeout_seconds", 1800)), check=False)
    except subprocess.TimeoutExpired as error:
        raise DocumentError("operation_timeout", "O processamento OCR excedeu o tempo configurado.") from error
    except OSError as error:
        raise DocumentError("dependency_unavailable", "Um executável necessário ao processamento OCR está indisponível.") from error
    if completed.returncode not in allowed_codes:
        raise DocumentError("ocr_failed", "O motor OCR não concluiu o processamento; confira os idiomas, dependências e formato do documento.")
    return completed


def _image_module():
    try:
        from PIL import Image, ImageOps, ImageStat
        return Image, ImageOps, ImageStat
    except ImportError as error:
        raise DocumentError("dependency_unavailable", "Instale o runtime básico de documentos pelo setup.") from error


def _deskew_angle(image) -> float:
    """Estima inclinação pequena por projeção horizontal, sem modelo externo."""
    Image, _, _ = _image_module()
    sample = image.convert("L")
    sample.thumbnail((900, 900))
    sample = sample.point(lambda value: 0 if value < 160 else 255)
    def score(angle):
        rotated = sample.rotate(angle, resample=Image.Resampling.NEAREST, expand=False, fillcolor=255)
        pixels = rotated.load()
        totals = [sum(255 - pixels[x, y] for x in range(0, rotated.width, 2)) for y in range(rotated.height)]
        return sum((right - left) ** 2 for left, right in zip(totals, totals[1:]))
    best = max((step / 2 for step in range(-10, 11)), key=score)
    return best if abs(best) >= 0.5 else 0.0


def _source_box(box, transforms):
    points = [(box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3])]
    for angle, before, after in reversed(transforms):
        cosine, sine = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        points = [(cosine * (x - after[0] / 2) - sine * (y - after[1] / 2) + before[0] / 2,
                   sine * (x - after[0] / 2) + cosine * (y - after[1] / 2) + before[1] / 2) for x, y in points]
    return [round(min(x for x, _ in points), 3), round(min(y for _, y in points), 3), round(max(x for x, _ in points), 3), round(max(y for _, y in points), 3)]


def prepare_image(image, options: dict, profile: dict, context):
    """Corrige geometria mantendo a transformação inversa das coordenadas."""
    transforms, warnings = [], []
    prepared = image.convert("RGB")
    if options.get("rotate", options.get("rotate_pages", profile.get("rotate_pages", False))):
        executable = command_path(str(profile.get("tesseract_path", "tesseract")))
        if not executable:
            raise DocumentError("dependency_unavailable", "Tesseract é necessário para estimar a orientação da imagem.")
        original = context.temporary_path(".png"); prepared.save(original)
        tessdata = ["--tessdata-dir", str(profile["tessdata_dir"])] if profile.get("tessdata_dir") else []
        orientation = _run([executable, str(original), "stdout", "--psm", "0", *tessdata], context, allowed_codes=(0, 1))
        match = re.search(r"Rotate:\s*(0|90|180|270)", orientation.stdout + orientation.stderr)
        if match and int(match.group(1)):
            before = prepared.size; angle = -int(match.group(1))
            prepared = prepared.rotate(angle, expand=True, fillcolor="white")
            transforms.append((angle, before, prepared.size))
        elif not match:
            warnings.append("A orientação não pôde ser estimada; a página foi preservada.")
    if options.get("deskew", profile.get("deskew", False)):
        angle = _deskew_angle(prepared)
        if angle:
            before = prepared.size
            prepared = prepared.rotate(angle, expand=True, fillcolor="white")
            transforms.append((angle, before, prepared.size))
    if prepared.width * prepared.height > int(context.defaults.get("max_pixels", 50000000)):
        raise DocumentError("resource_limit", "A imagem corrigida excede o limite de pixels.")
    return prepared, transforms, warnings


def _tesseract_frame(image, options: dict, profile: dict, context, page: int) -> dict:
    executable = command_path(str(profile.get("tesseract_path", "tesseract")))
    if not executable:
        raise DocumentError("dependency_unavailable", "Tesseract não foi encontrado; configure o executável no perfil OCR.")
    languages = options.get("languages", profile.get("languages", ["por", "eng"]))
    if isinstance(languages, str):
        languages = languages.replace(",", "+").split("+")
    if not isinstance(languages, list) or not languages or not all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_]+", value) for value in languages):
        raise DocumentError("invalid_argument", "Informe uma lista válida de idiomas OCR.")
    installed = installed_languages(profile)
    if any(language not in installed for language in languages):
        raise DocumentError("language_unavailable", "Um idioma solicitado não está instalado no Tesseract.")
    dpi = options.get("dpi", profile.get("dpi", 300))
    psm = options.get("page_segmentation_mode", 3)
    if type(dpi) is not int or not 72 <= dpi <= 1200 or type(psm) is not int or psm not in {1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13}:
        raise DocumentError("invalid_argument", "DPI ou modo de segmentação OCR inválido.")
    source_size = image.size
    prepared, transforms, warnings = prepare_image(image, options, profile, context)
    frame_path = context.temporary_path(".png")
    tessdata = ["--tessdata-dir", str(profile["tessdata_dir"])] if profile.get("tessdata_dir") else []
    prepared.save(frame_path)
    completed = _run([executable, str(frame_path), "stdout", "-l", "+".join(languages), "--dpi", str(dpi), "--psm", str(psm), *tessdata, "tsv"], context)
    words, lines = [], {}
    try:
        for row in csv.DictReader(io.StringIO(completed.stdout), delimiter="\t"):
            if row.get("level") != "5" or not (text := row.get("text", "").strip()):
                continue
            confidence = float(row["conf"])
            x, y, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
            word = {"page": page, "text": text, "bbox": _source_box([x, y, x + width, y + height], transforms), "source": "ocr"}
            if confidence >= 0:
                word["confidence"] = round(confidence / 100, 4)
            words.append(word)
            key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
            lines.setdefault(key, []).append(text)
    except (ValueError, KeyError) as error:
        raise DocumentError("invalid_engine_result", "O motor OCR retornou um resultado inválido.") from error
    confidences = [word["confidence"] for word in words if "confidence" in word]
    if confidences and min(confidences) < 0.5:
        warnings.append("Há palavras com baixa confiança; revise a transcrição.")
    return {"page": page, "text": "\n".join(" ".join(line) for line in lines.values()), "blocks": words, "width": source_size[0], "height": source_size[1], "coordinate_system": "source_image_pixels", "preprocessing": {"rotations": [step[0] for step in transforms]}, "warnings": warnings}


def recognize_image(path: Path, options: dict, context) -> dict:
    """Reconhece imagem já autorizada pelo chamador, inclusive frames TIFF.

    PDF usa este contrato para imagens temporárias que ele próprio renderizou.
    A autorização do documento original permanece no contexto PDF.
    """
    profile = _ocr_profile(context)
    if options.get("engine", profile.get("engine", "tesseract")) == "paddleocr":
        return _advanced(path, options, profile, context, layout=False)
    Image, ImageOps, _ = _image_module()
    pages = []
    try:
        with Image.open(path) as image:
            if getattr(image, "n_frames", 1) > int(context.defaults.get("max_pages", 2000)):
                raise DocumentError("resource_limit", "A imagem excede o limite de páginas.")
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                if image.width * image.height > int(context.defaults.get("max_pixels", 50000000)):
                    raise DocumentError("resource_limit", "A imagem excede o limite de pixels por página.")
                pages.append(_tesseract_frame(ImageOps.exif_transpose(image.copy()), options, profile, context, index + 1))
    except (OSError, Image.DecompressionBombError) as error:
        raise DocumentError("invalid_image", "A imagem é inválida ou excede os limites de segurança.") from error
    return {"engine": "tesseract", "pages": pages, "text": "\n\f\n".join(page["text"] for page in pages), "blocks": [block for page in pages for block in page["blocks"]], "warnings": [warning for page in pages for warning in page["warnings"]]}


def _advanced(path: Path, options: dict, profile: dict, context, *, layout: bool) -> dict:
    languages = options.get("languages", profile.get("languages", ["por", "eng"]))
    supported = {"por", "eng", "fra", "spa", "deu", "ita", "nld", "dan", "fin", "swe", "nor", "pol", "ces", "slk", "hun", "ron", "tur", "vie", "ind", "msa", "hrv", "slv", "cat", "eus", "glg", "isl", "lav", "lit", "est", "sqi", "afr", "swa"}
    if not isinstance(languages, list) or not languages or any(language not in supported for language in languages):
        raise DocumentError("language_unavailable", "O modelo avançado preparado reconhece idiomas de escrita latina; use Tesseract para outros idiomas instalados.")
    executable, models = advanced_python(context.root), model_directory(context.root, profile)
    if not executable.is_file() or not (models / "prepared.json").is_file():
        raise DocumentError("models_unavailable", "Prepare o runtime e os modelos avançados pelo setup OCR.")
    cached_models = copy_model_cache(models, context.temporary_path(".models"))
    request = context.temporary_path(".json")
    result = context.temporary_path(".json")
    effective_options = {"rotate_pages": profile.get("rotate_pages", False), "deskew": profile.get("deskew", False), **options}
    request.write_text(json.dumps({"input_path": str(path), "models": str(cached_models), "layout": layout, "options": effective_options, "preprocessing_profile": {"tesseract_path": profile.get("tesseract_path") or "tesseract", "tessdata_dir": profile.get("tessdata_dir", "")}, "max_pixels": context.defaults.get("max_pixels", 50000000), "max_pages": context.defaults.get("max_pages", 2000), "timeout_seconds": context.defaults.get("timeout_seconds", 1800)}, ensure_ascii=False), encoding="utf-8")
    environment = local_process_environment(context.job_dir, source=str(Path(__file__).resolve().parents[3]))
    environment.update({"HF_HUB_OFFLINE": "1", "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True", "PADDLE_PDX_CACHE_HOME": str(cached_models)})
    _run([str(executable), "-B", "-m", "onmyoji_daemon.mcp.ocr.advanced_worker", str(request), str(result)], context, env=environment)
    try:
        data = json.loads(result.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise DocumentError("invalid_engine_result", "O motor avançado não produziu um resultado válido.") from error
    if "error" in data:
        raise DocumentError(data["error"]["code"], data["error"]["message"])
    return data


def analyze_image_layout(path: Path, options: dict, context) -> dict:
    """Extrai layout e tabelas pelo motor avançado preparado, sempre offline."""
    return _advanced(path, options, _ocr_profile(context), context, layout=True)


def _open_pdf(path: Path, args: dict, context):
    try:
        import pymupdf
    except ImportError as error:
        raise DocumentError("dependency_unavailable", "Instale o runtime básico de PDF pelo setup.") from error
    try:
        document = pymupdf.open(path)
    except Exception as error:
        raise DocumentError("invalid_pdf", "O documento PDF não pôde ser aberto.") from error
    if document.needs_pass:
        password = context.resolve_password(args.get("password_ref")) if args.get("password_ref") else ""
        if not password or not document.authenticate(password):
            document.close()
            raise DocumentError("password_required", "Informe uma referência de senha válida para o PDF.")
    if document.page_count > int(context.defaults.get("max_pages", 2000)):
        document.close()
        raise DocumentError("resource_limit", "O documento excede o limite de páginas.")
    signed = document.get_sigflags() > 0
    if signed:
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            if reader.is_encrypted:
                reader.decrypt(password)
            signed = any(field.get("/FT") == "/Sig" and bool(field.get("/V")) for field in (reader.get_fields() or {}).values())
        except Exception:
            # Conservador somente quando o documento declara assinaturas mas
            # seu formulário não pode ser inspecionado pelo segundo leitor.
            signed = True
    if signed and not args.get("allow_signature_invalidation"):
        document.close()
        raise DocumentError("signed_document", "A transformação requer allow_signature_invalidation=true para gerar uma cópia sem preservar as assinaturas.")
    return document


def make_searchable(args: dict, context) -> dict:
    """Produz cópia pesquisável via OCRmyPDF sem modificar o PDF original."""
    profile = _ocr_profile(context)
    path = context.input_path(args["input_path"])
    output = context.stage_output(args["output_path"])
    mode = args.get("mode", "auto")
    if mode not in {"native", "auto", "ocr"}:
        raise DocumentError("invalid_argument", "Modo de OCR inválido.")
    document = _open_pdf(path, args, context)
    try:
        page_count = document.page_count
        if mode == "native":
            document.save(output, garbage=4, deflate=True)
            text_pages = sum(bool(page.get_text().strip()) for page in document)
            return {"engine": "pymupdf", "pages": page_count, "pages_with_text": text_pages, "warnings": ["Páginas sem texto nativo permanecem sem camada pesquisável."] if text_pages < page_count else []}
        dpi = args.get("dpi", profile.get("dpi", 300))
        if type(dpi) is not int or not 72 <= dpi <= 600:
            raise DocumentError("invalid_argument", "DPI de OCR deve estar entre 72 e 600.")
        for page in document:
            if math.ceil(page.rect.width * dpi / 72) * math.ceil(page.rect.height * dpi / 72) > int(context.defaults.get("max_pixels", 50000000)):
                raise DocumentError("resource_limit", "Uma página excederia o limite de pixels na resolução OCR solicitada.")
        if mode == "auto" and args.get("deskew", profile.get("deskew", False)):
            raise DocumentError("incompatible_options", "Correção de inclinação de PDF exige mode=ocr; auto preserva o texto e vetores originais.")
        if document.needs_pass:
            decrypted = context.temporary_path(".pdf")
            document.save(decrypted, encryption=1)
            path = decrypted
    finally:
        document.close()
    if importlib.util.find_spec("ocrmypdf") is None:
        raise DocumentError("dependency_unavailable", "Instale OCRmyPDF pelo setup de documentos.")
    languages = args.get("languages", profile.get("languages", ["por", "eng"]))
    if isinstance(languages, str):
        languages = languages.split("+")
    if not languages or not all(re.fullmatch(r"[A-Za-z0-9_]+", str(value)) for value in languages):
        raise DocumentError("invalid_argument", "Idiomas OCR inválidos.")
    if any(language not in installed_languages(profile) for language in languages):
        raise DocumentError("language_unavailable", "Instale os idiomas OCR solicitados antes de processar o PDF.")
    command = [sys.executable, "-B", "-m", "ocrmypdf", "--output-type", "pdf", "--force-ocr" if mode == "ocr" else "--redo-ocr", "--optimize", "0", "--jobs", "1", "--oversample", str(dpi), "--max-image-mpixels", str(context.defaults.get("max_pixels", 50000000) / 1000000), "-l", "+".join(languages)]
    if args.get("rotate", args.get("rotate_pages", profile.get("rotate_pages", False))):
        command.append("--rotate-pages")
    if args.get("deskew", profile.get("deskew", False)):
        command.append("--deskew")
    if args.get("allow_signature_invalidation"):
        command.append("--invalidate-digital-signatures")
    if args.get("pages"):
        if not re.fullmatch(r"[0-9, -]+", args["pages"]):
            raise DocumentError("invalid_argument", "Intervalo de páginas inválido.")
        command += ["--pages", args["pages"].replace(" ", "")]
    environment = {**os.environ, "TMPDIR": str(context.job_dir), "TMP": str(context.job_dir), "TEMP": str(context.job_dir), "PYTHONDONTWRITEBYTECODE": "1"}
    tesseract = command_path(str(profile.get("tesseract_path", "tesseract")))
    if tesseract:
        environment["PATH"] = str(Path(tesseract).parent) + os.pathsep + environment.get("PATH", "")
    if profile.get("tessdata_dir"):
        environment["TESSDATA_PREFIX"] = str(profile["tessdata_dir"])
    _run([*command, str(path), str(output)], context, env=environment)
    check = _open_pdf(output, {"allow_signature_invalidation": True}, context)
    try:
        if check.page_count != page_count:
            raise DocumentError("validation_failed", "O PDF pesquisável não preservou a quantidade de páginas.")
        text_pages = sum(bool(page.get_text().strip()) for page in check)
    finally:
        check.close()
    warnings = [] if text_pages else ["O motor não encontrou texto reconhecível no documento."]
    if mode == "ocr":
        warnings.append("OCR forçado rasteriza as páginas e achata objetos interativos na saída derivada.")
    return {"engine": "ocrmypdf", "pages": page_count, "pages_with_text": text_pages, "warnings": warnings}


def validate_pdfa(path: Path, context, profile: dict | None = None) -> dict:
    """Declara conformidade somente a partir do relatório XML do veraPDF."""
    profile = profile or context.profile
    executable = command_path(str(profile.get("verapdf_path", "verapdf")))
    if not executable:
        raise DocumentError("dependency_unavailable", "Configure o executável veraPDF para validar PDF/A.")
    completed = _run([executable, "--format", "xml", "--flavour", "2b", str(path)], context, allowed_codes=(0, 1))
    try:
        report = ET.fromstring(completed.stdout)
        reports = [node for node in report.iter() if node.tag.rsplit("}", 1)[-1] == "validationReport"]
        if not reports:
            raise ValueError("missing report")
        compliant = all(node.attrib.get("isCompliant") == "true" for node in reports)
    except (ET.ParseError, ValueError) as error:
        raise DocumentError("invalid_engine_result", "O veraPDF não produziu um relatório de conformidade válido.") from error
    return {"standard": "PDF/A-2b", "compliant": compliant, "validator": "veraPDF", "reports": [node.attrib for node in reports]}


def convert_pdfa(args: dict, context) -> dict:
    """Reutiliza o contrato de conversão e validação PDF/A do serviço PDF."""
    from ..pdf.service import execute as execute_pdf
    return execute_pdf("validate", {**args, "validation": "pdfa", "action": "convert_pdfa"}, context)


def execute(operation: str, args: dict, context) -> dict:
    if operation == "make_searchable":
        return make_searchable(args, context)
    path = context.input_path(args["input_path"])
    if operation == "recognize":
        result = recognize_image(path, args, context)
    elif operation == "analyze_layout":
        result = analyze_image_layout(path, args, context)
    else:
        raise DocumentError("unsupported_operation", "Operação OCR indisponível.")
    if args.get("output_path"):
        output = context.stage_output(args["output_path"])
        if output.suffix.lower() in {".txt", ".md"}:
            output.write_text(result.get("markdown", result.get("text", "")), encoding="utf-8")
        else:
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
