"""Tests for cooperative cancellation in :func:`run_pipeline`.

The pipeline checks ``JobManager.is_cancel_requested`` at every stage
boundary. When the flag is set, it must bail out cleanly: no further
``update_progress`` writes, no LLM calls past the checkpoint, and the
job transitions to the CANCELLED terminal status.

Mocks the LLM-dependent helpers (``_run_single_agent``) and the
network/disk-dependent ``fetch_arxiv_paper`` / ``tex_to_markdown`` so the
test runs entirely in-process.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.orchestrator import run_pipeline
from src.models.job import JobStatus


def _make_job_manager(cancel_requested_sequence: list[bool]) -> MagicMock:
    """Build a JobManager mock that flips ``is_cancel_requested`` across calls.

    ``cancel_requested_sequence`` is consumed in order — useful for asserting
    "cancelled at stage N" by returning False for N falses then True.
    """
    mgr = MagicMock(name="MockJobManager")
    mgr.update_progress = AsyncMock()
    mgr.set_cancelled = AsyncMock()
    mgr.set_error = AsyncMock()
    mgr.set_result = AsyncMock()
    mgr.is_cancel_requested = AsyncMock(side_effect=cancel_requested_sequence)
    return mgr


@pytest.mark.usefixtures("settings_override")
class TestPipelineCancellation:
    """Run-pipeline aborts cleanly when cancellation is observed."""

    async def test_cancel_before_first_stage(self) -> None:
        """Cancel observed at the very first checkpoint — no work done."""
        mgr = _make_job_manager([True])

        # fetch_arxiv_paper must NOT be called if cancel is seen first.
        with (
            patch("src.agents.orchestrator.fetch_arxiv_paper") as mock_fetch,
            patch("src.agents.orchestrator.tex_to_markdown") as mock_tex2md,
            patch("src.agents.orchestrator._run_single_agent") as mock_run_agent,
        ):
            await run_pipeline(
                arxiv_url="https://arxiv.org/abs/2301.00001",
                job_id="job-cancel-0",
                job_manager=mgr,
            )

        mock_fetch.assert_not_called()
        mock_tex2md.assert_not_called()
        mock_run_agent.assert_not_called()
        mgr.set_cancelled.assert_awaited_once_with("job-cancel-0")
        mgr.set_error.assert_not_awaited()
        mgr.set_result.assert_not_awaited()

    async def test_cancel_before_translation_skips_llm_stages(
        self, tmp_path: Any
    ) -> None:
        """Cancel observed after markdown stage — translation never runs."""
        # First two checkpoints return False, third (before translation) True.
        mgr = _make_job_manager([False, False, True])

        fake_paper = MagicMock()
        fake_paper.work_dir = tmp_path
        fake_paper.content = r"\documentclass{article}\begin{document}x\end{document}"
        fake_paper.images = []
        fake_paper.arxiv_id = "2301.00001"

        with (
            patch(
                "src.agents.orchestrator.fetch_arxiv_paper", return_value=fake_paper
            ),
            patch(
                "src.agents.orchestrator.tex_to_markdown", return_value="# x"
            ),
            patch("src.agents.orchestrator._run_single_agent") as mock_run_agent,
            patch("src.agents.orchestrator.create_zip_package") as mock_zip,
        ):
            await run_pipeline(
                arxiv_url="https://arxiv.org/abs/2301.00001",
                job_id="job-cancel-1",
                job_manager=mgr,
            )

        # The two pre-translation stages did run; translation/summary/packaging did not.
        mock_run_agent.assert_not_called()
        mock_zip.assert_not_called()
        mgr.set_cancelled.assert_awaited_once_with("job-cancel-1")
        mgr.set_result.assert_not_awaited()

    async def test_no_cancel_runs_full_pipeline(self, tmp_path: Any) -> None:
        """When ``is_cancel_requested`` always returns False, the pipeline completes."""
        mgr = _make_job_manager([False] * 5)

        fake_paper = MagicMock()
        fake_paper.work_dir = tmp_path
        fake_paper.content = r"\documentclass{article}\begin{document}x\end{document}"
        fake_paper.images = []
        fake_paper.arxiv_id = "2301.00001"

        with (
            patch(
                "src.agents.orchestrator.fetch_arxiv_paper", return_value=fake_paper
            ),
            patch(
                "src.agents.orchestrator.tex_to_markdown", return_value="# x"
            ),
            patch(
                "src.agents.orchestrator._run_single_agent",
                AsyncMock(side_effect=["# x (ja)", "summary"]),
            ),
            patch(
                "src.agents.orchestrator.create_translation_agent"
            ),
            patch(
                "src.agents.orchestrator.create_summary_agent"
            ),
            patch(
                "src.agents.orchestrator.create_zip_package",
                return_value=tmp_path / "out.zip",
            ),
        ):
            results = await run_pipeline(
                arxiv_url="https://arxiv.org/abs/2301.00001",
                job_id="job-ok",
                job_manager=mgr,
            )

        mgr.set_cancelled.assert_not_awaited()
        mgr.set_error.assert_not_awaited()
        mgr.set_result.assert_awaited_once()
        assert results["markdown_en"] == "# x"
        assert results["markdown_ja"] == "# x (ja)"
        assert results["summary_ja"] == "summary"

    async def test_cancel_does_not_call_set_error(self) -> None:
        """Cancellation is not an error — ``set_error`` must not be touched."""
        mgr = _make_job_manager([True])

        with (
            patch("src.agents.orchestrator.fetch_arxiv_paper"),
            patch("src.agents.orchestrator.tex_to_markdown"),
            patch("src.agents.orchestrator._run_single_agent"),
        ):
            await run_pipeline(
                arxiv_url="https://arxiv.org/abs/2301.00001",
                job_id="job-cancel-2",
                job_manager=mgr,
            )

        mgr.set_error.assert_not_awaited()
        assert mgr.set_cancelled.await_count == 1


def test_terminal_statuses_set_includes_cancelled() -> None:
    """Cancelled must be considered terminal so dispatcher won't clobber it."""
    from src.models.job import TERMINAL_STATUSES

    assert JobStatus.CANCELLED in TERMINAL_STATUSES
    assert JobStatus.COMPLETED in TERMINAL_STATUSES
    assert JobStatus.ERROR in TERMINAL_STATUSES
