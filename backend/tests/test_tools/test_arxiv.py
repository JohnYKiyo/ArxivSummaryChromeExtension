"""Tests for the TeX preprocessing helpers in ``tools/arxiv.py``.

These cover the two fixes that recover content lost by the pandoc-only
path: title-block synthesis and ``.bbl`` inlining. The arXiv HTTP path
itself is not tested here — that needs the live API.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.tools.arxiv import (
    _clean_author_list,
    _expand_inputs,
    _extract_metadata_and_rewrite,
    _match_balanced_brace,
)


def test_match_balanced_brace_simple() -> None:
    text = r"\title{Foo} rest"
    open_pos = text.index("{")
    close = _match_balanced_brace(text, open_pos)
    assert text[open_pos + 1 : close] == "Foo"


def test_match_balanced_brace_nested() -> None:
    text = r"\title{A \texttt{B} C}"
    open_pos = text.index("{")
    close = _match_balanced_brace(text, open_pos)
    assert text[open_pos + 1 : close] == r"A \texttt{B} C"


def test_match_balanced_brace_escaped_braces_ignored() -> None:
    text = r"\foo{a \{ b \} c}"
    open_pos = text.index("{")
    close = _match_balanced_brace(text, open_pos)
    assert text[open_pos + 1 : close] == r"a \{ b \} c"


def test_extract_metadata_basic() -> None:
    # Use the LaTeX-realistic shape: ``\maketitle`` followed by a newline.
    # A purely concatenated form ``\maketitle\begin{abstract}...Body`` is
    # technically ill-formed (TeX would lex ``\maketitleBody`` as one
    # command-name token), and we now refuse to mangle that case.
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\title{Foo}",
            r"\author{Alice \and Bob}",
            r"\begin{document}",
            r"\maketitle",
            r"\begin{abstract}X\end{abstract}",
            r"Body",
            r"\end{document}",
        ]
    )
    out = _extract_metadata_and_rewrite(tex)
    # Title / authors / abstract block is present in order.
    foo_idx = out.index(r"\section*{Foo}")
    authors_idx = out.index(r"\textit{Alice, Bob}")
    abs_head_idx = out.index(r"\section*{Abstract}")
    body_idx = out.index("Body")
    assert foo_idx < authors_idx < abs_head_idx < body_idx
    # The X (abstract body) sits between the abstract heading and the body.
    x_idx = out.index("X")
    assert abs_head_idx < x_idx < body_idx
    # Originals are gone.
    assert r"\maketitle" not in out
    assert r"\title{" not in out
    assert r"\begin{abstract}" not in out


def test_extract_metadata_braces_in_title() -> None:
    tex = (
        r"\documentclass{article}"
        r"\title{A \texttt{B} C}"
        r"\begin{document}\maketitle Body\end{document}"
    )
    out = _extract_metadata_and_rewrite(tex)
    assert r"\section*{A \texttt{B} C}" in out


def test_extract_metadata_author_thanks_and() -> None:
    tex = (
        r"\documentclass{article}"
        r"\title{T}"
        r"\author{Alice\thanks{alice@x.com} \and Bob\thanks{bob@y.com}}"
        r"\begin{document}\maketitle Body\end{document}"
    )
    out = _extract_metadata_and_rewrite(tex)
    assert r"\textit{Alice, Bob}" in out
    assert "alice@x.com" not in out
    assert "bob@y.com" not in out


def test_extract_metadata_author_double_backslash_separator() -> None:
    tex = (
        r"\documentclass{article}"
        r"\title{T}"
        r"\author{Alice \\ Bob \\ Charlie}"
        r"\begin{document}\maketitle Body\end{document}"
    )
    out = _extract_metadata_and_rewrite(tex)
    assert r"\textit{Alice, Bob, Charlie}" in out


def test_extract_metadata_missing_title_is_noop() -> None:
    tex = r"\documentclass{article}\begin{document}Body only\end{document}"
    out = _extract_metadata_and_rewrite(tex)
    assert out == tex


def test_extract_metadata_no_maketitle_inserts_after_begin_document() -> None:
    tex = (
        r"\documentclass{article}"
        r"\title{T}\author{A}"
        r"\begin{document}Body\end{document}"
    )
    out = _extract_metadata_and_rewrite(tex)
    # The synthesized title block follows \begin{document} but precedes "Body".
    doc_idx = out.index(r"\begin{document}")
    title_idx = out.index(r"\section*{T}")
    body_idx = out.index("Body")
    assert doc_idx < title_idx < body_idx


def test_extract_metadata_does_not_replace_renewcommand_arg() -> None:
    """``\\renewcommand{\\maketitle}{...}`` redefines ``\\maketitle`` and contains
    ``\\maketitle`` as a token (followed by ``}``) — that occurrence must not
    be substituted for the title block. Only the bare ``\\maketitle`` command
    invocation gets replaced.
    """
    tex = (
        r"\documentclass{article}"
        r"\title{Meet Your New Client}"
        r"\author{Paul}"
        r"\renewcommand{\maketitle}{\begin{center}Custom\end{center}}"
        r"\begin{document}"
        r"\maketitle"
        r"Body of paper."
        r"\end{document}"
    )
    out = _extract_metadata_and_rewrite(tex)
    # The renewcommand's brace must still contain \maketitle (as a token).
    assert r"\renewcommand{\maketitle}" in out
    # The synthesized title block appears once, replacing the bare \maketitle.
    assert r"\section*{Meet Your New Client}" in out
    assert out.count(r"\section*{Meet Your New Client}") == 1
    # The pathological output that crashed pandoc must NOT appear.
    assert r"\renewcommand{\section*" not in out


def test_extract_metadata_icml_template_recognised() -> None:
    """ICML 2022+ template uses ``\\icmltitle`` and multiple ``\\icmlauthor``
    invocations instead of the standard ``\\title`` / ``\\author``. Both must
    be picked up so the output paper has proper title and author lines.
    """
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\icmltitlerunning{Rethinking the Role of LLMs in Time Series Forecasting}",
            r"\icmltitle{Rethinking the Role of LLMs in Time Series Forecasting}",
            r"\icmlsetsymbol{equal}{*}",
            r"\icmlauthor{Xin Qiu}{yyy,zju}",
            r"\icmlauthor{Junlong Tong}{yyy}",
            r"\icmlauthor{Yirong Sun}{yyy}",
            r"\icmlaffiliation{yyy}{Eastern Institute of Technology}",
            r"\icmlaffiliation{zju}{Zhejiang University}",
            r"\icmlcorrespondingauthor{Xiaoyu Shen}{xyshen@eitech.edu.cn}",
            r"\icmlkeywords{Machine Learning, ICML}",
            r"\begin{document}",
            r"\begin{abstract}Abstract body.\end{abstract}",
            r"Body of paper.",
            r"\end{document}",
        ]
    )
    out = _extract_metadata_and_rewrite(tex)
    # Title appears once as section header.
    assert r"\section*{Rethinking the Role of LLMs in Time Series Forecasting}" in out
    # All three authors collected, in source order, with commas.
    assert r"\textit{Xin Qiu, Junlong Tong, Yirong Sun}" in out
    # Affiliations surfaced (deduplicated) so the summary agent has the
    # institution information available.
    assert r"\textit{Eastern Institute of Technology, Zhejiang University}" in out
    # Abstract preserved as \section*.
    assert r"\section*{Abstract}" in out
    assert "Abstract body." in out
    # ICML-only helpers stripped from the output (they would otherwise
    # surface as raw_tex noise downstream).
    assert r"\icmlauthor" not in out
    assert r"\icmlaffiliation" not in out
    assert r"\icmlcorrespondingauthor" not in out
    assert r"\icmlsetsymbol" not in out
    assert r"\icmltitle" not in out
    assert r"\icmltitlerunning" not in out
    assert r"\icmlkeywords" not in out


def test_clean_author_list_strips_percent_line_comments() -> None:
    """TeX ``%\\n`` line-continuation must not survive whitespace collapse.

    Real-world ``\\author{}`` bodies (e.g. arXiv 2508.15817) use
    ``Name%\\n\\\\\\nAffiliation%\\n`` so paragraphs join without space.
    After our cleaner runs we would emit a single-line string where the
    ``%`` would extend its comment to the end of the line — swallowing
    everything inside the surrounding ``\\textit{...}`` and crashing
    pandoc downstream with ``unexpected ()`` errors.
    """
    raw = (
        "{\\large Alice}%\n"
        "\\thanks{Corresponding.} \\\\%\n"
        "ACME Corp \\\\%\n"
        "{\\footnotesize \\url{alice@acme.com}} \\and\n"
        "{\\large Bob}%\n"
        " \\\\%\n"
        "ACME Corp \\\\%\n"
        "{\\footnotesize \\url{bob@acme.com}}"
    )
    out = _clean_author_list(raw)
    assert "%" not in out
    # Both author names survive; both ACME mentions survive too (since
    # we don't try to deduplicate affiliations here).
    assert "Alice" in out
    assert "Bob" in out


def test_extract_metadata_title_thanks_block_stripped() -> None:
    """``\\title{T\\thanks{...}}`` must not embed the footnote inside the
    synthesized ``\\section*{...}`` heading. The ``\\thanks`` body often has
    parentheses and multi-line content that breaks pandoc when nested
    inside a ``\\section*`` argument.
    """
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\title{Meet Your New Client: Writing Reports for AI -- Benchmarking",
            r"Information Loss\thanks{We thank participants of the workshop",
            r"(DGOF) for the discussion.}}",
            r"\author{A}",
            r"\begin{document}",
            r"\maketitle",
            r"\begin{abstract}X\end{abstract}",
            r"Body",
            r"\end{document}",
        ]
    )
    out = _extract_metadata_and_rewrite(tex)
    # Title heading carries only the bare title text — no \thanks.
    assert (
        r"\section*{Meet Your New Client: Writing Reports for AI -- Benchmarking"
        r" Information Loss}"
    ) in out
    # The thanks block is gone (no parentheses-containing footnote inside
    # the heading).
    assert r"\thanks" not in out
    assert "(DGOF)" not in out


def test_extract_metadata_does_not_match_longer_command_names() -> None:
    """``\\maketitlefigure`` (a different command) must not be mistaken for
    ``\\maketitle``."""
    tex = (
        r"\documentclass{article}\title{T}"
        r"\begin{document}"
        r"\maketitlefigure"
        r"\end{document}"
    )
    out = _extract_metadata_and_rewrite(tex)
    # The longer command stays intact.
    assert r"\maketitlefigure" in out
    # Title block is inserted at \begin{document} since no bare \maketitle exists.
    doc_idx = out.index(r"\begin{document}")
    title_idx = out.index(r"\section*{T}")
    assert doc_idx < title_idx < out.index(r"\maketitlefigure")


def test_clean_author_list_collapses_whitespace_and_commas() -> None:
    raw = r" Alice ,   Bob \and  Charlie\\ Dave "
    assert _clean_author_list(raw) == "Alice, Bob, Charlie, Dave"


def test_expand_inputs_inlines_bbl(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_text(
        r"Body \bibliographystyle{plain} \bibliography{refs}",
        encoding="utf-8",
    )
    (tmp_path / "refs.bbl").write_text(
        r"\begin{thebibliography}{99}"
        "\n"
        r"\bibitem{a} Author A. Foo. 2020."
        "\n"
        r"\end{thebibliography}",
        encoding="utf-8",
    )
    tex = (tmp_path / "main.tex").read_text(encoding="utf-8")
    out = _expand_inputs(tex, tmp_path)
    assert r"\bibliography{refs}" not in out
    assert r"\bibliographystyle{plain}" not in out
    assert r"\section*{References}" in out
    assert r"\bibitem{a}" in out
    assert "Author A. Foo. 2020." in out


def test_expand_inputs_strips_bibliographystyle_with_no_bbl(tmp_path: Path) -> None:
    """\\bibliographystyle is removed even when no .bbl is found."""
    (tmp_path / "main.tex").write_text(
        r"Body \bibliographystyle{ieee}",
        encoding="utf-8",
    )
    tex = (tmp_path / "main.tex").read_text(encoding="utf-8")
    out = _expand_inputs(tex, tmp_path)
    assert r"\bibliographystyle" not in out
    assert "Body" in out


def test_expand_inputs_bbl_fallback_to_glob(tmp_path: Path) -> None:
    """When the named .bbl is missing, fall back to any *.bbl in the tree."""
    (tmp_path / "main.tex").write_text(
        r"Body \bibliography{nonexistent}",
        encoding="utf-8",
    )
    (tmp_path / "actual.bbl").write_text(
        r"\begin{thebibliography}{1}\bibitem{x} Entry.\end{thebibliography}",
        encoding="utf-8",
    )
    out = _expand_inputs((tmp_path / "main.tex").read_text(encoding="utf-8"), tmp_path)
    assert r"\bibliography{nonexistent}" not in out
    assert r"\section*{References}" in out
    assert r"\bibitem{x}" in out


def test_expand_inputs_bbl_missing_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """If no .bbl exists anywhere, leave directive and log a warning."""
    (tmp_path / "main.tex").write_text(
        r"Body \bibliography{refs}",
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="src.tools.arxiv"):
        out = _expand_inputs((tmp_path / "main.tex").read_text(encoding="utf-8"), tmp_path)
    assert r"\bibliography{refs}" in out
    assert any("No .bbl found" in rec.message for rec in caplog.records)


def test_expand_inputs_still_handles_plain_inputs(tmp_path: Path) -> None:
    """Regression: existing \\input behaviour is unchanged."""
    (tmp_path / "main.tex").write_text(
        r"Before \input{section1} After",
        encoding="utf-8",
    )
    (tmp_path / "section1.tex").write_text("Middle", encoding="utf-8")
    out = _expand_inputs((tmp_path / "main.tex").read_text(encoding="utf-8"), tmp_path)
    assert out == "Before Middle After"
