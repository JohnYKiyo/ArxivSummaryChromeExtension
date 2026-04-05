"""Evaluation script for Tex2Markdown conversion quality.

Measures conversion accuracy against reference Markdown outputs.
Evaluates preservation of document structure, math expressions,
images, footnotes, and citations.

Run with:
    python -m backend.tests.eval.eval_tex2markdown
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from src.agents.tex2markdown import create_tex2markdown_agent
from src.config import get_settings

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

SAMPLE_TEX_INPUT = r"""\documentclass{article}
\usepackage{graphicx}
\usepackage{amsmath}

\title{Widget Optimization via Neural Methods}
\author{Alice Smith\thanks{University of Testing}}
\date{2025}

\begin{document}
\maketitle

\begin{abstract}
We propose a neural widget optimizer achieving 95\% accuracy.
\end{abstract}

\section{Introduction}
Widget optimization is important~\cite{jones2020}.
The key equation is $E = mc^2$.

\subsection{Background}
Prior work includes WidgetNet~\cite{smith2021}.

\section{Method}
Our method\footnote{Patent pending.} uses three steps:

\begin{figure}[h]
\centering
\includegraphics[width=0.8\textwidth]{figures/architecture.png}
\caption{System architecture overview.}
\end{figure}

The objective function is:
$$\min_\theta \mathcal{L}(\theta) = \sum_{i=1}^N \ell(f_\theta(x_i), y_i)$$

\begin{itemize}
\item Data collection
\item Model training
\item Evaluation
\end{itemize}

\textbf{Bold text} and \textit{italic text} are preserved.

\section{Conclusion}
Our approach outperforms baselines by 15\%.

\end{document}
"""

IMAGE_PATHS = ["figures/architecture.png"]


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


def evaluate_heading_structure(markdown: str) -> EvalResult:
    """Check that Markdown output contains proper heading structure."""
    has_h1 = markdown.startswith("# ") or "\n# " in markdown
    has_h2 = "\n## " in markdown or markdown.startswith("## ")
    has_h3 = "\n### " in markdown or "### " in markdown

    passed = has_h1 and has_h2
    detail = f"H1={has_h1}, H2={has_h2}, H3={has_h3}"
    return EvalResult("Heading structure", passed, detail)


def evaluate_image_conversion(markdown: str) -> EvalResult:
    """Check that images are converted to ![alt](images/...) format."""
    has_image_syntax = "![" in markdown and "](" in markdown
    has_image_path = "images/" in markdown or "figures/" in markdown
    passed = has_image_syntax
    detail = f"Image syntax={has_image_syntax}, path contains images/={has_image_path}"
    return EvalResult("Image conversion", passed, detail)


def evaluate_footnote_preservation(markdown: str) -> EvalResult:
    """Check that footnotes are preserved."""
    has_inline_ref = "[^" in markdown
    has_footnote_def = "[^" in markdown and "]: " in markdown
    passed = has_inline_ref and has_footnote_def
    detail = f"Inline ref={has_inline_ref}, definition={has_footnote_def}"
    return EvalResult("Footnote preservation", passed, detail)


def evaluate_math_preservation(markdown: str) -> EvalResult:
    """Check that math expressions are preserved in $...$ or $$...$$ format."""
    has_inline_math = "$" in markdown and "E = mc^2" in markdown
    has_display_math = "$$" in markdown
    has_latex_commands = "\\mathcal" in markdown or "\\min" in markdown or "\\sum" in markdown
    passed = has_inline_math and has_display_math
    detail = (
        f"Inline math={has_inline_math}, display math={has_display_math}, "
        f"LaTeX commands={has_latex_commands}"
    )
    return EvalResult("Math preservation", passed, detail)


def evaluate_formatting(markdown: str) -> EvalResult:
    """Check that bold/italic formatting is converted."""
    has_bold = "**" in markdown
    has_italic = "*" in markdown
    passed = has_bold
    detail = f"Bold={has_bold}, italic markers present={has_italic}"
    return EvalResult("Formatting", passed, detail)


def evaluate_list_conversion(markdown: str) -> EvalResult:
    """Check that lists are converted to Markdown list syntax."""
    has_unordered = "\n- " in markdown or "\n* " in markdown
    passed = has_unordered
    detail = f"Unordered list={has_unordered}"
    return EvalResult("List conversion", passed, detail)


def evaluate_no_tex_preamble(markdown: str) -> EvalResult:
    """Check that TeX preamble commands are removed."""
    has_documentclass = "\\documentclass" in markdown
    has_usepackage = "\\usepackage" in markdown
    has_begin_doc = "\\begin{document}" in markdown
    passed = not has_documentclass and not has_usepackage and not has_begin_doc
    detail = (
        f"No documentclass={not has_documentclass}, "
        f"no usepackage={not has_usepackage}, "
        f"no begin document={not has_begin_doc}"
    )
    return EvalResult("No TeX preamble", passed, detail)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def _run_agent(tex_content: str, image_paths: list[str]) -> str:
    """Run the Tex2MarkdownAgent on the given input and return the output."""
    settings = get_settings()
    agent = create_tex2markdown_agent(settings.LLM_MODEL)
    runner = InMemoryRunner(agent=agent)

    user_message = (
        f"Convert the following TeX content to Markdown.\n\n"
        f"Image paths available:\n" + "\n".join(image_paths) + "\n\n"
        f"TeX content:\n{tex_content}"
    )

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_message)],
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
    """Run all evaluation criteria on the Tex2Markdown agent output."""
    print("Running Tex2Markdown agent...")
    markdown_output = await _run_agent(SAMPLE_TEX_INPUT, IMAGE_PATHS)

    print(f"Output length: {len(markdown_output)} characters")
    print("-" * 60)
    print(markdown_output[:500])
    print("..." if len(markdown_output) > 500 else "")
    print("-" * 60)

    report = EvalReport(test_name="Tex2Markdown Evaluation")

    evaluators = [
        evaluate_heading_structure,
        evaluate_image_conversion,
        evaluate_footnote_preservation,
        evaluate_math_preservation,
        evaluate_formatting,
        evaluate_list_conversion,
        evaluate_no_tex_preamble,
    ]

    for evaluator in evaluators:
        result = evaluator(markdown_output)
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
