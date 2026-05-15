"""API route definitions.

Defines the following endpoints:
- POST /api/v1/convert - Create a new conversion job
- GET /api/v1/jobs/{job_id}/status - Poll job progress
- GET /api/v1/jobs/{job_id}/download - Download the ZIP (local dev only)
- GET /api/v1/health - Health check

This module deliberately knows nothing about ``boto3``, ``asyncio``, or
the pipeline implementation. Pipeline dispatch goes through the injected
:class:`PipelineDispatcher`; persistence goes through the injected
:class:`JobManager`. See ``services/pipeline_dispatcher.py``.
"""

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from src.api.auth import get_current_user
from src.config import get_settings
from src.models.api import ConvertRequest, ConvertResponse, ErrorResponse, HealthResponse, StatusResponse
from src.services.job_manager import JobManager
from src.services.pipeline_dispatcher import PipelineDispatcher

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service instances (set during app startup via init_routes)
# ---------------------------------------------------------------------------

_job_manager: JobManager | None = None
_dispatcher: PipelineDispatcher | None = None


def init_routes(job_manager: JobManager, dispatcher: PipelineDispatcher) -> None:
    """Inject shared service instances into the routes module.

    Called once during application startup from ``main.py``.

    Args:
        job_manager: The application-wide :class:`JobManager`.
        dispatcher: The strategy used to start a pipeline run for a job.
    """
    global _job_manager, _dispatcher  # noqa: PLW0603
    _job_manager = job_manager
    _dispatcher = dispatcher


def _get_job_manager() -> JobManager:
    """Return the shared JobManager, raising if not initialized."""
    if _job_manager is None:
        raise RuntimeError("JobManager not initialized; call init_routes first")
    return _job_manager


def _get_dispatcher() -> PipelineDispatcher:
    """Return the shared PipelineDispatcher, raising if not initialized."""
    if _dispatcher is None:
        raise RuntimeError("PipelineDispatcher not initialized; call init_routes first")
    return _dispatcher


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

    Validates the URL, creates a job record in DynamoDB, and asks the
    dispatcher to start the pipeline. Returns 202 Accepted with the job
    ID and status polling URL.
    """
    job_manager = _get_job_manager()
    dispatcher = _get_dispatcher()

    job = await job_manager.create_job(body.arxiv_url)
    await dispatcher.dispatch(job.job_id, body.arxiv_url)

    return ConvertResponse(
        job_id=job.job_id,
        status="accepted",
        status_url=f"/api/v1/jobs/{job.job_id}/status",
    )


@router.get(
    "/jobs/{job_id}/status",
    response_model=StatusResponse,
    responses={404: {"model": ErrorResponse}},
)
async def get_job_status(
    job_id: str,
    _user: dict[str, Any] = Depends(get_current_user),
) -> StatusResponse:
    """Poll the current status of a conversion job.

    Returns the job's progress, current step, and download URL when complete.
    """
    job_manager = _get_job_manager()

    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    return StatusResponse(
        job_id=job.job_id,
        status=job.status,
        current_step=job.current_step,
        progress=job.progress,
        message=job.message,
        error=job.error,
        download_url=job.download_url,
    )


@router.get(
    "/jobs/{job_id}/download",
    responses={404: {"model": ErrorResponse}},
)
async def download_job_result(
    job_id: str,
    _user: dict[str, Any] = Depends(get_current_user),
) -> FileResponse:
    """Download the ZIP result of a completed job (local development only).

    In production, the frontend downloads directly from the S3 presigned URL
    stored in ``download_url``. This endpoint exists only for local development
    where ``S3_BUCKET_NAME`` is not set and the ZIP is kept on disk.
    """
    job_manager = _get_job_manager()
    settings = get_settings()

    if settings.S3_BUCKET_NAME:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Direct download not available in production; use the presigned URL from /status",
        )

    job = await job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job not found: {job_id}",
        )

    if job.local_result_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} has no local result (not yet completed or result was deleted)",
        )

    result_path = Path(job.local_result_path)
    if not result_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result file no longer available",
        )

    # Use the on-disk filename (which the pipeline writes as
    # ``<arxiv_id>.zip``) so the user's browser downloads under that name.
    return FileResponse(
        path=result_path,
        media_type="application/zip",
        filename=result_path.name,
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service health status and version."""
    return HealthResponse(status="healthy", version="0.1.0")
