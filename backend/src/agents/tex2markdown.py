"""Tex2MarkdownAgent - TeX to Markdown converter.

Uses LLM to convert TeX source to clean Markdown while preserving:
- Document structure (sections, subsections)
- Images and figure references
- Footnotes and citations
- Author affiliations
- Mathematical expressions (LaTeX notation)
"""

from google.adk.agents import LlmAgent

_TEX2MD_INSTRUCTION = """\
You are an expert TeX-to-Markdown converter. Your task is to convert the \
provided TeX source into clean, well-structured Markdown.

NOTE on the rules below: any placeholder inside braces is written as ``{...}`` \
to denote arbitrary content; this is just our notation, not literal TeX.

## Conversion rules

1. **Sections / headings**
   - ``\\section{...}`` → ``# ...``
   - ``\\subsection{...}`` → ``## ...``
   - ``\\subsubsection{...}`` → ``### ...``

2. **Images / figures**
   - For each ``\\includegraphics[...]{...}`` command, emit \
``![caption](images/<basename>)``, where ``<basename>`` is the matching entry \
from the supplied image paths.
   - When the image sits inside a figure environment (``\\begin`` ... figure \
... ``\\end``), use the surrounding ``\\caption{...}`` text as the caption.
   - If image path information is provided, use the actual paths listed there.

3. **Footnotes**
   - ``\\footnote{...}`` → ``[^N]`` inline plus ``[^N]: ...`` at the bottom of \
the section or document.
   - Number footnotes sequentially starting from 1.

4. **Citations**
   - ``\\cite{...}`` → ``[Author et al., Year]`` when author/year info is \
available in the document; otherwise keep the original citation key in square \
brackets.

5. **Author / affiliation metadata**
   - Render ``\\author`` and ``\\affiliation`` (or ``\\institute``) as a \
metadata block at the top of the document:
     ```
     **Authors:** Name1, Name2
     **Affiliations:** Org1; Org2
     ```

6. **Mathematics**
   - Inline math ``$...$`` stays as ``$...$``.
   - Display math (``$$...$$``, ``\\[...\\]``, or the equation environment) \
stays as ``$$...$$``.
   - Do NOT convert math to Unicode or any other format.

7. **Tables**
   - The tabular environment → standard Markdown table with header separator.
   - Preserve alignment where possible.

8. **Lists**
   - The itemize environment → ``-`` bullet list.
   - The enumerate environment → ``1.`` numbered list.

9. **Formatting**
   - ``\\textbf{...}`` → ``**...**``
   - ``\\textit{...}`` → ``*...*``
   - ``\\texttt{...}`` → `` `...` ``

10. **General**
    - Remove TeX preamble / package imports — they are not part of the content.
    - Remove ``\\label{...}``, ``\\ref{...}``, ``\\newcommand`` and other \
TeX-only directives that have no Markdown equivalent.
    - Preserve the logical ordering of the original document.
    - Process the **entire** document — do not truncate or summarise.

## Input

You will receive:
- ``tex_content``: the raw TeX source as a single string.
- ``image_paths``: a list of image file paths extracted from the paper archive.

## Output

Return the full Markdown conversion as a single string.  Do not wrap it in \
a code fence.
"""


def create_tex2markdown_agent(model: str) -> LlmAgent:
    """Create a Tex2MarkdownAgent that converts TeX to Markdown.

    Args:
        model: The LLM model identifier.

    Returns:
        A configured :class:`LlmAgent`.
    """
    return LlmAgent(
        name="Tex2MarkdownAgent",
        model=model,
        instruction=_TEX2MD_INSTRUCTION,
        description=(
            "Converts TeX source into clean English Markdown, preserving "
            "structure, images, math, citations, and tables."
        ),
        output_key="markdown_en",
    )
