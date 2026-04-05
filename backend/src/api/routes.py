"""API route definitions.

Defines the following endpoints:
- POST /api/v1/convert - Create a new conversion job
- GET /api/v1/jobs/{job_id}/stream - SSE progress stream
- GET /api/v1/jobs/{job_id}/download - Download ZIP result
- GET /api/v1/health - Health check
"""

import asyncio
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator
from sse_starlette.sse import EventSourceResponse

from src.api.auth import get_current_user
from src.services.job_manager import JobManager, JobStatus
from src.services.sse import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service instances (set during app startup via init_routes)
# ---------------------------------------------------------------------------

_job_manager: JobManager | None = None
_event_bus: EventBus | None = None

_ARXIV_URL_PATTERN = re.compile(
    r"^https?://(www\.)?arxiv\.org/(abs|pdf|html)/\d{4}\.\d{4,5}(v\d+)?$"
)


def init_routes(job_manager: JobManager, event_bus: EventBus) -> None:
    """Inject shared service instances into the routes module.

    Called once during application startup from ``main.py``.

    Args:
        job_manager: The application-wide :class:`JobManager`.
        event_bus: The application-wide :class:`EventBus`.
    """
    global _job_manager, _event_bus  # noqa: PLW0603
    _job_manager = job_manager
    _event_bus = event_bus


def _get_job_manager() -> JobManager:
    """Return the shared JobManager, raising if not initialized."""
    if _job_manager is None:
        raise RuntimeError("JobManager not initialized; call init_routes first")
    return _job_manager


def _get_event_bus() -> EventBus:
    """Return the shared EventBus, raising if not initialized."""
    if _event_bus is None:
        raise RuntimeError("EventBus not initialized; call init_routes first")
    return _event_bus


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class ConvertRequest(BaseModel):
    """Request body for the ``POST /convert`` endpoint."""

    arxiv_url: str

    @field_validator("arxiv_url")
    @classmethod
    def validate_arxiv_url(cls, v: str) -> str:
        if not _ARXIV_URL_PATTERN.match(v):
            msg = "Invalid arXiv URL. Expected format: https://arxiv.org/abs/YYMM.NNNNN"
            raise ValueError(msg)
        return v


class ConvertResponse(BaseModel):
    """Response body for a successfully accepted conversion job."""

    job_id: str
    status: str
    stream_url: str


class HealthResponse(BaseModel):
    """Response body for the health check endpoint."""

    status: str
    version: str


class ErrorResponse(BaseModel):
    """Standard error response body."""

    detail: str


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/v1", tags=["v1"])


@router.post(
    "/convert",
    response_model=ConvertResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={422: {"model": ErrorResponse}},
)
async def create_conversion(
    body: ConvertRequest,
    _user: dict[str, Any] = Depends(get_current_user),
) -> ConvertResponse:
    """Create a new arXiv paper conversion job.

    Validates the URL, creates a job record, and launches the
    translation pipeline as a background task.

    Returns 202 Accepted with the job ID and SSE stream URL.
    """
    job_manager = _get_job_manager()
    event_bus = _get_event_bus()

    job = await job_manager.create_job(body.arxiv_url)

    # Import here to avoid circular imports at module level.
    from src.agents.orchestrator import run_pipeline

    asyncio.create_task(
        _run_pipeline_task(job.job_id, body.arxiv_url, job_manager, event_bus, run_pipeline),
    )

    return ConvertResponse(
        job_id=job.job_id,
        status="accepted",
        stream_url=f"/api/v1/jobs/{job.job_id}/stream",
    )


async def _run_pipeline_task(
    job_id: str,
    arxiv_url: str,
    job_manager: JobManager,
    event_bus: EventBus,
    run_pipeline: Any,
) -> None:
    """Wrapper that runs the pipeline and handles final cleanup.

    The orchestrator's ``run_pipeline`` already manages job status
    updates, SSE events, and event bus cleanup internally.
    This wrapper only catches unexpected errors that escape the
    orchestrator's own error handling.
    """
    try:
        await run_pipeline(
            arxiv_url=arxiv_url,
            job_id=job_id,
            event_bus=event_bus,
            job_manager=job_manager,
        )
    except Exception:
        logger.exception("Pipeline task failed for job %s", job_id)
        # Orchestrator should have already handled this, but just in case
        job = await job_manager.get_job(job_id)
        if job and job.status != JobStatus.ERROR:
            await job_manager.set_error(job_id, "Pipeline execution failed")


@router.get("/jobs/{job_id}/stream")
async def stream_job_progress(
    job_id: str,
    _user: dict[str, Any] = Depends(get_current_user),
) -> EventSourceResponse:
    """Stream real-time progress events for a conversion job via SSE.

    The stream terminates when the job reaches a terminal state
    (completed or error) or the client disconnects.
    """
    job_manager = _get_job_manager()
    event_bus = _get_event_bus()

    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    async def event_generator():  # noqa: ANN202
        async for event in event_bus.subscribe(job_id):
            yield {
                "event": event.event,
                "data": event.to_sse_string().split("data: ", 1)[1].split("\n")[0],
            }

    return EventSourceResponse(event_generator())


@router.get(
    "/jobs/{job_id}/download",
    responses={
        404: {"model": ErrorResponse},
    },
)
async def download_job_result(
    job_id: str,
    _user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    """Download the ZIP result of a completed conversion job.

    Returns 404 if the job does not exist or has not completed.
    """
    job_manager = _get_job_manager()

    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    if job.status != JobStatus.COMPLETED or job.result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} has not completed yet or has no result",
        )

    result_path = job.result
    if not result_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result file no longer available",
        )

    return FileResponse(
        path=result_path,
        media_type="application/zip",
        filename=result_path.name,
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service health status and version."""
    return HealthResponse(status="healthy", version="0.1.0")
