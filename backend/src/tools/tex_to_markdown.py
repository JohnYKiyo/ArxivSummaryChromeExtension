"""TeX → Markdown converter via pandoc.

A deterministic, library-based conversion that replaces the prior
``Tex2MarkdownAgent`` LLM step. We invoke ``pandoc`` as a subprocess with
options tuned for downstream translation:

- ``markdown+tex_math_dollars`` so inline math is ``$...$`` and display
  math is ``$$...$$`` (the same notation we already use for HTML papers).
- ``pipe_tables`` so tables render as standard pipe-delimited Markdown.
- ``--wrap=none`` to avoid pandoc inserting line wraps mid-paragraph, which
  would later confuse the translation agent.

Multi-file TeX is already inlined upstream in ``tools/arxiv.py`` via
``\\input`` / ``\\include`` expansion, so pandoc operates on a single
self-contained document.

This module performs no LLM calls and lives in ``tools/`` (not
``agents/``) per the SoC convention in CLAUDE.md.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from src.tools.markdown_layout import isolate_display_math, strip_math_labels

logger = logging.getLogger(__name__)


# Pandoc citations: ``\cite{Lin:1992}`` becomes ``[@Lin:1992]`` in Markdown
# via the ``citations`` extension. Disabling that extension makes pandoc
# drop the citations entirely (leaving dangling commas), so we keep the
# extension on and strip the ``@`` marker post-pandoc so the rendered
# text is a plain ``[Lin:1992]`` label — readable in every Markdown viewer
# without depending on the pandoc-citation extension.
#
# Match ``@`` that's at a word boundary (preceded by a non-identifier char)
# and followed by an identifier-shaped citation key. Limited to text inside
# ``[...]`` so Twitter-style ``@user`` mentions outside brackets are safe.
_CITATION_AT_SIGN = re.compile(r"(?<![A-Za-z0-9_])@(?=[A-Za-z][\w:.\-]*)")
_BRACKETED_TEXT = re.compile(r"\[([^\[\]\n]+)\]")

# Image-reference rewriting. The TeX path ships every figure under
# ``images/`` in the result ZIP (packaging.py:64), but pandoc emits bare
# filenames as ``\includegraphics{name}`` → ``![](name)``. We rewrite to
# ``![](images/name.ext)`` so the markdown actually points to the file
# the user has on disk after unzipping. PDF/EPS extensions are rewritten
# to ``.png`` because (a) we convert those at extraction time via
# ``image_convert.convert_pdf_figures_to_png`` and (b) Obsidian / GitHub /
# VS Code preview cannot render PDF or EPS inline.
_RENDERABLE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"})
_CONVERTED_TO_PNG_EXTS = frozenset({".pdf", ".eps"})
# Markdown image with optional trailing attribute block; the block is dropped.
_MD_IMAGE = re.compile(r"(!\[[^\]]*\])\(([^)\s]+)\)(?:\{[^{}]*\})?")
# Raw HTML ``<img src="...">`` or ``<embed src="...pdf">`` (pandoc emits
# ``<embed>`` for PDFs since the spec says PDFs can't go in ``<img>``).
# We canonicalise both to ``<img>`` because we've already converted PDFs
# to PNGs upstream — ``<img src="...png">`` works everywhere.
_HTML_IMG_OR_EMBED = re.compile(
    r"<(?:img|embed)\b([^>]*?)src=[\"']([^\"']+)[\"']([^>]*?)\s*/?>",
    re.IGNORECASE,
)

# Pandoc ``<div class="figure*">`` / ``<div class="figure">`` / ``<div class="center">``
# wrappers around figure environments. Each opener is paired with a ``</div>``
# at the same nesting level. Standard Markdown viewers ignore the class
# attribute (so the wrapper does nothing visually but adds clutter), and
# stripping it lets the inner ``<img>`` or markdown render directly.
_DIV_WRAPPER_OPEN = re.compile(r'^<div class="(?:figure\*?|center)">\s*$')
_DIV_CLOSE = re.compile(r"^</div>\s*$")

# Pandoc ``<a href="#anchor" data-reference-type="ref" data-reference="...">text</a>``
# emitted for ``\ref{...}`` / ``\eqref{...}`` cross-references. The
# ``data-*`` attributes are pandoc-specific; standard Markdown viewers
# show the whole ``<a>`` raw. Internal anchors aren't resolved either
# (we don't emit matching ``id=""`` on headings). Drop the wrapper —
# keep the inner text.
_PANDOC_CROSSREF = re.compile(
    r"<a\s+[^>]*?\bdata-reference-type=[\"'][^\"']*[\"'][^>]*>([^<]*)</a>",
    re.IGNORECASE,
)


class PandocNotInstalledError(RuntimeError):
    """Raised when the ``pandoc`` binary is not available on PATH."""


class PandocConversionError(RuntimeError):
    """Raised when pandoc exits non-zero converting a TeX document."""


# Pandoc options. Kept module-level so the choice is easy to audit and the
# command is identical between production runs and tests.
#
# Disabled extensions explained:
#   - raw_tex          : we don't want raw TeX leaking into the Markdown
#   - header_attributes: pandoc emits ``# Heading {#anchor .unnumbered}``
#                        which renders as literal text in GitHub, Obsidian,
#                        VS Code preview, etc.
#   - link_attributes  : pandoc emits ``[text](url){reference-type="eqref"
#                        reference="X"}`` for cross-refs and ``{width="3in"}``
#                        for images — same problem, shows as noise.
#   - fenced_divs      : pandoc wraps figure*/table* environments in
#                        ``::: figure* ... :::`` blocks which standard MD
#                        renders verbatim. The inner content survives.
_PANDOC_FROM = "latex"
# Disabled table extensions: pandoc otherwise picks ``simple_tables`` for
# header-less tables or ``multiline_tables`` for wide cells — neither is
# recognised by Obsidian / GitHub / VS Code preview, which all render
# them as preformatted text. Forcing only ``pipe_tables`` makes pandoc
# fall back to raw HTML ``<table>`` for tables that don't fit pipe
# format, and HTML tables do render in every viewer.
_PANDOC_TO = (
    "markdown"
    "+tex_math_dollars"
    "+pipe_tables"
    "-raw_tex"
    "-header_attributes"
    "-link_attributes"
    "-fenced_divs"
    "-simple_tables"
    "-multiline_tables"
    "-grid_tables"
)
_PANDOC_FLAGS: tuple[str, ...] = (
    "--wrap=none",
    "--from=" + _PANDOC_FROM,
    "--to=" + _PANDOC_TO,
)

# Hard cap on pandoc runtime. ArXiv papers should convert in seconds; if
# we hit this, something is wrong (input is malformed or recursion is bad).
_PANDOC_TIMEOUT_SECONDS = 120


def tex_to_markdown(tex_content: str, work_dir: Path | None = None) -> str:
    """Convert a TeX document string to Markdown using pandoc.

    The TeX is written to a temporary file (so pandoc's relative path
    resolution for figures and ``\\input`` directives works the same as
    when reading a file from disk), pandoc is invoked, and stdout is
    returned as the Markdown.

    Args:
        tex_content: Full TeX source (single document; ``\\input`` /
            ``\\include`` should already be expanded upstream).
        work_dir: Working directory for pandoc. Relative image paths in
            the TeX (e.g. ``\\includegraphics{figures/fig1}``) are
            resolved against this directory. If ``None``, a temporary
            directory is used.

    Returns:
        Markdown string. Math is preserved as ``$...$`` / ``$$...$$``;
        tables become pipe tables; figures become standard image links.

    Raises:
        PandocNotInstalledError: If the ``pandoc`` binary is not on PATH.
        PandocConversionError: If pandoc exits non-zero.
    """
    if shutil.which("pandoc") is None:
        raise PandocNotInstalledError(
            "pandoc binary not found on PATH. Install pandoc in the runtime "
            "environment (apt-get install pandoc in the backend Dockerfile)."
        )

    if work_dir is None:
        work_dir = Path(tempfile.mkdtemp(prefix="pandoc_"))

    tex_path = work_dir / "_pandoc_input.tex"
    tex_path.write_text(tex_content, encoding="utf-8")

    try:
        result = subprocess.run(  # noqa: S603 — args list, no shell
            ["pandoc", *_PANDOC_FLAGS, str(tex_path)],
            capture_output=True,
            text=True,
            cwd=work_dir,
            timeout=_PANDOC_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PandocConversionError(f"pandoc timed out after {_PANDOC_TIMEOUT_SECONDS}s") from exc

    if result.returncode != 0:
        # Pandoc puts useful errors on stderr; include them so the user
        # has something actionable in the logs.
        raise PandocConversionError(f"pandoc exited with code {result.returncode}: {result.stderr.strip()}")

    markdown = result.stdout
    if result.stderr:
        # Pandoc warns to stderr for unknown commands etc. — log at debug.
        logger.debug("pandoc stderr: %s", result.stderr.strip())

    markdown = _strip_citation_at_signs(markdown)
    markdown = _strip_pandoc_div_wrappers(markdown)
    markdown = _strip_pandoc_crossrefs(markdown)
    markdown = _rewrite_image_paths(markdown)
    markdown = strip_math_labels(markdown)
    markdown = isolate_display_math(markdown)

    logger.info("pandoc converted %d chars TeX → %d chars Markdown", len(tex_content), len(markdown))
    return markdown


def _strip_pandoc_div_wrappers(markdown: str) -> str:
    """Remove ``<div class="figure*|center">`` wrappers, keeping inner content.

    Pandoc emits a raw HTML ``<div>`` wrapping each figure environment when
    ``fenced_divs`` is disabled. The class attribute carries information
    pandoc itself uses for re-import but is invisible to standard Markdown
    viewers, which still render the bare opener/closer tags as preformatted
    text on their own lines.

    We strip the openers we recognise and pop a matching ``</div>`` for
    each one. Other ``<div>`` blocks (if the source somehow has them) are
    left alone.
    """
    out: list[str] = []
    pending_closes = 0
    for line in markdown.split("\n"):
        if _DIV_WRAPPER_OPEN.match(line.strip()):
            pending_closes += 1
            continue
        if pending_closes > 0 and _DIV_CLOSE.match(line.strip()):
            pending_closes -= 1
            continue
        out.append(line)
    return "\n".join(out)


def _strip_pandoc_crossrefs(markdown: str) -> str:
    """Replace ``<a data-reference-type="..." ...>text</a>`` with ``text``.

    pandoc emits raw HTML anchors for ``\\ref{...}`` / ``\\eqref{...}``
    with ``data-reference-type`` / ``data-reference`` attributes that
    only its own re-import understands. Standard viewers print the full
    ``<a ...>`` tag. The anchor target wouldn't resolve in Markdown
    anyway (we don't emit ``id=""`` on headings), so the link adds no
    value — keep the inner text.
    """
    return _PANDOC_CROSSREF.sub(lambda m: m.group(1), markdown)


def _rewrite_image_paths(markdown: str) -> str:
    """Point every image reference at ``images/<basename>.<renderable-ext>``.

    Pandoc emits figure references as bare filenames (``![](name)``) or
    raw HTML (``<img src="name">`` inside ``<figure>`` blocks). Neither
    form lines up with our ZIP layout (figures shipped under ``images/``)
    nor with what Obsidian / GitHub renders (PDF/EPS not supported inline).

    This rewrites both shapes to ``images/<basename>.<ext>``, mapping
    ``.pdf`` / ``.eps`` to ``.png`` (the conversion already done at
    extraction time by ``image_convert.convert_pdf_figures_to_png``) and
    stripping pandoc's trailing ``{width="3in"}`` attribute blocks, which
    standard Markdown viewers print as visible noise. External URLs
    (``http(s)://``, ``data:``) and already-correct ``images/`` paths
    pass through unchanged.
    """

    def _md_replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}({_to_renderable_image_path(match.group(2))})"

    def _html_replace(match: re.Match[str]) -> str:
        attrs_before = match.group(1)
        attrs_after = match.group(3)
        new_src = _to_renderable_image_path(match.group(2))
        return f'<img{attrs_before}src="{new_src}"{attrs_after} />'

    markdown = _MD_IMAGE.sub(_md_replace, markdown)
    markdown = _HTML_IMG_OR_EMBED.sub(_html_replace, markdown)
    return markdown


def _to_renderable_image_path(src: str) -> str:
    """Map an image source token to ``images/<basename>.<renderable-ext>``."""
    if src.startswith(("http://", "https://", "data:")):
        return src
    name = src.split("/")[-1]
    if "." in name:
        stem, _, ext = name.rpartition(".")
        ext_lower = "." + ext.lower()
        if ext_lower in _CONVERTED_TO_PNG_EXTS:
            return f"images/{stem}.png"
        if ext_lower in _RENDERABLE_EXTS:
            return f"images/{stem}.{ext}"
        # Unknown extension: leave as-is under images/ — better than guessing.
        return f"images/{name}"
    # No extension. Pandoc passes through ``\includegraphics{name}`` as-is.
    # Assume our PDF→PNG conversion produced ``name.png``.
    return f"images/{name}.png"


def _strip_citation_at_signs(markdown: str) -> str:
    """Convert pandoc citation tokens ``[@key]`` into plain labels ``[key]``.

    Pandoc's ``citations`` extension turns ``\\cite{Lin:1992}`` into the
    Markdown token ``[@Lin:1992]``. Standard Markdown viewers (Obsidian,
    GitHub, VS Code preview) do not recognise this and render it verbatim,
    which is just visual noise. Disabling the ``citations`` extension is
    not an alternative — pandoc then drops the citation entirely, leaving
    sentences with dangling commas and spaces.

    Stripping the ``@`` keeps the citation key visible as a label and
    works in every renderer. Untouched if the bracketed span contains no
    ``@`` (so Markdown links ``[text](url)``, footnote refs ``[^1]`` and
    pandoc cross-refs ``[\\[eq\\]]`` pass through unchanged).
    """

    def _clean(match: re.Match[str]) -> str:
        body = match.group(1)
        if "@" not in body:
            return match.group(0)
        return f"[{_CITATION_AT_SIGN.sub('', body)}]"

    return _BRACKETED_TEXT.sub(_clean, markdown)
