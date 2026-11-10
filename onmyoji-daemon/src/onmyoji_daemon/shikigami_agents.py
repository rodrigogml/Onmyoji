"""Distribuição opcional e recuperável de perfis TOML da instância.

A fonte pertence ao Shikigami; o workspace recebe somente cópias de posse
registrada. A descoberta e o momento de leitura pelo Codex precisam de prova
de compatibilidade antes que qualquer perfil seja publicado.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import tomllib
from typing import Any, Iterator, Mapping
import uuid

from .config_publication import publish_file
from .instance import shikigami_agents_path


STATE_SCHEMA_VERSION = 1
RUNTIME_SCHEMA_VERSION = 1
AGENTS_STATE_PATH = Path("configs/shikigami/agents.toml")
RUNTIME_STATE_PATH = Path("configs/shikigami/agents-runtime.json")
MAX_PROFILE_BYTES = 64 * 1024
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {"default", "worker", "explorer", "con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
_PROFILE_FIELDS = {"name", "description", "developer_instructions", "model", "model_reasoning_effort", "model_reasoning_summary", "model_verbosity", "features", "skills"}
_FEATURE_FIELDS = {"shell_tool", "apps", "plugins", "memories", "request_permissions_tool"}
_CLIENTS = {"native", "console", "app_server"}
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class ShikigamiAgentMaterializationError(RuntimeError):
    """Uma fonte, prova de capacidade ou posse impede a distribuição segura."""


@dataclass(frozen=True)
class AgentProfile:
    """Metadados de um perfil validado, sem expor instruções no diagnóstico."""

    identifier: str
    description: str
    source: Path
    content_sha256: str


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) or value in _RESERVED:
        raise ShikigamiAgentMaterializationError("Identificador de agente inválido ou reservado.")
    return value


def _reparse(path: Path) -> bool:
    try:
        value = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _safe_path(base: Path, path: Path) -> Path:
    if not path.is_relative_to(base):
        raise ShikigamiAgentMaterializationError("Caminho de agente fora da raiz autorizada.")
    current = base
    for part in ("", *path.relative_to(base).parts):
        current = current / part
        if _reparse(current):
            raise ShikigamiAgentMaterializationError("Links e reparse points não são aceitos na distribuição de agentes.")
    return path


def _digest(path: Path) -> str | None:
    if not path.exists():
        return None
    if not path.is_file() or _reparse(path):
        raise ShikigamiAgentMaterializationError("O destino do agente não é um arquivo regular seguro.")
    return sha256(path.read_bytes()).hexdigest()


def _sync_write(path: Path, content: bytes) -> None:
    with path.open("wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


@contextmanager
def _file_lock(path: Path, timeout: float = 10) -> Iterator[None]:
    key = os.path.normcase(str(path.absolute()))
    with _THREAD_LOCKS_GUARD:
        local = _THREAD_LOCKS.setdefault(key, threading.RLock())
    with local:
        if _reparse(path):
            raise ShikigamiAgentMaterializationError("O lock de agentes não pode ser um link ou reparse point.")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as lock:
            if not path.stat().st_size:
                lock.write(b"0"); lock.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    lock.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise ShikigamiAgentMaterializationError("Outra abertura está distribuindo agentes; tente novamente.") from error
                    time.sleep(0.02)
            try:
                yield
            finally:
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class ShikigamiAgentMaterializer:
    """Publica somente arquivos gerenciados e recupera publicações interrompidas.

    Dois manifestos operacionais redundantes preservam uma revisão incompleta
    durável antes de mutações. O lock protege aberturas gerenciadas; leitores
    externos só têm a garantia de snapshot comprovada pelo gate G4.
    """

    def __init__(self, root: Path):
        if _reparse(root.absolute()):
            raise ShikigamiAgentMaterializationError("A raiz da instância não pode ser um link.")
        self.root = root.resolve()
        self.source_root = shikigami_agents_path(self.root)
        self.agents_state_path = self.root / AGENTS_STATE_PATH
        self.runtime_state_path = self.root / RUNTIME_STATE_PATH
        self.runtime_backup_path = self.runtime_state_path.with_name("agents-runtime.backup.json")

    @staticmethod
    def target(workspace: Path, identifier: str) -> Path:
        return workspace.resolve() / ".codex" / "agents" / (_identifier(identifier) + ".toml")

    def configuration(self) -> dict[str, dict[str, object]]:
        """Lê habilitação local; ausência é uma distribuição vazia válida."""
        _safe_path(self.root, self.agents_state_path)
        try:
            value = tomllib.loads(self.agents_state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            raise ShikigamiAgentMaterializationError("O estado dos agentes está inválido.") from error
        records = value.get("agents", {})
        if value.get("schema_version") != STATE_SCHEMA_VERSION or not isinstance(records, dict):
            raise ShikigamiAgentMaterializationError("O estado dos agentes não é suportado.")
        result: dict[str, dict[str, object]] = {}
        for identifier, record in records.items():
            _identifier(identifier)
            if not isinstance(record, dict) or type(record.get("enabled")) is not bool or record.get("mode", "managed_copy") != "managed_copy" or set(record) - {"enabled", "mode", "source"}:
                raise ShikigamiAgentMaterializationError("Uma habilitação de agente está inválida.")
            if record.get("source", identifier + ".toml") != identifier + ".toml":
                raise ShikigamiAgentMaterializationError("A origem do agente deve ser seu arquivo TOML canônico.")
            result[identifier] = dict(record)
        return result

    def set_enabled(self, identifier: str, enabled: bool) -> None:
        """Persiste a opção sem criar perfil nem alterar cópia de runtime."""
        _identifier(identifier)
        if type(enabled) is not bool:
            raise ShikigamiAgentMaterializationError("A habilitação do agente deve ser booleana.")
        if enabled and identifier not in self.discover():
            raise ShikigamiAgentMaterializationError("A fonte do agente não existe.")
        temporary_root = _safe_path(self.root, self.root / "tmp")
        temporary_root.mkdir(parents=True, exist_ok=True)
        with _file_lock(temporary_root / "agents-state.lock"):
            records = self.configuration()
            records[identifier] = {"enabled": enabled, "mode": "managed_copy", "source": identifier + ".toml"}
            lines = ["schema_version = 1", ""]
            for key, record in sorted(records.items()):
                lines.extend([f"[agents.{key}]", f'enabled = {str(record["enabled"]).lower()}', 'mode = "managed_copy"', f'source = "{key}.toml"', ""])
            self.agents_state_path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix="agents-state-", suffix=".toml", dir=temporary_root)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write("\n".join(lines).encode("utf-8")); output.flush(); os.fsync(output.fileno())
                publish_file(Path(name), self.agents_state_path, mode=0o660)
            finally:
                Path(name).unlink(missing_ok=True)

    @staticmethod
    def _validate_profile(identifier: str, content: bytes) -> dict[str, Any]:
        if not content or len(content) > MAX_PROFILE_BYTES or content.startswith(b"\xef\xbb\xbf"):
            raise ShikigamiAgentMaterializationError("O perfil deve ser UTF-8 sem BOM e ter até 64 KiB.")
        try:
            data = tomllib.loads(content.decode("utf-8"))
        except (UnicodeError, ValueError) as error:
            raise ShikigamiAgentMaterializationError("O perfil de agente não é TOML UTF-8 válido.") from error
        if set(data) - _PROFILE_FIELDS:
            raise ShikigamiAgentMaterializationError("O perfil contém campo não suportado ou tenta substituir controles herdados.")
        if data.get("name") != identifier or any(not isinstance(data.get(key), str) or not data[key].strip() for key in ("description", "developer_instructions")):
            raise ShikigamiAgentMaterializationError("Nome, descrição e instruções do agente são obrigatórios; nome deve coincidir com o arquivo.")
        for key in ("model", "model_reasoning_effort", "model_reasoning_summary", "model_verbosity"):
            if key in data and (not isinstance(data[key], str) or not data[key].strip()):
                raise ShikigamiAgentMaterializationError("A opção de modelo do agente está inválida.")
        features = data.get("features", {})
        if not isinstance(features, dict) or set(features) - _FEATURE_FIELDS or any(value is not False for value in features.values()):
            raise ShikigamiAgentMaterializationError("O perfil só pode desabilitar capacidades suportadas.")
        skills = data.get("skills", {})
        if not isinstance(skills, dict) or set(skills) - {"config", "bundled", "include_instructions"}:
            raise ShikigamiAgentMaterializationError("A redução de skills do agente está inválida.")
        if "include_instructions" in skills and skills["include_instructions"] is not False:
            raise ShikigamiAgentMaterializationError("O perfil só pode reduzir capacidades de skills.")
        bundled = skills.get("bundled", {})
        if not isinstance(bundled, dict) or set(bundled) - {"enabled"} or (bundled and bundled.get("enabled") is not False):
            raise ShikigamiAgentMaterializationError("A redução de skills empacotadas está inválida.")
        configs = skills.get("config", [])
        if not isinstance(configs, list) or any(not isinstance(record, dict) or set(record) != {"path", "enabled"} or not isinstance(record["path"], str) or not record["path"].strip() or record["enabled"] is not False for record in configs):
            raise ShikigamiAgentMaterializationError("A redução de uma skill do agente está inválida.")
        return data

    def discover(self) -> dict[str, AgentProfile]:
        """Valida fontes TOML sem executar scripts ou considerar propostas."""
        _safe_path(self.root, self.source_root)
        if not self.source_root.exists():
            return {}
        if not self.source_root.is_dir():
            raise ShikigamiAgentMaterializationError("A fonte de agentes deve ser um diretório.")
        result: dict[str, AgentProfile] = {}
        for path in sorted(self.source_root.iterdir()):
            if path.suffix.lower() != ".toml":
                continue
            identifier = _identifier(path.stem)
            _safe_path(self.root, path)
            if not path.is_file():
                raise ShikigamiAgentMaterializationError("A fonte de agente deve ser um arquivo regular.")
            content = path.read_bytes()
            profile = self._validate_profile(identifier, content)
            if identifier.casefold() in {name.casefold() for name in result}:
                raise ShikigamiAgentMaterializationError("Há identificadores de agente duplicados.")
            result[identifier] = AgentProfile(identifier, profile["description"], path.resolve(), sha256(content).hexdigest())
        return result

    @staticmethod
    def _require_capability(capability: Mapping[str, object] | None) -> None:
        clients = capability.get("clients", []) if isinstance(capability, Mapping) else []
        if not isinstance(capability, Mapping) or capability.get("supported") is not True or capability.get("discovery") != "workspace_codex_agents" or capability.get("snapshot") != "startup" or not isinstance(clients, (list, tuple, set, frozenset)) or not _CLIENTS.issubset(set(clients)):
            raise ShikigamiAgentMaterializationError("Distribuição de agentes indisponível: G4 precisa comprovar descoberta e snapshot na abertura dos três clientes.")

    @staticmethod
    def _state_bytes(state: Mapping[str, Any]) -> bytes:
        return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _validate_records(self, workspace: Path, records: object) -> dict[str, dict[str, str]]:
        if not isinstance(records, dict):
            raise ShikigamiAgentMaterializationError("O inventário de agentes no manifesto está inválido.")
        for identifier, record in records.items():
            _identifier(identifier)
            if not isinstance(record, dict) or set(record) != {"source", "target", "content_sha256"} or record.get("target") != str(self.target(workspace, identifier)) or record.get("source") != str(self.source_root / (identifier + ".toml")) or not isinstance(record.get("content_sha256"), str) or not _HASH.fullmatch(record["content_sha256"]):
                raise ShikigamiAgentMaterializationError("O manifesto aponta agente para outro destino ou origem inválida.")
        return records

    def _read_state(self) -> dict[str, Any]:
        states: list[dict[str, Any]] = []
        present = False
        for path in (self.runtime_state_path, self.runtime_backup_path):
            _safe_path(self.root, path)
            if not path.exists():
                continue
            present = True
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    continue
                checksum = state.pop("checksum", None)
                if checksum != sha256(self._state_bytes(state)).hexdigest() or state.get("schema_version") != RUNTIME_SCHEMA_VERSION or type(state.get("revision")) is not int or state["revision"] < 0 or not isinstance(state.get("workspaces"), dict):
                    continue
                for key, record in state["workspaces"].items():
                    if not isinstance(record, dict) or not isinstance(record.get("workspace"), str):
                        raise ShikigamiAgentMaterializationError("O manifesto de workspace está inválido.")
                    workspace = Path(record["workspace"])
                    if not workspace.is_absolute() or key != os.path.normcase(str(workspace)):
                        raise ShikigamiAgentMaterializationError("A chave de workspace no manifesto está inválida.")
                    self._validate_records(workspace, record.get("agents"))
                pending = state.get("pending")
                if pending is not None:
                    self._validate_pending(pending)
                states.append(state)
            except (OSError, ValueError, TypeError):
                continue
        if not states:
            if present:
                raise ShikigamiAgentMaterializationError("O manifesto dos agentes está inválido; posse não pode ser inferida.")
            return {"schema_version": RUNTIME_SCHEMA_VERSION, "revision": 0, "workspaces": {}, "pending": None}
        return max(states, key=lambda value: (value["revision"], value.get("pending") is None))

    def _write_state(self, state: dict[str, Any]) -> None:
        _safe_path(self.root, self.runtime_state_path)
        self.runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
        document = dict(state, checksum=sha256(self._state_bytes(state)).hexdigest())
        content = self._state_bytes(document) + b"\n"
        for path in (self.runtime_backup_path, self.runtime_state_path):
            _safe_path(self.root, path)
            _sync_write(path, content)
            if path.read_bytes() != content:
                raise ShikigamiAgentMaterializationError("Não foi possível confirmar o manifesto persistido.")

    def _validate_pending(self, pending: object) -> None:
        if not isinstance(pending, dict) or not isinstance(pending.get("workspace"), str) or not isinstance(pending.get("operation"), str) or not re.fullmatch(r"[0-9a-f]{32}", pending["operation"]):
            raise ShikigamiAgentMaterializationError("A publicação incompleta dos agentes está inválida.")
        workspace = Path(pending["workspace"])
        if not workspace.is_absolute():
            raise ShikigamiAgentMaterializationError("O workspace da publicação está inválido.")
        self._validate_records(workspace, pending.get("previous"))
        self._validate_records(workspace, pending.get("candidate"))
        changes = pending.get("changes")
        if not isinstance(changes, list):
            raise ShikigamiAgentMaterializationError("O inventário da publicação está inválido.")
        for change in changes:
            if not isinstance(change, dict):
                raise ShikigamiAgentMaterializationError("Uma mudança de agente está inválida.")
            _identifier(change.get("identifier"))
            identifier = change["identifier"]
            if identifier not in (set(pending["previous"]) | set(pending["candidate"])) or change.get("after") != pending["candidate"].get(identifier, {}).get("content_sha256") or (identifier not in pending["previous"] and change.get("before") is not None):
                raise ShikigamiAgentMaterializationError("A mudança não pertence ao inventário da publicação.")
            for field in ("before", "after"):
                if change.get(field) is not None and (not isinstance(change[field], str) or not _HASH.fullmatch(change[field])):
                    raise ShikigamiAgentMaterializationError("O hash de recuperação de agente está inválido.")

    def _stage(self, workspace: Path, pending: Mapping[str, Any]) -> Path:
        return _safe_path(workspace, workspace / ".onmyoji" / "agents-staging" / pending["operation"])

    def _apply(self, workspace: Path, pending: Mapping[str, Any], *, rollback: bool = False) -> None:
        stage = self._stage(workspace, pending)
        for change in pending["changes"]:
            identifier = change["identifier"]
            target = _safe_path(workspace, self.target(workspace, identifier))
            current = _digest(target)
            if current not in {change["before"], change["after"]}:
                raise ShikigamiAgentMaterializationError("O destino mudou durante a publicação; recuperação exige decisão.")
            expected = change["before"] if rollback else change["after"]
            if current == expected:
                continue
            if expected is None:
                target.unlink(missing_ok=True)
                continue
            staged = _safe_path(workspace, stage / (identifier + (".previous" if rollback else ".candidate")))
            if _digest(staged) != expected:
                if rollback:
                    raise ShikigamiAgentMaterializationError("A cópia anterior de recuperação está indisponível.")
                source = _safe_path(self.root, self.source_root / (identifier + ".toml"))
                if _digest(source) != expected:
                    raise ShikigamiAgentMaterializationError("A fonte da revisão incompleta mudou; publicação não pode continuar.")
                stage.mkdir(parents=True, exist_ok=True)
                _sync_write(staged, source.read_bytes())
            os.replace(staged, target)
        for identifier, record in (pending["previous"] if rollback else pending["candidate"]).items():
            if _digest(_safe_path(workspace, self.target(workspace, identifier))) != record["content_sha256"] and not rollback:
                raise ShikigamiAgentMaterializationError("A revisão publicada não corresponde ao inventário.")

    def _finish(self, state: dict[str, Any], *, rollback: bool = False) -> None:
        pending = state["pending"]
        workspace = Path(pending["workspace"])
        self._apply(workspace, pending, rollback=rollback)
        records = pending["previous"] if rollback else pending["candidate"]
        key = os.path.normcase(str(workspace))
        completed = json.loads(self._state_bytes(state))
        if records:
            completed["workspaces"][key] = {"workspace": str(workspace), "agents": records}
        else:
            completed["workspaces"].pop(key, None)
        completed["pending"] = None
        self._write_state(completed)
        state.clear(); state.update(completed)
        stage = self._stage(workspace, pending)
        if stage.exists():
            for path in stage.iterdir():
                _safe_path(workspace, path)
                if path.is_file():
                    path.unlink()
            if not any(stage.iterdir()):
                stage.rmdir()

    def synchronize(self, workspace: Path, *, capability: Mapping[str, object] | None = None) -> bool:
        """Cria, repara e remove cópias somente com G4 e posse comprovados.

        Falha de publicação conserva revisão incompleta recuperável. Nenhuma
        configuração nativa do Codex é escrita por este mecanismo.
        """
        try:
            return self._synchronize(workspace, capability=capability)
        except OSError as error:
            raise ShikigamiAgentMaterializationError("Não foi possível publicar os agentes; preserve o manifesto para recuperação.") from error

    def _synchronize(self, workspace: Path, *, capability: Mapping[str, object] | None) -> bool:
        configured = self.configuration()
        if not any(record["enabled"] for record in configured.values()) and not self.runtime_state_path.exists() and not self.runtime_backup_path.exists():
            return False
        if _reparse(workspace.absolute()):
            raise ShikigamiAgentMaterializationError("O workspace não pode ser um link.")
        workspace = workspace.resolve()
        if not workspace.is_dir() or workspace.is_relative_to(self.root):
            raise ShikigamiAgentMaterializationError("Os perfis exigem workspace externo à instância.")
        temporary_root = _safe_path(self.root, self.root / "tmp")
        _safe_path(workspace, workspace / ".onmyoji" / "agents.lock")
        with _file_lock(temporary_root / "agents-runtime.lock"), _file_lock(workspace / ".onmyoji" / "agents.lock"):
            state = self._read_state()
            key = os.path.normcase(str(workspace))
            previous = state["workspaces"].get(key, {}).get("agents", {})
            enabled = {identifier for identifier, record in configured.items() if record["enabled"]}
            if enabled or previous or state.get("pending"):
                self._require_capability(capability)
            recovered = False
            if state.get("pending"):
                if state["pending"]["workspace"] != str(workspace):
                    raise ShikigamiAgentMaterializationError("Outra workspace possui publicação incompleta; recupere-a antes da abertura.")
                try:
                    self._finish(state)
                except ShikigamiAgentMaterializationError:
                    self._finish(state, rollback=True)
                recovered = True
                previous = state["workspaces"].get(key, {}).get("agents", {})
            profiles = self.discover() if enabled else {}
            candidate: dict[str, dict[str, str]] = {}
            changes: list[dict[str, Any]] = []
            target_root = _safe_path(workspace, workspace / ".codex" / "agents")
            for identifier in sorted(enabled):
                if identifier not in profiles:
                    raise ShikigamiAgentMaterializationError("A fonte canônica de um agente habilitado não existe.")
                profile = profiles[identifier]
                data = self._validate_profile(identifier, profile.source.read_bytes())
                certified = capability.get("profile_fields", ["name", "description", "developer_instructions"]) if capability else []
                if not isinstance(certified, (list, tuple, set, frozenset)) or set(data) - set(certified):
                    raise ShikigamiAgentMaterializationError("O perfil usa opções ainda não certificadas pelo gate G4.")
                if "model" in data and data["model"] not in capability.get("available_models", []):
                    raise ShikigamiAgentMaterializationError("O modelo do perfil não tem disponibilidade certificada.")
                if "model_reasoning_effort" in data and data["model_reasoning_effort"] not in capability.get("profile_reasoning_efforts", []):
                    raise ShikigamiAgentMaterializationError("O esforço do perfil não tem suporte certificado.")
                for field, values in (("model_reasoning_summary", "profile_reasoning_summaries"), ("model_verbosity", "profile_verbosities")):
                    if field in data and data[field] not in capability.get(values, []):
                        raise ShikigamiAgentMaterializationError("O modo de resumo ou verbosidade do perfil não tem suporte certificado.")
                target = _safe_path(workspace, self.target(workspace, identifier))
                if target_root.exists() and any(path.name.casefold() == target.name.casefold() and path.name != target.name for path in target_root.iterdir()):
                    raise ShikigamiAgentMaterializationError("O perfil colide com um arquivo local por diferença de maiúsculas.")
                before = _digest(target)
                if before is not None and identifier not in previous:
                    raise ShikigamiAgentMaterializationError("Já existe um perfil local não gerenciado no destino.")
                candidate[identifier] = {"source": str(profile.source), "target": str(target), "content_sha256": profile.content_sha256}
                if before != profile.content_sha256:
                    changes.append({"identifier": identifier, "before": before, "after": profile.content_sha256})
            for identifier in sorted(set(previous) - enabled):
                target = _safe_path(workspace, self.target(workspace, identifier))
                before = _digest(target)
                if before is not None:
                    changes.append({"identifier": identifier, "before": before, "after": None})
            if not changes and candidate == previous:
                return recovered
            operation = uuid.uuid4().hex
            pending = {"workspace": str(workspace), "operation": operation, "previous": previous, "candidate": candidate, "changes": changes}
            stage = self._stage(workspace, pending)
            stage.mkdir(parents=True, exist_ok=False)
            target_root.mkdir(parents=True, exist_ok=True)
            if stage.stat().st_dev != target_root.stat().st_dev:
                raise ShikigamiAgentMaterializationError("Staging e destino dos agentes precisam estar no mesmo volume do workspace.")
            for change in changes:
                identifier = change["identifier"]
                if change["before"] is not None:
                    _sync_write(stage / (identifier + ".previous"), self.target(workspace, identifier).read_bytes())
                if change["after"] is not None:
                    _sync_write(stage / (identifier + ".candidate"), profiles[identifier].source.read_bytes())
                    if _digest(stage / (identifier + ".candidate")) != change["after"]:
                        raise ShikigamiAgentMaterializationError("A fonte mudou durante a preparação do perfil.")
            state["revision"] += 1
            state["pending"] = pending
            self._write_state(state)
            self._finish(state)
            return bool(changes) or recovered
