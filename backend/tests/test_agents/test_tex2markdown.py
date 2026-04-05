"""Tests for the Tex2MarkdownAgent.

Verifies TeX to Markdown conversion preserves document structure,
math expressions, images, footnotes, and citations.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.tex2markdown import create_tex2markdown_agent


# ---------------------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------------------


class TestCreateTex2MarkdownAgent:
    """Verify create_tex2markdown_agent returns a properly configured agent."""

    def test_returns_llm_agent(self) -> None:
        agent = create_tex2markdown_agent("gemini-2.5-pro-preview-05-06")
        # google.adk.agents.LlmAgent should be returned
        assert agent is not None
        assert agent.name == "Tex2MarkdownAgent"

    def test_agent_model(self) -> None:
        model_name = "gemini-2.5-pro-preview-05-06"
        agent = create_tex2markdown_agent(model_name)
        assert agent.model == model_name

    def test_agent_output_key(self) -> None:
        agent = create_tex2markdown_agent("gemini-2.5-pro-preview-05-06")
        assert agent.output_key == "markdown_en"

    def test_agent_has_description(self) -> None:
        agent = create_tex2markdown_agent("gemini-2.5-pro-preview-05-06")
        assert agent.description is not None
        assert len(agent.description) > 0


# ---------------------------------------------------------------------------
# Instruction content verification
# ---------------------------------------------------------------------------


class TestTex2MarkdownInstruction:
    """Verify the agent instruction contains key conversion rules."""

    def _get_instruction(self) -> str:
        agent = create_tex2markdown_agent("gemini-2.5-pro-preview-05-06")
        return agent.instruction

    def test_instruction_mentions_section_conversion(self) -> None:
        instruction = self._get_instruction()
        assert r"\section" in instruction
        assert "# " in instruction or "``# ...``" in instruction

    def test_instruction_mentions_image_conversion(self) -> None:
        instruction = self._get_instruction()
        assert r"\includegraphics" in instruction
        assert "![" in instruction

    def test_instruction_mentions_footnote_handling(self) -> None:
        instruction = self._get_instruction()
        assert r"\footnote" in instruction
        assert "[^" in instruction

    def test_instruction_mentions_math_preservation(self) -> None:
        instruction = self._get_instruction()
        assert "$...$" in instruction or "inline math" in instruction.lower()
        assert "$$...$$" in instruction or "display math" in instruction.lower()

    def test_instruction_mentions_citation_handling(self) -> None:
        instruction = self._get_instruction()
        assert r"\cite" in instruction

    def test_instruction_mentions_bold_italic(self) -> None:
        instruction = self._get_instruction()
        assert r"\textbf" in instruction
        assert r"\textit" in instruction

    def test_instruction_no_truncation(self) -> None:
        """Instruction should tell agent to process the entire document."""
        instruction = self._get_instruction()
        assert "entire" in instruction.lower() or "truncate" in instruction.lower()
