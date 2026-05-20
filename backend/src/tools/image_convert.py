"""Convert non-renderable academic figure formats (PDF) to PNG.

ArXiv submissions almost always ship figures as PDFs — that's what
LaTeX's ``\\includegraphics`` consumes natively and what TikZ /
matplotlib emit by default. Standard Markdown viewers (Obsidian,
GitHub, VS Code preview) render only raster (PNG/JPG/WebP) and SVG
inline; ``![]/<img>`` referencing a PDF produces a broken image or
silent skip depending on the viewer.

This module rasterises each PDF figure to a 150-DPI PNG of the same
basename and removes the source PDF from disk. Pandoc, which later
resolves ``\\includegraphics{name}`` against the work directory, then
picks up the PNG automatically. Other formats (PNG/JPG/SVG) are
passed through unchanged.

EPS support would need ``ghostscript`` and an extra apt package; we
skip it because modern arXiv submissions practically never use EPS.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


_PDFTOPPM_DPI = 150
_CONVERTIBLE_EXTS = frozenset({".pdf"})
_PDFTOPPM_TIMEOUT_SECONDS = 30


def convert_pdf_figures_to_png(image_paths: list[Path]) -> list[Path]:
    """Rasterise PDF figures in *image_paths* to PNG; pass others through.

    For each ``.pdf`` entry, a sibling ``.png`` is produced via
    ``pdftoppm -singlefile`` and the source PDF is deleted so pandoc's
    figure-file lookup against the work directory unambiguously selects
    the PNG. Returns the updated list (PNG paths in place of PDFs).

    If ``pdftoppm`` is not on PATH, a warning is logged and the input
    is returned unchanged — the pipeline still produces output, just
    with broken images downstream.
    """
    if not image_paths:
        return image_paths

    if shutil.which("pdftoppm") is None:
        logger.warning(
            "pdftoppm not installed — PDF figures will not render in "
            "standard Markdown viewers. Install poppler-utils in the "
            "runtime environment to enable PDF→PNG conversion."
        )
        return image_paths

    converted: list[Path] = []
    for src in image_paths:
        if src.suffix.lower() not in _CONVERTIBLE_EXTS:
            converted.append(src)
            continue
        png = _pdf_to_png(src)
        if png is None:
            converted.append(src)
            continue
        converted.append(png)
        try:
            src.unlink()
        except OSError as exc:
            logger.debug("Could not remove source PDF %s: %s", src, exc)
    return converted


def _pdf_to_png(pdf: Path) -> Path | None:
    """Convert a single-page PDF to PNG. Returns new path or ``None`` on failure."""
    out_stem = pdf.with_suffix("")
    try:
        subprocess.run(  # noqa: S603 — args list, no shell
            [
                "pdftoppm",
                "-png",
                "-r",
                str(_PDFTOPPM_DPI),
                "-singlefile",
                str(pdf),
                str(out_stem),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=_PDFTOPPM_TIMEOUT_SECONDS,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("pdftoppm failed for %s: %s", pdf, exc.stderr.strip() if exc.stderr else exc)
        return None
    except subprocess.TimeoutExpired:
        logger.warning("pdftoppm timed out (%ss) for %s", _PDFTOPPM_TIMEOUT_SECONDS, pdf)
        return None

    png = out_stem.with_suffix(".png")
    if not png.exists():
        logger.warning("pdftoppm reported success but %s does not exist", png)
        return None
    return png
