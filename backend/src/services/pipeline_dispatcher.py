"""Pipeline dispatch service.

Abstracts *how* the conversion pipeline is started for a job:

- **Production** — invoke a separate AWS Lambda asynchronously via the
  ``boto3`` Lambda client (the API Lambda has a 29 s timeout, so it cannot
  run the pipeline itself).
- **Local development** — schedule ``run_pipeline()`` as an asyncio task
  inside the same process.

The HTTP layer (``api/routes.py``) only depends on this dispatcher's
interface, not on ``boto3``, ``asyncio``, or the orchestrator module.
This keeps the routes layer free of AWS SDK and pipeline-implementation
concerns (SoC), and makes ``POST /convert`` testable without mocking
either Lambda or the LLM stack (DIP).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

import boto3

if TYPE_CHECKING:
    from src.services.job_manager import JobManager

logger = logging.getLogger(__name__)


class PipelineDispatcher(Protocol):
    """Strategy for starting a conversion pipeline run for a given job.

    Two implementations exist (Lambda invoke / in-process asyncio task);
    callers depend on this protocol rather than either concrete class.
    """

    async def dispatch(
        self,
        job_id: str,
        arxiv_url: str,
        api_key: str | None = None,
    ) -> None:
        """Start the pipeline for ``job_id``. Returns immediately.

        ``api_key`` is the caller-supplied Google API key (Chrome
        extension flow). ``None`` means use the backend's env-based
        credentials (web UI flow).
        """
        ...


class LambdaPipelineDispatcher:
    """Production dispatcher — invokes a separate Lambda asynchronously."""

    def __init__(self, function_name: str) -> None:
        self._function_name = function_name
        self._client = boto3.client("lambda")

    async def dispatch(
        self,
        job_id: str,
        arxiv_url: str,
        api_key: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"job_id": job_id, "arxiv_url": arxiv_url}
        if api_key:
            # Forwarded to the pipeline Lambda's handler. Only set when the
            # caller actually supplied one so legacy callers (web UI) keep
            # using the Lambda's env var.
            payload["api_key"] = api_key
        self._client.invoke(
            FunctionName=self._function_name,
            InvocationType="Event",
            Payload=json.dumps(payload),
        )
        logger.info("Invoked pipeline Lambda for job %s", job_id)


class InProcessPipelineDispatcher:
    """Local-development dispatcher — runs the pipeline as an asyncio task.

    The orchestrator import is deferred to ``dispatch()`` so the heavy
    LLM/agent machinery is loaded only when actually needed.
    """

    def __init__(self, job_manager: JobManager) -> None:
        self._job_manager = job_manager

    async def dispatch(
        self,
        job_id: str,
        arxiv_url: str,
        api_key: str | None = None,
    ) -> None:
        # Deferred import: keeps cold-start light and avoids a circular
        # dependency between services and agents at module load time.
        from src.agents.orchestrator import run_pipeline

        asyncio.create_task(self._run(job_id, arxiv_url, api_key, run_pipeline))

    async def _run(
        self,
        job_id: str,
        arxiv_url: str,
        api_key: str | None,
        run_pipeline,  # noqa: ANN001 — typing the orchestrator coroutine here would force a circular import
    ) -> None:
        try:
            await run_pipeline(
                arxiv_url=arxiv_url,
                job_id=job_id,
                job_manager=self._job_manager,
                api_key=api_key,
            )
        except Exception:
            logger.exception("Pipeline task failed for job %s", job_id)
            job = await self._job_manager.get_job(job_id)
            # Don't clobber a terminal status (cancelled/completed/error)
            # with a generic "Pipeline execution failed" message.
            from src.models.job import TERMINAL_STATUSES

            if job and job.status not in TERMINAL_STATUSES:
                await self._job_manager.set_error(job_id, "Pipeline execution failed")


def build_dispatcher(
    job_manager: JobManager,
    pipeline_lambda_name: str,
) -> PipelineDispatcher:
    """Pick the right dispatcher based on configuration.

    Args:
        job_manager: Used by the in-process dispatcher to record errors.
        pipeline_lambda_name: Name of the pipeline Lambda; empty in local dev.

    Returns:
        A :class:`LambdaPipelineDispatcher` if ``pipeline_lambda_name`` is
        set, otherwise an :class:`InProcessPipelineDispatcher`.
    """
    if pipeline_lambda_name:
        return LambdaPipelineDispatcher(pipeline_lambda_name)
    return InProcessPipelineDispatcher(job_manager)
