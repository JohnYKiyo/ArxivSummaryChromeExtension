"""Tests for arXiv source fetching and extraction.

Covers the four behaviours the orchestrator depends on:

1. ``extract_arxiv_id`` URL parsing.
2. ``extract_source`` for tar.gz and single-.tex submissions, including
   the multi-file main-TeX selection and ``\\input`` expansion.
3. ``fetch_arxiv_paper`` HTTP integration: HTML-first, TeX fallback,
   PDF-only error.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.arxiv import (
    PaperSource,
    PdfOnlyPaperError,
    extract_arxiv_id,
    extract_source,
    fetch_arxiv_paper,
)

# ---------------------------------------------------------------------------
# extract_arxiv_id
# ---------------------------------------------------------------------------


class TestExtractArxivId:
    """Test extraction of arXiv IDs from various URL formats."""

    @pytest.mark.parametrize(
        ("url", "expected_id"),
        [
            ("https://arxiv.org/abs/2301.00001", "2301.00001"),
            ("https://arxiv.org/abs/2301.00001v2", "2301.00001v2"),
            ("https://arxiv.org/pdf/2401.12345", "2401.12345"),
            ("https://arxiv.org/pdf/2401.12345v1", "2401.12345v1"),
            ("https://arxiv.org/html/2305.54321", "2305.54321"),
            ("https://arxiv.org/html/2305.54321v3", "2305.54321v3"),
            ("https://arxiv.org/e-print/2301.00001", "2301.00001"),
            ("2301.00001", "2301.00001"),
            ("2301.00001v2", "2301.00001v2"),
            ("https://arxiv.org/abs/2401.12345", "2401.12345"),
            ("2401.12345", "2401.12345"),
            ("https://arxiv.org/abs/2301.00001?context=cs", "2301.00001"),
        ],
    )
    def test_valid_urls(self, url: str, expected_id: str) -> None:
        assert extract_arxiv_id(url) == expected_id

    @pytest.mark.parametrize(
        "url",
        [
            "https://arxiv.org/abs/",
            "https://example.com/not-arxiv",
            "not-a-url",
            "",
            "https://arxiv.org/list/cs.AI/recent",
            "12345",
        ],
    )
    def test_invalid_urls(self, url: str) -> None:
        with pytest.raises(ValueError, match="Could not extract arXiv ID"):
            extract_arxiv_id(url)


# ---------------------------------------------------------------------------
# extract_source
# ---------------------------------------------------------------------------


def _create_tar_gz(output_path: Path, files: dict[str, str]) -> Path:
    """Helper: create a tar.gz archive from a {name -> content} map."""
    tar_path = output_path / "test.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return tar_path


class TestExtractSource:
    """Test tar.gz extraction and main TeX file identification."""

    def test_single_tex_file(self, tmp_path: Path) -> None:
        """A plain .tex file (single-file submission) should be read directly."""
        tex_path = tmp_path / "paper.tex"
        tex_content = r"\documentclass{article}\begin{document}Hello\end{document}"
        tex_path.write_text(tex_content)

        content, images = extract_source(tex_path, tmp_path / "out")
        assert r"\documentclass" in content
        assert images == []

    def test_tar_gz_with_documentclass(self, tmp_path: Path) -> None:
        """The file containing \\documentclass should be selected as main."""
        files = {
            "main.tex": r"\documentclass{article}\begin{document}Main\end{document}",
            "appendix.tex": r"\section{Appendix}",
        }
        tar_path = _create_tar_gz(tmp_path, files)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        content, images = extract_source(tar_path, extract_dir)
        assert r"\documentclass" in content
        assert "Main" in content

    def test_main_tex_selection_prefers_begin_document(self, tmp_path: Path) -> None:
        """Files with both \\documentclass and \\begin{document} win over class-only."""
        files = {
            # Shared preamble with documentclass but no \begin{document}
            "preamble.tex": (
                r"\documentclass{article} " + ("padding " * 200)
            ),
            # Real main file: both markers, smaller in size
            "paper.tex": (
                r"\documentclass{article}\begin{document}Real body\end{document}"
            ),
        }
        tar_path = _create_tar_gz(tmp_path, files)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        content, _ = extract_source(tar_path, extract_dir)
        assert "Real body" in content
        assert "padding" not in content

    def test_input_directive_is_expanded(self, tmp_path: Path) -> None:
        """\\input{chapter} should pull the referenced file inline."""
        files = {
            "main.tex": (
                r"\documentclass{article}\begin{document}"
                r"Intro paragraph.\input{chapter1}\end{document}"
            ),
            "chapter1.tex": "Chapter one content here.",
        }
        tar_path = _create_tar_gz(tmp_path, files)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        content, _ = extract_source(tar_path, extract_dir)
        assert "Intro paragraph" in content
        assert "Chapter one content here." in content
        # The \input directive itself should be gone (expanded away).
        assert r"\input{chapter1}" not in content

    def test_tar_gz_with_images(self, tmp_path: Path) -> None:
        """Image files in the archive should be returned in image_paths."""
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            tex_data = (
                r"\documentclass{article}\begin{document}Hi\end{document}".encode()
            )
            tex_info = tarfile.TarInfo(name="paper.tex")
            tex_info.size = len(tex_data)
            tar.addfile(tex_info, io.BytesIO(tex_data))

            img_data = b"\x89PNG\r\n\x1a\n"
            img_info = tarfile.TarInfo(name="figures/fig1.png")
            img_info.size = len(img_data)
            tar.addfile(img_info, io.BytesIO(img_data))

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        content, images = extract_source(tar_path, extract_dir)
        assert len(images) >= 1
        assert any(p.name == "fig1.png" for p in images)

    def test_tar_gz_no_tex_raises(self, tmp_path: Path) -> None:
        """An archive with no .tex files should raise FileNotFoundError."""
        files = {"readme.txt": "No TeX here"}
        tar_path = _create_tar_gz(tmp_path, files)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        with pytest.raises(FileNotFoundError, match="No .tex files"):
            extract_source(tar_path, extract_dir)

    def test_includepdf_wrapper_raises_pdf_only(self, tmp_path: Path) -> None:
        """A TeX file that just \\includepdf a PDF is treated as PDF-only."""
        files = {
            "wrapper.tex": (
                r"\documentclass{article}\usepackage{pdfpages}"
                r"\begin{document}\includepdf[pages=1-last]{paper.pdf}\end{document}"
            ),
        }
        tar_path = _create_tar_gz(tmp_path, files)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        with pytest.raises(PdfOnlyPaperError, match=r"includepdf|PDF-only"):
            extract_source(tar_path, extract_dir)

    def test_include_directive_is_expanded(self, tmp_path: Path) -> None:
        """\\include{...} (not just \\input) should also be expanded."""
        files = {
            "main.tex": (
                r"\documentclass{article}\begin{document}"
                r"\include{section}\end{document}"
            ),
            "section.tex": "Included section content",
        }
        tar_path = _create_tar_gz(tmp_path, files)
        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()

        content, _ = extract_source(tar_path, extract_dir)
        assert "Included section content" in content
        assert r"\include{section}" not in content


# ---------------------------------------------------------------------------
# fetch_arxiv_paper (integration with mocked HTTP)
# ---------------------------------------------------------------------------


def _mock_response(
    *,
    status: int = 200,
    content: bytes = b"",
    content_type: str = "",
    url: str = "https://arxiv.org/html/2301.00001",
) -> MagicMock:
    """Build a mock requests.Response with the given status / body / content-type / url."""
    response = MagicMock()
    response.status_code = status
    response.content = content
    response.text = content.decode("utf-8", errors="replace") if content else ""
    response.headers = {"Content-Type": content_type}
    response.url = url
    if status >= 400:
        response.raise_for_status = MagicMock(side_effect=Exception(f"HTTP {status}"))
    else:
        response.raise_for_status = MagicMock()
    return response


class TestFetchArxivPaper:
    """Behaviour of TeX-only ``fetch_arxiv_paper``.

    The HTML path (LaTeXML → markdownify) was disabled because its output
    contained preamble leakage, inlined ``\\thanks`` titles, and emails
    treated as relative URLs that the post-processor could not reliably
    repair. All papers now go through the TeX e-print archive.
    """

    def test_tex_archive_is_extracted(self) -> None:
        """A multi-file ``.tar.gz`` e-print should produce kind="tex" content."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tex_data = rb"\documentclass{article}\begin{document}Hello\end{document}"
            info = tarfile.TarInfo(name="paper.tex")
            info.size = len(tex_data)
            tar.addfile(info, io.BytesIO(tex_data))
        tar_bytes = buf.getvalue()

        tex_response = _mock_response(content=tar_bytes, content_type="application/gzip")

        with patch("src.tools.arxiv.requests.get", return_value=tex_response) as mocked:
            paper = fetch_arxiv_paper("https://arxiv.org/abs/2301.00001")

        assert isinstance(paper, PaperSource)
        assert paper.kind == "tex"
        assert r"\documentclass" in paper.content
        # No HTML probe, just the TeX e-print fetch.
        assert mocked.call_count == 1

    def test_single_tex_file_response(self) -> None:
        """A text/plain e-print (single-file submission) should work as TeX."""
        tex_data = r"\documentclass{article}\begin{document}Single\end{document}"
        tex_response = _mock_response(content=tex_data.encode(), content_type="text/plain")

        with patch("src.tools.arxiv.requests.get", return_value=tex_response):
            paper = fetch_arxiv_paper("2301.00001")

        assert paper.kind == "tex"
        assert "Single" in paper.content
        assert paper.images == []

    def test_pdf_only_paper_raises(self) -> None:
        """A PDF-typed e-print response should raise PdfOnlyPaperError."""
        pdf_response = _mock_response(content=b"%PDF-1.5\n", content_type="application/pdf")

        with (
            patch("src.tools.arxiv.requests.get", return_value=pdf_response),
            pytest.raises(PdfOnlyPaperError, match="PDF"),
        ):
            fetch_arxiv_paper("2301.00001")
