"""Tests for math-scope macro normalisation.

Verifies that non-portable LaTeX commands (``\\bm``, ``\\!``,
``\\label`` inside math) are rewritten to KaTeX/MathJax-compatible
forms, and that prose outside math is left untouched.
"""

from src.tools.math_normalize import normalize_math_macros


def test_bm_braced_argument_becomes_boldsymbol() -> None:
    md = r"Result: $\bm{\theta}$ converges."
    assert normalize_math_macros(md) == r"Result: $\boldsymbol{\theta}$ converges."


def test_bm_macro_argument_becomes_boldsymbol() -> None:
    """The most common form in papers: ``\\bm\\theta`` without braces."""
    md = r"Result: $\bm\theta_t$ at step $t$."
    out = normalize_math_macros(md)
    assert r"$\boldsymbol{\theta}_t$" in out
    assert r"\bm" not in out


def test_bm_single_character_argument_becomes_boldsymbol() -> None:
    md = r"Vector $\bm a$ is bold."
    out = normalize_math_macros(md)
    assert r"$\boldsymbol{a}$" in out
    assert r"\bm" not in out


def test_bm_in_display_math() -> None:
    md = r"$$y = \bm\theta^\top \bm x$$"
    out = normalize_math_macros(md)
    assert out == r"$$y = \boldsymbol{\theta}^\top \boldsymbol{x}$$"


def test_negative_thin_space_removed() -> None:
    md = r"$\arg\!\max_a f(a)$"
    out = normalize_math_macros(md)
    assert r"\!" not in out
    assert out == r"$\arg\max_a f(a)$"


def test_label_inside_math_removed() -> None:
    md = "$$\\label{eq:1}\ny = x^2$$"
    out = normalize_math_macros(md)
    assert r"\label" not in out
    assert "y = x^2" in out


def test_does_not_touch_bm_outside_math() -> None:
    """A literal ``\\bm`` in prose (e.g. inside a code block) stays.

    \\bm is meaningless outside math, but we shouldn't silently rewrite
    arbitrary prose either — that's a separate kind of bug.
    """
    md = "Inline code: `\\bm\\theta` is the bm macro."
    out = normalize_math_macros(md)
    assert out == md


def test_multiple_math_regions_in_one_paragraph() -> None:
    md = r"Have $\bm a$, $\bm b$, and $\bm c$."
    out = normalize_math_macros(md)
    assert out == r"Have $\boldsymbol{a}$, $\boldsymbol{b}$, and $\boldsymbol{c}$."


def test_mixed_substitutions_in_single_block() -> None:
    md = r"$$\bm\theta = \arg\!\max_a Q(s, a; \bm\theta_t) \label{eq:dqn}$$"
    out = normalize_math_macros(md)
    # All three substitutions hit, in one display-math region.
    assert r"\boldsymbol{\theta}" in out
    assert r"\arg\max" in out
    assert r"\!" not in out
    assert r"\label" not in out


def test_escaped_dollar_is_not_treated_as_math_delimiter() -> None:
    """``\\$5`` in prose must not open a math region."""
    md = r"Costs \$5 and $\bm\theta$ converges."
    out = normalize_math_macros(md)
    assert r"\$5" in out  # literal escaped dollar preserved
    assert r"$\boldsymbol{\theta}$" in out


def test_empty_input() -> None:
    assert normalize_math_macros("") == ""


def test_no_math_means_no_change() -> None:
    md = "Plain paragraph with no math at all.\n\nAnother paragraph."
    assert normalize_math_macros(md) == md
