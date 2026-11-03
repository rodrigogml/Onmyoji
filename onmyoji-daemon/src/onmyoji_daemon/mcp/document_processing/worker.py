"""Ponto de entrada privado dos processos de PDF e OCR."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time

from .contracts import DocumentError, require
from .jobs import _write_json, execution_environment
from .paths import DocumentContext, file_digest, reject_links


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
    raw = Path(request_path)
    directory = None
    original_directory, original_temporary = Path.cwd(), tempfile.tempdir
    original_environment = {}
    started = time.monotonic()
    try:
        require(raw.is_absolute(), "invalid_path", "O pedido documental deve usar caminho absoluto.")
        reject_links(raw)
        request = json.loads(raw.read_text(encoding="utf-8"))
        if request.get("direct_password"):
            secret = sys.stdin.buffer.read(4097)
            require(1 <= len(secret) <= 4096, "invalid_password", "A senha direta do PDF não foi recebida.")
            try:
                request["arguments"] = {**request["arguments"], "password": secret.decode("utf-8")}
            except UnicodeDecodeError as error:
                raise DocumentError("invalid_password", "A senha direta do PDF precisa usar UTF-8.") from error
        namespace = request["namespace"]
        require(namespace in {"pdf", "ocr"}, "invalid_namespace", "Módulo documental inválido.")
        from .configuration import validate_profile_directories
        root = Path(request["root"])
        work = validate_profile_directories(root, request["profile"])
        candidate = raw.resolve().parent
        expected = work.resolve() / "document-jobs" / namespace / request["profile_name"] / candidate.name
        require(candidate == expected, "path_not_allowed", "O pedido não pertence ao diretório de trabalho do perfil.")
        directory = candidate
        runtime_environment = execution_environment(directory, str(Path(__file__).resolve().parents[3]))
        for name in ("TMP", "TEMP", "TMPDIR", "XDG_CACHE_HOME", "HF_HOME", "TORCH_HOME", "PADDLE_HOME", "PADDLE_PDX_CACHE_HOME", "MPLCONFIGDIR", "NUMBA_CACHE_DIR"):
            original_environment[name] = os.environ.get(name)
            os.environ[name] = runtime_environment[name]
        os.chdir(directory)
        tempfile.tempdir = runtime_environment["TMP"]
        context = DocumentContext(root, namespace, request["profile_name"], request["profile"], request["defaults"], directory)
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
        if directory is not None:
            _write_json(directory / "completion.json", {"ok": False, "error": {"code": error.code, "message": str(error)}})
    except ImportError:
        if directory is not None:
            _write_json(directory / "completion.json", {"ok": False, "error": {"code": "dependency_missing", "message": "Instale as dependências desta operação pelo Setup MCP."}})
    except Exception:
        if directory is not None:
            _write_json(directory / "completion.json", {"ok": False, "error": {"code": "processing_failed", "message": "Não foi possível processar o documento. Confira o arquivo e os parâmetros da operação."}})
    finally:
        os.chdir(original_directory)
        tempfile.tempdir = original_temporary
        for name, value in original_environment.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
