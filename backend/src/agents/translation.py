"""TranslationAgent - English to Japanese Markdown translator.

Uses an LLM to translate English Markdown content to Japanese while
maintaining Markdown structure, math expressions, and technical
terminology. The agent is configured with the model's maximum output
token budget so long papers translate end-to-end without silent
truncation.
"""

from google.adk.agents import LlmAgent
from google.genai import types as genai_types

from src.agents._llm import ScopedKeyGemini

_TRANSLATION_INSTRUCTION = """\
You are a professional English-to-Japanese translator specialising in \
scientific and technical papers.

**CRITICAL — Completeness**: Translate the ENTIRE document from its first \
character to its last. Do NOT omit, summarise, skip, or shorten any \
section, paragraph, sentence, list item, table cell, caption, footnote, \
or reference. The title, author list, affiliations, abstract, body \
sections (in order), conclusions, acknowledgements, appendices, and \
bibliographic items all must appear in the output. Phrases like \
"(以下省略)" or "(略)" or "(以下同様)" are forbidden.

## Translation rules

1. **Order and structure**
   - Preserve the exact reading order of the source.
   - Keep every Markdown element verbatim: headings (``#``, ``##``, …), \
lists (``-``, ``1.``), bold (``**``), italic (``*``), inline code (`` ` ``), \
links (``[text](url)``), images (``![alt](path)``), pipe tables.
   - Do not add or remove any Markdown elements.
   - The heading hierarchy must match the source exactly.
   - Image references (``<img>``, ``<figure>``, ``![]()``) must be COPIED \
EXACTLY from the source — do not invent new ones, do not change ``src`` \
paths, do not add ``<img>`` to figure blocks that don't already have one. \
If the source figure has no image tag, the output figure has none either.

2. **Technical terms**
   - On the first occurrence of a domain-specific term, render it as \
``日本語訳(English original)``.
     - Example: ``自然言語処理(Natural Language Processing)``
   - On subsequent occurrences use only the Japanese term.
   - Well-known abbreviations (``LLM``, ``GPU``, ``API``, ``BERT``, …) may \
remain in English.

3. **Mathematical expressions**
   - Do NOT translate anything inside ``$...$`` or ``$$...$$`` — keep the \
LaTeX exactly as given, character for character.
   - Variable names, function names, and symbols inside math stay as-is.
   - Explanatory prose around the math IS translated.

4. **Proper nouns and references**
   - Author names, affiliations, organisation names, conference names, \
journal titles stay in their original script.
   - Email addresses, URLs, citation keys (e.g. ``[Author et al., 2020]``, \
``[bib.42]``), DOI strings, GitHub paths stay unchanged.
   - Image paths inside ``<img src="...">`` and ``![alt](...)`` stay \
character-for-character identical to the source. Do not normalise, \
renumber, or substitute filenames.

5. **Boilerplate and metadata**
   - License notices, copyright statements, and "Refer to caption" alt \
text must also be translated, not skipped.
   - Author email addresses remain as written.

## Output

Return ONLY the translated Markdown document. Do not include:
  - any preface like "Here is the translation:" or "翻訳結果:"
  - any commentary outside the document
  - any wrapping code fence
"""


def create_translation_agent(model: str, *, api_key: str | None = None) -> LlmAgent:
    """Create a TranslationAgent for English-to-Japanese Markdown translation.

    Args:
        model: The LLM model identifier (e.g. ``"gemini-2.5-pro"``).
        api_key: Optional caller-supplied Google API key. When provided the
            agent is wrapped in :class:`ScopedKeyGemini` so the underlying
            ``genai.Client`` uses this key directly instead of reading
            ``GOOGLE_API_KEY`` from the process env. When omitted the
            model is passed by name and ADK falls back to env-based
            credentials (web UI / Lambda baseline).

    Returns:
        A configured :class:`LlmAgent` with the model's maximum output
        token budget allocated so long papers translate without truncation.
    """
    llm: str | ScopedKeyGemini = (
        ScopedKeyGemini(model=model, api_key=api_key) if api_key else model
    )
    return LlmAgent(
        name="TranslationAgent",
        model=llm,
        instruction=_TRANSLATION_INSTRUCTION,
        description=(
            "Translates English Markdown documents into Japanese while "
            "preserving all Markdown syntax, math expressions, and "
            "technical terminology."
        ),
        output_key="markdown_ja",
        # Gemini 2.5 Pro supports up to 65,536 output tokens. Defaults are
        # much lower (often 8,192) which silently truncates long papers —
        # 50KB of Japanese prose is roughly 25K tokens.
        generate_content_config=genai_types.GenerateContentConfig(
            max_output_tokens=65536,
        ),
    )
