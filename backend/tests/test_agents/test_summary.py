"""Tests for the SummaryAgent.

Verifies paper type classification (regular vs review/survey)
and correct template selection for summary generation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.summary import create_summary_agent, load_summary_template


# ---------------------------------------------------------------------------
# load_summary_template
# ---------------------------------------------------------------------------


class TestLoadSummaryTemplate:
    """Verify template loading for regular and review papers."""

    def test_load_regular_template(self, settings_override: dict) -> None:
        """'regular' should load the standard summary template."""
        template = load_summary_template("regular")
        assert "## 要約" in template
        assert "## 使用された手法" in template
        assert "## 技術の主要なポイント" in template
        assert "## 先行研究との比較" in template
        assert "## 実験方法" in template
        assert "## 議論" in template

    def test_load_review_template(self, settings_override: dict) -> None:
        """'review' should load the review/survey summary template."""
        template = load_summary_template("review")
        assert "## 要約" in template
        assert "## レビュー/サーベイ項目" in template
        assert "## 比較" in template
        assert "## 議論" in template

    def test_review_template_case_insensitive(self, settings_override: dict) -> None:
        """Paper type matching should be case-insensitive."""
        template_lower = load_summary_template("review")
        template_upper = load_summary_template("REVIEW")
        template_mixed = load_summary_template("Review")
        assert template_lower == template_upper == template_mixed

    def test_review_template_with_whitespace(self, settings_override: dict) -> None:
        """Paper type matching should strip whitespace."""
        template = load_summary_template("  review  ")
        assert "## レビュー/サーベイ項目" in template

    def test_regular_template_has_title_placeholder(self, settings_override: dict) -> None:
        """Template should contain a title placeholder."""
        template = load_summary_template("regular")
        assert "タイトル" in template or "記入" in template

    def test_review_template_differs_from_regular(self, settings_override: dict) -> None:
        """The two templates should have different content."""
        regular = load_summary_template("regular")
        review = load_summary_template("review")
        assert regular != review

    def test_unknown_type_falls_back_to_regular(self, settings_override: dict) -> None:
        """Any non-'review' value should load the regular template."""
        template = load_summary_template("something_else")
        assert "## 使用された手法" in template

    def test_missing_template_file_raises(self, settings_override: dict, monkeypatch) -> None:
        """FileNotFoundError should propagate when the template is missing."""
        monkeypatch.setenv("SUMMARY_TEMPLATE_PATH", "/nonexistent/template.md")
        # Clear cache so settings reload
        from src.config import get_settings
        get_settings.cache_clear()

        with pytest.raises(FileNotFoundError):
            load_summary_template("regular")

        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# create_summary_agent
# ---------------------------------------------------------------------------


class TestCreateSummaryAgent:
    """Verify create_summary_agent returns a properly configured agent."""

    def test_returns_llm_agent(self) -> None:
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        assert agent is not None
        assert agent.name == "SummaryAgent"

    def test_agent_model(self) -> None:
        model_name = "gemini-2.5-pro-preview-05-06"
        agent = create_summary_agent(model_name)
        assert agent.model == model_name

    def test_agent_output_key(self) -> None:
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        assert agent.output_key == "summary_ja"

    def test_agent_has_tools(self) -> None:
        """SummaryAgent should have the load_summary_template tool."""
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        assert agent.tools is not None
        assert len(agent.tools) > 0

    def test_agent_tool_is_load_template(self) -> None:
        """The tool should be load_summary_template."""
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        tool_names = []
        for tool in agent.tools:
            if callable(tool):
                tool_names.append(tool.__name__)
        assert "load_summary_template" in tool_names

    def test_agent_instruction_mentions_classify(self) -> None:
        """Instruction should mention paper classification."""
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        instruction = agent.instruction
        assert "classify" in instruction.lower() or "Classify" in instruction

    def test_agent_instruction_mentions_template(self) -> None:
        """Instruction should reference loading the template."""
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        instruction = agent.instruction
        assert "load_summary_template" in instruction

    def test_agent_instruction_mentions_english_title(self) -> None:
        """Title should be kept in original language (English)."""
        agent = create_summary_agent("gemini-2.5-pro-preview-05-06")
        instruction = agent.instruction
        assert "English" in instruction
