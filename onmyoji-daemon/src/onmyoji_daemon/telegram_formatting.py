"""Renderização limitada e segura de respostas textuais para o Telegram."""
from __future__ import annotations

import html
import re


MAX_MESSAGE_LENGTH = 4096
_TAG = re.compile(r'<(?:/(?:b|i|u|s|blockquote|a)|(?:b|i|u|s|blockquote)|a href="[^"]+")>')
_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")


def render_html(text: str) -> str:
    """Converte o subconjunto textual usado pelo agente em HTML do Telegram.

    O HTML de origem nunca é aceito: primeiro todo conteúdo é escapado. Assim,
    somente as marcações produzidas por este módulo chegam ao ``parse_mode``.
    """
    escaped = html.escape(text.strip(), quote=False)

    def link(match: re.Match[str]) -> str:
        label, url = match.group(1), html.escape(html.unescape(match.group(2)), quote=True)
        if len(url) > 2048:
            return f"{label} ({url})"
        return f'<a href="{url}">{label}</a>'

    formatted = _LINK.sub(link, escaped)
    for pattern, opening, closing in (
        (re.compile(r"\*\*([^*\n]+)\*\*"), "<b>", "</b>"),
        (re.compile(r"__([^_\n]+)__"), "<u>", "</u>"),
        (re.compile(r"~~([^~\n]+)~~"), "<s>", "</s>"),
        (re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)"), "<i>", "</i>"),
    ):
        formatted = pattern.sub(lambda match: opening + match.group(1) + closing, formatted)
    return "\n".join(f"<blockquote>{line[2:]}</blockquote>" if line.startswith("&gt; ") else line for line in formatted.splitlines())


def _tag_name(token: str) -> str:
    match = re.match(r"</?([a-z]+)", token)
    return match.group(1) if match else ""


def _closing(stack: list[str]) -> str:
    return "".join(f"</{_tag_name(token)}>" for token in reversed(stack))


def split_html(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Divide HTML gerado localmente sem ultrapassar o limite nem perder tags."""
    if limit < 32:
        raise ValueError("O limite de mensagem deve comportar as marcações HTML.")
    chunks: list[str] = []
    current, stack = "", []

    def flush() -> None:
        nonlocal current
        if current:
            chunks.append(current + _closing(stack))
            current = "".join(stack)

    tokens: list[tuple[bool, str]] = []
    position = 0
    for match in _TAG.finditer(text):
        if match.start() > position:
            tokens.append((False, text[position:match.start()]))
        tokens.append((True, match.group(0)))
        position = match.end()
    if position < len(text):
        tokens.append((False, text[position:]))

    for is_tag, token in tokens:
        if is_tag:
            closing = token.startswith("</")
            if len(current) + len(token) + len(_closing(stack)) > limit:
                flush()
            current += token
            if closing:
                name = _tag_name(token)
                if stack and _tag_name(stack[-1]) == name:
                    stack.pop()
            else:
                stack.append(token)
            continue
        remainder = token
        while remainder:
            capacity = limit - len(current) - len(_closing(stack))
            if capacity <= 0:
                flush(); capacity = limit - len(current) - len(_closing(stack))
            take = min(len(remainder), capacity)
            if take < len(remainder):
                split_at = max(remainder.rfind(" ", 0, take), remainder.rfind("\n", 0, take))
                if split_at > 0:
                    take = split_at + 1
            current += remainder[:take]
            remainder = remainder[take:]
            if remainder:
                flush()
    if current:
        chunks.append(current + _closing(stack))
    return chunks or [""]


def visible_text(html_text: str) -> str:
    """Recupera o texto visível de HTML que foi produzido por este módulo."""
    return html.unescape(_TAG.sub("", html_text))


def plain_text(text: str) -> str:
    """Remove a marcação local antes de entregar uma resposta à síntese de voz."""
    return visible_text(render_html(text))
