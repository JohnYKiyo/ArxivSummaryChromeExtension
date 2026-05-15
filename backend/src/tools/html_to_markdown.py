"""HTML → Markdown converter for arXiv HTML papers.

arxiv.org/html pages are produced by LaTeXML and follow a predictable
structure (``<section>`` / ``<figure>`` / ``<math>`` with ``alttext``
holding the original TeX). We swap math elements for opaque placeholder
tokens, run markdownify, then substitute the placeholders back with the
original LaTeX. This avoids markdownify escaping underscores / asterisks
inside math source (e.g. ``\\sum_i`` would otherwise become ``\\sum\\_i``).

This module performs no LLM calls — it is a deterministic, pure-library
conversion, intentionally kept separate from ``agents/``.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify as _markdownify

logger = logging.getLogger(__name__)


# Tags whose content should be discarded outright (no useful Markdown).
_STRIP_TAGS = ("script", "style", "noscript", "meta", "link", "head", "nav")

# LaTeXML wraps the actual paper body in <article class="ltx_document">.
# We narrow to that element to drop arXiv's site chrome (header, footer,
# "Report GitHub Issue" widget, accessibility blurb, etc.).
_ARTICLE_SELECTOR = "article.ltx_document"

# Additional LaTeXML-specific noise to remove from the article body itself.
_ARTICLE_NOISE_SELECTORS = (
    "div.package-alerts",  # "Report GitHub Issue" widget
    "div.ltx_page_logo",  # arXiv logo
    "div.ltx_page_navbar",
    "div.ltx_page_footer",
    "div.ar5iv-footer",
    "div.ltx_dates",  # license info block
)

# Whitespace cleanup: collapse 3+ blank lines to 2.
_EXCESS_BLANK_LINES = re.compile(r"\n{3,}")

# Placeholder format that markdownify will pass through untouched
# (no special Markdown chars). Index is unique per math element so we can
# substitute back after conversion.
_MATH_PLACEHOLDER = "MATHTOKENZZZ{index}MATHTOKENZZZ"
_MATH_PLACEHOLDER_RE = re.compile(r"MATHTOKENZZZ(\d+)MATHTOKENZZZ")


def html_to_markdown(html: str, base_url: str | None = None) -> str:
    """Convert an arXiv HTML paper to Markdown.

    Math is preserved by reading the ``alttext`` attribute LaTeXML emits on
    ``<math>`` elements (which contains the original LaTeX). Images keep
    their URLs; if ``base_url`` is given, relative ``src`` values are made
    absolute so the resulting Markdown renders without a local image dir.

    Args:
        html: The raw HTML body of the arXiv paper page.
        base_url: Base URL for resolving relative image and link references
            (typically ``"https://arxiv.org/html/<id>/"``).

    Returns:
        A Markdown string. Heading levels, lists, tables and code blocks
        are preserved. Math becomes ``$...$`` (inline) or ``$$...$$``
        (display) using the original LaTeX from ``alttext``.
    """
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in _STRIP_TAGS:
        for el in soup.find_all(tag_name):
            el.decompose()

    # Narrow to the paper body if LaTeXML's <article> wrapper is present.
    article = soup.select_one(_ARTICLE_SELECTOR)
    if article is not None:
        for selector in _ARTICLE_NOISE_SELECTORS:
            for el in article.select(selector):
                el.decompose()
        root: Tag = article
    else:
        # Non-arXiv HTML or non-standard layout: fall back to <body>.
        root = soup.body or soup  # type: ignore[assignment]

    math_sources = _stash_math_as_placeholders(root)

    if base_url is not None:
        _absolutize_urls(root, base_url)

    markdown = _markdownify(
        str(root),
        heading_style="ATX",
        bullets="-",
    )

    markdown = _restore_math(markdown, math_sources)
    markdown = _EXCESS_BLANK_LINES.sub("\n\n", markdown)
    return markdown.strip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _stash_math_as_placeholders(soup: Tag) -> list[str]:
    """Replace each ``<math>`` element with a unique placeholder token.

    LaTeXML emits ``<math alttext="...">`` where ``alttext`` is the source
    LaTeX. Display math additionally has ``display="block"``. We record
    the ``$...$`` / ``$$...$$`` form indexed by position; the placeholder
    text passes through markdownify untouched and is substituted back in
    :func:`_restore_math`.

    Returns:
        A list where ``result[i]`` is the LaTeX-wrapped Markdown for the
        ``i``-th math element encountered. Math elements without
        ``alttext`` are dropped (no placeholder reserved).
    """
    sources: list[str] = []
    for math in soup.find_all("math"):
        alttext = math.get("alttext")
        if not alttext:
            math.decompose()
            continue
        display = (math.get("display") or "").lower()
        delim = "$$" if display == "block" else "$"
        sources.append(f"{delim}{alttext}{delim}")
        math.replace_with(NavigableString(_MATH_PLACEHOLDER.format(index=len(sources) - 1)))
    return sources


def _restore_math(markdown: str, sources: list[str]) -> str:
    """Substitute math placeholders in *markdown* with their LaTeX source."""
    if not sources:
        return markdown

    def _replace(match: re.Match[str]) -> str:
        idx = int(match.group(1))
        if 0 <= idx < len(sources):
            return sources[idx]
        # Unknown index — defensive, leave placeholder in place.
        return match.group(0)

    return _MATH_PLACEHOLDER_RE.sub(_replace, markdown)


def _absolutize_urls(soup: Tag, base_url: str) -> None:
    """Resolve relative ``src``/``href`` against ``base_url``."""
    for el in soup.find_all(["img", "a"]):
        if not isinstance(el, Tag):
            continue
        for attr in ("src", "href"):
            value = el.get(attr)
            if isinstance(value, str) and value and not value.startswith(("http://", "https://", "data:", "#")):
                el[attr] = urljoin(base_url, value)
