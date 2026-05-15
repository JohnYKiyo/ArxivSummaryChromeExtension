"""Tests for ``tools.packaging.create_zip_package``.

Verifies the ZIP filename, the per-member filenames carry the arXiv ID
prefix, and image files land under ``images/``.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from src.tools.packaging import create_zip_package


def test_zip_basename_is_arxiv_id(tmp_path: Path) -> None:
    """The on-disk ZIP file should be named ``<arxiv_id>.zip``."""
    zip_path = create_zip_package(
        paper_en_md="en",
        paper_ja_md="ja",
        summary_ja_md="sum",
        image_paths=[],
        work_dir=tmp_path,
        arxiv_id="2301.00001v2",
    )
    assert zip_path.name == "2301.00001v2.zip"
    assert zip_path.is_file()


def test_member_filenames_use_arxiv_id_prefix(tmp_path: Path) -> None:
    """Each Markdown member inside the ZIP should be prefixed with the arXiv ID."""
    zip_path = create_zip_package(
        paper_en_md="english body",
        paper_ja_md="日本語本文",
        summary_ja_md="要約",
        image_paths=[],
        work_dir=tmp_path,
        arxiv_id="1706.03762",
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
    assert "1706.03762_paper_en.md" in names
    assert "1706.03762_paper_ja.md" in names
    assert "1706.03762_summary_ja.md" in names
    # Make sure the old non-prefixed names are gone (regression guard).
    assert "paper_en.md" not in names
    assert "paper_ja.md" not in names
    assert "summary_ja.md" not in names


def test_member_file_contents_are_preserved(tmp_path: Path) -> None:
    """Reading a member back should yield exactly the input string."""
    zip_path = create_zip_package(
        paper_en_md="line 1\nline 2",
        paper_ja_md="行1\n行2",
        summary_ja_md="要約本文",
        image_paths=[],
        work_dir=tmp_path,
        arxiv_id="x",
    )
    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("x_paper_en.md").decode("utf-8") == "line 1\nline 2"
        assert zf.read("x_paper_ja.md").decode("utf-8") == "行1\n行2"
        assert zf.read("x_summary_ja.md").decode("utf-8") == "要約本文"


def test_images_go_under_images_directory(tmp_path: Path) -> None:
    """Image files should be added under the (un-prefixed) ``images/`` dir."""
    img_dir = tmp_path / "src_images"
    img_dir.mkdir()
    img1 = img_dir / "fig1.png"
    img1.write_bytes(b"\x89PNG")
    img2 = img_dir / "fig2.jpg"
    img2.write_bytes(b"\xff\xd8\xff")

    zip_path = create_zip_package(
        paper_en_md="en",
        paper_ja_md="ja",
        summary_ja_md="sum",
        image_paths=[img1, img2],
        work_dir=tmp_path,
        arxiv_id="2301.00001",
    )
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "images/fig1.png" in names
    assert "images/fig2.jpg" in names


def test_missing_image_is_skipped_not_fatal(tmp_path: Path) -> None:
    """A non-existent image in the list should be skipped with a warning."""
    missing = tmp_path / "does-not-exist.png"
    # No file written.
    zip_path = create_zip_package(
        paper_en_md="en",
        paper_ja_md="ja",
        summary_ja_md="sum",
        image_paths=[missing],
        work_dir=tmp_path,
        arxiv_id="2301.00001",
    )
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        # Markdown members are present, but no images/ entry for the missing file.
        assert "images/does-not-exist.png" not in zf.namelist()
