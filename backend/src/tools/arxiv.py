"""arXiv API interaction tools.

Fetches a paper's source from arXiv, preferring the HTML version
(``arxiv.org/html/<id>``) when available because it converts to Markdown
deterministically without needing the TeX-to-Markdown LLM stage.

Falls back to the TeX e-print (``arxiv.org/e-print/<id>``), which is then
extracted, with ``\\input`` / ``\\include`` directives expanded inline so
multi-file submissions are processed as a single document.

Papers that exist only as a PDF (no HTML and no TeX source) raise
:class:`PdfOnlyPaperError`.
"""

from __future__ import annotations

import logging
import re
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ARXIV_ID_PATTERN = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv\.org/e-print/)?"
    r"(\d{4}\.\d{4,5}(?:v\d+)?)",
)

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"})

_HTML_URL = "https://arxiv.org/html/{arxiv_id}"
_EPRINT_URL = "https://arxiv.org/e-print/{arxiv_id}"

# Recognise ``\input{path}`` and ``\include{path}`` (no nesting).
_INPUT_DIRECTIVE = re.compile(r"\\(?:input|include)\{([^{}]+)\}")

# Max recursion depth for \input expansion (defensive — sane TeX trees
# rarely nest more than 3 deep).
_MAX_INPUT_DEPTH = 10

# Wrapper TeX that just embeds a PDF (e.g. via the pdfpages package).
# Authors who submit only a PDF sometimes upload a stub TeX file like this
# so arXiv accepts it; for our purposes it is equivalent to a PDF-only
# paper since there is no textual content to translate.
_PDF_WRAPPER_DIRECTIVE = re.compile(r"\\includepdf\b")

# Filename sanitisation for HTML image downloads. Keep alphanumerics plus
# the conventional path characters; everything else becomes underscore.
_BASENAME_SAFE_CHARS = re.compile(r"[^a-zA-Z0-9._-]")

# Per-image download timeout. arXiv-hosted figures are small (KB to a few MB).
_IMAGE_DOWNLOAD_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaperSource:
    """Fetched paper content plus metadata.

    Attributes:
        kind: ``"html"`` for a paper fetched via arxiv.org/html (preferred,
            converted to Markdown deterministically), ``"tex"`` for the
            e-print source archive (converted via the LLM agent).
        content: Raw HTML or TeX string. For TeX, this is the main document
            with ``\\input`` / ``\\include`` directives already expanded.
        images: Image file paths extracted from the TeX archive. Empty for
            HTML papers (images are referenced as remote URLs).
        work_dir: Temporary directory containing the extracted source.
            The caller is responsible for cleaning it up when done.
        arxiv_id: The canonical paper ID (e.g. ``"2301.00001v2"``).
    """

    kind: Literal["html", "tex"]
    content: str
    work_dir: Path
    arxiv_id: str
    images: list[Path] = field(default_factory=list)


class PdfOnlyPaperError(ValueError):
    """Raised when a paper is available only as PDF (no HTML or TeX source).

    The translation pipeline currently cannot process PDF-only papers.
    """


# ---------------------------------------------------------------------------
# URL / ID parsing
# ---------------------------------------------------------------------------


def extract_arxiv_id(url: str) -> str:
    """Extract the arXiv paper ID from various URL formats.

    Supports ``/abs/``, ``/pdf/``, ``/html/``, and ``/e-print/`` URLs
    as well as bare IDs like ``2401.12345`` or ``2401.12345v2``.

    Args:
        url: An arXiv URL or bare arXiv ID string.

    Returns:
        The arXiv paper ID (e.g. ``"2401.12345v2"``).

    Raises:
        ValueError: If no valid arXiv ID can be found in the input.
    """
    match = _ARXIV_ID_PATTERN.search(url)
    if not match:
        raise ValueError(f"Could not extract arXiv ID from: {url}")
    return match.group(1)


# ---------------------------------------------------------------------------
# HTML path
# ---------------------------------------------------------------------------


def _safe_image_basename(url: str, used: set[str]) -> str:
    """Derive a unique, filesystem-safe filename from an image URL.

    Strategy:
    - Use the URL's path component basename when available, else ``image``.
    - Replace any character outside ``[A-Za-z0-9._-]`` with underscore.
    - If the basename collides with one already taken, append ``_1``, ``_2``,
      ... before the extension.
    """
    name = Path(urlparse(url).path).name or "image"
    safe = _BASENAME_SAFE_CHARS.sub("_", name) or "image"

    if safe not in used:
        return safe

    stem, dot, ext = safe.partition(".")
    for n in range(1, 10_000):
        candidate = f"{stem}_{n}{dot}{ext}"
        if candidate not in used:
            return candidate
    # Practical impossibility — bail out by overwriting.
    return safe


def _download_html_images(
    html: str, base_url: str, target_dir: Path
) -> tuple[str, list[Path]]:
    """Fetch every ``<img>`` referenced by *html* and rewrite ``src`` to a local path.

    For each ``<img src="...">`` whose URL we can resolve, we GET the image
    and write it to ``target_dir / <safe-basename>``. The ``src`` attribute
    in the HTML is rewritten to ``images/<safe-basename>`` so the ZIP that
    we ultimately ship has self-contained references (``packaging.create_zip_package``
    places listed images at ``images/<file>`` already).

    If a download fails (network error, 404, etc.), the ``<img>`` keeps its
    *absolute* URL — readers with internet will still see something rather
    than a broken link.

    ``data:`` URIs and empty ``src`` are left alone.

    Args:
        html: Raw HTML of the paper page.
        base_url: Base URL for resolving relative ``src`` attributes.
        target_dir: Directory to write downloaded images into. Created if
            absent.

    Returns:
        ``(rewritten_html, downloaded_paths)``. Only successfully downloaded
        images appear in *downloaded_paths*.
    """
    soup = BeautifulSoup(html, "html.parser")
    target_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[Path] = []
    used_basenames: set[str] = set()

    for img in soup.find_all("img"):
        src = img.get("src")
        if not isinstance(src, str) or not src or src.startswith("data:"):
            continue

        abs_url = urljoin(base_url, src)
        basename = _safe_image_basename(abs_url, used_basenames)
        local_path = target_dir / basename

        try:
            response = requests.get(abs_url, timeout=_IMAGE_DOWNLOAD_TIMEOUT_SECONDS)
            response.raise_for_status()
            local_path.write_bytes(response.content)
        except (requests.RequestException, OSError) as exc:
            logger.warning("Failed to download image %s: %s — keeping absolute URL", abs_url, exc)
            img["src"] = abs_url  # leave readers with a working external link
            continue

        used_basenames.add(basename)
        downloaded.append(local_path)
        img["src"] = f"images/{basename}"

    logger.info("Downloaded %d HTML images to %s", len(downloaded), target_dir)
    return str(soup), downloaded


def try_fetch_html(arxiv_id: str) -> tuple[str, str] | None:
    """Attempt to fetch the HTML version of a paper.

    Args:
        arxiv_id: A valid arXiv paper ID.

    Returns:
        A tuple ``(html_body, final_url)`` if HTML is available, otherwise
        ``None``. The *final_url* is the page URL after redirects (arXiv
        typically redirects ``/html/<id>`` to ``/html/<id>v<n>``), which
        is what we need as the base URL for resolving relative ``<img>``
        sources.

        ``None`` is returned for 404 or non-HTML responses; other HTTP
        failures propagate as :class:`requests.HTTPError`.
    """
    url = _HTML_URL.format(arxiv_id=arxiv_id)
    try:
        response = requests.get(url, timeout=60)
    except requests.RequestException as exc:
        logger.warning("HTML fetch failed for %s: %s", arxiv_id, exc)
        return None

    if response.status_code == 404:
        logger.info("No HTML version available for %s", arxiv_id)
        return None
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "text/html" not in content_type.lower():
        logger.info("Unexpected Content-Type for HTML endpoint: %s", content_type)
        return None

    logger.info("Fetched HTML version of %s (%d bytes) from %s", arxiv_id, len(response.content), response.url)
    return response.text, response.url


# ---------------------------------------------------------------------------
# TeX e-print path
# ---------------------------------------------------------------------------


def download_arxiv_source(arxiv_id: str, output_dir: Path) -> Path:
    """Download the arXiv e-print source archive for a given paper.

    Args:
        arxiv_id: A valid arXiv paper ID.
        output_dir: Directory where the downloaded file will be saved.

    Returns:
        Path to the downloaded file (tar.gz or .tex).

    Raises:
        PdfOnlyPaperError: If the e-print endpoint returns a PDF (meaning
            the author did not submit TeX source).
        requests.HTTPError: If the download request fails for other reasons.
    """
    url = _EPRINT_URL.format(arxiv_id=arxiv_id)
    logger.info("Downloading arXiv source from %s", url)

    response = requests.get(url, timeout=120)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()

    if "application/pdf" in content_type:
        raise PdfOnlyPaperError(
            f"Paper {arxiv_id} is available only as PDF — no HTML or TeX source. "
            "PDF-only papers are not currently supported."
        )

    # arXiv may return a raw .tex file for single-file submissions
    if "text/plain" in content_type or "text/x-tex" in content_type:
        dest = output_dir / f"{arxiv_id}.tex"
    else:
        dest = output_dir / f"{arxiv_id}.tar.gz"

    dest.write_bytes(response.content)
    logger.info("Saved source to %s (%d bytes)", dest, len(response.content))
    return dest


def _find_main_tex_file(tex_files: list[Path]) -> Path:
    """Pick the most-likely main TeX file from a list of candidates.

    Selection priority (best to worst):

    1. Largest file that contains BOTH ``\\documentclass`` and
       ``\\begin{document}`` — the canonical signature of a main file.
    2. Largest file containing ``\\documentclass`` only.
    3. Largest file overall, as a last-resort fallback.

    Raises:
        FileNotFoundError: If ``tex_files`` is empty.
    """
    if not tex_files:
        raise FileNotFoundError("No .tex files to choose from")

    def _read(p: Path) -> str:
        return p.read_text(encoding="utf-8", errors="replace")

    with_both: list[tuple[Path, int]] = []
    with_class: list[tuple[Path, int]] = []

    for tex in tex_files:
        content = _read(tex)
        size = len(content)
        if r"\documentclass" in content and r"\begin{document}" in content:
            with_both.append((tex, size))
        elif r"\documentclass" in content:
            with_class.append((tex, size))

    for candidates, label in ((with_both, "documentclass+begin"), (with_class, "documentclass")):
        if candidates:
            main = max(candidates, key=lambda t: t[1])[0]
            logger.info("Main TeX file selected (%s): %s", label, main.name)
            return main

    # Last-resort fallback
    main = max(tex_files, key=lambda p: p.stat().st_size)
    logger.warning("No \\documentclass found; falling back to largest file: %s", main.name)
    return main


def _expand_inputs(tex: str, base_dir: Path, depth: int = 0, seen: set[Path] | None = None) -> str:
    """Inline ``\\input{...}`` and ``\\include{...}`` directives.

    Looks for each referenced file relative to ``base_dir``, trying both
    the literal path and the path with a ``.tex`` extension appended.
    Cycles are broken by tracking already-included files. Unresolved
    directives are left as-is so the LLM can see something is missing.

    Args:
        tex: The TeX content to scan.
        base_dir: Directory to resolve relative paths against.
        depth: Current recursion depth (used internally).
        seen: Set of already-included resolved paths (used internally).

    Returns:
        ``tex`` with all resolvable directives inlined.
    """
    if seen is None:
        seen = set()
    if depth > _MAX_INPUT_DEPTH:
        logger.warning("Reached max \\input expansion depth (%d); halting", _MAX_INPUT_DEPTH)
        return tex

    def _resolve(arg: str) -> Path | None:
        arg = arg.strip()
        for candidate in (base_dir / arg, base_dir / f"{arg}.tex"):
            if candidate.is_file():
                return candidate.resolve()
        return None

    def _replace(match: re.Match[str]) -> str:
        path = _resolve(match.group(1))
        if path is None:
            return match.group(0)
        if path in seen:
            return ""  # cycle — drop the directive
        seen.add(path)
        inner = path.read_text(encoding="utf-8", errors="replace")
        return _expand_inputs(inner, path.parent, depth + 1, seen)

    return _INPUT_DIRECTIVE.sub(_replace, tex)


def extract_source(tar_path: Path, output_dir: Path) -> tuple[str, list[Path]]:
    """Extract a TeX source archive and locate the main document.

    If *tar_path* is a plain ``.tex`` file (single-file submission), it is
    read directly.  Otherwise it is treated as a tar/gzip archive.

    The main TeX file is identified by :func:`_find_main_tex_file`, and any
    ``\\input`` / ``\\include`` directives within it are expanded inline so
    multi-file papers behave as a single document.

    Args:
        tar_path: Path to the downloaded source file.
        output_dir: Directory to extract the archive into.

    Returns:
        A tuple of ``(tex_content, image_paths)`` where *tex_content* is the
        full text of the main TeX document (with includes expanded) and
        *image_paths* is a list of paths to image files found in the archive.

    Raises:
        FileNotFoundError: If no TeX file can be located.
    """
    if tar_path.suffix == ".tex":
        return tar_path.read_text(encoding="utf-8", errors="replace"), []

    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=output_dir, filter="data")

    image_paths: list[Path] = [
        p for p in output_dir.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    ]

    tex_files = list(output_dir.rglob("*.tex"))
    if not tex_files:
        raise FileNotFoundError(f"No .tex files found in archive: {tar_path}")

    main_tex = _find_main_tex_file(tex_files)
    raw_content = main_tex.read_text(encoding="utf-8", errors="replace")
    expanded = _expand_inputs(raw_content, main_tex.parent)

    # Some submissions are TeX wrappers that just embed a PDF. There is no
    # actual text to translate, so surface this as a PDF-only paper.
    if _PDF_WRAPPER_DIRECTIVE.search(expanded):
        raise PdfOnlyPaperError(
            f"TeX source for {main_tex.name} is only a PDF wrapper (uses \\includepdf). "
            "Authors uploaded a PDF rather than real TeX source; PDF-only papers are not supported."
        )

    logger.info(
        "Extracted main TeX: %s (raw=%d chars, expanded=%d chars, images=%d)",
        main_tex.name,
        len(raw_content),
        len(expanded),
        len(image_paths),
    )
    return expanded, image_paths


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def fetch_arxiv_paper(url: str) -> PaperSource:
    """Fetch an arXiv paper, preferring HTML over TeX source.

    Tries the HTML version first (``arxiv.org/html/<id>``); if unavailable,
    falls back to the e-print TeX archive. Raises :class:`PdfOnlyPaperError`
    for PDF-only papers.

    Args:
        url: An arXiv URL or bare arXiv paper ID.

    Returns:
        A :class:`PaperSource` describing the fetched content.

    Raises:
        ValueError: If ``url`` is not a recognisable arXiv reference.
        PdfOnlyPaperError: If only PDF is available.
        requests.HTTPError: On other HTTP failures.
        FileNotFoundError: If the TeX archive contains no .tex files.
    """
    arxiv_id = extract_arxiv_id(url)
    work_dir = Path(tempfile.mkdtemp(prefix=f"arxiv_{arxiv_id}_"))
    logger.info("Working directory: %s", work_dir)

    # 1. Try the HTML version first.
    html_result = try_fetch_html(arxiv_id)
    if html_result is not None:
        html, final_url = html_result
        # final_url is the post-redirect page URL (e.g. .../1706.03762v7);
        # urljoin treats its last path component as a "filename" and uses
        # the parent for relative resolution, which is exactly the browser
        # rule we need for ``<img src="1706.03762v7/Figures/X.png">``.
        images_dir = work_dir / "html_images"
        rewritten_html, image_paths = _download_html_images(html, final_url, images_dir)
        return PaperSource(
            kind="html",
            content=rewritten_html,
            work_dir=work_dir,
            arxiv_id=arxiv_id,
            images=image_paths,
        )

    # 2. Fall back to the TeX e-print source.
    source_path = download_arxiv_source(arxiv_id, work_dir)
    extract_dir = work_dir / "source"
    extract_dir.mkdir(exist_ok=True)

    tex_content, image_paths = extract_source(source_path, extract_dir)
    return PaperSource(
        kind="tex",
        content=tex_content,
        work_dir=work_dir,
        arxiv_id=arxiv_id,
        images=image_paths,
    )
