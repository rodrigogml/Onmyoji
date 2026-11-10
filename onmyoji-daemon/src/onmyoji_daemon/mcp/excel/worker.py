"""Entrada privada de jobs Excel: temporários locais, watchdog e publicação."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

from ..document_processing.configuration import validate_profile_directories
from ..document_processing.contracts import DocumentError
from ..document_processing.jobs import _write_json, execution_environment
from ..document_processing.paths import DocumentContext, file_digest, reject_links
from .component.process import is_process_descendant, process_identity, same_process
from .exception import ExcelExceptions, require


def _watch_parent(identity: dict) -> None:
    """Sai se o supervisor morreu; o SO fecha a contenção Excel do worker."""
    while True:
        time.sleep(0.25)
        try:
            if not same_process(identity, process_identity(identity["pid"])):
                os._exit(3)
        except (OSError, ValueError, KeyError, TypeError):
            os._exit(3)


def main(request_path: str) -> int:
    directory = None
    original_directory, original_temporary = Path.cwd(), tempfile.tempdir
    original_environment = {}
    started = time.monotonic()
    try:
        raw = Path(request_path)
        require(raw.is_absolute(), "invalid_path", "O pedido Excel deve usar caminho absoluto.")
        reject_links(raw)
        require(raw.stat().st_size <= 16777216, "invalid_request", "O pedido Excel excede o limite de processamento.")
        request = json.loads(raw.read_text(encoding="utf-8"))
        require(request.get("namespace") == "excel", "invalid_namespace", "Pedido Excel inválido.")
        root = Path(request["root"])
        work = validate_profile_directories(root, request["profile"])
        candidate = raw.resolve().parent
        expected = work / "document-jobs" / "excel" / request["profile_name"] / candidate.name
        import re
        require(re.fullmatch(r"[a-f0-9]{32}", candidate.name) is not None and candidate == expected,
                "path_not_allowed", "O pedido Excel não pertence ao diretório de trabalho autorizado.")
        directory = candidate
        identity = request.get("parent_identity")
        require(isinstance(identity, dict) and same_process(identity, process_identity(identity["pid"])),
                "supervisor_unavailable", "O supervisor Excel não está disponível.")
        threading.Thread(target=_watch_parent, args=(identity,), daemon=True, name="excel-parent-watchdog").start()
        activation = directory / "activation.json"
        activation_deadline = time.monotonic() + 10
        while not activation.exists():
            require(time.monotonic() < activation_deadline, "supervisor_unavailable", "O supervisor não confirmou a contenção do worker Excel.")
            time.sleep(0.01)
        reject_links(activation)
        require(activation.stat().st_size <= 4096, "invalid_request", "Registro de ativação do worker inválido.")
        activation_data = json.loads(activation.read_text(encoding="utf-8"))
        launcher_identity = activation_data.get("launcher_identity")
        worker_identity = process_identity(os.getpid())
        require(isinstance(launcher_identity, dict) and same_process(identity, activation_data.get("parent_identity"))
                and is_process_descendant(worker_identity, launcher_identity),
                "supervisor_unavailable", "A ativação não corresponde ao supervisor do worker Excel.")
        _write_json(directory / "worker-ready.json", {"worker_identity": worker_identity, "launcher_identity": launcher_identity})
        accepted = directory / "worker-accepted.json"
        while not accepted.exists():
            require(time.monotonic() < activation_deadline and same_process(launcher_identity, process_identity(launcher_identity["pid"])),
                    "supervisor_unavailable", "O supervisor não confirmou o processo Python real do worker.")
            time.sleep(0.01)
        reject_links(accepted)
        require(accepted.stat().st_size <= 4096, "invalid_request", "Registro de contenção do worker inválido.")
        accepted_data = json.loads(accepted.read_text(encoding="utf-8"))
        require(same_process(worker_identity, accepted_data.get("worker_identity")),
                "supervisor_unavailable", "A contenção aceita não corresponde ao processo real do worker.")
        environment = execution_environment(directory, str(Path(__file__).resolve().parents[3]))
        for name in ("TMP", "TEMP", "TMPDIR", "XDG_CACHE_HOME", "PYTHONPYCACHEPREFIX"):
            original_environment[name] = os.environ.get(name)
            os.environ[name] = environment[name]
        os.chdir(directory)
        tempfile.tempdir = environment["TMP"]
        context = DocumentContext(root, "excel", request["profile_name"], request["profile"], request["defaults"], directory)
        for path, expected_digest in request.get("sources", {}).items():
            require(file_digest(context.input_path(path)) == expected_digest, "source_changed", "Um arquivo de entrada foi alterado após o envio.")
        from .service.execution import execute
        result = execute(request["operation"], request["arguments"], context)
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
        require(len(serialized) <= context.defaults.get("max_result_bytes", 33554432),
                "result_too_large", "O resultado excede o limite configurado; reduza a seleção ou use paginação.")
        # Serialize and revalidate sources before any final artifact appears.
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
            _write_json(directory / "completion.json", {"ok": False, "error": {"code": "dependency_missing", "message": "Prepare as dependências Excel no runtime Python selecionado pelo Setup."}})
    except Exception:
        if directory is not None:
            _write_json(directory / "completion.json", {"ok": False, "error": {"code": "processing_failed", "message": "Não foi possível processar a pasta Excel; confira formato e parâmetros da operação."}})
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
