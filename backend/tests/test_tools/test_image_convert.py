"""Tests for the PDF → PNG figure converter.

Generates a 1-page test PDF with reportlab if available; otherwise
skips. The conversion itself depends on ``pdftoppm`` from poppler-utils;
tests that need it skip when it isn't on PATH.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from src.tools.image_convert import convert_pdf_figures_to_png


def _make_minimal_pdf(path: Path) -> None:
    """Write a hand-rolled minimal valid 1-page PDF.

    Avoiding reportlab keeps the test dependency surface small and works
    in any environment with the standard library. The PDF is intentionally
    blank — pdftoppm rasterises it to an all-white PNG, which is enough
    to verify the conversion plumbing.
    """
    # cribbed from the PDF reference: 5 objects, single empty page.
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/Resources<<>>/MediaBox[0 0 100 100]/Contents 4 0 R>>endobj\n"
        b"4 0 obj<</Length 0>>stream\nendstream\nendobj\n"
        b"5 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    )
    xref_offset = len(body)
    xref = (
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000053 00000 n \n"
        b"0000000098 00000 n \n"
        b"0000000180 00000 n \n"
        b"0000000228 00000 n \n"
    )
    trailer = (
        b"trailer<</Size 6/Root 1 0 R>>\nstartxref\n"
        + str(xref_offset).encode()
        + b"\n%%EOF\n"
    )
    path.write_bytes(body + xref + trailer)


_HAS_PDFTOPPM = shutil.which("pdftoppm") is not None


def test_empty_list_is_passthrough() -> None:
    assert convert_pdf_figures_to_png([]) == []


def test_non_pdf_image_paths_pass_through_unchanged(tmp_path: Path) -> None:
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    jpg = tmp_path / "y.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")
    svg = tmp_path / "z.svg"
    svg.write_text("<svg/>")
    out = convert_pdf_figures_to_png([png, jpg, svg])
    assert out == [png, jpg, svg]
    # All originals still exist.
    assert png.exists()
    assert jpg.exists()
    assert svg.exists()


@pytest.mark.skipif(not _HAS_PDFTOPPM, reason="pdftoppm not installed")
def test_pdf_is_replaced_by_png_and_original_deleted(tmp_path: Path) -> None:
    pdf = tmp_path / "fig1.pdf"
    _make_minimal_pdf(pdf)
    out = convert_pdf_figures_to_png([pdf])
    assert len(out) == 1
    assert out[0].name == "fig1.png"
    assert out[0].exists()
    assert not pdf.exists()


@pytest.mark.skipif(not _HAS_PDFTOPPM, reason="pdftoppm not installed")
def test_mixed_list_preserves_order_and_non_pdf_entries(tmp_path: Path) -> None:
    png = tmp_path / "a.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    pdf = tmp_path / "b.pdf"
    _make_minimal_pdf(pdf)
    out = convert_pdf_figures_to_png([png, pdf])
    assert [p.name for p in out] == ["a.png", "b.png"]


def test_missing_pdftoppm_yields_passthrough_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """If pdftoppm is absent, return inputs unchanged (don't crash)."""
    pdf = tmp_path / "fig.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr("src.tools.image_convert.shutil.which", lambda _name: None)
    out = convert_pdf_figures_to_png([pdf])
    assert out == [pdf]
    assert pdf.exists()
