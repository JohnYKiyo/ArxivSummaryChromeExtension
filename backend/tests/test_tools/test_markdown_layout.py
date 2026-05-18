"""Tests for ``$$...$$`` display-math isolation and label stripping."""

from src.tools.markdown_layout import isolate_display_math, strip_math_labels


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


# ---------------------------------------------------------------------------
# strip_math_labels
# ---------------------------------------------------------------------------


def test_strip_label_at_start_of_display_math() -> None:
    """``$$\\label{...}\\n...$$`` — the pandoc-emitted shape — drops the label."""
    md = r"$$\label{TDDQ}" + "\n" + r"Y_t = R_{t+1} + \gamma Q$$"
    out = strip_math_labels(md)
    assert r"\label" not in out
    assert "Y_t = R_{t+1} + \\gamma Q" in out


def test_strip_label_with_spacing() -> None:
    md = r"$$  \label{eq:1}   y = x^2$$"
    out = strip_math_labels(md)
    assert r"\label" not in out
    assert "y = x^2" in out


def test_strip_label_does_not_touch_other_math_content() -> None:
    """``\\arg\\!\\max`` and friends must survive untouched."""
    md = (
        r"$$\label{TDDQ}" + "\n"
        + r"Y^{\text{DoubleQ}}_t \!\equiv R_{t+1} + \gamma Q(S_{t+1},"
        + r" \mathop{\mathrm{\arg\!\max}}_a Q(S_{t+1}, a; \bm\theta_t);"
        + r" \bm\theta'_t ) \,.$$"
    )
    out = strip_math_labels(md)
    assert r"\label" not in out
    # All the meaningful math operators stay.
    assert r"\!\equiv" in out
    assert r"\arg\!\max" in out
    assert r"\bm\theta" in out
    assert r"\mathop{\mathrm{" in out


def test_strip_label_leaves_inline_math_alone() -> None:
    """Inline ``$...$`` is not touched even if it (theoretically) contained \\label."""
    md = r"Inline $\label{x} y = x$ in prose."
    out = strip_math_labels(md)
    # We deliberately scope to $$...$$; inline labels (if any) survive.
    assert out == md


def test_strip_label_preserves_prose_around_math() -> None:
    md = r"Before. $$\label{eq} y = x$$ After."
    out = strip_math_labels(md)
    assert "Before." in out
    assert "After." in out
    assert r"\label" not in out
    assert "y = x" in out
