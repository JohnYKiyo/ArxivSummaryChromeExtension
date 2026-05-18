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
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


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

    logger.info("pandoc converted %d chars TeX → %d chars Markdown", len(tex_content), len(markdown))
    return markdown
