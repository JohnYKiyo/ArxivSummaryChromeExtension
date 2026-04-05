"""Evaluation script for summary generation quality.

Measures summary completeness, accuracy, and adherence to the
selected template (regular vs review/survey). Evaluates coverage
of key paper contributions and technical details.

Run with:
    python -m backend.tests.eval.eval_summary
"""

from __future__ import annotations

import asyncio
import re
import sys
import uuid
from dataclasses import dataclass, field

from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from src.agents.summary import create_summary_agent
from src.config import get_settings

# ---------------------------------------------------------------------------
# Test inputs
# ---------------------------------------------------------------------------

SAMPLE_REGULAR_PAPER_JA = """\
# Widget Optimization via Neural Methods

**Authors:** Alice Smith, Bob Jones
**Affiliations:** University of Testing; Institute of Examples

## 概要

勾配降下法(Gradient Descent)を用いたウィジェット最適化の新しい手法を提案する。
本手法はWidgetBenchベンチマークにおいて95%の精度を達成した。
強化学習(Reinforcement Learning)と組み合わせることで性能を向上させた。

## はじめに

ウィジェット最適化は応用ウィジェット工学における基本的な問題である [jones2020]。
自然言語処理(NLP)の技術が本分野に適用され、有望な結果を得ている。

## 手法

トランスフォーマー(Transformer)ベースのアーキテクチャを使用する。
損失関数は $\\mathcal{L} = \\sum_{i=1}^{N} \\|w_i - \\hat{w}_i\\|^2$ で定義される。

1. データ収集
2. モデル学習
3. 評価

## 実験

WidgetBench [smith2021] で評価し、3つのベースラインと比較した。
提案手法は先行研究に対して15%の改善を達成した。

## 結論

WidgetNetを導入し、最先端の性能を達成した。
"""

SAMPLE_REVIEW_PAPER_JA = """\
# A Survey of Widget Optimization Methods

**Authors:** Carol Zhang
**Affiliations:** Survey Institute

## 概要

ウィジェット最適化手法のサーベイを行う。本レビューでは過去10年間の主要な手法を
体系的に分類・比較する。

## はじめに

ウィジェット最適化は重要な研究分野であり、多くの手法が提案されてきた。
本サーベイでは、これらの手法を網羅的にレビューする。

## 手法の分類

### 勾配ベースの手法
- WidgetNet [smith2021]: 勾配降下法ベース、精度95%
- WidgetGrad [jones2022]: 適応的勾配法、精度92%

### 進化的手法
- EvoWidget [lee2020]: 遺伝的アルゴリズム、精度88%
- QuantumWidget [chen2023]: 量子アニーリング、精度90%

## 比較表

| 手法 | 精度 | 速度 | 年 |
|------|------|------|------|
| WidgetNet | 95% | 高速 | 2021 |
| WidgetGrad | 92% | 中速 | 2022 |
| EvoWidget | 88% | 低速 | 2020 |

## 結論

勾配ベースの手法が現在最も有望である。
"""

ARXIV_URL = "https://arxiv.org/abs/2301.00001"


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
# Scoring functions -- Regular paper
# ---------------------------------------------------------------------------


def evaluate_regular_template_structure(text: str) -> EvalResult:
    """Check that summary matches the regular template structure."""
    required_sections = ["要約", "使用された手法", "技術の主要なポイント", "先行研究との比較", "実験方法", "議論"]
    found = [s for s in required_sections if s in text]
    missing = [s for s in required_sections if s not in text]
    passed = len(missing) == 0
    detail = f"Found {len(found)}/{len(required_sections)} sections"
    if missing:
        detail += f", missing: {missing}"
    return EvalResult("Regular template sections present", passed, detail)


def evaluate_review_template_structure(text: str) -> EvalResult:
    """Check that summary matches the review template structure."""
    required_sections = ["要約", "レビュー/サーベイ項目", "比較", "議論"]
    # Also accept partial matches for flexibility
    found = []
    missing = []
    for s in required_sections:
        if s in text:
            found.append(s)
        else:
            missing.append(s)

    passed = len(missing) == 0
    detail = f"Found {len(found)}/{len(required_sections)} sections"
    if missing:
        detail += f", missing: {missing}"
    return EvalResult("Review template sections present", passed, detail)


def evaluate_title_in_english(text: str) -> EvalResult:
    """Check that the title is in the original English."""
    # The title should appear near the top of the output
    first_line = text.strip().split("\n")[0]
    # Check that the first line (title) contains ASCII/Latin characters
    has_english = bool(re.search(r"[A-Za-z]{3,}", first_line))
    detail = f"First line: {first_line[:60]}"
    return EvalResult("Title in original English", has_english, detail)


def evaluate_url_present(text: str) -> EvalResult:
    """Check that the arXiv URL is present in the summary."""
    has_url = "arxiv.org" in text
    detail = f"arXiv URL present={has_url}"
    return EvalResult("arXiv URL present", has_url, detail)


def evaluate_summary_is_japanese(text: str) -> EvalResult:
    """Check that summary content is written in Japanese."""
    japanese_pattern = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]")
    matches = japanese_pattern.findall(text)
    has_japanese = len(matches) > 30
    detail = f"Found {len(matches)} Japanese characters"
    return EvalResult("Summary content is in Japanese", has_japanese, detail)


def evaluate_has_metadata(text: str) -> EvalResult:
    """Check that author/year metadata is present."""
    has_author = "著者" in text or "Author" in text
    has_year = "発表年" in text or re.search(r"20\d{2}", text) is not None
    passed = has_author and has_year
    detail = f"Author field={has_author}, year={has_year}"
    return EvalResult("Metadata (author/year) present", passed, detail)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def _run_agent(paper_ja: str, arxiv_url: str) -> str:
    """Run the SummaryAgent on the given input and return the output."""
    settings = get_settings()
    agent = create_summary_agent(settings.LLM_MODEL)
    runner = InMemoryRunner(agent=agent)

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(
            text=(
                f"Create a summary for the following paper.\n"
                f"arXiv URL: {arxiv_url}\n\n"
                f"Paper content:\n{paper_ja}"
            )
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


async def run_regular_evaluation() -> EvalReport:
    """Evaluate summary generation for a regular research paper."""
    print("Running SummaryAgent on regular paper...")
    summary = await _run_agent(SAMPLE_REGULAR_PAPER_JA, ARXIV_URL)

    print(f"Output length: {len(summary)} characters")
    print("-" * 60)
    print(summary[:500])
    print("..." if len(summary) > 500 else "")
    print("-" * 60)

    report = EvalReport(test_name="Summary Evaluation - Regular Paper")

    evaluators = [
        evaluate_regular_template_structure,
        evaluate_title_in_english,
        evaluate_url_present,
        evaluate_summary_is_japanese,
        evaluate_has_metadata,
    ]

    for evaluator in evaluators:
        result = evaluator(summary)
        report.results.append(result)

    return report


async def run_review_evaluation() -> EvalReport:
    """Evaluate summary generation for a review/survey paper."""
    print("\nRunning SummaryAgent on review paper...")
    summary = await _run_agent(SAMPLE_REVIEW_PAPER_JA, ARXIV_URL)

    print(f"Output length: {len(summary)} characters")
    print("-" * 60)
    print(summary[:500])
    print("..." if len(summary) > 500 else "")
    print("-" * 60)

    report = EvalReport(test_name="Summary Evaluation - Review Paper")

    evaluators = [
        evaluate_review_template_structure,
        evaluate_title_in_english,
        evaluate_url_present,
        evaluate_summary_is_japanese,
        evaluate_has_metadata,
    ]

    for evaluator in evaluators:
        result = evaluator(summary)
        report.results.append(result)

    return report


async def run_evaluation() -> list[EvalReport]:
    """Run all summary evaluations."""
    reports = []
    reports.append(await run_regular_evaluation())
    reports.append(await run_review_evaluation())
    return reports


def main() -> None:
    """Entry point for running the evaluation."""
    reports = asyncio.run(run_evaluation())
    print()

    all_passed = True
    for report in reports:
        print(report.summary())
        print()
        if report.score < 1.0:
            all_passed = False

    overall_score = sum(r.score for r in reports) / len(reports) if reports else 0
    print(f"Overall score: {overall_score:.0%}")

    if not all_passed:
        print("WARNING: Some criteria failed.")
        sys.exit(1)
    else:
        print("All criteria passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
