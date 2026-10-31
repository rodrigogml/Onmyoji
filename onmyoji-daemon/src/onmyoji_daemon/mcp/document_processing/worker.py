"""Ponto de entrada privado dos processos de PDF e OCR."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import threading
import time

from .contracts import DocumentError, require
from .jobs import _write_json
from .paths import DocumentContext, file_digest


def _watch_parent() -> None:
    parent = int(os.environ.get("ONMYOJI_DOCUMENT_PARENT_PID", os.getppid()))
    while True:
        time.sleep(0.5)
        if os.getppid() != parent:
            os.killpg(os.getpgrp(), signal.SIGTERM)
            return


def main(request_path: str) -> int:
    if os.name != "nt" and "ONMYOJI_DOCUMENT_PARENT_PID" in os.environ:
        threading.Thread(target=_watch_parent, daemon=True).start()
    directory = Path(request_path).resolve().parent
    started = time.monotonic()
    try:
        request = json.loads(Path(request_path).read_text(encoding="utf-8"))
        namespace = request["namespace"]
        require(namespace in {"pdf", "ocr"}, "invalid_namespace", "Módulo documental inválido.")
        context = DocumentContext(Path(request["root"]), namespace, request["profile_name"], request["profile"], request["defaults"], directory)
        for path, expected in request.get("sources", {}).items():
            require(file_digest(context.input_path(path)) == expected, "source_changed", "Um arquivo de entrada foi alterado após o envio.")
        if namespace == "pdf":
            from ..pdf.service import execute
        else:
            from ..ocr.service import execute
        result = execute(request["operation"], request["arguments"], context)
        require(isinstance(result, dict), "invalid_result", "O motor documental retornou um resultado inválido.")
        # Serializar antes de publicar também detecta resultados não portáteis.
        json.dumps(result, ensure_ascii=False)
        artifacts = context.publish()
        result.update(artifacts=artifacts, duration_seconds=round(time.monotonic() - started, 3))
        _write_json(directory / "result.json", result)
        _write_json(directory / "completion.json", {"ok": True, "artifacts": artifacts})
        return 0
    except DocumentError as error:
        _write_json(directory / "completion.json", {"ok": False, "error": {"code": error.code, "message": str(error)}})
    except ImportError:
        _write_json(directory / "completion.json", {"ok": False, "error": {"code": "dependency_missing", "message": "Instale as dependências desta operação pelo Setup MCP."}})
    except Exception:
        _write_json(directory / "completion.json", {"ok": False, "error": {"code": "processing_failed", "message": "Não foi possível processar o documento. Confira o arquivo e os parâmetros da operação."}})
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
