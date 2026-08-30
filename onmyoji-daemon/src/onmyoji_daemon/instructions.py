"""Composição segura das developer instructions do Onmyōji."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

MAX_SHIKIGAMI_BYTES = 64 * 1024
MAX_COMPOSED_BYTES = 96 * 1024


@dataclass(frozen=True)
class InstructionBundle:
    text: str
    baseline_hash: str
    overlay_hash: str
    sources: tuple[str, ...]


class InstructionError(ValueError): pass


class InstructionComposer:
    def __init__(self, root: Path, data_dir: Path, shikigami_file: str, enabled: bool = True):
        self.root, self.data_dir, self.shikigami_file, self.enabled = root.resolve(), data_dir.resolve(), shikigami_file, enabled
        self.resources = Path(__file__).resolve().parents[2] / "resources" / "instructions"
        self.instruction_root = (self.root / "configs" / "daemon" / "instructions").resolve()

    @staticmethod
    def _read(path: Path, limit: int) -> str:
        try: raw = path.read_bytes()
        except OSError as error: raise InstructionError(f"Não foi possível ler {path.name}: {error}") from error
        if len(raw) > limit: raise InstructionError(f"{path.name} excede o limite de {limit} bytes.")
        try: text = raw.decode("utf-8")
        except UnicodeDecodeError as error: raise InstructionError(f"{path.name} deve usar UTF-8.") from error
        if not text.strip(): raise InstructionError(f"{path.name} não pode estar vazio.")
        return text.strip()

    def _shikigami(self) -> tuple[str | None, str | None]:
        if not self.enabled: return None, None
        relative = Path(self.shikigami_file or "shikigami.md")
        if relative.is_absolute() or ".." in relative.parts: raise InstructionError("O arquivo de instruções do Shikigami deve ser relativo e permanecer na raiz autorizada.")
        candidate = (self.instruction_root / relative).resolve()
        if not candidate.is_relative_to(self.instruction_root): raise InstructionError("O arquivo de instruções está fora de configs/daemon/instructions.")
        if not candidate.exists(): return None, None
        if candidate.is_symlink(): raise InstructionError("Links simbólicos não são aceitos para instruções do Shikigami.")
        return self._read(candidate, MAX_SHIKIGAMI_BYTES), str(candidate)

    @staticmethod
    def _hash(text: str) -> str: return sha256(text.encode("utf-8")).hexdigest()

    def compose(self, *, identity: str, telegram: bool, reply_mode: str = "text", outbound_media: bool = False) -> InstructionBundle:
        onmyoji = self._read(self.resources / "onmyoji.md", MAX_SHIKIGAMI_BYTES)
        shikigami, shikigami_path = self._shikigami()
        parts = ["[ONMYŌJI — INSTRUÇÕES GERAIS]\n" + onmyoji, "[SHIKIGAMI — IDENTIDADE]\nSeu nome é " + identity + ". Ao ser perguntado, identifique-se assim, não como Codex."]
        sources = ["onmyoji.md", "identidade"]
        if shikigami: parts.append("[SHIKIGAMI — INSTRUÇÕES PARTICULARES]\n" + shikigami); sources.append(shikigami_path or "shikigami.md")
        if telegram:
            parts.append("[TELEGRAM — CONTRATO DE CANAL]\n" + self._read(self.resources / "telegram.md", MAX_SHIKIGAMI_BYTES))
            parts.append("[TELEGRAM — CAPACIDADES DO CANAL]\nMídia de saída: " + ("permitida." if outbound_media else "não disponível."))
            sources.append("telegram.md")
        baseline = "\n\n".join(parts)
        overlay = ""
        if telegram:
            overlay = "[TELEGRAM — ESTADO CONFIÁVEL]\nResposta final: " + reply_mode + ". " + ("Use linguagem natural breve, sem Markdown, código, tabelas ou diagramas." if reply_mode == "audio" else "Responda normalmente.")
        text = baseline + ("\n\n" + overlay if overlay else "")
        if len(text.encode("utf-8")) > MAX_COMPOSED_BYTES: raise InstructionError("A composição de instruções excede o limite total.")
        return InstructionBundle(text, self._hash(baseline), self._hash(overlay), tuple(sources))
