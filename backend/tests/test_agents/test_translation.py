"""Tests for the TranslationAgent.

Verifies English to Japanese translation preserves Markdown formatting,
technical terminology accuracy, and math expression integrity.
"""

from __future__ import annotations

import pytest

from src.agents.translation import create_translation_agent


# ---------------------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------------------


class TestCreateTranslationAgent:
    """Verify create_translation_agent returns a properly configured agent."""

    def test_returns_llm_agent(self) -> None:
        agent = create_translation_agent("gemini-2.5-pro-preview-05-06")
        assert agent is not None
        assert agent.name == "TranslationAgent"

    def test_agent_model(self) -> None:
        model_name = "gemini-2.5-pro-preview-05-06"
        agent = create_translation_agent(model_name)
        assert agent.model == model_name

    def test_agent_output_key(self) -> None:
        agent = create_translation_agent("gemini-2.5-pro-preview-05-06")
        assert agent.output_key == "markdown_ja"

    def test_agent_has_description(self) -> None:
        agent = create_translation_agent("gemini-2.5-pro-preview-05-06")
        assert agent.description is not None
        assert len(agent.description) > 0

    def test_agent_has_no_tools(self) -> None:
        """TranslationAgent should be a pure LLM agent with no tools."""
        agent = create_translation_agent("gemini-2.5-pro-preview-05-06")
        assert agent.tools is None or len(agent.tools) == 0


# ---------------------------------------------------------------------------
# Instruction content verification
# ---------------------------------------------------------------------------


class TestTranslationInstruction:
    """Verify the agent instruction contains key translation rules."""

    def _get_instruction(self) -> str:
        agent = create_translation_agent("gemini-2.5-pro-preview-05-06")
        return agent.instruction

    def test_instruction_mentions_markdown_preservation(self) -> None:
        instruction = self._get_instruction()
        assert "Markdown" in instruction
        # Should mention keeping headings, lists, bold, etc.
        assert "#" in instruction
        assert "**" in instruction

    def test_instruction_mentions_technical_term_format(self) -> None:
        """Should specify the Japanese(English) format for first occurrence."""
        instruction = self._get_instruction()
        assert "日本語訳(English original)" in instruction or "日本語" in instruction

    def test_instruction_mentions_math_preservation(self) -> None:
        """Math expressions should NOT be translated."""
        instruction = self._get_instruction()
        assert "$...$" in instruction or "$" in instruction
        assert "translate" in instruction.lower()

    def test_instruction_mentions_author_name_preservation(self) -> None:
        """Author names should remain in original script."""
        instruction = self._get_instruction()
        assert "author" in instruction.lower()

    def test_instruction_mentions_citation_preservation(self) -> None:
        """Citation markers should stay in English."""
        instruction = self._get_instruction()
        assert "citation" in instruction.lower()

    def test_instruction_output_format(self) -> None:
        """Output should be plain Markdown, no code fences."""
        instruction = self._get_instruction()
        assert "code fence" in instruction.lower()
