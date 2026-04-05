"""arXiv API interaction tools.

Provides utilities for interacting with arXiv:
- URL parsing and validation (abs, pdf, html, source URLs)
- Source archive (tar.gz) downloading
- TeX file extraction from archives
"""

import logging
import re
import tarfile
import tempfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_ARXIV_ID_PATTERN = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf|html)/|arxiv\.org/e-print/)?"
    r"(\d{4}\.\d{4,5}(?:v\d+)?)",
)

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".pdf", ".eps", ".svg"})

_EPRINT_URL = "https://arxiv.org/e-print/{arxiv_id}"


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


def download_arxiv_source(arxiv_id: str, output_dir: Path) -> Path:
    """Download the arXiv e-print source archive for a given paper.

    Args:
        arxiv_id: A valid arXiv paper ID.
        output_dir: Directory where the downloaded file will be saved.

    Returns:
        Path to the downloaded file (tar.gz or .tex).

    Raises:
        requests.HTTPError: If the download request fails.
    """
    url = _EPRINT_URL.format(arxiv_id=arxiv_id)
    logger.info("Downloading arXiv source from %s", url)

    response = requests.get(url, timeout=120)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")

    # arXiv may return a raw .tex file for single-file submissions
    if "text/plain" in content_type or "text/x-tex" in content_type:
        dest = output_dir / f"{arxiv_id}.tex"
    else:
        dest = output_dir / f"{arxiv_id}.tar.gz"

    dest.write_bytes(response.content)
    logger.info("Saved source to %s (%d bytes)", dest, len(response.content))
    return dest


def extract_source(tar_path: Path, output_dir: Path) -> tuple[str, list[Path]]:
    """Extract a TeX source archive and locate the main document.

    If *tar_path* is a plain ``.tex`` file (single-file submission), it is
    read directly.  Otherwise it is treated as a tar/gzip archive.

    The main TeX file is identified as the one containing
    ``\\documentclass``.

    Args:
        tar_path: Path to the downloaded source file.
        output_dir: Directory to extract the archive into.

    Returns:
        A tuple of ``(tex_content, image_paths)`` where *tex_content* is the
        full text of the main TeX document and *image_paths* is a list of
        paths to image files found in the archive.

    Raises:
        FileNotFoundError: If no TeX file with ``\\documentclass`` is found.
    """
    if tar_path.suffix == ".tex":
        tex_content = tar_path.read_text(encoding="utf-8", errors="replace")
        return tex_content, []

    # Extract tar.gz archive
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(path=output_dir, filter="data")

    # Collect image files
    image_paths: list[Path] = [
        p for p in output_dir.rglob("*") if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    ]

    # Find main .tex file (the one containing \documentclass)
    tex_files = list(output_dir.rglob("*.tex"))
    main_tex: Path | None = None

    for tex_file in tex_files:
        content = tex_file.read_text(encoding="utf-8", errors="replace")
        if r"\documentclass" in content:
            main_tex = tex_file
            break

    if main_tex is None:
        # Fallback: use the largest .tex file if no \documentclass found
        if tex_files:
            main_tex = max(tex_files, key=lambda p: p.stat().st_size)
            logger.warning(
                "No \\documentclass found; falling back to largest .tex file: %s",
                main_tex.name,
            )
        else:
            raise FileNotFoundError(f"No .tex files found in archive: {tar_path}")

    tex_content = main_tex.read_text(encoding="utf-8", errors="replace")
    logger.info(
        "Extracted main TeX file: %s (%d chars, %d images)",
        main_tex.name,
        len(tex_content),
        len(image_paths),
    )
    return tex_content, image_paths


def fetch_arxiv_paper(url: str) -> tuple[str, list[Path], Path]:
    """Fetch and extract an arXiv paper's source.

    This is the high-level convenience function that chains
    :func:`extract_arxiv_id`, :func:`download_arxiv_source`, and
    :func:`extract_source`.

    Args:
        url: An arXiv URL or bare arXiv paper ID.

    Returns:
        A tuple of ``(tex_content, image_paths, work_dir)`` where
        *work_dir* is the temporary directory containing all extracted files.
        The caller is responsible for cleaning up *work_dir* when done.
    """
    arxiv_id = extract_arxiv_id(url)
    work_dir = Path(tempfile.mkdtemp(prefix=f"arxiv_{arxiv_id}_"))
    logger.info("Working directory: %s", work_dir)

    source_path = download_arxiv_source(arxiv_id, work_dir)

    extract_dir = work_dir / "source"
    extract_dir.mkdir(exist_ok=True)

    tex_content, image_paths = extract_source(source_path, extract_dir)
    return tex_content, image_paths, work_dir
