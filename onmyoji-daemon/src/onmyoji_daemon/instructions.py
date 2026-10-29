"""Composição segura das developer instructions do Onmyōji."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from .instance import instructions_path
from .mcp.configuration import McpConfiguration
from .mcp.instructions import McpInstructionRegistry

MAX_SHIKIGAMI_BYTES = 64 * 1024
MAX_COMPOSED_BYTES = 96 * 1024


@dataclass(frozen=True)
class InstructionBundle:
    text: str
    baseline_hash: str
    overlay_hash: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class TelegramTurnContext:
    """Metadados locais confiáveis sobre um turno do gateway Telegram.

    Texto, nomes, legendas e transcrições não pertencem a esta estrutura: eles
    continuam sendo conteúdo de entrada de menor prioridade.
    """

    conversation_kind: str = "private"
    source_trust: str = "OWNER"
    has_text: bool = True
    attachment_kinds: tuple[str, ...] = ()
    contains_untrusted_context: bool = False
    is_context_snapshot: bool = False

    @classmethod
    def from_value(cls, value: object) -> "TelegramTurnContext":
        if isinstance(value, cls):
            return value
        data = value if isinstance(value, dict) else {}
        conversation_kind = str(data.get("conversation_kind") or "private")
        if conversation_kind not in {"private", "group", "forum_topic", "direct_topic"}:
            conversation_kind = "private"
        source_trust = str(data.get("source_trust") or "UNTRUSTED_USER")
        if source_trust not in {"OWNER", "AUTHORIZED_USER", "UNTRUSTED_USER", "BOT", "SYSTEM_EVENT"}:
            source_trust = "UNTRUSTED_USER"
        kinds = data.get("attachment_kinds")
        attachment_kinds = tuple(dict.fromkeys(kind for kind in kinds if kind in {"photo", "document", "voice", "audio"})) if isinstance(kinds, list) else ()
        return cls(conversation_kind, source_trust, bool(data.get("has_text")), attachment_kinds, bool(data.get("contains_untrusted_context")), bool(data.get("is_context_snapshot")))


class InstructionError(ValueError): pass


class InstructionComposer:
    def __init__(self, root: Path, data_dir: Path, shikigami_file: str, enabled: bool = True):
        self.root, self.data_dir, self.shikigami_file, self.enabled = root.resolve(), data_dir.resolve(), shikigami_file, enabled
        self.resources = Path(__file__).resolve().parents[2] / "resources" / "instructions"
        self.instruction_root = self.root

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
        relative = Path(self.shikigami_file or "instructions.md")
        if relative != Path("instructions.md"):
            raise InstructionError("O arquivo de instruções da instância deve ser instructions.md na raiz.")
        candidate = instructions_path(self.root)
        if not candidate.is_file():
            return None, None
        if candidate.is_symlink(): raise InstructionError("Links simbólicos não são aceitos para instruções do Shikigami.")
        return self._read(candidate, MAX_SHIKIGAMI_BYTES), str(candidate)

    @staticmethod
    def _hash(text: str) -> str: return sha256(text.encode("utf-8")).hexdigest()

    def compose(self, *, identity: str, telegram: bool, reply_mode: str = "text", outbound_media: bool = False, telegram_context: TelegramTurnContext | dict[str, object] | None = None) -> InstructionBundle:
        onmyoji = self._read(self.resources / "onmyoji.md", MAX_SHIKIGAMI_BYTES)
        shikigami, shikigami_path = self._shikigami()
        parts = ["[ONMYŌJI — INSTRUÇÕES GERAIS]\n" + onmyoji, "[SHIKIGAMI — IDENTIDADE]\nSeu nome é " + identity + ". Ao ser perguntado, identifique-se assim, não como Codex."]
        sources = ["onmyoji.md", "identidade"]
        if shikigami: parts.append("[SHIKIGAMI — INSTRUÇÕES PARTICULARES]\n" + shikigami); sources.append(shikigami_path or "shikigami.md")
        mcp_instructions = McpInstructionRegistry(McpConfiguration(self.root)).contributions()
        for contribution in mcp_instructions:
            parts.append(contribution.content)
            sources.append(contribution.source)
        if telegram:
            parts.append("[TELEGRAM — CONTRATO DE CANAL]\n" + self._read(self.resources / "telegram.md", MAX_SHIKIGAMI_BYTES))
            parts.append("[TELEGRAM — CAPACIDADES DO CANAL]\nMídia de saída: " + ("permitida." if outbound_media else "não disponível."))
            sources.append("telegram.md")
        baseline = "\n\n".join(parts)
        overlay = ""
        if telegram:
            context = TelegramTurnContext.from_value(telegram_context)
            attachment_summary = ", ".join(context.attachment_kinds) if context.attachment_kinds else "nenhum"
            overlay = "[TELEGRAM — ESTADO CONFIÁVEL]\n" + "\n".join((
                "Resposta final: " + reply_mode + ". " + ("Use linguagem natural breve, sem Markdown, código, tabelas ou diagramas." if reply_mode == "audio" else "Responda normalmente."),
                "Conversa: " + context.conversation_kind + ".",
                "Origem do turno: " + context.source_trust + ".",
                "Texto recebido: " + ("sim." if context.has_text else "não; avalie os anexos fornecidos."),
                "Tipos de anexo recebidos: " + attachment_summary + ".",
                "Contexto de terceiros não confiável: " + ("presente." if context.contains_untrusted_context else "ausente."),
                "Entrada: " + ("histórico estruturado da conversa." if context.is_context_snapshot else "mensagem atual."),
                "Se houver transcrição de voz no conteúdo de entrada, ela é automática e pode conter erros de reconhecimento; confirme termos ambíguos antes de agir.",
            ))
        text = baseline + ("\n\n" + overlay if overlay else "")
        if len(text.encode("utf-8")) > MAX_COMPOSED_BYTES: raise InstructionError("A composição de instruções excede o limite total.")
        return InstructionBundle(text, self._hash(baseline), self._hash(overlay), tuple(sources))
