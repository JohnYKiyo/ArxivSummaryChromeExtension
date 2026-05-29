"""Tests for mid-LLM-call cancellation inside ``_run_single_agent``.

ADK runs each LLM call as one un-interruptible ``await`` (StreamingMode.NONE),
so cancellation has to race the consumption task against a cancel-flag poller
and ``cancel()`` the task mid-call. These tests drive the *real*
``_run_single_agent`` (not the patched version the run_pipeline cancellation
suite uses) against a fake, slow ADK runner.

Mocking notes:
- ``InMemoryRunner`` is patched so construction returns a configured mock.
- ``run_async`` is a SYNC function returning an async generator, so it is mocked
  with ``MagicMock(return_value=<async-gen>)`` — an ``AsyncMock`` would make it a
  coroutine and break ``async for``.
- ``_run_single_agent`` does not read settings (the agent is a ``MagicMock`` and
  the runner is patched), so ``settings_override`` is not needed.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.orchestrator import JobCancelledError, _run_single_agent


def _text_event(text: str) -> MagicMock:
    """Build a fake ADK event carrying a single non-thought text part."""
    part = MagicMock()
    part.thought = False
    part.text = text
    event = MagicMock()
    event.content.parts = [part]
    return event


async def _one_shot_stream(text: str) -> Any:
    """An async generator that yields one text event and completes."""
    yield _text_event(text)


async def _boom_stream() -> Any:
    """An async generator that raises before yielding."""
    raise RuntimeError("genai exploded")
    yield  # pragma: no cover — present only to make this a generator


def _make_runner(stream: Any) -> MagicMock:
    """Build a fake InMemoryRunner whose ``run_async`` returns ``stream``."""
    runner = MagicMock(name="FakeInMemoryRunner")
    runner.app_name = "fake-app"
    runner.session_service.create_session = AsyncMock()
    # run_async is sync and returns an async generator (NOT a coroutine).
    runner.run_async = MagicMock(return_value=stream)
    return runner


class _SlowRunnerFactory:
    """Factory whose runner's ``run_async`` hangs on an ``await``.

    ``started`` / ``cancelled`` let the test assert the consumption task was
    actually entered and then interrupted mid-call.
    """

    def __init__(self) -> None:
        self.started = False
        self.cancelled = False

    def __call__(self, *_args: Any, **_kwargs: Any) -> MagicMock:
        async def _slow_stream() -> Any:
            self.started = True
            try:
                await asyncio.sleep(3600)  # the "in-flight LLM await"
                yield _text_event("never reached")
            except asyncio.CancelledError:
                self.cancelled = True
                raise

        return _make_runner(_slow_stream())


class TestRunSingleAgentCancel:
    """``_run_single_agent`` aborts the in-flight call when cancel is requested."""

    async def test_cancel_midcall_raises_and_cancels_consumption(self) -> None:
        """Cancel observed during the LLM await → JobCancelledError + task cancelled."""
        factory = _SlowRunnerFactory()
        mgr = MagicMock(name="MockJobManager")
        # First poll False, then True → triggers the mid-call cancel.
        mgr.is_cancel_requested = AsyncMock(side_effect=[False, True, True, True])

        with patch("src.agents.orchestrator.InMemoryRunner", factory), pytest.raises(JobCancelledError):
            await _run_single_agent(
                agent=MagicMock(),
                user_message="hello",
                job_manager=mgr,
                job_id="job-x",
                poll_interval=0.01,
            )

        assert factory.started is True  # the LLM consumption began
        assert factory.cancelled is True  # ...and was cancelled mid-await
        assert mgr.is_cancel_requested.await_count >= 1

    async def test_completes_before_cancel_returns_text(self) -> None:
        """Agent finishes first; no JobCancelledError; full text returned."""
        runner = _make_runner(_one_shot_stream("translated text"))
        mgr = MagicMock(name="MockJobManager")
        # Never cancels — the watcher loops until we cancel it.
        mgr.is_cancel_requested = AsyncMock(return_value=False)

        with patch("src.agents.orchestrator.InMemoryRunner", return_value=runner):
            result = await _run_single_agent(
                agent=MagicMock(),
                user_message="hello",
                job_manager=mgr,
                job_id="job-ok",
                poll_interval=0.01,
            )

        assert result == "translated text"

    async def test_no_job_context_skips_watcher(self) -> None:
        """Without job_manager/job_id, behaves like the original (no polling)."""
        runner = _make_runner(_one_shot_stream("x"))

        with patch("src.agents.orchestrator.InMemoryRunner", return_value=runner):
            result = await _run_single_agent(agent=MagicMock(), user_message="hello")

        assert result == "x"

    async def test_llm_error_propagates(self) -> None:
        """An error from the stream is re-raised via run_task.result() (→ set_error)."""
        runner = _make_runner(_boom_stream())
        mgr = MagicMock(name="MockJobManager")
        mgr.is_cancel_requested = AsyncMock(return_value=False)

        with (
            patch("src.agents.orchestrator.InMemoryRunner", return_value=runner),
            pytest.raises(RuntimeError, match="genai exploded"),
        ):
            await _run_single_agent(
                agent=MagicMock(),
                user_message="hello",
                job_manager=mgr,
                job_id="job-err",
                poll_interval=0.01,
            )
