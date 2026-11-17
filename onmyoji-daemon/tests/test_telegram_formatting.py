"""Contratos de citações do agente, com resultados visíveis independentes do renderer."""

import pytest

from onmyoji_daemon.telegram_formatting import (
    MAX_MESSAGE_LENGTH,
    plain_text,
    render_html,
    split_html,
    visible_text,
)


@pytest.mark.parametrize(("source", "expected_html", "expected_text"), [
    ("> Objetivo", "<blockquote>Objetivo</blockquote>", "Objetivo"),
    ("> **Objetivo**", "<blockquote><b>Objetivo</b></blockquote>", "Objetivo"),
    (
        "> *itálico* __sublinhado__ ~~tachado~~",
        "<blockquote><i>itálico</i> <u>sublinhado</u> <s>tachado</s></blockquote>",
        "itálico sublinhado tachado",
    ),
    (
        '> <script> & valor > 10 "reais"',
        '<blockquote>&lt;script&gt; &amp; valor &gt; 10 "reais"</blockquote>',
        '<script> & valor > 10 "reais"',
    ),
    (
        "> [site](https://example.test/?a=1&b=2)",
        '<blockquote><a href="https://example.test/?a=1&amp;b=2">site</a></blockquote>',
        "site",
    ),
    (
        "> t; é parte do conteúdo",
        "<blockquote>t; é parte do conteúdo</blockquote>",
        "t; é parte do conteúdo",
    ),
    (
        "> &gt; é texto literal",
        "<blockquote>&amp;gt; é texto literal</blockquote>",
        "&gt; é texto literal",
    ),
])
def test_quote_removes_only_its_markdown_marker(source, expected_html, expected_text):
    """Citações removem o marcador inteiro sem alterar o conteúdo nem aceitar HTML."""
    rendered = render_html(source)

    assert rendered == expected_html
    assert visible_text(rendered) == expected_text
    assert plain_text(source) == expected_text


def test_separated_quote_paragraphs_preserve_the_agent_message():
    """Títulos e parágrafos citados não deixam resíduos de entidades em cada linha."""
    source = (
        "**Lavelinha:**\n\n"
        "Passe para o outro agente:\n\n"
        "> **Objetivo**\n\n"
        "> Corrigir a NF-e 000596516 da Eletro Dalbem.\n\n"
        "> O resultado desejado é manter **uma única categoria** em toda a cadeia:"
    )
    expected_html = (
        "<b>Lavelinha:</b>\n\n"
        "Passe para o outro agente:\n\n"
        "<blockquote><b>Objetivo</b></blockquote>\n\n"
        "<blockquote>Corrigir a NF-e 000596516 da Eletro Dalbem.</blockquote>\n\n"
        "<blockquote>O resultado desejado é manter <b>uma única categoria</b> em toda a cadeia:</blockquote>"
    )
    expected_text = (
        "Lavelinha:\n\n"
        "Passe para o outro agente:\n\n"
        "Objetivo\n\n"
        "Corrigir a NF-e 000596516 da Eletro Dalbem.\n\n"
        "O resultado desejado é manter uma única categoria em toda a cadeia:"
    )

    assert render_html(source) == expected_html
    assert visible_text(expected_html) == expected_text
    assert plain_text(source) == expected_text


def test_consecutive_quote_lines_preserve_their_content_and_line_breaks():
    source = "> Primeira linha\n> Segunda linha\nTexto final"
    expected_text = "Primeira linha\nSegunda linha\nTexto final"

    assert render_html(source) == (
        "<blockquote>Primeira linha</blockquote>\n"
        "<blockquote>Segunda linha</blockquote>\n"
        "Texto final"
    )
    assert plain_text(source) == expected_text


@pytest.mark.parametrize(("url_length", "hyperlink"), [(2048, True), (2049, False)])
def test_quoted_links_at_the_supported_length_limit_preserve_the_destination(url_length, hyperlink):
    """O link de até 2048 caracteres é clicável; além disso, o destino permanece em texto."""
    prefix = "https://example.test/"
    url = prefix + "a" * (url_length - len(prefix))
    source = "> [site](" + url + ")"
    expected_content = f'<a href="{url}">site</a>' if hyperlink else f"site ({url})"
    expected_text = "site" if hyperlink else f"site ({url})"

    assert render_html(source) == f"<blockquote>{expected_content}</blockquote>"
    assert plain_text(source) == expected_text


@pytest.mark.parametrize(("source", "expected_html", "expected_text"), [
    ("Valor > 10", "Valor &gt; 10", "Valor > 10"),
    ("&gt; Objetivo", "&amp;gt; Objetivo", "&gt; Objetivo"),
    (">", "&gt;", ">"),
    ("> ", "&gt;", ">"),
    (">> Objetivo", "&gt;&gt; Objetivo", ">> Objetivo"),
    ("", "", ""),
    (" \t\n ", "", ""),
])
def test_non_quote_markers_and_empty_text_keep_the_existing_contract(source, expected_html, expected_text):
    """Só o prefixo de citação já suportado é convertido; entidades literais são preservadas."""
    assert render_html(source) == expected_html
    assert plain_text(source) == expected_text


@pytest.mark.parametrize("limit", [64, MAX_MESSAGE_LENGTH])
def test_long_quote_chunks_keep_balanced_formatting_and_all_visible_text(limit):
    """Cada parte reabre a citação e o negrito sem introduzir texto na divisão."""
    expected_text = "palavra " * 1000
    source = "> **" + expected_text + "**"

    chunks = split_html(render_html(source), limit)

    assert len(chunks) > 1
    assert all(len(chunk) <= limit for chunk in chunks)
    assert all(chunk.startswith("<blockquote><b>") and chunk.endswith("</b></blockquote>") for chunk in chunks)
    assert "".join(visible_text(chunk) for chunk in chunks) == expected_text
