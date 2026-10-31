"""Operações PDF verificáveis sobre arquivos autorizados do workspace.

As páginas da interface começam em 1. Coordenadas públicas usam pontos e a
origem superior esquerda da página visível já rotacionada. Toda saída passa
pelo staging transacional do contexto; este módulo nunca sobrescreve fontes.
"""
from __future__ import annotations

import csv
import datetime
import difflib
import importlib
import io
import json
import math
from pathlib import Path
import re
import subprocess
from typing import Any
import xml.etree.ElementTree as ET

from ..document_processing.contracts import DocumentError


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise DocumentError(code, message)


def _dependency(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise DocumentError("backend_unavailable", f"Instale o componente documental {name} pelo setup.") from error


def pages(selection: object, count: int) -> list[int]:
    """Converte seleção pública em índices, preservando ordem e repetições."""
    if selection is None or selection == "":
        return list(range(count))
    if isinstance(selection, list):
        values = selection
    else:
        _require(isinstance(selection, str), "invalid_pages", "Informe páginas ou intervalos como 1-3,7.")
        values = []
        for part in selection.split(","):
            match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
            _require(match is not None, "invalid_pages", "Seleção de páginas inválida.")
            start, end = int(match[1]), int(match[2] or match[1])
            _require(end >= start and end - start <= count, "invalid_pages", "Intervalo de páginas inválido.")
            values.extend(range(start, end + 1))
    _require(bool(values) and all(type(n) is int and 1 <= n <= count for n in values), "invalid_pages", "Página fora do documento.")
    return [n - 1 for n in values]


def _number(value: object, label: str, minimum: float = 0) -> float:
    _require(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum, "invalid_argument", f"{label} inválido.")
    return float(value)


def _rect(page, value: object = None):
    fitz = _dependency("pymupdf")
    if value is None:
        return page.rect * page.derotation_matrix
    _require(isinstance(value, (list, tuple)) and len(value) == 4, "invalid_region", "Região deve conter x0, y0, x1 e y1.")
    visible = fitz.Rect(*[_number(n, "Coordenada") for n in value])
    _require(not visible.is_empty and not visible.is_infinite and page.rect.contains(visible), "invalid_region", "Região fora da página visível.")
    return visible * page.derotation_matrix


def _bbox(page, value) -> list[float]:
    return [round(n, 4) for n in (_dependency("pymupdf").Rect(value) * page.rotation_matrix)]


def _password(context, args: dict) -> str:
    return context.resolve_password(args["password_ref"]) if args.get("password_ref") else ""


def _open(context, args: dict, *, value: str | None = None, modify: bool = False):
    fitz = _dependency("pymupdf")
    path = context.input_path(value or args.get("input_path", ""))
    _require(path.suffix.lower() == ".pdf", "invalid_document", "A entrada deve ser um PDF.")
    try:
        doc = fitz.open(path)
    except Exception as error:
        raise DocumentError("invalid_document", "Não foi possível abrir o documento PDF.") from error
    try:
        password = ""
        if doc.needs_pass:
            password = _password(context, args)
            _require(bool(password), "password_required", "O PDF exige uma referência de senha no vault.")
            _require(bool(doc.authenticate(password)), "invalid_password", "A senha do PDF não foi aceita.")
        if doc.metadata.get("encryption"):
            if not password and args.get("password_ref"):
                password = _password(context, args)
                _require(bool(doc.authenticate(password)), "invalid_password", "A senha do PDF não foi aceita.")
            context._pdf_encrypted = True
            context._pdf_password = password
            context._pdf_permissions = doc.permissions
        _require(0 < len(doc) <= context.defaults.get("max_pages", 2000), "document_limit", "Quantidade de páginas fora do limite configurado.")
        if modify:
            signed = any(doc.xref_get_key(xref, "ByteRange")[0] != "null" for xref in range(1, doc.xref_length()))
            _require(not signed or args.get("allow_signature_invalidation") is True, "signed_document", "A transformação de PDF assinado exige allow_signature_invalidation=true.")
        return doc
    except BaseException:
        doc.close()
        raise


def _output(context, args: dict, suffix: str = ".pdf") -> Path:
    value = args.get("output_path")
    _require(isinstance(value, str) and Path(value).suffix.lower() == suffix, "invalid_output", f"Informe output_path com extensão {suffix}.")
    return context.stage_output(value)


def _destination(args: dict, name: str, index: int = 0) -> str:
    if args.get("output_paths"):
        _require(index < len(args["output_paths"]), "invalid_output", "Faltam caminhos de saída.")
        return args["output_paths"][index]
    directory = args.get("output_directory")
    _require(isinstance(directory, str) and bool(directory), "invalid_output", "Informe output_directory para os arquivos extraídos.")
    return str(Path(directory) / name)


def _save(doc, args: dict, context, *, verifier=None, **options) -> dict:
    fitz = _dependency("pymupdf")
    target = _output(context, args)
    count = len(doc)
    if "encryption" not in options and getattr(context, "_pdf_encrypted", False):
        password = getattr(context, "_pdf_password", "")
        _require(bool(password), "password_required", "Forneça password_ref para preservar a proteção na saída; use protect.decrypt para gerar uma cópia sem senha.")
        options.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password, permissions=getattr(context, "_pdf_permissions", 4095))
    doc.save(target, garbage=4, clean=True, deflate=True, **options)
    with fitz.open(target) as check:
        if check.needs_pass:
            secret = context.resolve_password(args["new_password_ref"]) if args.get("new_password_ref") else getattr(context, "_pdf_password", "")
            _require(bool(check.authenticate(secret)), "verification_failed", "Não foi possível verificar a saída protegida.")
        _require(len(check) == count, "verification_failed", "A saída PDF não preservou a quantidade esperada de páginas.")
        if verifier is not None:
            verifier(check)
    pdf = _dependency("pypdf")
    with target.open("rb") as stream:
        reader = pdf.PdfReader(stream, strict=True)
        if reader.is_encrypted:
            secret = context.resolve_password(args["new_password_ref"]) if args.get("new_password_ref") else getattr(context, "_pdf_password", "")
            _require(bool(reader.decrypt(secret)), "verification_failed", "O leitor independente não conseguiu verificar a proteção da saída.")
        _require(len(reader.pages) == count, "verification_failed", "O leitor independente não confirmou a estrutura da saída PDF.")
    return {"output_path": args["output_path"], "pages": count, "engine": "pymupdf"}


def _render(page, context, args: dict, *, clip=None):
    dpi = _number(args.get("dpi", 150), "DPI", 36)
    _require(dpi <= 1200, "render_limit", "DPI máximo de renderização: 1200.")
    area = clip or _rect(page)
    _require(area.width * area.height * (dpi / 72) ** 2 <= context.defaults.get("max_pixels", 50000000), "render_limit", "A página renderizada excede o limite de pixels.")
    return page.get_pixmap(dpi=round(dpi), clip=area * page.rotation_matrix, alpha=False)


def _write_export(context, args: dict, result: dict) -> None:
    if not args.get("output_path"):
        return
    target = context.stage_output(args["output_path"])
    fmt = args.get("format", target.suffix.lstrip("."))
    if fmt in {"json", ""}:
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt in {"text", "txt", "markdown", "md"}:
        target.write_text(result.get("text", ""), encoding="utf-8")
    elif fmt == "csv":
        with target.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            for table in result.get("tables", []):
                writer.writerows(table["cells"])
                writer.writerow([])
    elif fmt == "xlsx":
        workbook = _dependency("openpyxl").Workbook()
        workbook.remove(workbook.active)
        for n, table in enumerate(result.get("tables", []), 1):
            sheet = workbook.create_sheet(f"Tabela {n}")
            for row in table["cells"]:
                # Fórmulas de um documento são dados, não código de planilha.
                sheet.append([("'" + cell if isinstance(cell, str) and cell.startswith(("=", "+", "-", "@")) else cell) for cell in row])
        if not workbook.worksheets:
            workbook.create_sheet("Sem tabelas")
        workbook.save(target)
    else:
        raise DocumentError("invalid_format", "Formato de exportação indisponível.")


def _inspect(doc, args: dict, context) -> dict:
    selected = pages(args.get("pages"), len(doc))
    details = []
    for index in selected:
        page = doc[index]
        details.append({"page": index + 1, "width": page.rect.width, "height": page.rect.height, "rotation": page.rotation,
                        "cropbox": list(page.cropbox), "mediabox": list(page.mediabox), "text_characters": len(page.get_text()),
                        "fonts": [{"xref": value[0], "type": value[2], "name": value[3]} for value in page.get_fonts()],
                        "images": len(page.get_images()), "annotations": sum(1 for _ in page.annots() or []),
                        "forms": [{"name": widget.field_name, "type": widget.field_type_string} for widget in page.widgets() or []]})
    return {"pages": len(doc), "page_details": details, "metadata": doc.metadata, "bookmarks": doc.get_toc(),
            "attachments": doc.embfile_names(), "encrypted": bool(doc.metadata.get("encryption")), "signature_flags": doc.get_sigflags(),
            "repaired": bool(doc.is_repaired), "engine": "pymupdf"}


def _native_blocks(page, clip) -> list[dict]:
    result = []
    for block in page.get_text("dict", clip=clip, sort=True)["blocks"]:
        if block.get("type") == 0:
            value = "\n".join("".join(span["text"] for span in line["spans"]) for line in block["lines"])
            if value.strip():
                result.append({"text": value, "bbox": _bbox(page, block["bbox"]), "source": "native"})
    return result


def _text_blocks(doc, args: dict, context) -> dict:
    selected = pages(args.get("pages"), len(doc))
    mode = args.get("mode", "native")
    _require(mode in {"native", "ocr", "auto"}, "invalid_mode", "Modo de texto inválido.")
    blocks, warnings = [], []
    for index in selected:
        page = doc[index]
        clip = _rect(page, args.get("region"))
        native = _native_blocks(page, clip) if mode != "ocr" else []
        if mode == "auto" and native:
            useful = [block for block in native if any(char.isalnum() for char in block["text"]) and block["text"].count("\ufffd") <= max(1, len(block["text"]) * .1)]
            if len(useful) != len(native):
                warnings.append(f"Página {index + 1}: parte do texto nativo tem codificação incerta; confira a extração OCR.")
            native = useful
        for block in native:
            block["page"] = index + 1
        blocks.extend(native)
        regions = []
        if mode == "ocr" or (mode == "auto" and not native):
            regions = [clip]
        elif mode == "auto":
            # Reconhecer apenas imagens sem texto nativo sobreposto evita duplicar
            # camadas OCR de documentos híbridos ou previamente reconhecidos.
            fitz = _dependency("pymupdf")
            for image in page.get_image_info():
                image_rect = fitz.Rect(image["bbox"]) & clip
                if image_rect.is_empty:
                    continue
                if any((fitz.Rect(block["bbox"]) * page.derotation_matrix).intersects(image_rect) for block in native):
                    continue
                regions.append(image_rect)
        for region in regions:
            from ..ocr.service import recognize_image
            pix = _render(page, context, {**args, "dpi": args.get("dpi", 300)}, clip=region)
            image_path = context.temporary_path(".png")
            pix.save(image_path)
            recognized = recognize_image(image_path, args, context)
            for item in recognized.get("blocks", []):
                # OCR coordinates are image pixels, normalized to visible points.
                rect = item.get("bbox", [0, 0, pix.width, pix.height])
                visible = region * page.rotation_matrix
                box = [visible.x0 + rect[0] * visible.width / pix.width, visible.y0 + rect[1] * visible.height / pix.height,
                       visible.x0 + rect[2] * visible.width / pix.width, visible.y0 + rect[3] * visible.height / pix.height]
                block = {"page": index + 1, "text": item.get("text", ""), "bbox": box, "source": "ocr"}
                if "confidence" in item:
                    block["confidence"] = item["confidence"]
                blocks.append(block)
            warnings.extend(recognized.get("warnings", []))
    text = "\n\n".join(block["text"] for block in blocks)
    if args.get("format") == "markdown":
        text = "\n\n".join(f"<!-- página {block['page']} -->\n{block['text']}" for block in blocks)
    return {"text": text, "blocks": blocks, "pages": [n + 1 for n in selected], "warnings": warnings, "engine": "pymupdf"}


def _search(doc, args: dict, context) -> dict:
    query = args.get("query")
    _require(isinstance(query, str) and 0 < len(query) <= 1000, "invalid_query", "A consulta deve conter de 1 a 1000 caracteres.")
    fitz = _dependency("pymupdf")
    hits = []
    if not args.get("regex"):
        for index in pages(args.get("pages"), len(doc)):
            page = doc[index]
            for rect in page.search_for(query, clip=_rect(page, args.get("region"))):
                found = page.get_textbox(rect).strip()
                if not args.get("case_sensitive") or query in found:
                    hits.append({"page": index + 1, "bbox": _bbox(page, rect), "text": found})
    else:
        try:
            pattern = re.compile(query, 0 if args.get("case_sensitive") else re.IGNORECASE)
        except re.error as error:
            raise DocumentError("invalid_query", "Expressão regular inválida.") from error
        for index in pages(args.get("pages"), len(doc)):
            page = doc[index]
            for block in page.get_text("rawdict", clip=_rect(page, args.get("region")), sort=True)["blocks"]:
                chars = [char for line in block.get("lines", []) for span in line["spans"] for char in span["chars"]]
                text = "".join(char["c"] for char in chars)
                for match in pattern.finditer(text):
                    if match.end() == match.start():
                        continue
                    rect = fitz.Rect(chars[match.start()]["bbox"])
                    for char in chars[match.start() + 1:match.end()]:
                        rect |= fitz.Rect(char["bbox"])
                    hits.append({"page": index + 1, "bbox": _bbox(page, rect), "text": match[0]})
    return {"matches": hits, "total": len(hits), "engine": "pymupdf"}


def _tables(doc, args: dict, context) -> dict:
    tables, warnings = [], []
    mode = args.get("mode", "native")
    _require(mode in {"native", "ocr", "auto"}, "invalid_mode", "Modo de tabela inválido.")
    for index in pages(args.get("pages"), len(doc)):
        page = doc[index]
        found = page.find_tables(clip=_rect(page, args.get("region")), strategy=args.get("strategy", "lines")) if mode != "ocr" else None
        for table in found.tables if found else []:
            cells = table.extract()
            tables.append({"page": index + 1, "bbox": _bbox(page, table.bbox), "cells": cells,
                           "cell_regions": [{"row": row_index + 1, "column": column_index + 1, "bbox": _bbox(page, rect)} for row_index, row in enumerate(table.rows) for column_index, rect in enumerate(row.cells) if rect],
                           "source": "native", "partial": any(value is None for row in cells for value in row)})
        if mode == "ocr" or (mode == "auto" and not found.tables):
            from ..ocr.service import analyze_image_layout
            clip = _rect(page, args.get("region"))
            pix = _render(page, context, {**args, "dpi": args.get("dpi", 300)}, clip=clip)
            temporary = context.temporary_path(".png"); pix.save(temporary)
            layout = analyze_image_layout(temporary, args, context)
            for table in layout.get("tables", []):
                box = table.get("bbox") or [0, 0, pix.width, pix.height]
                visible = clip * page.rotation_matrix
                box = [visible.x0 + box[0] * visible.width / pix.width, visible.y0 + box[1] * visible.height / pix.height,
                       visible.x0 + box[2] * visible.width / pix.width, visible.y0 + box[3] * visible.height / pix.height]
                tables.append({**table, "page": index + 1, "bbox": box, "source": "ocr", "partial": table.get("partial", True)})
            warnings.extend(layout.get("warnings", []))
    return {"tables": tables, "warnings": warnings, "engine": "pymupdf"}


def _render_pages(doc, args: dict, context) -> dict:
    fmt = args.get("format", "png")
    _require(fmt in {"png", "jpeg"}, "invalid_format", "Use png ou jpeg para renderizar páginas.")
    selected = pages(args.get("pages"), len(doc))
    targets = []
    if args.get("contact_sheet"):
        Image = _dependency("PIL.Image")
        thumbs = []
        for index in selected:
            pix = _render(doc[index], context, {**args, "dpi": args.get("dpi", 60)}, clip=_rect(doc[index], args.get("region")))
            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            image.thumbnail((240, 320)); thumbs.append(image)
        columns = min(4, len(thumbs)); rows = math.ceil(len(thumbs) / columns)
        _require(columns * 260 * rows * 340 <= context.defaults.get("max_pixels", 50000000), "render_limit", "Folha de contato excede o limite de pixels.")
        sheet = Image.new("RGB", (columns * 260, rows * 340), "white")
        for n, image in enumerate(thumbs):
            sheet.paste(image, (n % columns * 260 + 10, n // columns * 340 + 10))
        target = _output(context, args, ".png" if fmt == "png" else ".jpg")
        sheet.save(target)
        targets.append({"path": args["output_path"], "pages": [n + 1 for n in selected]})
    else:
        for n, index in enumerate(selected):
            page = doc[index]
            pix = _render(page, context, args, clip=_rect(page, args.get("region")))
            destination = args.get("output_path") if len(selected) == 1 and args.get("output_path") else _destination(args, f"page-{index + 1:04d}.{'png' if fmt == 'png' else 'jpg'}", n)
            target = context.stage_output(destination)
            pix.save(target, jpg_quality=int(args.get("quality", 90))) if fmt == "jpeg" else pix.save(target)
            targets.append({"path": destination, "page": index + 1, "width": pix.width, "height": pix.height})
    return {"images": targets, "engine": "pymupdf"}


def _extract_images(doc, args: dict, context) -> dict:
    result, seen = [], set()
    for index in pages(args.get("pages"), len(doc)):
        for image in doc[index].get_images():
            xref = image[0]
            if xref in seen:
                continue
            seen.add(xref)
            extracted = doc.extract_image(xref)
            _require(extracted["width"] * extracted["height"] <= context.defaults.get("max_pixels", 50000000), "render_limit", "Imagem incorporada excede o limite de pixels.")
            destination = _destination(args, f"image-{xref}.{extracted['ext']}", len(result))
            context.stage_output(destination).write_bytes(extracted["image"])
            result.append({"path": destination, "page": index + 1, "xref": xref, "width": extracted["width"], "height": extracted["height"]})
    return {"images": result, "engine": "pymupdf"}


def _select(doc, args: dict, context) -> dict:
    fitz = _dependency("pymupdf")
    action = args.get("action", "extract")
    selected = pages(args.get("page_order", args.get("pages")), len(doc))
    if action == "delete":
        selected = [index for index in range(len(doc)) if index not in selected]
    elif action == "duplicate":
        selected = [n for index in range(len(doc)) for n in ([index, index] if index in selected else [index])]
    _require(bool(selected), "invalid_pages", "A operação não pode produzir um PDF sem páginas.")
    with fitz.open() as result:
        for index in selected:
            result.insert_pdf(doc, from_page=index, to_page=index, links=True, annots=True, widgets=True)
        if action == "insert":
            _require(args.get("insert_path") is not None, "invalid_argument", "Informe insert_path.")
            at = args.get("insert_at", len(result) + 1)
            _require(type(at) is int and 1 <= at <= len(result) + 1, "invalid_pages", "Posição de inserção inválida.")
            with _open(context, args, value=args["insert_path"], modify=True) as incoming:
                result.insert_pdf(incoming, start_at=at - 1, widgets=True)
        else:
            _require(action in {"extract", "delete", "reorder", "duplicate"}, "invalid_action", "Operação de seleção inválida.")
        return _save(result, args, context)


def _merge(args: dict, context) -> dict:
    fitz = _dependency("pymupdf")
    paths = args.get("input_paths", [])
    _require(isinstance(paths, list) and len(paths) >= 2, "invalid_argument", "Informe ao menos dois PDFs para mesclar.")
    documents = []
    try:
        for value in paths:
            documents.append(_open(context, args, value=value, modify=True))
        _require(sum(len(doc) for doc in documents) <= context.defaults.get("max_pages", 2000), "document_limit", "A mesclagem excede o limite de páginas.")
        with fitz.open() as result:
            if args.get("action", "concat") == "interleave":
                for index in range(max(map(len, documents))):
                    for doc in documents:
                        if index < len(doc):
                            result.insert_pdf(doc, from_page=index, to_page=index, widgets=True)
            else:
                _require(args.get("action", "concat") == "concat", "invalid_action", "Mesclagem deve usar concat ou interleave.")
                for doc in documents:
                    result.insert_pdf(doc, widgets=True)
            return _save(result, args, context)
    finally:
        for doc in documents:
            doc.close()


def _split(doc, args: dict, context) -> dict:
    fitz = _dependency("pymupdf")
    ranges = args.get("ranges")
    if args.get("action") == "bookmarks":
        starts = sorted({entry[2] - 1 for entry in doc.get_toc() if entry[0] == 1 and entry[2] > 0})
        _require(bool(starts), "bookmarks_unavailable", "O documento não possui marcadores de primeiro nível.")
        if starts[0] != 0:
            starts.insert(0, 0)
        selections = [list(range(start, end)) for start, end in zip(starts, starts[1:] + [len(doc)])]
    else:
        selections = [pages(value, len(doc)) for value in ranges] if ranges else [[index] for index in pages(args.get("pages"), len(doc))]
    outputs = []
    for n, selection in enumerate(selections, 1):
        destination = _destination(args, f"part-{n:04d}.pdf", n - 1)
        with fitz.open() as result:
            for index in selection:
                result.insert_pdf(doc, from_page=index, to_page=index, widgets=True)
            outputs.append(_save(result, {**args, "output_path": destination}, context))
    return {"documents": outputs, "engine": "pymupdf"}


def _transform(doc, args: dict, context) -> dict:
    fitz = _dependency("pymupdf")
    selected = pages(args.get("pages"), len(doc))
    action = args.get("action")
    if action in {"rotate", "crop"}:
        for index in selected:
            page = doc[index]
            if action == "rotate":
                angle = args.get("angle", 90)
                _require(type(angle) is int and angle % 90 == 0, "invalid_angle", "A rotação deve ser um múltiplo inteiro de 90 graus.")
                page.set_rotation((page.rotation + angle) % 360)
            else:
                rect = _rect(page, args.get("region"))
                _require(args.get("region") is not None, "invalid_region", "O recorte exige uma região.")
                rect += (page.cropbox_position.x, page.cropbox_position.y, page.cropbox_position.x, page.cropbox_position.y)
                page.set_cropbox(rect)
        result = _save(doc, args, context)
        if action == "crop":
            result["warnings"] = ["O recorte altera a área visível e não remove conteúdo oculto."]
        return result
    _require(action in {"resize", "margins"}, "invalid_action", "Transformação de página inválida.")
    with fitz.open() as result:
        for index, page in enumerate(doc):
            if index not in selected:
                result.insert_pdf(doc, from_page=index, to_page=index, widgets=True)
                continue
            width, height = page.rect.width, page.rect.height
            if action == "resize":
                width = _number(args.get("width"), "Largura", 1); height = _number(args.get("height"), "Altura", 1)
                destination = fitz.Rect(0, 0, width, height)
            else:
                margins = args.get("margins", [])
                _require(isinstance(margins, list) and len(margins) == 4, "invalid_argument", "Margens devem conter esquerda, topo, direita e base.")
                left, top, right, bottom = [_number(v, "Margem") for v in margins]
                width += left + right; height += top + bottom
                destination = fitz.Rect(left, top, width - right, height - bottom)
            target = result.new_page(width=width, height=height)
            target.show_pdf_page(destination, doc, index)
        value = _save(result, args, context)
        value["warnings"] = ["Redimensionamento compõe as aparências das páginas; campos e anotações interativas das páginas transformadas não são transferidos."]
        return value


def _create(args: dict, context) -> dict:
    fitz = _dependency("pymupdf")
    if args.get("input_paths"):
        dpi = _number(args.get("dpi", 150), "DPI", 36)
        with fitz.open() as result:
            for value in args["input_paths"]:
                path = context.input_path(value)
                image = _dependency("PIL.Image").open(path)
                for frame in _dependency("PIL.ImageSequence").Iterator(image):
                    _require(frame.width * frame.height <= context.defaults.get("max_pixels", 50000000), "render_limit", "Imagem de entrada excede o limite de pixels.")
                    data = io.BytesIO(); frame.convert("RGB").save(data, format="PNG")
                    page = result.new_page(width=frame.width * 72 / dpi, height=frame.height * 72 / dpi)
                    page.insert_image(page.rect, stream=data.getvalue())
                    _require(len(result) <= context.defaults.get("max_pages", 2000), "document_limit", "Quantidade de páginas excedida.")
            return _save(result, args, context)
    canvas = _dependency("reportlab.pdfgen.canvas")
    text = args.get("text", "")
    _require(isinstance(text, str) and bool(text.strip()), "invalid_text", "Informe texto ou imagens para criar o documento.")
    width = _number(args.get("width", 595), "Largura", 100); height = _number(args.get("height", 842), "Altura", 100)
    size = _number(args.get("font_size", 12), "Tamanho da fonte", 1)
    temporary = context.temporary_path(".pdf")
    output = canvas.Canvas(str(temporary), pagesize=(width, height))
    import textwrap
    y = height - 48
    for paragraph in text.splitlines():
        for line in textwrap.wrap(paragraph, max(1, int((width - 96) / (size * .55)))) or [""]:
            if y < 48:
                output.showPage(); y = height - 48
            output.setFont("Helvetica", size); output.drawString(48, y, line); y -= size * 1.4
    output.save()
    with fitz.open(temporary) as result:
        _require(len(result) <= context.defaults.get("max_pages", 2000), "document_limit", "Quantidade de páginas excedida.")
        return _save(result, args, context)


def _stamp(doc, args: dict, context) -> dict:
    size = _number(args.get("font_size", 12), "Tamanho da fonte", 1)
    color = args.get("color", [0, 0, 0])
    _require(isinstance(color, list) and len(color) == 3 and all(isinstance(v, (int, float)) and 0 <= v <= 1 for v in color), "invalid_color", "Cor deve conter três componentes entre 0 e 1.")
    opacity = _number(args.get("opacity", 1), "Opacidade")
    _require(opacity <= 1, "invalid_argument", "Opacidade deve estar entre 0 e 1.")
    overlay = _open(context, args, value=args["overlay_path"], modify=True) if args.get("overlay_path") else None
    try:
        for index in pages(args.get("pages"), len(doc)):
            page = doc[index]
            if args.get("region"):
                rect = _rect(page, args["region"])
            else:
                rect = _dependency("pymupdf").Rect(24, 24, page.rect.width - 24, 60) * page.derotation_matrix
                if args.get("position") in {"footer", "page_number"} or args.get("action") == "page_number":
                    rect = _dependency("pymupdf").Rect(24, page.rect.height - 48, page.rect.width - 24, page.rect.height - 16) * page.derotation_matrix
            if overlay:
                page.show_pdf_page(rect, overlay, 0, overlay=True)
            elif args.get("image_path"):
                page.insert_image(rect, filename=str(context.input_path(args["image_path"])), overlay=True)
            else:
                text = args.get("text", "{page} / {pages}" if args.get("position") == "page_number" or args.get("action") == "page_number" else "")
                text = text.replace("{page}", str(index + 1)).replace("{pages}", str(len(doc)))
                _require(bool(text), "invalid_text", "Informe texto, imagem ou página PDF para o carimbo.")
                available = page.insert_textbox(rect, text, fontsize=size, color=color, fill_opacity=opacity, align=1)
                _require(available >= 0, "text_does_not_fit", "O texto do carimbo não cabe na região; aumente a região ou reduza a fonte.")
        return _save(doc, args, context)
    finally:
        if overlay:
            overlay.close()


def _metadata(doc, args: dict, context) -> dict:
    if args.get("action", "get") == "get":
        return {"metadata": doc.metadata, "xml": doc.get_xml_metadata()}
    _require(args.get("action") in {"set", "clear"}, "invalid_action", "Operação de metadados inválida.")
    values = args.get("values", {}) if args["action"] == "set" else {}
    _require(isinstance(values, dict) and set(values) <= {"title", "author", "subject", "keywords", "creator", "producer", "creationDate", "modDate", "trapped"}, "invalid_argument", "Atributos de metadados inválidos.")
    doc.set_metadata(values)
    doc.del_xml_metadata()
    return _save(doc, args, context)


def _bookmarks(doc, args: dict, context) -> dict:
    if args.get("action", "get") == "get":
        return {"entries": doc.get_toc()}
    entries = args.get("entries", [])
    _require(args.get("action") in {"set", "clear"} and isinstance(entries, list), "invalid_action", "Operação de marcadores inválida.")
    for value in entries:
        _require(isinstance(value, list) and len(value) == 3 and type(value[0]) is int and value[0] >= 1 and isinstance(value[1], str) and type(value[2]) is int and 1 <= value[2] <= len(doc), "invalid_argument", "Marcador deve conter nível, título e página.")
    doc.set_toc(entries if args["action"] == "set" else [])
    return _save(doc, args, context)


def _attachments(doc, args: dict, context) -> dict:
    action = args.get("action", "list")
    if action == "list":
        return {"attachments": [doc.embfile_info(name) for name in doc.embfile_names()]}
    if action == "extract":
        selected = [args["attachment_name"]] if args.get("attachment_name") else doc.embfile_names()
        result = []
        for n, name in enumerate(selected):
            _require(name in doc.embfile_names(), "attachment_unavailable", "Anexo incorporado não encontrado.")
            # O nome incorporado é conteúdo não confiável e nunca define caminho.
            destination = args.get("output_path") if len(selected) == 1 and args.get("output_path") else _destination(args, f"attachment-{n + 1:04d}.bin", n)
            content = doc.embfile_get(name)
            _require(len(content) <= context.defaults.get("max_input_bytes", 262144000), "document_limit", "Anexo excede o limite configurado.")
            context.stage_output(destination).write_bytes(content)
            result.append({"name": name, "path": destination})
        return {"attachments": result}
    if action == "add":
        name = args.get("attachment_name")
        _require(isinstance(name, str) and bool(name) and name not in doc.embfile_names(), "invalid_argument", "Nome do anexo inválido ou repetido.")
        path = context.input_path(args.get("attachment_path", ""))
        doc.embfile_add(name, path.read_bytes(), filename=path.name)
    elif action == "remove":
        name = args.get("attachment_name")
        _require(name in doc.embfile_names(), "attachment_unavailable", "Anexo incorporado não encontrado.")
        doc.embfile_del(name)
    else:
        raise DocumentError("invalid_action", "Operação de anexos inválida.")
    return _save(doc, args, context)


def _annotations(doc, args: dict, context) -> dict:
    action = args.get("action", "list")
    if action == "list":
        return {"annotations": [{"page": index + 1, "xref": annot.xref, "type": annot.type[1], "bbox": _bbox(doc[index], annot.rect), "info": annot.info} for index in pages(args.get("pages"), len(doc)) for annot in doc[index].annots() or []]}
    _require(action in {"add", "remove", "clear"}, "invalid_action", "Operação de anotações inválida.")
    if action in {"remove", "clear"}:
        xrefs = args.get("xrefs", args.get("annotation_ids", []))
        for index in pages(args.get("pages"), len(doc)):
            page = doc[index]
            for annot in list(page.annots() or []):
                if action == "clear" or annot.xref in xrefs:
                    page.delete_annot(annot)
    else:
        for entry in args.get("entries", []):
            index = pages([entry.get("page")], len(doc))[0]; page = doc[index]
            rect = _rect(page, entry.get("rect", entry.get("bbox")))
            kind = entry.get("type", "comment")
            if kind == "highlight":
                annot = page.add_highlight_annot(rect)
            elif kind == "rectangle":
                annot = page.add_rect_annot(rect)
            elif kind == "circle":
                annot = page.add_circle_annot(rect)
            elif kind in {"comment", "text"}:
                annot = page.add_text_annot(rect.tl, entry.get("text", ""))
            elif kind == "freetext":
                annot = page.add_freetext_annot(rect, entry.get("text", ""), fontsize=entry.get("font_size", 12))
            else:
                raise DocumentError("invalid_annotation", "Tipo de anotação indisponível.")
            if entry.get("color"):
                annot.set_colors(stroke=entry["color"])
            annot.set_info(content=entry.get("text", ""), title=entry.get("author", "")); annot.update()
    return _save(doc, args, context)


def _forms(doc, args: dict, context) -> dict:
    pdf = _dependency("pypdf")
    path = context.input_path(args["input_path"])
    reader = pdf.PdfReader(path)
    if reader.is_encrypted:
        _require(bool(reader.decrypt(_password(context, args))), "invalid_password", "A senha do PDF não foi aceita.")
    acroform = reader.trailer["/Root"].get("/AcroForm")
    xfa = bool(acroform and "/XFA" in acroform.get_object())
    fields = reader.get_fields() or {}
    action = args.get("action", "list")
    if action in {"list", "export"}:
        result = {"xfa_present": xfa, "editable": not xfa, "fields": [{"name": name, "type": str(field.get("/FT", "")), "value": str(field.get("/V", "")), "options": [str(v) for v in field.get("/Opt", [])]} for name, field in fields.items()]}
        if action == "export":
            _write_export(context, {**args, "format": "json"}, result)
        return result
    _require(action in {"fill", "flatten"}, "invalid_action", "Operação de formulário inválida.")
    _require(not xfa, "xfa_unsupported", "Formulários XFA são detectados, mas não editados por esta ferramenta.")
    values = args.get("values", {})
    _require(isinstance(values, dict) and set(values) <= set(fields), "unknown_field", "Há campo não encontrado no formulário.")
    _require(all(field.get("/FT") != "/Sig" for name, field in fields.items() if name in values), "signature_field", "A ferramenta não preenche campos de assinatura digital.")
    writer = pdf.PdfWriter(); writer.clone_document_from_reader(reader)
    canonical = {name: field for name, field in (writer.get_fields() or {}).items()}
    for page in writer.pages:
        for ref in page.get("/Annots", []):
            widget = ref.get_object()
            if widget.get("/Subtype") != "/Widget":
                continue
            # Campos sem relação canônica não podem ser corrigidos por tentativa:
            # nomes repetidos podem identificar objetos independentes.
            if widget.get("/T") in canonical and "/Parent" not in widget:
                field = canonical[widget["/T"]]
                _require(getattr(field, "indirect_reference", None) == ref or field == widget, "ambiguous_form", "Há widgets e campos canônicos divergentes; o formulário exige reparo.")
    flatten = action == "flatten"
    if flatten:
        values = {name: values.get(name, field.get("/V", "/Off" if field.get("/FT") == "/Btn" else "")) for name, field in fields.items() if field.get("/FT") != "/Sig"}
    writer.update_page_form_field_values(None, values, auto_regenerate=False, flatten=flatten)
    if flatten:
        writer.remove_annotations(subtypes="/Widget")
        writer.root_object.pop(pdf.generic.NameObject("/AcroForm"), None)
    if getattr(context, "_pdf_encrypted", False):
        password = getattr(context, "_pdf_password", "")
        _require(bool(password), "password_required", "Forneça password_ref para preservar a proteção do formulário.")
        writer.encrypt(password, password, algorithm="AES-256")
    target = _output(context, args)
    writer.write(target)
    check = pdf.PdfReader(target)
    if check.is_encrypted:
        _require(bool(check.decrypt(getattr(context, "_pdf_password", ""))), "verification_failed", "A proteção do formulário não foi preservada.")
    if flatten:
        _require(not check.get_fields() and all(not any(ref.get_object().get("/Subtype") == "/Widget" for ref in page.get("/Annots", [])) for page in check.pages), "verification_failed", "A saída ainda contém campos interativos.")
    else:
        actual = check.get_fields() or {}
        for name, value in values.items():
            _require(name in actual and str(actual[name].get("/V", "")) == str(value), "verification_failed", "Um valor canônico do formulário não foi persistido.")
        for page in check.pages:
            for ref in page.get("/Annots", []):
                widget = ref.get_object()
                if widget.get("/Subtype") != "/Widget":
                    continue
                field = widget
                while "/Parent" in field:
                    field = field["/Parent"].get_object()
                name = field.get("/T", "")
                if name in values:
                    _require(str(widget.get("/V", field.get("/V", ""))) == str(values[name]) and bool(widget.get("/AP", {}).get("/N")), "verification_failed", "Valor ou aparência do widget não foi atualizado.")
    return {"output_path": args["output_path"], "pages": len(check.pages), "fields": len(check.get_fields() or {}), "flattened": flatten, "engine": "pypdf"}


def _redaction_regions(doc, args: dict, context) -> list[dict]:
    regions = []
    for value in args.get("redactions", []):
        index = pages([value.get("page")], len(doc))[0]
        rect = _rect(doc[index], value.get("rect", value.get("bbox")))
        regions.append({"page": index + 1, "bbox": _bbox(doc[index], rect)})
    if args.get("query"):
        regions.extend({"page": item["page"], "bbox": item["bbox"]} for item in _search(doc, args, context)["matches"])
    _require(bool(regions), "empty_selection", "Nenhuma região selecionada para a tarja.")
    return regions


def _redact(doc, args: dict, context, preview: bool) -> dict:
    regions = _redaction_regions(doc, args, context)
    for region in regions:
        page = doc[region["page"] - 1]
        page.add_redact_annot(_rect(page, region["bbox"]), fill=(0, 0, 0))
    if not preview:
        for index in sorted({region["page"] - 1 for region in regions}):
            doc[index].apply_redactions(images=2, graphics=2, text=0)
        # Attachments, metadata and old revisions may otherwise retain a copy of
        # information removed from visible pages.
        for name in doc.embfile_names():
            doc.embfile_del(name)
        doc.set_metadata({}); doc.del_xml_metadata()
    def verify(check):
        if preview:
            return
        for region in regions:
            page = check[region["page"] - 1]
            rect = _rect(page, region["bbox"])
            _require(not page.get_text(clip=rect).strip(), "verification_failed", "Permanece texto na região selecionada para remoção.")
            inset = min(1, rect.width * .1, rect.height * .1)
            inner = rect + (inset, inset, -inset, -inset)
            pix = _render(page, context, {"dpi": 144}, clip=inner)
            black = sum(1 for pixel in zip(*[iter(pix.samples)] * pix.n) if all(component < 8 for component in pixel))
            _require(black >= pix.width * pix.height * .98, "verification_failed", "A região removida não possui a aparência final esperada.")
        _require(not check.embfile_names(), "verification_failed", "A saída da tarja ainda contém anexos incorporados.")

    result = _save(doc, args, context, verifier=verify)
    result.update({"regions": regions, "applied": not preview})
    if preview:
        result["warnings"] = ["Prévia de marcação: o conteúdo original ainda está presente."]
    else:
        result["removed_auxiliary_data"] = ["metadata", "embedded_files", "previous_revisions"]
    return result


def _sanitize(doc, args: dict, context) -> dict:
    remove_metadata = args.get("remove_metadata", True)
    remove_attachments = args.get("remove_attachments", True)
    if remove_metadata:
        doc.set_metadata({}); doc.del_xml_metadata()
    if remove_attachments:
        for name in doc.embfile_names():
            doc.embfile_del(name)
    if args.get("remove_actions", True):
        for xref in range(1, doc.xref_length()):
            for key in ("AA", "OpenAction", "A"):
                if doc.xref_get_key(xref, key)[0] != "null":
                    doc.xref_set_key(xref, key, "null")
            if doc.xref_get_key(xref, "Type")[1] == "/Action" or doc.xref_get_key(xref, "S")[1] in {"/JavaScript", "/Launch", "/SubmitForm", "/ImportData", "/GoToR", "/GoToE", "/URI"}:
                doc.update_object(xref, "<< >>")
    doc.scrub(attached_files=remove_attachments, clean_pages=True, embedded_files=remove_attachments,
              hidden_text=args.get("remove_hidden_text", False), javascript=args.get("remove_actions", True), metadata=remove_metadata,
              redactions=False, remove_links=args.get("remove_links", False), reset_fields=False,
              reset_responses=True, thumbnails=True, xml_metadata=remove_metadata)
    return _save(doc, args, context)


def _optimize(doc, args: dict, context) -> dict:
    before = context.input_path(args["input_path"]).stat().st_size
    if args.get("lossy"):
        _require(args.get("image_dpi") is not None, "invalid_argument", "Redução com perdas exige image_dpi explícito.")
        target_dpi = _number(args["image_dpi"], "DPI", 36)
        _require(target_dpi <= 600, "invalid_argument", "DPI de otimização deve ser de até 600.")
        quality = args.get("quality", 85)
        _require(type(quality) is int and 1 <= quality <= 100, "invalid_argument", "Qualidade JPEG deve estar entre 1 e 100.")
        Image = _dependency("PIL.Image")
        for page in doc:
            for image in page.get_images(full=True):
                xref = image[0]
                extracted = doc.extract_image(xref)
                if not extracted:
                    continue
                _require(extracted["width"] * extracted["height"] <= context.defaults.get("max_pixels", 50000000), "render_limit", "Imagem excede o limite de pixels.")
                boxes = page.get_image_rects(xref)
                if not boxes:
                    continue
                max_width = max(round(box.width * target_dpi / 72) for box in boxes)
                max_height = max(round(box.height * target_dpi / 72) for box in boxes)
                bitmap = Image.open(io.BytesIO(extracted["image"]))
                bitmap.thumbnail((max(1, max_width), max(1, max_height)))
                buffer = io.BytesIO(); bitmap.convert("RGB").save(buffer, format="JPEG", quality=quality)
                page.replace_image(xref, stream=buffer.getvalue())
    result = _save(doc, args, context)
    result.update({"input_bytes": before, "lossy": bool(args.get("lossy"))})
    return result


def _protect(doc, args: dict, context) -> dict:
    fitz = _dependency("pymupdf")
    action = args.get("action", "encrypt")
    if action == "decrypt":
        return _save(doc, args, context, encryption=fitz.PDF_ENCRYPT_NONE)
    _require(action == "encrypt" and bool(args.get("new_password_ref")), "invalid_argument", "Criptografia exige uma referência de senha no vault.")
    secret = context.resolve_password(args["new_password_ref"])
    _require(bool(secret), "invalid_password", "A senha de proteção não pode ser vazia.")
    permissions = fitz.PDF_PERM_ACCESSIBILITY
    for label, flag in (("print", fitz.PDF_PERM_PRINT), ("copy", fitz.PDF_PERM_COPY), ("annotate", fitz.PDF_PERM_ANNOTATE), ("modify", fitz.PDF_PERM_MODIFY)):
        if label in args.get("permissions", ["print"]):
            permissions |= flag
    return _save(doc, args, context, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw=secret, user_pw=secret, permissions=permissions)


def _compare(doc, args: dict, context) -> dict:
    with _open(context, args, value=args.get("other_path", "")) as other:
        differences = []
        for index in range(max(len(doc), len(other))):
            if index >= len(doc) or index >= len(other):
                differences.append({"page": index + 1, "status": "added" if index >= len(doc) else "removed"}); continue
            first, second = doc[index], other[index]
            text_a, text_b = first.get_text(), second.get_text()
            pix_a = _render(first, context, {**args, "dpi": args.get("dpi", 72)})
            pix_b = _render(second, context, {**args, "dpi": args.get("dpi", 72)})
            visual = (pix_a.width, pix_a.height, pix_a.samples) != (pix_b.width, pix_b.height, pix_b.samples)
            if visual or text_a != text_b:
                value = {"page": index + 1, "status": "changed", "text_changed": text_a != text_b, "visual_changed": visual,
                         "text_diff": list(difflib.unified_diff(text_a.splitlines(), text_b.splitlines(), lineterm=""))}
                if visual and args.get("output_directory"):
                    Image = _dependency("PIL.Image"); ImageChops = _dependency("PIL.ImageChops")
                    width, height = max(pix_a.width, pix_b.width), max(pix_a.height, pix_b.height)
                    _require(width * height <= context.defaults.get("max_pixels", 50000000), "render_limit", "Comparação excede limite de pixels.")
                    a, b = Image.new("RGB", (width, height), "white"), Image.new("RGB", (width, height), "white")
                    a.paste(Image.frombytes("RGB", (pix_a.width, pix_a.height), pix_a.samples)); b.paste(Image.frombytes("RGB", (pix_b.width, pix_b.height), pix_b.samples))
                    destination = _destination(args, f"difference-{index + 1:04d}.png")
                    ImageChops.difference(a, b).save(context.stage_output(destination))
                    value["difference_image"] = destination
                differences.append(value)
        return {"equal": not differences, "differences": differences, "pages_before": len(doc), "pages_after": len(other), "engine": "pymupdf"}


def _signature_validation(doc, args: dict, context) -> dict:
    reader_module = _dependency("pyhanko.pdf_utils.reader")
    validation = _dependency("pyhanko.sign.validation")
    validator = _dependency("pyhanko_certvalidator")
    trust = []
    if args.get("trust_roots"):
        keys = _dependency("pyhanko.keys")
        trust = [keys.load_cert_from_pemder(str(context.input_path(path))) for path in args["trust_roots"]]
    statuses = []
    with context.input_path(args["input_path"]).open("rb") as stream:
        reader = reader_module.PdfFileReader(stream)
        if reader.encrypted:
            reader.decrypt(_password(context, args))
        for signature in reader.embedded_signatures:
            status = validation.validate_pdf_signature(signature, signer_validation_context=validator.ValidationContext(trust_roots=trust, allow_fetching=False))
            now = datetime.datetime.now(datetime.timezone.utc)
            validity = signature.signer_cert["tbs_certificate"]["validity"]
            before, after = validity["not_before"].native, validity["not_after"].native
            statuses.append({"field": signature.field_name, "intact": bool(status.intact), "cryptographically_valid": bool(status.valid),
                             "trusted_chain": bool(status.trusted), "revocation": "revoked" if getattr(status, "revoked", False) else "unknown", "validation_time": now.isoformat(),
                             "certificate_valid_from": before.isoformat(), "certificate_valid_until": after.isoformat(), "certificate_time_valid": before <= now <= after,
                             "coverage": str(getattr(status, "coverage", "unknown")),
                             "modification_level": str(getattr(status, "modification_level", "unknown")), "network_used": False,
                             "summary": status.summary()})
    return {"signatures": statuses, "network_used": False, "engine": "pyhanko"}


def _pdfa(doc, args: dict, context) -> dict:
    from ..document_processing.configuration import pdf_resource_path
    from ..ocr.runtime import command_path
    from ..ocr.service import _run
    executable = command_path(str(context.profile.get("verapdf_path", "verapdf")))
    _require(bool(executable), "backend_unavailable", "Instale e configure veraPDF para validar conformidade PDF/A.")
    source = context.input_path(args["input_path"])
    converting = args.get("action") in {"convert", "convert_pdfa"}
    if converting:
        _require(not getattr(context, "_pdf_encrypted", False), "encrypted_document", "PDF/A não admite criptografia; gere primeiro uma cópia com protect.decrypt.")
        gs = command_path(str(context.profile.get("ghostscript_path", "gs")))
        _require(bool(gs), "backend_unavailable", "Instale Ghostscript para conversão PDF/A.")
        icc_value = context.profile.get("icc_profile_path")
        _require(bool(icc_value), "backend_unavailable", "Configure o perfil ICC sRGB para conversão PDF/A.")
        icc = pdf_resource_path(context.root, icc_value)
        # A definição de OutputIntent precisa existir: apenas -dPDFA não prova
        # conformidade. Os caminhos são argumentos, nunca comandos de shell.
        definition = context.temporary_path(".ps")
        escaped = str(icc).replace("\\", "/").encode("utf-8").hex()
        definition.write_text(f"%!\n/ICCProfile <{escaped}> def\n[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark\n[{{icc_PDFA}} << /N 3 >> /PUT pdfmark\n[{{icc_PDFA}} ICCProfile (r) file /PUT pdfmark\n[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark\n[{{OutputIntent_PDFA}} << /Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {{icc_PDFA}} /OutputConditionIdentifier (sRGB) >> /PUT pdfmark\n[{{Catalog}} << /OutputIntents [{{OutputIntent_PDFA}}] >> /PUT pdfmark\n", encoding="ascii")
        target = _output(context, args)
        temporary = context.temporary_path(".pdf")
        doc.save(temporary, garbage=4, deflate=True)
        command = [str(gs), "-dBATCH", "-dNOPAUSE", "-dSAFER", "-sDEVICE=pdfwrite", "-dPDFA=2", "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB", "-dCompatibilityLevel=1.7", f"--permit-file-read={icc}", f"-sOutputFile={target}", str(definition), str(temporary)]
        completed = _run(command, context)
        _require(completed.returncode == 0 and target.is_file(), "conversion_failed", "Ghostscript não concluiu a conversão PDF/A.")
        with _dependency("pymupdf").open(target) as check:
            _require(len(check) == len(doc), "verification_failed", "A conversão PDF/A alterou a quantidade de páginas.")
        source = target
    completed = _run([str(executable), "--format", "xml", "--flavour", "2b", str(source)], context, allowed_codes=(0, 1))
    try:
        root = ET.fromstring(completed.stdout)
        reports = [element for element in root.iter() if element.tag.split("}")[-1] == "validationReport"]
        conformant = bool(reports) and all(element.attrib.get("isCompliant") == "true" for element in reports)
    except ET.ParseError as error:
        raise DocumentError("validation_failed", "veraPDF não retornou um relatório válido.") from error
    if converting:
        _require(conformant, "pdfa_nonconformant", "A saída convertida não passou na validação PDF/A-2b e não será publicada.")
    return {"standard": "PDF/A-2b", "conformant": conformant, "output_path": args.get("output_path") if converting else None, "engine": "verapdf"}


def execute(operation: str, args: dict[str, Any], context) -> dict:
    """Executa uma operação isolada; falhas não publicam arquivos parciais.

    Erros de domínio expõem código estável e mensagem operacional. Bibliotecas
    opcionais só são importadas quando a operação correspondente é solicitada.
    """
    operation = operation.removeprefix("pdf.")
    try:
        if operation == "merge":
            return _merge(args, context)
        if operation == "create":
            return _create(args, context)
        if operation == "make_searchable":
            from ..ocr.service import make_searchable
            return make_searchable(args, context)
        read_actions = {"metadata": {"get"}, "bookmarks": {"get"}, "attachments": {"list", "extract"}, "annotations": {"list"}, "forms": {"list", "export"}}
        read_ops = {"inspect", "extract_text", "search", "extract_tables", "render_pages", "extract_images", "compare"}
        modify = operation not in read_ops and not (operation in read_actions and args.get("action", "list" if operation in {"attachments", "annotations", "forms"} else "get") in read_actions[operation])
        if operation == "validate" and args.get("action") not in {"convert", "convert_pdfa"}:
            modify = False
        with _open(context, args, modify=modify) as doc:
            functions = {"inspect": _inspect, "extract_text": _text_blocks, "search": _search, "extract_tables": _tables,
                         "render_pages": _render_pages, "extract_images": _extract_images, "select_pages": _select,
                         "split": _split, "transform_pages": _transform, "stamp": _stamp, "metadata": _metadata,
                         "bookmarks": _bookmarks, "attachments": _attachments, "annotations": _annotations,
                         "forms": _forms, "sanitize": _sanitize, "optimize": _optimize, "protect": _protect, "compare": _compare}
            if operation in functions:
                result = functions[operation](doc, args, context)
                if operation in {"extract_text", "search", "extract_tables", "compare"}:
                    _write_export(context, args, result)
                return result
            if operation in {"preview_redactions", "apply_redactions"}:
                return _redact(doc, args, context, operation == "preview_redactions")
            if operation == "repair":
                result = _save(doc, args, context); result["repaired"] = bool(doc.is_repaired); return result
            if operation == "validate":
                validation = args.get("validation", "structure")
                if validation == "signatures":
                    return _signature_validation(doc, args, context)
                if validation == "pdfa":
                    return _pdfa(doc, args, context)
                _require(validation == "structure", "invalid_argument", "Validação indisponível.")
                pdf = _dependency("pypdf")
                reader = pdf.PdfReader(context.input_path(args["input_path"]), strict=True)
                if reader.is_encrypted:
                    reader.decrypt(_password(context, args))
                _require(len(reader.pages) == len(doc), "verification_failed", "Os leitores discordam sobre a estrutura do documento.")
                return {"structurally_readable": True, "pages": len(doc), "repaired_by_reader": bool(doc.is_repaired), "engine": "pymupdf+pypdf"}
        raise DocumentError("operation_unavailable", "Operação PDF indisponível.")
    except DocumentError:
        raise
    except (ValueError, TypeError, KeyError, RuntimeError, OSError, subprocess.SubprocessError) as error:
        raise DocumentError("pdf_operation_failed", "Não foi possível concluir a operação PDF. Confira o documento e os parâmetros.") from error
