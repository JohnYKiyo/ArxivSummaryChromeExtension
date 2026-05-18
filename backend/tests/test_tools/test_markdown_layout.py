"""Tests for ``$$...$$`` display-math isolation."""

from src.tools.markdown_layout import isolate_display_math


def test_inline_display_math_gets_its_own_paragraph() -> None:
    """Mid-paragraph ``$$...$$`` must be re-flowed onto its own block."""
    md = "Text before. $$f(x) = x^2$$ Text after."
    out = isolate_display_math(md)
    assert "\n\n$$\nf(x) = x^2\n$$\n\n" in out
    assert "$$f(x)" not in out  # no longer inline


def test_already_isolated_display_math_is_preserved() -> None:
    """A correctly-isolated block must remain valid (no over-spacing)."""
    md = "Text before.\n\n$$\nf(x) = x^2\n$$\n\nText after.\n"
    out = isolate_display_math(md)
    # Body unchanged; no triple+ newlines introduced.
    assert "$$\nf(x) = x^2\n$$" in out
    assert "\n\n\n" not in out


def test_multiple_display_math_blocks_each_isolated() -> None:
    md = "Intro. $$a = 1$$ Then $$b = 2$$ End."
    out = isolate_display_math(md)
    # Each $$...$$ pair on its own paragraph.
    assert "$$\na = 1\n$$" in out
    assert "$$\nb = 2\n$$" in out


def test_label_inside_display_math_preserved() -> None:
    """``\\label{...}`` and other content inside the math must not be touched."""
    md = "See. $$\\label{eq:1}\nf(x) = x$$ Done."
    out = isolate_display_math(md)
    assert "\\label{eq:1}" in out
    assert "f(x) = x" in out


def test_inline_math_left_alone() -> None:
    """Single-dollar inline math must NOT be touched."""
    md = "The energy is $E = mc^2$ in this universe."
    assert isolate_display_math(md) == md


def test_multiline_display_math_body_preserved() -> None:
    md = "$$\na = 1 \\\\\nb = 2 \\\\\nc = 3\n$$"
    out = isolate_display_math(md)
    assert "a = 1" in out
    assert "b = 2" in out
    assert "c = 3" in out


def test_empty_input() -> None:
    assert isolate_display_math("") == ""


def test_no_math_means_no_change() -> None:
    md = "Plain paragraph with no math.\n\nAnother paragraph."
    assert isolate_display_math(md) == md
