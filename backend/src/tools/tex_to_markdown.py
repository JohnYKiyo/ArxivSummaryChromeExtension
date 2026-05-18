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
_PANDOC_TO = "markdown+tex_math_dollars+pipe_tables-raw_tex-header_attributes-link_attributes-fenced_divs"
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
    markdown = strip_math_labels(markdown)
    markdown = isolate_display_math(markdown)

    logger.info("pandoc converted %d chars TeX → %d chars Markdown", len(tex_content), len(markdown))
    return markdown


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
