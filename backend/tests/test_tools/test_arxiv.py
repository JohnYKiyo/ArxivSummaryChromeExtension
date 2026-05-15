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
    tex = (
        r"\documentclass{article}"
        r"\title{Foo}"
        r"\author{Alice \and Bob}"
        r"\begin{document}"
        r"\maketitle"
        r"\begin{abstract}X\end{abstract}"
        r"Body"
        r"\end{document}"
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
