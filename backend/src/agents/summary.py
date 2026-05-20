"""SummaryAgent - Paper summarizer with type classification.

First classifies the paper as either a regular paper or a review/survey paper,
then generates a structured summary using the appropriate template:
- Regular paper: summary_template.md (要約, 使用された手法, 技術の主要なポイント, 先行研究との比較, 実験方法, 議論)
- Review/Survey paper: summary_review_template.md (要約, レビュー/サーベイ項目, 比較, 議論)
"""

from pathlib import Path

from google.adk.agents import LlmAgent

from src.agents._llm import ScopedKeyGemini
from src.config import get_settings


def load_summary_template(paper_type: str) -> str:
    """Load the appropriate summary template based on paper type.

    Args:
        paper_type: Either ``"review"`` for review/survey papers, or any
            other value for regular research papers.

    Returns:
        The template content as a string.

    Raises:
        FileNotFoundError: If the template file does not exist.
    """
    settings = get_settings()

    if paper_type.strip().lower() == "review":
        template_path = settings.SUMMARY_REVIEW_TEMPLATE_PATH
    else:
        template_path = settings.SUMMARY_TEMPLATE_PATH

    path = Path(template_path)
    # Also try relative to the project root (two levels up from this file)
    if not path.is_absolute() and not path.exists():
        project_root = Path(__file__).resolve().parents[3]
        path = project_root / template_path

    return path.read_text(encoding="utf-8")


_SUMMARY_INSTRUCTION = """\
You are an expert scientific paper analyst. Your task is to create a \
structured Japanese summary of a paper.

## Process

1. **Classify the paper type** by analysing its content:
   - If the paper primarily surveys, reviews, or compares existing work \
across a field (keywords: "survey", "review", "comparative study", \
"overview", "state-of-the-art"), classify it as ``"review"``.
   - Otherwise classify it as ``"regular"``.

2. **Load the template** by calling ``load_summary_template`` with the \
determined paper type (``"review"`` or ``"regular"``).

3. **Fill in the template** with information extracted from the paper:
   - Replace every ``[placeholder]`` in the template with the relevant \
information.
   - The title should be kept in its original language (English).
   - Use the provided ``arxiv_url`` for the URL field.
   - Write all content sections in Japanese.
   - Be thorough and detailed — do not omit important findings, methods, \
or conclusions.
   - When describing methods or results, include specific numbers, metrics, \
and comparisons from the paper.

## Input

You will receive:
- The paper content in Japanese Markdown (translated from the original).
- The arXiv URL of the paper.

## Output

Return the completed summary following the template structure exactly. \
Do not wrap the output in a code fence.
"""


def create_summary_agent(model: str, *, api_key: str | None = None) -> LlmAgent:
    """Create a SummaryAgent that classifies and summarises papers.

    The agent uses a function tool to load the correct template based on
    whether the paper is a regular research paper or a review/survey.

    Args:
        model: The LLM model identifier.
        api_key: Optional caller-supplied Google API key. When provided,
            wraps the model in :class:`ScopedKeyGemini` so the underlying
            ``genai.Client`` is bound to this key (bypasses
            ``GOOGLE_API_KEY`` env). Falls back to env-based auth when
            omitted.

    Returns:
        A configured :class:`LlmAgent` with the template-loading tool.
    """
    llm: str | ScopedKeyGemini = (
        ScopedKeyGemini(model=model, api_key=api_key) if api_key else model
    )
    return LlmAgent(
        name="SummaryAgent",
        model=llm,
        instruction=_SUMMARY_INSTRUCTION,
        description=(
            "Classifies a paper as regular or review/survey, loads the "
            "matching template, and produces a structured Japanese summary."
        ),
        tools=[load_summary_template],
        output_key="summary_ja",
    )
