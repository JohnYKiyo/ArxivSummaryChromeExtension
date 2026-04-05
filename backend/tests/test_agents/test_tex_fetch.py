"""Tests for the TexFetchAgent.

Verifies arXiv source downloading, tar.gz extraction,
and correct identification of .tex and image files.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.tools.arxiv import extract_arxiv_id, extract_source, fetch_arxiv_paper


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
            # Bare IDs
            ("2301.00001", "2301.00001"),
            ("2301.00001v2", "2301.00001v2"),
            # IDs with 5-digit second part
            ("https://arxiv.org/abs/2401.12345", "2401.12345"),
            ("2401.12345", "2401.12345"),
            # URL with trailing path or query
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
            "12345",  # not in YYMM.NNNNN format
        ],
    )
    def test_invalid_urls(self, url: str) -> None:
        with pytest.raises(ValueError, match="Could not extract arXiv ID"):
            extract_arxiv_id(url)


# ---------------------------------------------------------------------------
# extract_source
# ---------------------------------------------------------------------------


def _create_tar_gz(output_path: Path, files: dict[str, str]) -> Path:
    """Helper: create a tar.gz archive with the given filename->content map."""
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

    def test_tar_gz_with_images(self, tmp_path: Path) -> None:
        """Image files in the archive should be returned in image_paths."""
        tar_path = tmp_path / "test.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            # Add a TeX file
            tex_data = r"\documentclass{article}\begin{document}Hi\end{document}".encode()
            tex_info = tarfile.TarInfo(name="paper.tex")
            tex_info.size = len(tex_data)
            tar.addfile(tex_info, io.BytesIO(tex_data))

            # Add an image file
            img_data = b"\x89PNG\r\n\x1a\n"  # PNG magic bytes
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


# ---------------------------------------------------------------------------
# fetch_arxiv_paper (integration with mocked HTTP)
# ---------------------------------------------------------------------------


class TestFetchArxivPaper:
    """Test the high-level fetch_arxiv_paper function with mocked HTTP."""

    def test_fetch_with_mocked_response(self, tmp_path: Path) -> None:
        """Mocked HTTP download should produce a valid extraction."""
        # Build a tar.gz in-memory
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tex_data = r"\documentclass{article}\begin{document}Hello\end{document}".encode()
            info = tarfile.TarInfo(name="paper.tex")
            info.size = len(tex_data)
            tar.addfile(info, io.BytesIO(tex_data))
        tar_bytes = buf.getvalue()

        mock_response = MagicMock()
        mock_response.content = tar_bytes
        mock_response.headers = {"Content-Type": "application/gzip"}
        mock_response.raise_for_status = MagicMock()

        with patch("src.tools.arxiv.requests.get", return_value=mock_response):
            tex_content, image_paths, work_dir = fetch_arxiv_paper(
                "https://arxiv.org/abs/2301.00001"
            )

        assert r"\documentclass" in tex_content
        assert isinstance(image_paths, list)
        assert Path(work_dir).exists()

    def test_fetch_single_tex_response(self, tmp_path: Path) -> None:
        """A text/plain response (single-file submission) should work."""
        tex_data = r"\documentclass{article}\begin{document}Single\end{document}"

        mock_response = MagicMock()
        mock_response.content = tex_data.encode("utf-8")
        mock_response.headers = {"Content-Type": "text/plain"}
        mock_response.raise_for_status = MagicMock()

        with patch("src.tools.arxiv.requests.get", return_value=mock_response):
            tex_content, image_paths, work_dir = fetch_arxiv_paper("2301.00001")

        assert "Single" in tex_content
        assert image_paths == []
