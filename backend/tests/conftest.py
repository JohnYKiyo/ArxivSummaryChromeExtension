"""Shared pytest fixtures for the arXiv Translator test suite.

Provides common test fixtures including:
- FastAPI test client
- Mock job data
- Sample TeX content
- Temporary directory management
"""

from __future__ import annotations

import pytest

from src.services.job_manager import JobManager


# ---------------------------------------------------------------------------
# Settings override
# ---------------------------------------------------------------------------

@pytest.fixture()
def settings_override(monkeypatch: pytest.MonkeyPatch):
    """Override settings for tests so a real .env file is not required."""
    env_vars = {
        "GOOGLE_API_KEY": "test-api-key-not-real",
        "LLM_MODEL": "gemini-2.5-pro",
        "AWS_REGION": "us-east-1",
        # Defaults represent local-dev mode (no S3 / no pipeline Lambda),
        # matching the empty-by-default values in .env.example. Tests that
        # need production behaviour should monkeypatch these explicitly.
        "S3_BUCKET_NAME": "",
        "PIPELINE_LAMBDA_NAME": "",
        "COGNITO_USER_POOL_ID": "us-east-1_TestPool",
        "COGNITO_APP_CLIENT_ID": "test-client-id",
        "DYNAMODB_TABLE_NAME": "test-arxiv-translator-jobs",
        "APP_ENV": "test",
        "LOG_LEVEL": "DEBUG",
        "CORS_ORIGINS": "http://localhost:3000",
        "SUMMARY_TEMPLATE_PATH": "docs/summary_template.md",
        "SUMMARY_REVIEW_TEMPLATE_PATH": "docs/summary_review_template.md",
    }
    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    # Clear the lru_cache so Settings is re-created with test env vars
    from src.config import get_settings
    get_settings.cache_clear()

    yield env_vars

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Sample TeX content
# ---------------------------------------------------------------------------

SAMPLE_TEX = r"""\documentclass{article}
\usepackage{graphicx}
\usepackage{amsmath}

\title{A Novel Approach to Widget Optimization}
\author{Alice Smith\thanks{University of Testing} \and Bob Jones\thanks{Institute of Examples}}
\date{2025}

\begin{document}
\maketitle

\begin{abstract}
We present a novel method for optimizing widgets using gradient descent.
Our approach achieves state-of-the-art results on the WidgetBench benchmark.
\end{abstract}

\section{Introduction}
Widget optimization is a fundamental problem in applied widgetry~\cite{jones2020}.
In this paper, we propose WidgetNet, a neural approach to widget tuning.

The loss function is defined as $\mathcal{L} = \sum_{i=1}^{N} \|w_i - \hat{w}_i\|^2$.

\section{Method}
Our method consists of three stages\footnote{Each stage is described in detail below.}:

\begin{enumerate}
\item Pre-processing
\item Neural optimization
\item Post-refinement
\end{enumerate}

\begin{figure}[h]
\centering
\includegraphics[width=0.8\textwidth]{figures/architecture.png}
\caption{The WidgetNet architecture overview.}
\end{figure}

The optimization objective is:
$$\min_{\theta} \mathbb{E}_{x \sim \mathcal{D}} \left[ \mathcal{L}(f_\theta(x), y) \right]$$

\section{Experiments}
We evaluate on WidgetBench~\cite{smith2021} and compare against three baselines.

\section{Conclusion}
We introduced WidgetNet, achieving a 15\% improvement over prior work.

\bibliographystyle{plain}
\bibliography{refs}
\end{document}
"""


@pytest.fixture()
def sample_tex_content() -> str:
    """A short sample TeX document with common elements."""
    return SAMPLE_TEX


# ---------------------------------------------------------------------------
# Sample Markdown outputs
# ---------------------------------------------------------------------------

SAMPLE_MARKDOWN_EN = """\
# A Novel Approach to Widget Optimization

**Authors:** Alice Smith, Bob Jones
**Affiliations:** University of Testing; Institute of Examples

## Abstract

We present a novel method for optimizing widgets using gradient descent.
Our approach achieves state-of-the-art results on the WidgetBench benchmark.

## Introduction

Widget optimization is a fundamental problem in applied widgetry [jones2020].
In this paper, we propose WidgetNet, a neural approach to widget tuning.

The loss function is defined as $\\mathcal{L} = \\sum_{i=1}^{N} \\|w_i - \\hat{w}_i\\|^2$.

## Method

Our method consists of three stages[^1]:

1. Pre-processing
2. Neural optimization
3. Post-refinement

![The WidgetNet architecture overview.](images/figures/architecture.png)

The optimization objective is:

$$\\min_{\\theta} \\mathbb{E}_{x \\sim \\mathcal{D}} \\left[ \\mathcal{L}(f_\\theta(x), y) \\right]$$

## Experiments

We evaluate on WidgetBench [smith2021] and compare against three baselines.

## Conclusion

We introduced WidgetNet, achieving a 15% improvement over prior work.

[^1]: Each stage is described in detail below.
"""


@pytest.fixture()
def sample_markdown_en() -> str:
    """Expected English markdown output from the sample TeX."""
    return SAMPLE_MARKDOWN_EN


SAMPLE_MARKDOWN_JA = """\
# A Novel Approach to Widget Optimization

**Authors:** Alice Smith, Bob Jones
**Affiliations:** University of Testing; Institute of Examples

## 概要

勾配降下法(Gradient Descent)を用いたウィジェット最適化の新しい手法を提案する。
本手法はWidgetBenchベンチマークにおいて最先端の結果を達成した。

## はじめに

ウィジェット最適化(Widget Optimization)は応用ウィジェット工学における基本的な問題である [jones2020]。
本論文では、ウィジェット調整へのニューラルアプローチであるWidgetNetを提案する。

損失関数は $\\mathcal{L} = \\sum_{i=1}^{N} \\|w_i - \\hat{w}_i\\|^2$ で定義される。

## 手法

本手法は3つの段階から構成される[^1]:

1. 前処理
2. ニューラル最適化
3. 後処理による改善

![WidgetNetのアーキテクチャ概要](images/figures/architecture.png)

最適化目的関数は以下の通りである:

$$\\min_{\\theta} \\mathbb{E}_{x \\sim \\mathcal{D}} \\left[ \\mathcal{L}(f_\\theta(x), y) \\right]$$

## 実験

WidgetBench [smith2021] で評価し、3つのベースラインと比較する。

## 結論

WidgetNetを導入し、先行研究に対して15%の改善を達成した。

[^1]: 各段階の詳細を以下に説明する。
"""


@pytest.fixture()
def sample_markdown_ja() -> str:
    """Sample Japanese markdown translation."""
    return SAMPLE_MARKDOWN_JA


# ---------------------------------------------------------------------------
# Service fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def job_manager() -> JobManager:
    """Return a fresh JobManager instance for testing.

    Uses DynamoDB Local if available, otherwise tests requiring
    this fixture should be run with a local DynamoDB instance.
    """
    return JobManager(
        table_name="test-arxiv-translator-jobs",
        endpoint_url="http://localhost:8100",
    )
