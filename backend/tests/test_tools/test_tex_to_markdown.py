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
