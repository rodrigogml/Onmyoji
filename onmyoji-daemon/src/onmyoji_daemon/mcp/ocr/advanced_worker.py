"""Worker PaddleOCR: preparação explícita e inferência sem acesso à rede."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from uuid import uuid4


class TableParser(HTMLParser):
    """Preserva texto e spans de células sem executar HTML produzido pelo motor."""
    def __init__(self):
        super().__init__(); self.rows = []; self.current = None; self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.current = []
        elif tag in {"td", "th"} and self.current is not None:
            values = dict(attrs)
            self.cell = {"text": "", "rowspan": int(values.get("rowspan", 1)), "colspan": int(values.get("colspan", 1))}

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.current.append(self.cell); self.cell = None
        elif tag == "tr" and self.current is not None:
            self.rows.append(self.current); self.current = None


def _json_default(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    if hasattr(value, "item"):
        return value.item()
    return str(value)


def _rectangle(value):
    if not value:
        return None
    if len(value) == 4 and all(isinstance(item, (int, float)) for item in value):
        return list(value)
    points = value if isinstance(value[0], list) else list(zip(value[::2], value[1::2]))
    return [min(point[0] for point in points), min(point[1] for point in points), max(point[0] for point in points), max(point[1] for point in points)]


def _normalize_result(result, index: int) -> dict:
    raw = result.json
    if callable(raw):
        raw = raw()
    if isinstance(raw, str):
        raw = json.loads(raw)
    raw = json.loads(json.dumps(raw, default=_json_default))
    data = raw.get("res", raw)
    ocr = data.get("overall_ocr_res", {})
    texts = ocr.get("rec_texts", [])
    boxes = ocr.get("rec_boxes", [])
    if not boxes:
        boxes = [[min(point[0] for point in polygon), min(point[1] for point in polygon), max(point[0] for point in polygon), max(point[1] for point in polygon)] for polygon in ocr.get("rec_polys", [])]
    scores = ocr.get("rec_scores", [])
    blocks = []
    for item, text in enumerate(texts):
        block = {"page": index, "text": text, "source": "ocr"}
        if item < len(boxes):
            block["bbox"] = boxes[item]
        if item < len(scores):
            block["confidence"] = float(scores[item])
        blocks.append(block)
    layout = [{"label": item.get("block_label", "unknown"), "text": item.get("block_content", ""), "bbox": item.get("block_bbox"), "page": index, "source": "ocr"} for item in data.get("parsing_res_list", [])]
    tables = []
    table_regions = [item["bbox"] for item in layout if item.get("label") == "table" and item.get("bbox")]
    for table_index, item in enumerate(data.get("table_res_list", [])):
        parser = TableParser(); parser.feed(item.get("pred_html", ""))
        boxes = item.get("cell_box_list", [])
        cells = [cell for row in parser.rows for cell in row]
        for cell_index, cell in enumerate(cells):
            if cell_index < len(boxes):
                cell["bbox"] = _rectangle(boxes[cell_index])
        bbox = _rectangle(item.get("table_region_bbox")) or (table_regions[table_index] if table_index < len(table_regions) else None)
        if bbox is None:
            cell_boxes = [cell["bbox"] for cell in cells if cell.get("bbox")]
            if cell_boxes:
                bbox = [min(box[0] for box in cell_boxes), min(box[1] for box in cell_boxes), max(box[2] for box in cell_boxes), max(box[3] for box in cell_boxes)]
        tables.append({"page": index, "rows": parser.rows, "cells": [[cell["text"] for cell in row] for row in parser.rows], "bbox": bbox, "source": "ocr", "uncertain": True, "partial": bbox is None or not bool(cells)})
    return {"page": index, "text": "\n".join(texts), "blocks": blocks, "layout": layout, "tables": tables, "coordinate_system": "source_image_pixels"}


def _deny_network(*_args, **_kwargs):
    raise OSError("Networking is disabled during document inference; prepare models in setup.")


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("request", nargs="?"); parser.add_argument("output", nargs="?")
    parser.add_argument("--prepare"); parser.add_argument("--device", default="cpu")
    args = parser.parse_args(argv)
    if args.prepare:
        models = Path(args.prepare).resolve()
        models.mkdir(parents=True, exist_ok=True)
        os.environ["PADDLE_PDX_CACHE_HOME"] = str(models)
        from paddleocr import PPStructureV3
        pipeline = PPStructureV3(device=args.device, text_recognition_model_name="latin_PP-OCRv5_mobile_rec", use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=True, use_formula_recognition=False, use_seal_recognition=False, use_chart_recognition=False)
        import numpy as np
        # Exercita os componentes preparados antes de marcar o cache utilizável.
        list(pipeline.predict(input=np.full((320, 640, 3), 255, dtype=np.uint8)))
        del pipeline
        (models / "prepared.json").write_text(json.dumps({"pipeline": "PPStructureV3", "text_recognition_model": "latin_PP-OCRv5_mobile_rec", "device": args.device, "offline_required": True}), encoding="utf-8")
        return 0
    if not args.request or not args.output:
        parser.error("request and output are required")
    output = Path(args.output)
    try:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
        models = Path(request["models"]).resolve()
        if not (models / "prepared.json").is_file():
            raise ValueError("Models are not prepared")
        manifest = json.loads((models / "prepared.json").read_text(encoding="utf-8"))
        os.environ.update({"PADDLE_PDX_CACHE_HOME": str(models), "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True", "HF_HUB_OFFLINE": "1", "HF_HOME": str(models / "huggingface"), "PADDLE_HOME": str(models / "paddle")})
        socket.create_connection = _deny_network
        socket.socket.connect = _deny_network
        socket.socket.connect_ex = _deny_network
        from PIL import Image, ImageOps
        from paddleocr import PPStructureV3
        from .service import prepare_image, _source_box
        if str(manifest.get("device", "cpu")).startswith("gpu"):
            import paddle
            if not paddle.is_compiled_with_cuda() or paddle.device.cuda.device_count() < 1:
                raise ValueError("Requested GPU is unavailable; CPU fallback is not allowed")
        with Image.open(request["input_path"]) as image:
            if image.width * image.height > int(request["max_pixels"]) or getattr(image, "n_frames", 1) > int(request["max_pages"]):
                raise ValueError("Resource limit")
            pipeline = PPStructureV3(device=manifest.get("device", "cpu"), text_recognition_model_name="latin_PP-OCRv5_mobile_rec", use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=True, use_formula_recognition=False, use_seal_recognition=False, use_chart_recognition=False)
            pages = []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                if image.width * image.height > int(request["max_pixels"]):
                    raise ValueError("Resource limit")
                context = SimpleNamespace(defaults={"max_pixels": request["max_pixels"], "timeout_seconds": request.get("timeout_seconds", 1800)}, temporary_path=lambda suffix: output.parent / (uuid4().hex + suffix))
                prepared, transforms, warnings = prepare_image(ImageOps.exif_transpose(image.copy()), request.get("options", {}), request.get("preprocessing_profile", {}), context)
                import numpy as np
                for result in pipeline.predict(input=np.asarray(prepared)[:, :, ::-1]):
                    normalized = _normalize_result(result, index + 1)
                    normalized["width"], normalized["height"] = image.size
                    normalized["warnings"] = warnings
                    for block in [*normalized["blocks"], *normalized["layout"], *normalized["tables"]]:
                        if block.get("bbox"):
                            block["bbox"] = _source_box(block["bbox"], transforms)
                    for table in normalized["tables"]:
                        for row in table["rows"]:
                            for cell in row:
                                if cell.get("bbox") and len(cell["bbox"]) == 4 and all(isinstance(value, (int, float)) for value in cell["bbox"]):
                                    cell["bbox"] = _source_box(cell["bbox"], transforms)
                    pages.append(normalized)
        data = {"engine": "paddleocr", "pages": pages, "text": "\n\f\n".join(page["text"] for page in pages), "blocks": [block for page in pages for block in page["blocks"]], "tables": [table for page in pages for table in page["tables"]], "layout": [block for page in pages for block in page["layout"]], "warnings": ["Revise células e ordem de leitura reconhecidas; manuscritos e tabelas complexas podem conter ambiguidades."]}
        markdown = []
        for page in pages:
            markdown.append(f"<!-- página {page['page']} -->\n" + page["text"])
            for table in page["tables"]:
                rows = table["cells"]
                if rows:
                    width = max(map(len, rows))
                    for index, row in enumerate(rows):
                        markdown.append("| " + " | ".join(str(value).replace("|", "\\|").replace("\n", " ") for value in [*row, *([""] * (width - len(row)))]) + " |")
                        if index == 0:
                            markdown.append("| " + " | ".join(["---"] * width) + " |")
        data["markdown"] = "\n\n".join(markdown)
        output.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        output.write_text(json.dumps({"error": {"code": "advanced_ocr_failed", "message": "O OCR avançado não concluiu a inferência offline. Confira o runtime, modelos preparados e formato da imagem."}}, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
