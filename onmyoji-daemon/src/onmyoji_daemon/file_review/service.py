"""Snapshots, rascunhos e aplicação explícita da revisão humana de arquivos."""
from __future__ import annotations

import codecs
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
import time
from typing import Any
import uuid

from .exception import FileReviewError


MAX_FILE_BYTES = 1024 * 1024
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_chunks(original: str, proposed: str) -> list[dict[str, Any]]:
    """Compara textos normalizados; linhas de ``before``/``after`` incluem terminadores.

    Os identificadores são estáveis para o mesmo par de snapshots. Índices de
    apresentação começam em um; ``original_end`` é exclusivo e também começa
    em um, permitindo representar inclusões sem intervalo removido.
    """
    before, after = original.splitlines(keepends=True), proposed.splitlines(keepends=True)
    chunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after).get_opcodes():
        if tag == "equal":
            continue
        chunks.append({"id": f"chunk-{len(chunks)}", "tag": tag,
                       "original_start": i1 + 1, "original_end": i2 + 1,
                       "proposed_start": j1 + 1, "proposed_end": j2 + 1,
                       "before": before[i1:i2], "after": after[j1:j2], "decision": "pending"})
    return chunks


def _no_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise FileReviewError("unsafe_path", "Links simbólicos e pontos de redirecionamento não são permitidos.")


def _read_file(path: Path) -> tuple[bytes, dict[str, int]]:
    _no_links(path)
    try:
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise FileReviewError("invalid_file", "A revisão exige arquivos regulares.")
        if before.st_size > MAX_FILE_BYTES:
            raise FileReviewError("file_too_large", "Cada documento deve ter no máximo 1 MiB.", 413)
        with path.open("rb") as source:
            opened = os.fstat(source.fileno())
            content = source.read(MAX_FILE_BYTES + 1)
            closed = os.fstat(source.fileno())
        after = path.stat()
        stamps = [(s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns) for s in (before, opened, closed, after)]
        if len(content) > MAX_FILE_BYTES:
            raise FileReviewError("file_too_large", "Cada documento deve ter no máximo 1 MiB.", 413)
        if len(set(stamps)) != 1:
            raise FileReviewError("file_changed", "O arquivo mudou durante a leitura. Abra uma nova revisão.", 409)
        return content, {"dev": int(after.st_dev), "ino": int(after.st_ino), "mode": stat.S_IMODE(after.st_mode)}
    except FileReviewError:
        raise
    except OSError as error:
        raise FileReviewError("file_unavailable", "Um dos arquivos não está disponível para revisão.", 409) from error


def _decode(content: bytes) -> tuple[str, dict[str, str]]:
    encoding, bom = "utf-8", b""
    for prefix, candidate in ((codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"),
                              (codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"),
                              (codecs.BOM_UTF16_BE, "utf-16-be")):
        if content.startswith(prefix):
            bom, encoding = prefix, candidate
            break
    try:
        text = content[len(bom):].decode(encoding)
    except UnicodeError as error:
        raise FileReviewError("unsupported_encoding", "Use UTF-8 ou UTF-16/UTF-32 com BOM para revisar o documento.") from error
    if any(ord(character) < 32 and character not in "\r\n\t" for character in text):
        raise FileReviewError("binary_file", "O documento contém caracteres de controle incompatíveis com texto.")
    endings = set(re.findall(r"\r\n|\r|\n", text))
    if len(endings) > 1:
        raise FileReviewError("mixed_newlines", "O documento mistura tipos de quebra de linha. Normalize-o antes da revisão.")
    newline = next(iter(endings), "\n")
    return text.replace("\r\n", "\n").replace("\r", "\n"), {"encoding": encoding, "bom": bom.hex(), "newline": newline}


def _encode(text: str, format_info: dict[str, str]) -> bytes:
    if not isinstance(text, str) or "\r" in text or any(ord(c) < 32 and c not in "\n\t" for c in text):
        raise FileReviewError("invalid_result", "O resultado deve ser texto com quebras de linha normalizadas.")
    try:
        content = bytes.fromhex(format_info["bom"]) + text.replace("\n", format_info["newline"]).encode(format_info["encoding"])
    except UnicodeError as error:
        raise FileReviewError("invalid_result", "O resultado contém texto que não pode ser gravado na codificação original.") from error
    if len(content) > MAX_FILE_BYTES:
        raise FileReviewError("file_too_large", "O resultado deve ter no máximo 1 MiB.", 413)
    return content


def _merge_chunk(original: str, result: str, chunk: dict[str, Any]) -> str:
    source, current = original.splitlines(keepends=True), result.splitlines(keepends=True)
    start, end = chunk["original_start"] - 1, chunk["original_end"] - 1
    edits = [op for op in difflib.SequenceMatcher(None, source, current).get_opcodes() if op[0] != "equal"]
    for _tag, i1, i2, _j1, _j2 in edits:
        overlaps = (i1 == i2 and (start < i1 < end or start == end == i1)) or (i1 < end and start < i2)
        if start == end and i1 <= start < i2:
            overlaps = True
        if overlaps:
            raise FileReviewError("chunk_conflict", "Este bloco já foi editado. Revise o texto e ignore a sugestão para manter sua edição.", 409)

    def mapped(position: int, beginning: bool) -> int:
        offset = 0
        for _tag, i1, i2, j1, j2 in edits:
            if i1 == i2:
                if i1 < position or i1 == position and beginning:
                    offset += j2 - j1
            elif i2 <= position:
                offset += (j2 - j1) - (i2 - i1)
        return position + offset

    left, right = mapped(start, True), mapped(end, start == end)
    return "".join(current[:left] + chunk["after"] + current[right:])


class FileReviewService:
    """Mantém snapshots imutáveis e aplica somente o resultado confirmado na tela.

    Conteúdo e recibos ficam no workspace; ``state_dir`` guarda apenas referências.
    Revisões e gravações são serializadas entre instâncias no mesmo processo.
    ``revision_conflict`` impede abas antigas; ``source_conflict`` impede aplicar
    sobre arquivos alterados externamente. O registro de intenção permite retomar
    uma gravação concluída cujo recibo não tenha sido persistido após uma falha.
    """

    def __init__(self, root: Path, workspace: Path, state_dir: Path):
        self.root, self.workspace, self.state_dir = root.resolve(), workspace.resolve(), state_dir.resolve()
        if not self.state_dir.is_relative_to(self.root / "configs"):
            raise FileReviewError("invalid_state_directory", "O índice de revisões deve permanecer em configs/.")
        if not self.workspace.is_dir() or self.workspace.is_relative_to(self.root):
            raise FileReviewError("invalid_workspace", "A revisão exige o workspace externo configurado.")
        _no_links(workspace.absolute())
        _no_links(state_dir.absolute())
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.reviews_dir = self.workspace / "file-reviews"
        _no_links(self.reviews_dir)
        self.reviews_dir.mkdir(parents=True, exist_ok=True)
        with _LOCKS_GUARD:
            self.lock = _LOCKS.setdefault(str(self.state_dir), threading.RLock())

    def _atomic_write(self, target: Path, content: bytes, mode: int | None = None) -> None:
        staging = self.root / "tmp" / "file-review" if target.is_relative_to(self.root) else self.reviews_dir / ".staging"
        _no_links(staging)
        staging.mkdir(parents=True, exist_ok=True)
        if staging.stat().st_dev != target.parent.stat().st_dev:
            raise FileReviewError("incompatible_volume", "O workspace e o destino precisam permitir gravação atômica no mesmo volume.")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=staging, delete=False) as output:
                temporary = Path(output.name)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            if mode is None and target.is_relative_to(self.root / "configs"):
                mode = 0o660
            if mode is not None:
                os.chmod(temporary, mode)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _write_json(self, target: Path, value: dict[str, Any]) -> None:
        self._atomic_write(target, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def _directory(self, review_id: str) -> Path:
        if not isinstance(review_id, str) or re.fullmatch(r"[a-f0-9]{32}", review_id) is None:
            raise FileReviewError("review_not_found", "Revisão não encontrada.", 404)
        registry = self.state_dir / (review_id + ".json")
        try:
            _no_links(registry)
            pointer = json.loads(registry.read_text(encoding="utf-8"))
            directory = Path(pointer["directory"])
            if directory != self.reviews_dir / review_id:
                raise ValueError("reference")
            _no_links(directory)
            if not directory.is_dir():
                raise ValueError("missing")
            return directory
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise FileReviewError("review_not_found", "Revisão não encontrada no workspace vigente.", 404) from error

    def _persist(self, directory: Path, data: dict[str, Any]) -> None:
        self._write_json(directory / "review.json", data)

    def _load(self, review_id: str) -> tuple[Path, dict[str, Any]]:
        directory = self._directory(review_id)
        try:
            _no_links(directory / "review.json")
            data = json.loads((directory / "review.json").read_text(encoding="utf-8"))
            for name in ("original", "proposed"):
                snapshot, _identity = _read_file(directory / (name + ".bin"))
                if _digest(snapshot) != data[name + "_hash"]:
                    raise ValueError("snapshot")
            if data["status"] == "applying":
                self._recover(directory, data)
            return directory, data
        except (OSError, ValueError, KeyError, TypeError) as error:
            if isinstance(error, FileReviewError):
                raise
            raise FileReviewError("review_corrupt", "Os dados da revisão não estão disponíveis ou foram alterados.", 409) from error

    def create(self, original_path: str, proposed_path: str) -> dict[str, Any]:
        """Cria snapshots do par informado, sem modificar nenhum documento.

        ``invalid_path`` rejeita caminhos relativos, arquivos iguais e proposta
        fora do diretório original. Erros de codificação e tamanho são explícitos.
        """
        if not isinstance(original_path, str) or not isinstance(proposed_path, str):
            raise FileReviewError("invalid_path", "Informe caminhos absolutos para os documentos.")
        original, proposed = Path(original_path), Path(proposed_path)
        if (not original.is_absolute() or not proposed.is_absolute() or original.parent != proposed.parent
                or not proposed.name.endswith(".proposed.md") or original.name.endswith(".proposed.md")):
            raise FileReviewError("invalid_path", "A proposta .proposed.md deve estar ao lado do arquivo original, em caminhos absolutos.")
        original_bytes, original_identity = _read_file(original)
        proposed_bytes, proposed_identity = _read_file(proposed)
        if (original_identity["dev"], original_identity["ino"]) == (proposed_identity["dev"], proposed_identity["ino"]):
            raise FileReviewError("invalid_path", "Original e proposta devem ser arquivos distintos.")
        original_text, format_info = _decode(original_bytes)
        proposed_text, _proposed_format = _decode(proposed_bytes)
        with self.lock:
            review_id = uuid.uuid4().hex
            directory = self.reviews_dir / review_id
            directory.mkdir()
            data = {"review_id": review_id, "status": "editing", "app_id": None, "revision": 0,
                    "created_at": time.time(), "updated_at": time.time(), "csrf_token": secrets.token_urlsafe(32),
                    "original_path": str(original), "proposed_path": str(proposed),
                    "original_hash": _digest(original_bytes), "proposed_hash": _digest(proposed_bytes),
                    "original_identity": original_identity, "proposed_identity": proposed_identity,
                    "format": format_info, "result": original_text, "chunks": build_chunks(original_text, proposed_text)}
            self._atomic_write(directory / "original.bin", original_bytes)
            self._atomic_write(directory / "proposed.bin", proposed_bytes)
            self._persist(directory, data)
            self._write_json(self.state_dir / (review_id + ".json"), {"directory": str(directory)})
            return self._view(directory, data)

    def bind_publication(self, review_id: str, app_id: str) -> None:
        with self.lock:
            directory, data = self._load(review_id)
            data["app_id"] = app_id
            self._persist(directory, data)

    def _view(self, directory: Path, data: dict[str, Any]) -> dict[str, Any]:
        result = self._status(directory, data)
        result.update({"original": _decode((directory / "original.bin").read_bytes())[0],
                       "proposed": _decode((directory / "proposed.bin").read_bytes())[0],
                       "result": data["result"], "chunks": data["chunks"], "csrf_token": data["csrf_token"]})
        return result

    def _status(self, directory: Path, data: dict[str, Any]) -> dict[str, Any]:
        result = {key: data[key] for key in ("review_id", "status", "app_id", "revision", "created_at", "updated_at")}
        result.update({"filename": Path(data["original_path"]).name, "proposed_filename": Path(data["proposed_path"]).name,
                       "result_hash": _digest(data["result"].encode("utf-8")),
                       "pending": sum(chunk["decision"] == "pending" for chunk in data["chunks"])})
        if data.get("receipt"):
            result["receipt"] = data["receipt"]
        if data.get("error_code"):
            result["error_code"] = data["error_code"]
        return result

    def view(self, review_id: str) -> dict[str, Any]:
        with self.lock:
            return self._view(*self._load(review_id))

    def status(self, review_id: str) -> dict[str, Any]:
        """Consulta metadados e recibo sem retornar documentos nem segredo CSRF."""
        with self.lock:
            return self._status(*self._load(review_id))

    @staticmethod
    def _editable(data: dict[str, Any], revision: int) -> None:
        if data["status"] != "editing":
            raise FileReviewError("review_closed", "A revisão não está aberta para edição.", 409)
        if type(revision) is not int or revision != data["revision"]:
            raise FileReviewError("revision_conflict", "O rascunho mudou em outra aba. Recarregue antes de continuar.", 409)

    def save(self, review_id: str, revision: int, result: str) -> dict[str, Any]:
        with self.lock:
            directory, data = self._load(review_id)
            self._editable(data, revision)
            _encode(result, data["format"])
            if data["result"] != result:
                data.update(result=result, revision=revision + 1, updated_at=time.time())
                self._persist(directory, data)
            return self._view(directory, data)

    def decide(self, review_id: str, revision: int, chunk_id: str, decision: str) -> dict[str, Any]:
        with self.lock:
            directory, data = self._load(review_id)
            self._editable(data, revision)
            if not isinstance(decision, str) or decision not in {"accepted", "rejected"}:
                raise FileReviewError("invalid_decision", "Escolha incorporar ou ignorar a sugestão.")
            if not isinstance(chunk_id, str):
                raise FileReviewError("chunk_not_found", "Bloco de alteração não encontrado.", 404)
            chunk = next((chunk for chunk in data["chunks"] if chunk["id"] == chunk_id), None)
            if chunk is None:
                raise FileReviewError("chunk_not_found", "Bloco de alteração não encontrado.", 404)
            if chunk["decision"] == decision:
                return self._view(directory, data)
            if chunk["decision"] != "pending":
                raise FileReviewError("decision_closed", "Este bloco já foi decidido. Edite o resultado para fazer outro ajuste.", 409)
            if decision == "accepted":
                original = _decode((directory / "original.bin").read_bytes())[0]
                data["result"] = _merge_chunk(original, data["result"], chunk)
                _encode(data["result"], data["format"])
            chunk["decision"] = decision
            data.update(revision=revision + 1, updated_at=time.time())
            self._persist(directory, data)
            return self._view(directory, data)

    def _check_sources(self, data: dict[str, Any]) -> None:
        for name in ("original", "proposed"):
            content, identity = _read_file(Path(data[name + "_path"]))
            expected = data[name + "_identity"]
            if (_digest(content) != data[name + "_hash"] or identity["dev"] != expected["dev"]
                    or identity["ino"] != expected["ino"]):
                raise FileReviewError("source_conflict", "Original ou proposta mudou fora da revisão. Abra uma nova revisão.", 409)

    def _finish(self, directory: Path, data: dict[str, Any]) -> dict[str, Any]:
        intent = data["intent"]
        receipt = {"review_id": data["review_id"], "status": "applied", "app_id": data["app_id"],
                   "revision": data["revision"], "result_hash": intent["result_hash"],
                   "approval": intent["approval"], "applied_at": intent["applied_at"],
                   "backup_path": str(directory / "original.bin"), "proposed_removed": False}
        if intent["remove_proposed"]:
            proposed = Path(data["proposed_path"])
            try:
                _no_links(proposed)
                if proposed.exists():
                    content, identity = _read_file(proposed)
                    expected = data["proposed_identity"]
                    if (_digest(content) != data["proposed_hash"] or identity["dev"] != expected["dev"]
                            or identity["ino"] != expected["ino"]):
                        raise FileReviewError("proposal_changed", "A proposta mudou após a aplicação.")
                    proposed.unlink()
                receipt["proposed_removed"] = True
            except (OSError, FileReviewError):
                receipt["cleanup_warning"] = "O resultado foi aplicado; a proposta foi preservada porque sua remoção não pôde ser confirmada."
        data.update(status="applied", receipt=receipt, updated_at=time.time())
        self._persist(directory, data)
        return receipt

    def _recover(self, directory: Path, data: dict[str, Any]) -> None:
        try:
            content, identity = _read_file(Path(data["original_path"]))
            expected = data["original_identity"]
            if _digest(content) == data["original_hash"] and all(identity[k] == expected[k] for k in ("dev", "ino")):
                data.update(status="editing", updated_at=time.time())
                del data["intent"]
            elif _digest(content) == data["intent"]["encoded_hash"]:
                self._finish(directory, data)
                return
            else:
                data.update(status="conflict", error_code="source_conflict", updated_at=time.time())
        except FileReviewError:
            data.update(status="conflict", error_code="source_conflict", updated_at=time.time())
        self._persist(directory, data)

    def apply(self, review_id: str, revision: int, result_hash: str, discard_remaining: bool = False) -> dict[str, Any]:
        """Aplica o conteúdo confirmado; repetições do mesmo resultado retornam o recibo.

        Exige revisão atual, hash do texto exibido e todas as sugestões decididas.
        Conflitos externos fecham a revisão sem gravar. Uma aplicação parcial
        conserva a proposta, exceto quando o usuário descarta explicitamente o
        restante. Falhas ao excluir a proposta são registradas no recibo sem
        anunciar falha da gravação já concluída.
        """
        with self.lock:
            directory, data = self._load(review_id)
            if data["status"] == "applied" and data["receipt"]["result_hash"] == result_hash:
                return data["receipt"]
            self._editable(data, revision)
            if (not isinstance(result_hash, str) or not result_hash.isascii()
                    or not secrets.compare_digest(_digest(data["result"].encode("utf-8")), result_hash)):
                raise FileReviewError("result_conflict", "O resultado mudou depois da conferência final.", 409)
            if type(discard_remaining) is not bool:
                raise FileReviewError("invalid_discard", "O descarte do restante deve ser uma escolha explícita.")
            if any(chunk["decision"] == "pending" for chunk in data["chunks"]):
                raise FileReviewError("pending_decisions", "Decida todas as sugestões antes de aplicar o resultado.", 409)
            try:
                self._check_sources(data)
            except FileReviewError as error:
                data.update(status="conflict", error_code="source_conflict", updated_at=time.time())
                self._persist(directory, data)
                raise FileReviewError("source_conflict", "Original ou proposta mudou fora da revisão. Abra uma nova revisão.", 409) from error
            proposed = _decode((directory / "proposed.bin").read_bytes())[0]
            content = _encode(data["result"], data["format"])
            approval = "full" if data["result"] == proposed and all(c["decision"] == "accepted" for c in data["chunks"]) else "partial"
            data.update(status="applying", intent={"result_hash": result_hash, "encoded_hash": _digest(content),
                        "approval": approval, "remove_proposed": approval == "full" or discard_remaining,
                        "applied_at": time.time()}, updated_at=time.time())
            self._persist(directory, data)
            try:
                self._check_sources(data)
                self._atomic_write(Path(data["original_path"]), content, data["original_identity"]["mode"])
            except (OSError, FileReviewError) as error:
                self._recover(directory, data)
                if data["status"] == "applied":
                    return data["receipt"]
                if isinstance(error, FileReviewError):
                    raise
                raise FileReviewError("apply_failed", "Não foi possível gravar o resultado. O original foi preservado.", 409) from error
            return self._finish(directory, data)

    def cancel(self, review_id: str) -> dict[str, Any]:
        with self.lock:
            directory, data = self._load(review_id)
            if data["status"] == "applied":
                raise FileReviewError("review_closed", "O resultado desta revisão já foi aplicado.", 409)
            if data["status"] != "cancelled":
                data.update(status="cancelled", updated_at=time.time())
                self._persist(directory, data)
            return self._status(directory, data)
