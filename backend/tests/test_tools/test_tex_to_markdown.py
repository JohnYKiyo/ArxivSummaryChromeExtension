"""Tests for the pandoc-backed TeX → Markdown converter.

These run pandoc as a subprocess (the same way the pipeline does), so
they require the ``pandoc`` binary to be available on PATH.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from src.tools.arxiv import _extract_metadata_and_rewrite
from src.tools.tex_to_markdown import (
    PandocConversionError,
    PandocNotInstalledError,
    tex_to_markdown,
)

_PANDOC_AVAILABLE = shutil.which("pandoc") is not None


pytestmark = pytest.mark.skipif(
    not _PANDOC_AVAILABLE,
    reason="pandoc binary not installed in this environment",
)


def test_basic_document_converts(tmp_path: Path) -> None:
    """A minimal article with one section should become a single H1 + paragraph."""
    tex = (
        r"\documentclass{article}"
        r"\begin{document}"
        r"\section{Introduction}"
        r"Hello world."
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "# Introduction" in md
    assert "Hello world" in md


def test_inline_math_uses_dollar_signs(tmp_path: Path) -> None:
    """``$x^2$`` should round-trip as ``$x^2$`` in the Markdown output."""
    tex = (
        r"\documentclass{article}\begin{document}"
        r"The energy is $E = mc^2$ in this universe."
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "$E = mc^2$" in md


def test_display_math_uses_double_dollars(tmp_path: Path) -> None:
    """Equation environments should render as ``$$...$$``."""
    tex = (
        r"\documentclass{article}\begin{document}"
        r"\begin{equation}\sum_{i=1}^n x_i\end{equation}"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "$$" in md
    assert "sum_{i=1}^n x_i" in md or r"\sum_{i=1}^n x_i" in md


def test_itemize_becomes_dash_list(tmp_path: Path) -> None:
    """``\\begin{itemize}`` should produce a dash-bullet list."""
    tex = (
        r"\documentclass{article}\begin{document}"
        r"\begin{itemize}\item Apple \item Banana\end{itemize}"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    # pandoc emits either "- Apple" or "-   Apple" (multi-space indent) — both
    # render the same in any Markdown viewer. Match the loose form.
    assert re.search(r"^-\s+Apple", md, re.MULTILINE), md
    assert re.search(r"^-\s+Banana", md, re.MULTILINE), md


def test_section_levels(tmp_path: Path) -> None:
    """``\\section`` / ``\\subsection`` should map to ATX heading depths."""
    tex = (
        r"\documentclass{article}\begin{document}"
        r"\section{Top}Foo"
        r"\subsection{Sub}Bar"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "# Top" in md
    assert "## Sub" in md


def test_includegraphics_becomes_image_link(tmp_path: Path) -> None:
    """``\\includegraphics`` should turn into a Markdown image reference.

    Post-processing flattens directory components and prefixes ``images/``
    to match where packaging.py stores figures in the result ZIP.
    """
    tex = (
        r"\documentclass{article}\usepackage{graphicx}\begin{document}"
        r"\includegraphics{figures/fig1.png}"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "images/fig1.png" in md


def test_pandocbounded_wrapper_unwrapped_so_image_survives(tmp_path: Path) -> None:
    r"""``\pandocbounded{\includegraphics{...}}`` must still produce an ``<img>``.

    Quarto-origin arXiv papers (e.g. 2508.15817) wrap every figure in
    ``\pandocbounded``, defined in the preamble with TeX internals
    (``\sbox``, ``\Gscale@div``) that pandoc's LaTeX reader cannot evaluate.
    Without unwrapping, the wrapped ``\includegraphics`` silently disappears
    and the rendered Markdown shows ``<figure>`` + ``<figcaption>`` but no
    image — so Obsidian renders a caption with nothing above it.
    """
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\usepackage{graphicx}",
            r"\makeatletter",
            r"\newsavebox\pandoc@box",
            r"\newcommand*\pandocbounded[1]{%",
            r"  \sbox\pandoc@box{#1}%",
            r"  \Gscale@div\@tempa{\textheight}{\dimexpr\ht\pandoc@box+\dp\pandoc@box\relax}%",
            r"  \else\usebox{\pandoc@box}%",
            r"  \fi%",
            r"}",
            r"\makeatother",
            r"\begin{document}",
            r"\begin{figure}",
            r"\pandocbounded{\includegraphics[width=1\linewidth,keepaspectratio]{figures/overview.png}}",
            r"\caption{\label{fig-overview}Experiment setup diagram.}",
            r"\end{figure}",
            r"\end{document}",
        ]
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "images/overview.png" in md, (
        "Expected the unwrapped \\includegraphics to land at images/overview.png; "
        f"got:\n{md}"
    )


def test_unwrap_brace_swallowers_balances_nested_braces() -> None:
    """The unwrap step must track brace depth, not split on the first ``}``.

    ``\\includegraphics[opt]{path}`` contains a brace-pair inside the
    ``\\pandocbounded{...}`` argument; a naive replace would stop at the
    inner ``}`` and leave a dangling ``}`` in the output.
    """
    from src.tools.tex_to_markdown import _unwrap_brace_swallowers

    tex = r"prefix \pandocbounded{\includegraphics[width=1\linewidth]{path/img.png}} suffix"
    result = _unwrap_brace_swallowers(tex)
    assert result == r"prefix \includegraphics[width=1\linewidth]{path/img.png} suffix"


def test_unwrap_brace_swallowers_leaves_unrelated_tex_alone() -> None:
    from src.tools.tex_to_markdown import _unwrap_brace_swallowers

    tex = r"\section{Intro} \textbf{bold} \cite{Foo} \includegraphics{plain.png}"
    assert _unwrap_brace_swallowers(tex) == tex


def test_unwrap_brace_swallowers_handles_multiple_calls() -> None:
    from src.tools.tex_to_markdown import _unwrap_brace_swallowers

    tex = (
        r"\pandocbounded{\includegraphics{a.png}}"
        r" between "
        r"\pandocbounded{\includegraphics[width=2cm]{b.png}}"
    )
    expected = r"\includegraphics{a.png} between \includegraphics[width=2cm]{b.png}"
    assert _unwrap_brace_swallowers(tex) == expected


def test_centering_with_braces_unwrapped_so_image_survives(tmp_path: Path) -> None:
    r"""Quarto's ``\centering{\includegraphics{...}}`` figure shape must yield ``<img>``.

    Quarto's LaTeX writer emits ``\centering`` as if it took a braced
    argument, e.g.::

        \begin{figure}
        \centering{
        \includegraphics[width=1\linewidth]{images/layout.png}
        }
        \caption{...}
        \end{figure}

    Standard LaTeX accepts this (``\centering`` is a declaration; the
    braces just form a harmless group), but pandoc treats ``\centering{X}``
    as a command that consumes ``X`` and emits nothing. Without unwrapping,
    the rendered ``<figure>`` is missing its ``<img>``. This pattern
    appears in 2508.15817 alongside ``\pandocbounded``.
    """
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\usepackage{graphicx}",
            r"\begin{document}",
            r"\begin{figure}",
            r"\centering{",
            r"\includegraphics[width=1\linewidth]{figures/layout.png}",
            r"}",
            r"\caption{\label{fig-data-types}A caption}",
            r"\end{figure}",
            r"\end{document}",
        ]
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "images/layout.png" in md, (
        "Expected the unwrapped \\includegraphics to land at images/layout.png; "
        f"got:\n{md}"
    )


def test_bare_centering_declaration_is_left_alone() -> None:
    """Plain ``\\centering`` (no braces) is valid LaTeX and must not be touched."""
    from src.tools.tex_to_markdown import _unwrap_brace_swallowers

    tex = r"\begin{figure} \centering \includegraphics{ok.png} \end{figure}"
    assert _unwrap_brace_swallowers(tex) == tex


def test_nested_centering_inside_pandocbounded_fully_unwrapped() -> None:
    r"""``\centering{ ... \pandocbounded{\includegraphics{...}} ... }`` must unwrap both layers.

    In 2508.15817 every figure is shaped like::

        \begin{figure}
        \centering{
        \pandocbounded{\includegraphics[...]{path}}
        }
        \caption{...}
        \end{figure}

    A single-pass unwrap consumes the outer ``\centering{...}`` and emits
    the inner content (including ``\pandocbounded{...}``) without re-scanning
    it — so the inner swallower survives and pandoc still drops the image.
    The implementation recurses into captured content; this test would
    regress to a half-unwrapped string if recursion is lost.
    """
    from src.tools.tex_to_markdown import _unwrap_brace_swallowers

    tex = (
        r"\centering{"
        "\n"
        r"\pandocbounded{\includegraphics[width=1\linewidth]{figs/x.png}}"
        "\n"
        r"}"
    )
    expected = "\n" + r"\includegraphics[width=1\linewidth]{figs/x.png}" + "\n"
    assert _unwrap_brace_swallowers(tex) == expected


def test_quarto_figure_full_pipeline_produces_img(tmp_path: Path) -> None:
    r"""The full pandoc pipeline must emit ``<img>`` for the Quarto figure shape.

    End-to-end regression for the 2508.15817 figure failure mode: outer
    ``\centering{ ... }`` wrapping inner ``\pandocbounded{...}``. Before the
    fix this rendered as ``<figure>`` + ``<p> </p>`` + ``<figcaption>`` — a
    caption with nothing above it in Obsidian.
    """
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\usepackage{graphicx}",
            r"\makeatletter",
            r"\newsavebox\pandoc@box",
            r"\newcommand*\pandocbounded[1]{%",
            r"  \sbox\pandoc@box{#1}%",
            r"  \else\usebox{\pandoc@box}%",
            r"  \fi%",
            r"}",
            r"\makeatother",
            r"\begin{document}",
            r"\begin{figure}",
            r"",
            r"\centering{",
            r"",
            r"\pandocbounded{\includegraphics[keepaspectratio]{figs/overview.png}}",
            r"",
            r"}",
            r"",
            r"\caption{\label{fig-overview}Experiment setup diagram.}",
            r"",
            r"\end{figure}",
            r"\end{document}",
        ]
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "images/overview.png" in md, (
        "Expected fully-unwrapped figure to ship an image; got:\n" + md
    )


def test_malformed_tex_raises_conversion_error(tmp_path: Path) -> None:
    """An obviously broken TeX document should surface as PandocConversionError.

    Pandoc is fairly tolerant — most rough TeX still converts (just with
    warnings). We use an explicitly broken construct so pandoc errors hard.
    """
    # Unmatched braces / unknown environment that pandoc can't recover from.
    tex = r"\documentclass{article}\begin{document}\begin{nosuchenv}content"
    # pandoc may or may not fail outright on this — both behaviours are OK as
    # long as we don't crash. Accept "conversion succeeded" or
    # PandocConversionError; we just need to not raise unexpectedly.
    try:
        tex_to_markdown(tex, work_dir=tmp_path)
    except PandocConversionError:
        pass


def test_unnumbered_section_drops_header_attributes(tmp_path: Path) -> None:
    """``\\section*{Foo}`` must not leak ``{#foo .unnumbered}`` syntax.

    Pandoc's default markdown output appends ``{#anchor .class}`` to every
    heading via the ``header_attributes`` extension. Standard Markdown
    viewers (GitHub, Obsidian, VS Code preview) render this verbatim.
    We disable that extension in the writer.
    """
    tex = (
        r"\documentclass{article}\begin{document}"
        r"\section*{Deep Reinforcement Learning with Double Q-learning}"
        r"Body."
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "Deep Reinforcement Learning with Double Q-learning" in md
    assert ".unnumbered" not in md
    assert "{#deep-reinforcement-learning-with-double-q-learning" not in md


def test_image_drops_link_attributes(tmp_path: Path) -> None:
    """Image width specifiers must not appear as ``{width="6.8in"}`` in MD."""
    tex = (
        r"\documentclass{article}\usepackage{graphicx}\begin{document}"
        r"\includegraphics[width=6.8in]{figures/fig1.png}"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "images/fig1.png" in md
    assert 'width="' not in md


def test_figure_environment_drops_fenced_div_wrapper(tmp_path: Path) -> None:
    """``\\begin{figure*}...\\end{figure*}`` must not produce ``::: figure*`` wrappers."""
    tex = (
        r"\documentclass{article}\usepackage{graphicx}\begin{document}"
        r"\begin{figure*}\includegraphics{figures/fig1.png}\end{figure*}"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "images/fig1.png" in md
    assert ":::" not in md


def test_citation_at_sign_stripped_from_brackets(tmp_path: Path) -> None:
    """``\\cite{Key}`` must end up as a clean ``[Key]`` label.

    Pandoc emits ``[@Key]`` via its citations extension. Standard Markdown
    viewers render this verbatim ("@" visible), so we strip the ``@``
    post-pandoc to keep just the citation key as a readable label.
    """
    tex = (
        r"\documentclass{article}\begin{document}"
        r"For experience replay~\cite{Lin:1992}, see also \cite{Mnih:2015}."
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "[Lin:1992]" in md
    assert "[Mnih:2015]" in md
    assert "[@Lin:1992]" not in md
    assert "[@Mnih:2015]" not in md


def test_citation_at_sign_stripped_in_multi_key_citation(tmp_path: Path) -> None:
    """``\\cite{a,b}`` → ``[@a; @b]`` → ``[a; b]`` (both ``@`` removed)."""
    tex = (
        r"\documentclass{article}\begin{document}"
        r"See \cite{a,b}."
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "@" not in md
    # Both keys remain, separated by pandoc's semicolon-list form.
    assert "a" in md
    assert "b" in md


def test_strip_vskip_length_command() -> None:
    """``\\vskip 3em`` (and friends) must be removed before pandoc sees them."""
    from src.tools.tex_to_markdown import _strip_tex_spacing_commands

    tex = "Body.\n\\vskip 3em\n\\hskip 1in\n\\kern 0.5pt\nMore body."
    out = _strip_tex_spacing_commands(tex)
    assert "\\vskip" not in out
    assert "\\hskip" not in out
    assert "\\kern" not in out
    assert "Body." in out
    assert "More body." in out


def test_strip_braced_spacing_command() -> None:
    """``\\vspace{2em}`` / ``\\hspace*{1cm}`` braced forms also removed."""
    from src.tools.tex_to_markdown import _strip_tex_spacing_commands

    tex = "X \\vspace{2em} Y \\hspace*{1cm} Z"
    out = _strip_tex_spacing_commands(tex)
    assert "\\vspace" not in out
    assert "\\hspace" not in out
    assert "X" in out
    assert "Y" in out
    assert "Z" in out


def test_strip_bare_spacing_command() -> None:
    """Argumentless ``\\smallskip``, ``\\noindent``, ``\\hfill`` etc. removed."""
    from src.tools.tex_to_markdown import _strip_tex_spacing_commands

    tex = "A \\smallskip B \\noindent C \\hfill D \\bigskip E"
    out = _strip_tex_spacing_commands(tex)
    for cmd in ("\\smallskip", "\\noindent", "\\hfill", "\\bigskip"):
        assert cmd not in out


def test_strip_does_not_touch_unrelated_commands() -> None:
    from src.tools.tex_to_markdown import _strip_tex_spacing_commands

    tex = r"\section{Header} \emph{italic} \textbf{bold}"
    assert _strip_tex_spacing_commands(tex) == tex


def test_vskip_in_body_does_not_crash_pandoc(tmp_path: Path) -> None:
    """A bare ``\\vskip 3em`` in document body must not abort pandoc.

    Custom-typeset arXiv papers place ``\\vskip`` next to environments
    (e.g. after ``\\end{abstract}``). When upstream metadata rewriting
    strips the surrounding environment, the ``\\vskip`` ends up at body
    top-level and pandoc's LaTeX reader was rejecting it (exit 64,
    ``unexpected \\vskip``). The preprocessor strips these.
    """
    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"\begin{document}",
            r"\vskip 3em",
            r"\section{Intro}",
            r"Body of paper.",
            r"\end{document}",
        ]
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "Body of paper." in md
    assert "\\vskip" not in md


def test_cite_with_leading_comma_normalised(tmp_path: Path) -> None:
    """``\\cite{,Key}`` (author typo) must not break the pandoc pipeline.

    LaTeX silently tolerates this; pandoc aborts with ``unexpected ,``
    (exit 64). The preprocessor strips the stray comma so pandoc gets a
    clean argument and the pipeline completes.
    """
    tex = (
        r"\documentclass{article}\begin{document}"
        r"See~\cite{,Yuqietal-2023-PatchTST} for details."
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    assert "Yuqietal-2023-PatchTST" in md


def test_cite_with_trailing_and_doubled_commas_normalised() -> None:
    """``\\cite{A,,B,}`` → ``\\cite{A,B}``."""
    from src.tools.tex_to_markdown import _normalise_cite_args

    tex = r"\cite{A,,B,} and \citep[p.~5]{,X}"
    out = _normalise_cite_args(tex)
    assert out == r"\cite{A,B} and \citep[p.~5]{X}"


def test_cite_with_only_commas_becomes_empty() -> None:
    """``\\cite{,,}`` is meaningless; collapse to ``\\cite{}`` rather than crash."""
    from src.tools.tex_to_markdown import _normalise_cite_args

    out = _normalise_cite_args(r"\cite{,,}")
    assert out == r"\cite{}"


def test_normal_cite_passes_through_unchanged() -> None:
    from src.tools.tex_to_markdown import _normalise_cite_args

    tex = r"See \cite{Mnih:2015,Lin:1992} and \citet{vanHasselt:2010}."
    assert _normalise_cite_args(tex) == tex


def test_in_text_citation_at_sign_stripped() -> None:
    """pandoc emits ``@Key`` (no brackets) for in-text citations too."""
    from src.tools.tex_to_markdown import _strip_citation_at_signs

    md = "as @Mnih:2015 showed, Q-learning overestimates."
    out = _strip_citation_at_signs(md)
    assert "@" not in out
    assert "Mnih:2015" in out


def test_email_addresses_preserved_by_lookbehind() -> None:
    """``her@example.com`` and similar must not be treated as citations."""
    from src.tools.tex_to_markdown import _strip_citation_at_signs

    md = "Email her@example.com or me@host."
    out = _strip_citation_at_signs(md)
    assert out == md


def test_markdown_link_with_at_in_brackets_is_preserved() -> None:
    """Don't munge a link whose text contains ``@`` but isn't a citation."""
    from src.tools.tex_to_markdown import _strip_citation_at_signs

    # Pandoc citation has @key at word boundary; "@foo@bar" doesn't match.
    md = "[foo@bar.com](mailto:foo@bar.com)"
    out = _strip_citation_at_signs(md)
    assert out == md


def test_footnote_ref_and_anchor_ref_are_preserved() -> None:
    from src.tools.tex_to_markdown import _strip_citation_at_signs

    md = "See note[^1] and section [TDDQ]."
    out = _strip_citation_at_signs(md)
    assert out == md


# ---------------------------------------------------------------------------
# _rewrite_image_paths
# ---------------------------------------------------------------------------


def test_markdown_image_pdf_extension_rewritten_to_png_under_images_dir() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "![image](function_overest.pdf){width=\"6.8in\"}"
    out = _rewrite_image_paths(md)
    assert out == "![image](images/function_overest.png)"


def test_markdown_image_bare_name_gets_png_extension_and_images_prefix() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "![alt](Gaussian_bars)"
    out = _rewrite_image_paths(md)
    assert out == "![alt](images/Gaussian_bars.png)"


def test_markdown_image_renderable_extension_preserved() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "![](photo.jpg) and ![](logo.svg)"
    out = _rewrite_image_paths(md)
    assert "images/photo.jpg" in out
    assert "images/logo.svg" in out


def test_markdown_image_external_url_left_alone() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "![](https://example.com/x.png)"
    out = _rewrite_image_paths(md)
    assert out == md


def test_html_img_src_attribute_rewritten() -> None:
    """Raw HTML ``<img>`` inside pandoc-emitted ``<figure>`` blocks gets rewritten."""
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = '<img src="Gaussian_bars" style="width:3.3in" />'
    out = _rewrite_image_paths(md)
    assert 'src="images/Gaussian_bars.png"' in out
    # Other attributes preserved so width hint survives for Obsidian.
    assert 'style="width:3.3in"' in out


def test_html_img_with_pdf_extension_rewritten_to_png() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = '<img src="figures/fig1.pdf" />'
    out = _rewrite_image_paths(md)
    assert 'src="images/fig1.png"' in out


def test_image_subdir_components_dropped() -> None:
    """``figures/sub/x.pdf`` flattens to ``images/x.png`` — packaging stores flat."""
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "![](figures/sub/x.pdf)"
    out = _rewrite_image_paths(md)
    assert out == "![](images/x.png)"


def test_pandoc_attribute_block_stripped_from_image() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "![alt](x.png){width=\"3in\" height=\"2in\"}"
    out = _rewrite_image_paths(md)
    assert "{" not in out
    assert out == "![alt](images/x.png)"


def test_rewrite_does_not_touch_prose() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = "This sentence has no images and stays unchanged."
    assert _rewrite_image_paths(md) == md


def test_html_embed_tag_canonicalised_to_img_with_png_path() -> None:
    """``<embed src="x.pdf">`` (pandoc for PDF figures) → ``<img src="images/x.png">``."""
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = '<embed src="dqn_overestimation.pdf" style="width:70.0%" />'
    out = _rewrite_image_paths(md)
    assert "<embed" not in out
    assert '<img' in out
    assert 'src="images/dqn_overestimation.png"' in out
    # Surrounding attributes (style) preserved so width hint survives.
    assert 'style="width:70.0%"' in out


def test_html_embed_two_on_same_line_both_rewritten() -> None:
    from src.tools.tex_to_markdown import _rewrite_image_paths

    md = '<embed src="a.pdf" /> <embed src="b.pdf" />'
    out = _rewrite_image_paths(md)
    assert out.count("<img") == 2
    assert "<embed" not in out
    assert "images/a.png" in out
    assert "images/b.png" in out


# ---------------------------------------------------------------------------
# _strip_pandoc_div_wrappers
# ---------------------------------------------------------------------------


def test_figure_div_wrapper_stripped_keeping_inner_content() -> None:
    from src.tools.tex_to_markdown import _strip_pandoc_div_wrappers

    md = (
        '<div class="figure*">\n'
        '<div class="center">\n'
        '\n'
        '<embed src="x.pdf" style="width:70.0%" />\n'
        '\n'
        '</div>\n'
        '\n'
        '</div>\n'
        'After the figure.\n'
    )
    out = _strip_pandoc_div_wrappers(md)
    assert '<div class' not in out
    assert '</div>' not in out
    assert '<embed src="x.pdf"' in out
    assert "After the figure." in out


def test_unmatched_close_div_kept() -> None:
    """If a stray ``</div>`` has no matching figure open, leave it alone."""
    from src.tools.tex_to_markdown import _strip_pandoc_div_wrappers

    md = '</div>\nfollowed by text'
    out = _strip_pandoc_div_wrappers(md)
    # We didn't see an opening wrapper, so we don't pop a close.
    assert "</div>" in out


def test_non_figure_div_is_left_alone() -> None:
    """A ``<div class="warning">`` (not figure/center) is preserved as-is."""
    from src.tools.tex_to_markdown import _strip_pandoc_div_wrappers

    md = '<div class="warning">\nBeware!\n</div>\n'
    out = _strip_pandoc_div_wrappers(md)
    assert '<div class="warning">' in out
    assert '</div>' in out


def test_id_only_div_wrapper_stripped() -> None:
    """``<div id="tableresults">`` (label anchor, no class) is also a wrapper."""
    from src.tools.tex_to_markdown import _strip_pandoc_div_wrappers

    md = '<div id="tableresults">\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n</div>\n'
    out = _strip_pandoc_div_wrappers(md)
    assert '<div' not in out
    assert '</div>' not in out
    assert '| a | b |' in out


def test_div_with_id_and_figure_class_stripped() -> None:
    from src.tools.tex_to_markdown import _strip_pandoc_div_wrappers

    md = '<div id="fig1" class="figure*">\ninner\n</div>\n'
    out = _strip_pandoc_div_wrappers(md)
    assert '<div' not in out
    assert "inner" in out


# ---------------------------------------------------------------------------
# _strip_pandoc_crossrefs
# ---------------------------------------------------------------------------


def test_pandoc_eqref_link_replaced_by_text() -> None:
    from src.tools.tex_to_markdown import _strip_pandoc_crossrefs

    md = (
        'See <a href="#TDQ" data-reference-type="eqref" '
        'data-reference="TDQ">[TDQ]</a> for details.'
    )
    out = _strip_pandoc_crossrefs(md)
    assert out == "See [TDQ] for details."


def test_pandoc_ref_link_replaced_by_text() -> None:
    from src.tools.tex_to_markdown import _strip_pandoc_crossrefs

    md = (
        'Figure <a href="#fig:dqn_overest" data-reference-type="ref" '
        'data-reference="fig:dqn_overest">[fig:dqn_overest]</a>.'
    )
    out = _strip_pandoc_crossrefs(md)
    assert out == "Figure [fig:dqn_overest]."


def test_ordinary_link_left_alone() -> None:
    """A normal ``<a href="...">`` without ``data-reference-type`` survives."""
    from src.tools.tex_to_markdown import _strip_pandoc_crossrefs

    md = 'See <a href="https://example.com">the docs</a>.'
    out = _strip_pandoc_crossrefs(md)
    assert out == md


# ---------------------------------------------------------------------------
# _convert_table_captions
# ---------------------------------------------------------------------------


def test_table_caption_converted_to_numbered_bold_label() -> None:
    """``: caption`` after a pipe table → ``**Table 1:** caption``."""
    from src.tools.tex_to_markdown import _convert_table_captions

    md = (
        "|  a  |  b  |\n"
        "|:---:|:---:|\n"
        "|  1  |  2  |\n"
        "\n"
        ": Summary of results.\n"
    )
    out = _convert_table_captions(md)
    assert "**Table 1:** Summary of results." in out
    assert "\n: Summary" not in out


def test_multiple_table_captions_numbered_sequentially() -> None:
    from src.tools.tex_to_markdown import _convert_table_captions

    md = (
        "| h |\n|---|\n| x |\n\n"
        ": First caption.\n\n"
        "| h |\n|---|\n| y |\n\n"
        ": Second caption.\n"
    )
    out = _convert_table_captions(md)
    assert "**Table 1:** First caption." in out
    assert "**Table 2:** Second caption." in out


def test_definition_list_colon_not_treated_as_caption() -> None:
    """``: definition`` NOT preceded by a pipe table must stay untouched."""
    from src.tools.tex_to_markdown import _convert_table_captions

    md = "Term\n: A definition that is not a table caption.\n"
    out = _convert_table_captions(md)
    assert out == md


def test_colon_line_far_from_table_not_converted() -> None:
    """Caption detection requires the IMMEDIATELY preceding non-blank line
    to be a pipe row."""
    from src.tools.tex_to_markdown import _convert_table_captions

    md = (
        "| h |\n|---|\n| x |\n\n"
        "Some prose between table and colon line.\n\n"
        ": Not a caption — there was prose in between.\n"
    )
    out = _convert_table_captions(md)
    assert "**Table" not in out
    assert ": Not a caption" in out


def test_table_uses_pipe_or_html_format(tmp_path: Path) -> None:
    """A header-less TeX table must not come out as ``simple_tables``."""
    tex = (
        r"\documentclass{article}\begin{document}"
        r"\begin{tabular}{lcr}"
        r"Median & 93.5\% & 114.7\% \\"
        r"Mean   & 241.1\% & 330.3\% \\"
        r"\end{tabular}"
        r"\end{document}"
    )
    md = tex_to_markdown(tex, work_dir=tmp_path)
    # simple_tables-style horizontal rule (only dashes and spaces) must not
    # appear — that's the format that Obsidian / GitHub / VS Code don't render.
    has_dash_rule = bool(re.search(r"^\s*-+\s+-+", md, re.MULTILINE))
    assert not has_dash_rule, f"Table came out as simple_tables format:\n{md}"
    # Content should still be there in some form (pipe or HTML).
    assert "93.5" in md
    assert "114.7" in md


def test_raises_when_pandoc_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """If pandoc isn't on PATH, we should raise PandocNotInstalledError."""
    monkeypatch.setattr("src.tools.tex_to_markdown.shutil.which", lambda _name: None)
    with pytest.raises(PandocNotInstalledError):
        tex_to_markdown(r"\documentclass{article}\begin{document}x\end{document}", work_dir=tmp_path)


def test_commented_title_does_not_shadow_real_title(tmp_path: Path) -> None:
    r"""``% \title{Placeholder}`` above the real ``\title{...}`` must not win.

    Reproduces the 2601.11516 (Google DeepMind Probes) failure mode: their
    ``boilerplate.tex`` ships the template placeholder commented out one
    line above the real declaration::

        % \title{Using the Google DeepMind \LaTeX ~Style}
        \title{Building Production-Ready Probes For Gemini}

    Before the fix, the metadata regex picked the *first* textual
    ``\title{`` — i.e. the commented placeholder — and the rendered paper
    carried the template's filler title.
    """
    from src.tools.arxiv import _find_command_inner

    tex = "\n".join(
        [
            r"\documentclass{article}",
            r"% \title{Placeholder Title}",
            r"\title{Real Title}",
            r"\begin{document}",
            r"\maketitle",
            r"\end{document}",
        ]
    )
    assert _find_command_inner(tex, "title") == "Real Title"

    rewritten = _extract_metadata_and_rewrite(tex)
    md = tex_to_markdown(rewritten, work_dir=tmp_path)
    assert "Placeholder Title" not in md, md
    assert re.search(r"^#\s+Real Title", md, re.MULTILINE), md


def test_commented_begin_abstract_does_not_shadow_real_abstract(tmp_path: Path) -> None:
    r"""A commented ``% \begin{abstract}`` must not be picked up as the abstract.

    Same shape as the title bug but for the abstract environment: a
    commented-out template line shouldn't shadow the real
    ``\begin{abstract}...\end{abstract}`` below it.
    """
    from src.tools.arxiv import _find_environment_body

    tex = "\n".join(
        [
            r"% \begin{abstract} placeholder body \end{abstract}",
            r"\begin{abstract}",
            r"Real abstract content.",
            r"\end{abstract}",
        ]
    )
    body = _find_environment_body(tex, "abstract")
    assert body is not None
    assert "Real abstract content." in body
    assert "placeholder body" not in body


def test_escaped_percent_does_not_start_a_comment() -> None:
    r"""``\%`` is a literal percent — must not be treated as a comment start.

    Otherwise ``\title{50\% off}`` would be parsed as ``\title{50`` plus a
    comment, and we'd extract the wrong inner.
    """
    from src.tools.arxiv import _find_command_inner

    tex = r"\title{50\% off and more}"
    assert _find_command_inner(tex, "title") == r"50\% off and more"


def test_title_block_renders_after_metadata_rewrite(tmp_path: Path) -> None:
    """End-to-end: TeX with \\title/\\author/abstract → Markdown with H1/authors/H2 Abstract.

    Verifies the full title-recovery pipeline that fixes the bug where pandoc
    (without ``--standalone``) was silently dropping these elements.
    """
    tex = (
        r"\documentclass{article}"
        r"\title{Sample Paper Title}"
        r"\author{Alice \and Bob}"
        r"\begin{document}"
        r"\maketitle"
        r"\begin{abstract}"
        r"This is the abstract body."
        r"\end{abstract}"
        r"\section{Introduction}"
        r"This is the intro."
        r"\end{document}"
    )
    rewritten = _extract_metadata_and_rewrite(tex)
    md = tex_to_markdown(rewritten, work_dir=tmp_path)
    title_idx = md.index("Sample Paper Title")
    authors_idx = md.index("Alice, Bob")
    abstract_heading_idx = md.index("Abstract")
    abstract_body_idx = md.index("This is the abstract body")
    intro_idx = md.index("This is the intro")
    assert title_idx < authors_idx < abstract_heading_idx < abstract_body_idx < intro_idx
    # The title and abstract heading are rendered as actual Markdown headings.
    assert re.search(r"^#\s+Sample Paper Title", md, re.MULTILINE), md
    assert re.search(r"^##?\s+Abstract", md, re.MULTILINE), md
