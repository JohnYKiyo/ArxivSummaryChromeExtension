"""Layout post-processing for converted Markdown.

Both the HTML (LaTeXML → markdownify) and TeX (pandoc) converters can
emit display math (``$$...$$``) **inline** within a paragraph:

    Text before. $$f(x) = x^2$$ Text after.

This is valid pandoc-flavoured Markdown, but it renders unreliably:

- Obsidian's MathJax expects display math on its own paragraph and
  often fails to recognise mid-paragraph ``$$`` as a block opener.
- GitHub's MathJax renders it as inline-styled display math, which
  collides with the surrounding line.
- VS Code's preview behaves similarly to Obsidian.

This module re-flows each ``$$...$$`` block onto its own paragraph,
preserving the math content character-for-character. Inline math
(``$...$``) is left alone.
"""

from __future__ import annotations

import re

_DISPLAY_MATH = re.compile(r"\$\$([\s\S]+?)\$\$")
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")


def isolate_display_math(markdown: str) -> str:
    """Put every ``$$...$$`` block on its own paragraph.

    The match is non-greedy so adjacent blocks are isolated individually
    rather than swallowed into one giant span. Excess blank lines created
    by the re-flow are collapsed back to a single blank line.

    Math content (including any ``\\label{...}`` or whitespace inside)
    is preserved exactly — only the surrounding line breaks change.
    """

    def _replace(match: re.Match[str]) -> str:
        body = match.group(1).strip()
        return f"\n\n$$\n{body}\n$$\n\n"

    out = _DISPLAY_MATH.sub(_replace, markdown)
    return _EXCESS_BLANK_LINES.sub("\n\n", out)
