"""Evaluation script for translation quality.

Measures English-to-Japanese translation accuracy and fluency.
Evaluates technical terminology consistency, Markdown structure
preservation, and overall readability.

Run with:
    python -m backend.tests.eval.eval_translation
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from src.agents.translation import create_translation_agent
from src.config import get_settings

# ---------------------------------------------------------------------------
# Test input
# ---------------------------------------------------------------------------

SAMPLE_MARKDOWN_EN = """\
# Widget Optimization via Neural Methods

**Authors:** Alice Smith, Bob Jones
**Affiliations:** University of Testing; Institute of Examples

## Abstract

We propose a novel neural widget optimizer that achieves 95% accuracy
on the WidgetBench benchmark. Our approach combines gradient descent
with reinforcement learning for improved performance.

## Introduction

Widget optimization is a fundamental problem in applied widgetry [jones2020].
Natural Language Processing (NLP) techniques have recently been applied to
this domain with promising results.

The key equation is $E = mc^2$.

## Method

Our method uses a transformer-based architecture[^1]:

![System architecture](images/architecture.png)

The loss function is:

$$\\mathcal{L} = \\sum_{i=1}^{N} \\|w_i - \\hat{w}_i\\|^2$$

- **Step 1:** Data collection
- **Step 2:** Model training
- **Step 3:** Evaluation

## Conclusion

We demonstrated a 15% improvement over prior work [smith2021].

[^1]: Based on the attention mechanism.
"""


@dataclass
class EvalResult:
    """Result of a single evaluation criterion."""

    criterion: str
    passed: bool
    detail: str = ""


@dataclass
class EvalReport:
    """Aggregated evaluation report for a test case."""

    test_name: str
    results: list[EvalResult] = field(default_factory=list)

    @property
    def score(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.passed) / len(self.results)

    def summary(self) -> str:
        lines = [f"=== {self.test_name} (score: {self.score:.0%}) ==="]
        for r in self.results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{status}] {r.criterion}: {r.detail}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoring functions
# ---------------------------------------------------------------------------


def evaluate_is_japanese(text: str) -> EvalResult:
    """Check that the output contains Japanese characters (hiragana/katakana/kanji)."""
    # Look for hiragana, katakana, or CJK unified ideographs
    japanese_pattern = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]")
    matches = japanese_pattern.findall(text)
    has_japanese = len(matches) > 20  # Expect substantial Japanese content
    detail = f"Found {len(matches)} Japanese characters"
    return EvalResult("Output is in Japanese", has_japanese, detail)


def evaluate_markdown_structure(text: str) -> EvalResult:
    """Check that Markdown formatting structures are preserved."""
    has_h1 = text.startswith("# ") or "\n# " in text
    has_h2 = "\n## " in text or text.startswith("## ")
    has_bold = "**" in text
    has_list = "\n- " in text or "\n* " in text
    has_image = "![" in text and "](" in text
    has_footnote = "[^" in text

    all_preserved = has_h1 and has_h2 and has_bold
    detail = (
        f"H1={has_h1}, H2={has_h2}, bold={has_bold}, "
        f"list={has_list}, image={has_image}, footnote={has_footnote}"
    )
    return EvalResult("Markdown structure preserved", all_preserved, detail)


def evaluate_technical_terms(text: str) -> EvalResult:
    """Check that technical terms use the Japanese(English) format."""
    # Look for patterns like 日本語訳(English) -- parenthesized English after Japanese
    term_pattern = re.compile(
        r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]+"
        r"\s*[\(（]"
        r"[A-Za-z\s]+"
        r"[\)）]"
    )
    matches = term_pattern.findall(text)
    has_terms = len(matches) >= 1
    detail = f"Found {len(matches)} term(s) in Japanese(English) format"
    if matches:
        detail += f", e.g.: {matches[0][:40]}"
    return EvalResult("Technical terms in Japanese(English) format", has_terms, detail)


def evaluate_math_unchanged(text: str) -> EvalResult:
    """Check that math expressions are left unchanged (not translated)."""
    has_inline_math = "$E = mc^2$" in text
    has_display_math = "$$" in text
    has_latex = "\\mathcal" in text or "\\sum" in text
    passed = has_inline_math and has_display_math
    detail = (
        f"Inline math preserved={has_inline_math}, "
        f"display math present={has_display_math}, "
        f"LaTeX commands={has_latex}"
    )
    return EvalResult("Math expressions unchanged", passed, detail)


def evaluate_author_names_preserved(text: str) -> EvalResult:
    """Check that author names remain in Latin script."""
    has_alice = "Alice" in text
    has_bob = "Bob" in text or "Smith" in text
    passed = has_alice or has_bob
    detail = f"Alice found={has_alice}, Bob/Smith found={has_bob}"
    return EvalResult("Author names in original script", passed, detail)


def evaluate_citation_preserved(text: str) -> EvalResult:
    """Check that citation markers remain in English."""
    has_citation = "[jones2020]" in text or "[smith2021]" in text
    detail = f"Citations preserved={has_citation}"
    return EvalResult("Citations preserved", has_citation, detail)


def evaluate_image_ref_preserved(text: str) -> EvalResult:
    """Check that image references remain intact."""
    has_image = "images/architecture.png" in text
    detail = f"Image path preserved={has_image}"
    return EvalResult("Image references preserved", has_image, detail)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def _run_agent(markdown_en: str) -> str:
    """Run the TranslationAgent on the given input and return the output."""
    settings = get_settings()
    agent = create_translation_agent(settings.LLM_MODEL)
    runner = InMemoryRunner(agent=agent)

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(
            text=f"Translate the following English Markdown to Japanese:\n\n{markdown_en}"
        )],
    )

    final_text = ""
    async for event in runner.run_async(
        user_id="eval",
        session_id=str(uuid.uuid4()),
        new_message=content,
    ):
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text = part.text

    return final_text


async def run_evaluation() -> EvalReport:
    """Run all evaluation criteria on the TranslationAgent output."""
    print("Running TranslationAgent...")
    translated_output = await _run_agent(SAMPLE_MARKDOWN_EN)

    print(f"Output length: {len(translated_output)} characters")
    print("-" * 60)
    print(translated_output[:500])
    print("..." if len(translated_output) > 500 else "")
    print("-" * 60)

    report = EvalReport(test_name="Translation Evaluation")

    evaluators = [
        evaluate_is_japanese,
        evaluate_markdown_structure,
        evaluate_technical_terms,
        evaluate_math_unchanged,
        evaluate_author_names_preserved,
        evaluate_citation_preserved,
        evaluate_image_ref_preserved,
    ]

    for evaluator in evaluators:
        result = evaluator(translated_output)
        report.results.append(result)

    return report


def main() -> None:
    """Entry point for running the evaluation."""
    report = asyncio.run(run_evaluation())
    print()
    print(report.summary())
    print()

    if report.score < 1.0:
        print(f"WARNING: Score {report.score:.0%} is below 100%.")
        sys.exit(1)
    else:
        print("All criteria passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
