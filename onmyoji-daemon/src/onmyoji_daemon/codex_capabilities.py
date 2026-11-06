"""Certificação local de capacidades do Codex, vinculada ao executável real.

O esquema descreve formatos, mas não habilita comportamento. Somente evidência
de execução do gate correspondente pode habilitar uma capacidade. A leitura é
conservadora: relatório ausente, inválido ou de outro runtime mantém o fallback.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import stat
from typing import Any, Mapping


ADAPTER_REVISION = 1
REPORT_VERSION = 1
MAX_REPORT_BYTES = 128 * 1024
REPORT_RELATIVE = Path("configs") / "shikigami" / "codex-capabilities.json"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")
_FEATURE_GATES: dict[str, tuple[str, ...]] = {
    "base": ("G0",),
    "steering": ("G0", "G1"),
    "input_reconciliation": ("G0", "G2"),
    "subagents": ("G0", "G3"),
    "agent_profiles": ("G0", "G4"),
    "post_turn": ("G0", "G3", "G5", "G7"),
    "process_resume": ("G0", "G3", "G5", "G6", "G7"),
    "tool_results": ("G0", "G7"),
}


def executable_digest(executable: str) -> str:
    """Calcula SHA-256 do binário resolvido; retorna vazio se não legível.

    Não executa o programa, lê configuração de conta nem pesquisa credenciais.
    O caminho não é devolvido em mensagens públicas de diagnóstico.
    """
    if not isinstance(executable, str) or not executable.strip():
        return ""
    try:
        resolved = shutil.which(executable) or executable
        path = Path(resolved).expanduser().resolve(strict=True)
        if not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except (OSError, ValueError):
        return ""


def _safe_report_path(root: Path) -> Path | None:
    path = root / REPORT_RELATIVE
    try:
        for part in (root, root / "configs", root / "configs" / "shikigami", path):
            if part.is_symlink():
                return None
            if part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                return None
        if path.stat().st_size > MAX_REPORT_BYTES:
            return None
        return path
    except (OSError, ValueError):
        return None


def _reason_code(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and _REASON.fullmatch(value) else fallback


@dataclass(frozen=True)
class CapabilitySet:
    """Visão imutável das provas de um runtime e de seu workspace.

    ``supports`` exige fingerprint compatível e gates de execução completos.
    ``reason`` retorna somente código sanitizado; conteúdo arbitrário de um
    relatório não é adequado para logs ou mensagens ao usuário.
    """

    executable_sha256: str = ""
    schema_sha256: str = ""
    transport: str = "stdio"
    compatible: bool = False
    compatibility_reason: str = "report_missing"
    gates: Mapping[str, Mapping[str, Any]] = field(default_factory=dict, repr=False)
    workspace_kind: str = "plain"

    @classmethod
    def from_instance(cls, root: Path, executable: str | None = None, workspace: Path | None = None, transport: str = "stdio") -> "CapabilitySet":
        """Lê certificação privada sem modificar configuração ou iniciar Codex.

        Mudança do binário, SO, transporte ou revisão do adaptador invalida a
        certificação. SHA do esquema é obrigatório e fica ligado ao binário
        certificado; o relatório nunca é inferido a partir da versão textual.
        """
        try:
            root = Path(root).resolve()
        except (OSError, ValueError, TypeError):
            return cls(transport=transport, compatibility_reason="instance_unavailable")
        if executable is None or workspace is None:
            try:
                import tomllib
                config = tomllib.loads((root / "configs" / "onmyoji-system.toml").read_text(encoding="utf-8")).get("codex", {})
                if not isinstance(config, dict):
                    config = {}
                executable = executable if executable is not None else str(config.get("executable") or "")
                if workspace is None:
                    candidate = Path(str(config.get("project_directory") or root))
                    workspace = candidate if candidate.is_absolute() else root / candidate
            except (OSError, ValueError, TypeError):
                executable = executable or ""
                workspace = workspace or root
        binary_hash = executable_digest(executable or "")
        try:
            workspace = Path(workspace or root)
            workspace_kind = "git" if (workspace / ".git").exists() else "plain"
        except (OSError, ValueError, TypeError):
            return cls(executable_sha256=binary_hash, transport=transport, compatibility_reason="workspace_unavailable")
        defaults = {"executable_sha256": binary_hash, "transport": transport, "workspace_kind": workspace_kind}
        private_path = root / REPORT_RELATIVE
        try:
            private_present = private_path.exists() or private_path.is_symlink()
        except (OSError, ValueError):
            return cls(**defaults, compatibility_reason="report_invalid")
        if private_present:
            path = _safe_report_path(root)
            if path is None:
                return cls(**defaults, compatibility_reason="report_invalid")
        else:
            try:
                path = Path(__file__).resolve().parents[2] / "resources" / "codex-capabilities" / f"{binary_hash}.json"
                if not binary_hash or not path.is_file():
                    return cls(**defaults)
            except (OSError, ValueError):
                return cls(**defaults, compatibility_reason="report_invalid")
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            return cls(**defaults, compatibility_reason="report_invalid")
        if not isinstance(report, dict) or report.get("schema_version") != REPORT_VERSION:
            return cls(**defaults, compatibility_reason="report_invalid")
        fingerprint = report.get("runtime")
        gates = report.get("gates")
        if not isinstance(fingerprint, dict) or not isinstance(gates, dict):
            return cls(**defaults, compatibility_reason="report_invalid")
        schema_hash = fingerprint.get("schema_sha256")
        if not isinstance(schema_hash, str) or not _DIGEST.fullmatch(schema_hash):
            return cls(**defaults, compatibility_reason="schema_identity_missing")
        if not binary_hash or fingerprint.get("executable_sha256") != binary_hash:
            return cls(**defaults, schema_sha256=schema_hash, compatibility_reason="executable_changed")
        if fingerprint.get("transport") != transport:
            return cls(**defaults, schema_sha256=schema_hash, compatibility_reason="transport_changed")
        if fingerprint.get("platform") != platform.system():
            return cls(**defaults, schema_sha256=schema_hash, compatibility_reason="platform_changed")
        if fingerprint.get("adapter_revision") != ADAPTER_REVISION:
            return cls(**defaults, schema_sha256=schema_hash, compatibility_reason="adapter_changed")
        sanitized = {key: dict(value) for key, value in gates.items() if key in {f"G{i}" for i in range(8)} and isinstance(value, dict)}
        return cls(**defaults, schema_sha256=schema_hash, compatible=True, compatibility_reason="certified_runtime", gates=sanitized)

    def supports(self, feature: str) -> bool:
        """Indica suporte comprovado; recursos desconhecidos são indisponíveis."""
        if self.reason(feature) != "supported":
            return False
        return True

    def reason(self, feature: str) -> str:
        """Retorna a primeira condição que impede o uso, sem payload sensível."""
        required = _FEATURE_GATES.get(feature)
        if required is None:
            return "unknown_capability"
        if not self.compatible:
            return self.compatibility_reason
        for gate in required:
            evidence = self.gates.get(gate, {})
            if evidence.get("status") != "supported":
                return _reason_code(evidence.get("reason"), f"{gate.lower()}_unverified")
            checks = evidence.get("checks")
            if evidence.get("evidence_kind") != "runtime" or not isinstance(checks, list) or not checks or not all(isinstance(check, str) and _REASON.fullmatch(check) for check in checks):
                return f"{gate.lower()}_runtime_evidence_missing"
        if feature == "agent_profiles":
            profile = self.gates.get("G4", {})
            if profile.get("discovery") != "workspace_codex_agents" or profile.get("snapshot") != "startup":
                return "profile_snapshot_unverified"
            clients = profile.get("clients")
            if not isinstance(clients, list) or not all(client in clients for client in ("native", "console", "app_server")):
                return "profile_clients_unverified"
            kinds = profile.get("workspace_kinds")
            if not isinstance(kinds, list) or self.workspace_kind not in kinds:
                return "profile_workspace_unverified"
        return "supported"

    def evidence(self, feature: str) -> dict[str, dict[str, Any]]:
        """Entrega somente metadados de prova, sem texto arbitrário do relatório."""
        result = {}
        for key in _FEATURE_GATES.get(feature, ()):
            gate = self.gates.get(key, {})
            status = gate.get("status")
            result[key] = {"status": status if isinstance(status, str) and status in {"supported", "unsupported", "unverified"} else "unverified", "evidence_kind": "runtime" if gate.get("evidence_kind") == "runtime" else "unverified", "reason": _reason_code(gate.get("reason"), "not_recorded")}
        return result

    def input_supported(self, kind: str) -> bool:
        """Aplica a matriz de inputs certificada a steering, inclusive anexos."""
        kinds = self.gates.get("G1", {}).get("input_kinds")
        return self.supports("steering") and isinstance(kinds, list) and kind in kinds


def load_profile_capability(root: Path, executable: str, workspace: Path) -> dict[str, Any]:
    """Adaptador mínimo para o materializador opcional de perfis do Shikigami."""
    capabilities = CapabilitySet.from_instance(root, executable, workspace)
    supported = capabilities.supports("agent_profiles")
    evidence = capabilities.gates.get("G4", {})
    optional = {}
    for key in ("profile_fields", "available_models", "profile_reasoning_efforts", "profile_reasoning_summaries", "profile_verbosities"):
        value = evidence.get(key)
        optional[key] = [item for item in value if isinstance(item, str)] if supported and isinstance(value, list) else []
    return {"supported": supported, "executable_sha256": capabilities.executable_sha256, "schema_sha256": capabilities.schema_sha256, "discovery": "workspace_codex_agents" if supported else "", "snapshot": "startup" if supported else "", "clients": list(evidence.get("clients", [])) if supported else [], "reason": capabilities.reason("agent_profiles"), **optional}
