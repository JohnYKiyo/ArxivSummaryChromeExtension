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
# and followed by an identifier-shaped citation key. Applied globally so
# in-text citations (``as @Mnih:2015 showed``) and bracketed forms
# (``[@Mnih:2015]``) are both cleaned. The lookbehind excludes
# email-like ``user@example.com`` and any ``@`` preceded by identifier
# characters, which is the conservative thing for academic prose.
_CITATION_AT_SIGN = re.compile(r"(?<![A-Za-z0-9_])@(?=[A-Za-z][\w:.\-]*)")

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

# Pandoc raw HTML ``<div>`` wrappers emitted for ``figure*`` / ``figure`` /
# ``center`` environments and for any environment carrying a ``\label{...}``
# (which pandoc turns into ``<div id="label">``). All three are
# pandoc-specific structural hints with no visible effect in standard
# viewers — bare opener / closer tags appear as preformatted text.
# We accept ``id`` and/or the figure-flavoured ``class`` attribute in
# either order; ``<div class="theorem">`` and other named classes are
# left alone since they may convey real document structure.
_DIV_WRAPPER_OPEN = re.compile(
    r"^<div"
    r'(?:\s+(?:id="[^"]*"|class="(?:figure\*?|center)"))+'
    r"\s*>\s*$"
)
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
# Malformed citation arguments authors sometimes commit:
#   \cite{,Key}      — leading comma
#   \cite{Key,}      — trailing comma
#   \cite{A,,B}      — duplicate comma
#   \cite{ , Key }   — whitespace-padded
# LaTeX itself silently accepts these. Pandoc's strict parser aborts with
# ``unexpected ,`` (exit 64) on the leading-comma form. Fix in the input
# layer so pandoc gets a clean citation list. Matches the whole ``cite``
# family (``\cite``, ``\citep``, ``\citet``, ``\citeyear`` etc.).
_CITE_FAMILY = re.compile(r"\\(cite[a-zA-Z]*)(\[[^\]]*\])*\s*\{([^{}]*)\}")

# TeX low-level spacing commands. Custom-typeset papers (e.g. ones with
# redefined ``\preauthor`` / ``\maketitlehookX`` / hand-rolled keyword
# blocks) sprinkle these throughout — and after we strip the abstract
# environment they sometimes end up as bare ``\vskip 3em`` lines in the
# document body, which pandoc's LaTeX reader rejects with
# ``unexpected \vskip`` (exit 64). They are purely typographic, so
# dropping them does not change semantic content.
_TEX_SKIP_LENGTH = re.compile(
    r"\\(?:vskip|hskip|kern|lineskip)"
    r"(?:\s+-?\d+(?:\.\d+)?\s*[A-Za-z]+)?"
)
_TEX_SPACE_BRACED = re.compile(r"\\(?:vspace|hspace)\*?\s*\{[^{}]*\}")
_TEX_SKIP_BARE = re.compile(r"\\(?:smallskip|medskip|bigskip|noindent|hfill|hfil|vfill|vfil)\b")

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

    tex_content = _normalise_cite_args(tex_content)
    tex_content = _strip_tex_spacing_commands(tex_content)
    tex_content = _unwrap_brace_swallowers(tex_content)

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
    markdown = _convert_table_captions(markdown)
    markdown = _rewrite_image_paths(markdown)
    markdown = strip_math_labels(markdown)
    markdown = isolate_display_math(markdown)

    logger.info("pandoc converted %d chars TeX → %d chars Markdown", len(tex_content), len(markdown))
    return markdown


# Quarto-origin TeX habitually decorates each figure with two pandoc-hostile
# patterns that silently swallow the wrapped ``\includegraphics``:
#
#   1. ``\pandocbounded{\includegraphics{...}}`` — pandoc-LaTeX's image
#      sizing helper. The preamble defines it with TeX internals (``\sbox``,
#      ``\Gscale@div``, ``\dimexpr``) that pandoc's LaTeX *reader* cannot
#      evaluate, so the expansion yields nothing and the inner image is lost.
#   2. ``\centering{...}`` inside ``\begin{figure}`` — Quarto's LaTeX writer
#      uses brace-grouped form, but ``\centering`` is a *declaration*, not a
#      command. Standard LaTeX would tolerate ``\centering`` followed by a
#      ``{...}`` group, but pandoc treats ``\centering{...}`` as a command
#      that consumes its braced argument and emits nothing.
#
# Both patterns share the same shape — command name immediately followed by
# ``{`` — and the same fix: unwrap to the inner content, brace-balanced so
# nested arguments like ``\includegraphics[opts]{path}`` survive.
_BRACE_SWALLOWERS: tuple[str, ...] = (
    r"\pandocbounded",
    r"\centering",
    r"\raggedright",
    r"\raggedleft",
)


def _unwrap_brace_swallowers(tex: str) -> str:
    r"""Unwrap brace-grouped declarations that pandoc would otherwise eat.

    Targets ``\pandocbounded{X}``, ``\centering{X}``, ``\raggedright{X}`` and
    ``\raggedleft{X}`` — all common in Quarto-generated arXiv sources
    (e.g. 2508.15817). In every case the same failure mode applies: pandoc's
    LaTeX reader treats the command as consuming its braced argument and
    silently drops ``X``, so the wrapped ``\includegraphics`` vanishes and
    the rendered Markdown has ``<figure>`` + ``<figcaption>`` but no
    ``<img>``. After unwrapping, the bare ``\centering``/``\raggedright``
    declarations are no longer present — that's fine, pandoc ignores them
    in figure environments anyway, since alignment isn't expressible in
    Markdown.

    Brace-balanced: ``\pandocbounded{\includegraphics[w=1\linewidth]{path}}``
    unwraps without breaking on the inner ``{path}`` brace. Backslash
    escapes (``\{``, ``\}``, plus any ``\X``) are skipped so they don't
    mis-count depth.

    Bare declarations like ``\centering\n\includegraphics{...}`` (no
    following ``{``) are left alone — they are already valid LaTeX and
    pandoc handles them correctly.
    """
    if not any(name + "{" in tex for name in _BRACE_SWALLOWERS):
        return tex
    out: list[str] = []
    i = 0
    n = len(tex)
    while i < n:
        next_idx = -1
        next_name = ""
        for name in _BRACE_SWALLOWERS:
            needle = name + "{"
            hit = tex.find(needle, i)
            if hit >= 0 and (next_idx < 0 or hit < next_idx):
                next_idx = hit
                next_name = needle
        if next_idx < 0:
            out.append(tex[i:])
            break
        out.append(tex[i:next_idx])
        j = next_idx + len(next_name)
        depth = 1
        inner_start = j
        while j < n and depth > 0:
            ch = tex[j]
            if ch == "\\" and j + 1 < n:
                j += 2  # skip ``\{`` / ``\}`` and other backslash escapes
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if depth == 0:
            # The captured inner content may itself contain another swallower
            # — e.g. ``\centering{\pandocbounded{\includegraphics{...}}}`` in
            # Quarto-origin sources. Recurse so every layer is unwrapped, not
            # just the outermost.
            out.append(_unwrap_brace_swallowers(tex[inner_start:j]))
            i = j + 1
        else:
            # Unbalanced braces — leave the rest untouched rather than corrupt
            # the document. Pandoc will surface the real syntax error.
            out.append(tex[next_idx:])
            break
    return "".join(out)


def _strip_tex_spacing_commands(tex: str) -> str:
    """Strip low-level TeX spacing commands that pandoc rejects in body context.

    Custom-typeset arXiv papers commonly use ``\\vskip 3em``, ``\\hskip``,
    ``\\vspace{2em}`` etc. for visual layout. After we remove the
    ``\\begin{abstract}...\\end{abstract}`` environment as part of
    metadata rewriting, any ``\\vskip`` lines that surrounded the
    abstract end up exposed at the top level of the document body.
    pandoc's LaTeX reader then errors out (exit 64,
    ``unexpected \\vskip``). These commands are purely typographic, so
    removing them is safe.

    Covers:
      - ``\\vskip <length>`` / ``\\hskip`` / ``\\kern`` / ``\\lineskip``
        (unbraced length argument like ``3em``)
      - ``\\vspace{<length>}`` / ``\\hspace{<length>}`` (braced argument,
        with optional ``*`` modifier)
      - ``\\smallskip`` / ``\\medskip`` / ``\\bigskip``,
        ``\\noindent``, ``\\hfill`` / ``\\vfill`` (no argument)
    """
    tex = _TEX_SKIP_LENGTH.sub("", tex)
    tex = _TEX_SPACE_BRACED.sub("", tex)
    tex = _TEX_SKIP_BARE.sub("", tex)
    return tex


def _normalise_cite_args(tex: str) -> str:
    """Strip stray commas from ``\\cite{...}`` arguments.

    LaTeX tolerates malformed citation lists such as ``\\cite{,Key}`` or
    ``\\cite{A,,B}``; pandoc's strict parser aborts (exit 64,
    ``unexpected ,``) and the whole pipeline fails. Common author typos
    that we normalise:

    * leading ``,``  — ``\\cite{,Key}``     → ``\\cite{Key}``
    * trailing ``,`` — ``\\cite{Key,}``     → ``\\cite{Key}``
    * doubled ``,``  — ``\\cite{A,,B}``     → ``\\cite{A,B}``
    * whitespace      — ``\\cite{ A , B }`` → ``\\cite{A,B}``

    Handles the whole ``cite`` family (``\\citep``, ``\\citet``,
    ``\\citeyear``, etc.) including an optional ``[prenote]``/``[postnote]``
    argument. Other commands and prose are untouched.
    """

    def _clean(match: re.Match[str]) -> str:
        cmd = match.group(1)
        optional_args = match.group(2) or ""
        keys = [k.strip() for k in match.group(3).split(",") if k.strip()]
        return f"\\{cmd}{optional_args}{{{','.join(keys)}}}"

    return _CITE_FAMILY.sub(_clean, tex)


def _convert_table_captions(markdown: str) -> str:
    """Turn pandoc pipe-table captions (``: caption``) into ``**Table N:** ...``.

    Pandoc places a pipe-table caption on a line of its own, starting with
    ``: ``, immediately (with one blank line gap) after the table. Standard
    Markdown viewers do not recognise this syntax — Obsidian / GitHub /
    VS Code preview all show the literal ``: caption``. Convert each
    such caption into a numbered, bold ``**Table N:** caption`` label so
    it reads like the original paper's table caption.

    Detection is intentionally conservative: a line is only treated as a
    caption when the previous non-blank line is a pipe-table row (starts
    AND ends with ``|``). Definition-list ``: definition`` constructs
    that don't follow a table are left alone.
    """
    lines = markdown.split("\n")
    out: list[str] = []
    last_nonblank_was_pipe_row = False
    table_count = 0
    for line in lines:
        stripped = line.strip()
        if last_nonblank_was_pipe_row and stripped.startswith(": "):
            table_count += 1
            indent = line[: len(line) - len(line.lstrip())]
            out.append(f"{indent}**Table {table_count}:** {stripped[2:]}")
            last_nonblank_was_pipe_row = False
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            last_nonblank_was_pipe_row = True
        elif stripped:
            last_nonblank_was_pipe_row = False
        out.append(line)
    return "\n".join(out)


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
    """Convert pandoc citation tokens ``@key`` / ``[@key]`` into plain labels.

    Pandoc's ``citations`` extension turns ``\\cite{Lin:1992}`` into the
    Markdown token ``[@Lin:1992]`` and bare ``@Lin:1992`` for in-text
    forms (``as @Lin:1992 showed``). Standard Markdown viewers do not
    recognise either and render them verbatim. Disabling the extension
    is not an alternative — pandoc then drops the citation entirely,
    leaving dangling commas.

    Stripping the ``@`` keeps the citation key visible as a label and
    works in every renderer. The lookbehind in ``_CITATION_AT_SIGN``
    protects email addresses (``her@example.com``) and any other ``@``
    that is glued to a preceding identifier character.
    """
    return _CITATION_AT_SIGN.sub("", markdown)
