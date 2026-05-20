"""Tests for the LaTeXML HTML → Markdown converter.

Focused on the cleanup steps that meaningfully affect downstream
translation quality: author-name clutter from affiliation markers,
and the h6 abstract heading that breaks the document outline.
"""

from src.tools.html_to_markdown import html_to_markdown


def test_strips_math_affiliation_markers_from_author_names() -> None:
    """`<math alttext="{}^{1^{*}}">` next to author names must not leak through.

    Without the strip step the translator sees ``Zahra Asadi${}^{1^{*}}$``
    and either echoes that noise or mis-handles it as content. We want a
    clean ``Zahra Asadi`` followed (eventually) by the affiliation block.
    """
    html = """
    <article class="ltx_document">
      <div class="ltx_authors">
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Zahra Asadi<math alttext="{}^{1^{*}}" display="inline">junk</math></span>
        </span>
      </div>
    </article>
    """
    md = html_to_markdown(html)
    assert "Zahra Asadi" in md
    assert "{}^{1^{*}}" not in md
    assert "${" not in md  # no leftover inline math fragments


def test_strips_sup_affiliation_markers_from_author_names() -> None:
    """``<sup>1</sup>`` next to author names becomes a stray digit; drop it."""
    html = """
    <article class="ltx_document">
      <div class="ltx_authors">
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Mehrdad Asadi<sup class="ltx_sup">1</sup></span>
        </span>
      </div>
    </article>
    """
    md = html_to_markdown(html)
    assert "Mehrdad Asadi" in md
    # The bare "1" right after the name would survive without the strip.
    assert "Mehrdad Asadi1" not in md
    assert "Mehrdad Asadi 1" not in md


def test_does_not_strip_sup_outside_author_block() -> None:
    """Stripping only applies to ``span.ltx_personname``; body sup stays."""
    html = """
    <article class="ltx_document">
      <section>
        <p>Footnote ref<sup>2</sup>.</p>
      </section>
    </article>
    """
    md = html_to_markdown(html)
    # markdownify keeps <sup> content inline; we just don't strip it.
    assert "2" in md


def test_preserves_affiliation_notes_block() -> None:
    """The author-notes block (where affiliations live) must survive."""
    html = """
    <article class="ltx_document">
      <div class="ltx_authors">
        <span class="ltx_creator ltx_role_author">
          <span class="ltx_personname">Marta Kersten-Oertel<sup>1</sup></span>
          <span class="ltx_author_notes">
            <span class="ltx_contact ltx_role_address">
              <sup>1</sup>Gina Cody School, Concordia University
            </span>
          </span>
        </span>
      </div>
    </article>
    """
    md = html_to_markdown(html)
    assert "Marta Kersten-Oertel" in md
    assert "Gina Cody School" in md
    assert "Concordia University" in md


def test_promotes_abstract_h6_to_h2() -> None:
    """`<h6 class="ltx_title_abstract">` must end up as `## Abstract`."""
    html = """
    <article class="ltx_document">
      <div class="ltx_abstract">
        <h6 class="ltx_title ltx_title_abstract">Abstract</h6>
        <p>This paper presents...</p>
      </div>
    </article>
    """
    md = html_to_markdown(html)
    assert "## Abstract" in md
    assert "###### Abstract" not in md


def test_promotes_keywords_h6_to_h2() -> None:
    """The keyword classification block is also h6 in LaTeXML."""
    html = """
    <article class="ltx_document">
      <h6 class="ltx_title ltx_title_classification">keywords:</h6>
      <p>AR, neurosurgery</p>
    </article>
    """
    md = html_to_markdown(html)
    assert "## keywords:" in md
    assert "###### " not in md


def test_does_not_promote_other_h6_headings() -> None:
    """Non-abstract h6 (rare, but possible in body) is left alone."""
    html = """
    <article class="ltx_document">
      <section>
        <h6>Deeply nested subsection</h6>
      </section>
    </article>
    """
    md = html_to_markdown(html)
    assert "###### Deeply nested subsection" in md


def test_math_alttext_still_preserved_in_body() -> None:
    """The author-marker strip must not break math preservation elsewhere."""
    html = r"""
    <article class="ltx_document">
      <p>Loss <math alttext="\mathcal{L}" display="inline">junk</math> is minimised.</p>
      <p>Display: <math alttext="\sum_{i=1}^{n} x_i" display="block">junk</math></p>
    </article>
    """
    md = html_to_markdown(html)
    assert r"$\mathcal{L}$" in md
    # Display math is isolated onto its own paragraph by markdown_layout —
    # the body survives intact across line breaks.
    assert r"\sum_{i=1}^{n} x_i" in md
    assert "$$\n\\sum_{i=1}^{n} x_i\n$$" in md
