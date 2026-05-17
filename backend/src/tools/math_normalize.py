"""Normalise non-portable LaTeX macros inside Markdown math regions.

Both the HTML (LaTeXML) and TeX (pandoc) conversion paths produce
Markdown where math is delimited by ``$...$`` (inline) and ``$$...$$``
(display), with the original LaTeX preserved verbatim inside. Several
macros widely used in academic papers do not render in the KaTeX /
MathJax pipelines that most Markdown viewers (GitHub, VS Code preview,
Obsidian, etc.) use by default:

* ``\\bm{X}`` (from the ``bm`` package) — bold math symbol.
  Not defined in KaTeX, partially supported in MathJax.
* ``\\!`` — negative thin space. Unsupported in KaTeX, present in many
  papers as ``\\arg\\!\\max`` style typography.
* ``\\label{...}`` left inside math by pandoc when authors put labels
  next to equations. Renderers either error or print the tag.

This module rewrites the math content into a widely-supported subset
without changing the visible mathematics. Substitutions touch only the
content inside ``$...$`` / ``$$...$$`` regions, never surrounding prose.
"""

from __future__ import annotations

import re

# Display math (``$$...$$``, possibly multi-line) OR inline math
# (``$...$``, single line, with backslash-escapes honoured). Display is
# tried first so ``$$`` is never mistaken for two adjacent inline opens.
_MATH_BLOCK = re.compile(
    r"\$\$[\s\S]*?\$\$|(?<!\\)\$(?:\\.|[^$\n])+?(?<!\\)\$",
)

# ``\bm`` followed by either a braced group, another macro, or a single
# alphanumeric character — the three shapes that appear in real papers.
_BM_RE = re.compile(r"\\bm\s*(\\[a-zA-Z]+\*?|\{[^{}]*\}|[A-Za-z0-9])")

# ``\label{name}`` inside math is a pandoc cross-reference artefact.
_LABEL_RE = re.compile(r"\\label\s*\{[^{}]*\}")


def normalize_math_macros(markdown: str) -> str:
    """Rewrite non-portable LaTeX macros inside ``$...$`` / ``$$...$$``.

    Substitutions applied (math-scope only):

    * ``\\bm{X}`` / ``\\bm\\X`` / ``\\bm X`` → ``\\boldsymbol{X}``
    * ``\\!``                                  → removed
    * ``\\label{...}``                         → removed

    Markdown outside math regions is returned unchanged.
    """
    return _MATH_BLOCK.sub(_normalize_one, markdown)


def _normalize_one(match: re.Match[str]) -> str:
    body = match.group(0)
    body = _BM_RE.sub(_replace_bm, body)
    body = body.replace(r"\!", "")
    body = _LABEL_RE.sub("", body)
    return body


def _replace_bm(match: re.Match[str]) -> str:
    arg = match.group(1)
    if arg.startswith("{") and arg.endswith("}"):
        return r"\boldsymbol" + arg
    return r"\boldsymbol{" + arg + "}"
