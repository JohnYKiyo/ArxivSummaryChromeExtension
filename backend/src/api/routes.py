"""API route definitions.

Defines the following endpoints:
- POST /api/v1/convert - Create a new conversion job
- GET /api/v1/jobs/{job_id}/status - Poll job progress
- GET /api/v1/health - Health check
"""

import json
import logging
from pathlib import Path
from typing import Any

import boto3
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

from src.api.auth import get_current_user
from src.config import get_settings
from src.models.api import ConvertRequest, ConvertResponse, ErrorResponse, HealthResponse, StatusResponse
from src.services.job_manager import JobManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service instances (set during app startup via init_routes)
# ---------------------------------------------------------------------------

_job_manager: JobManager | None = None


def init_routes(job_manager: JobManager) -> None:
    """Inject shared service instances into the routes module.

    Called once during application startup from ``main.py``.

    Args:
        job_manager: The application-wide :class:`JobManager`.
    """
    global _job_manager  # noqa: PLW0603
    _job_manager = job_manager


def _get_job_manager() -> JobManager:
    """Return the shared JobManager, raising if not initialized."""
    if _job_manager is None:
        raise RuntimeError("JobManager not initialized; call init_routes first")
    return _job_manager


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

    Validates the URL, creates a job record in DynamoDB, and invokes
    the pipeline Lambda asynchronously.

    Returns 202 Accepted with the job ID and status polling URL.
    """
    job_manager = _get_job_manager()
    settings = get_settings()

    job = await job_manager.create_job(body.arxiv_url)

    if settings.PIPELINE_LAMBDA_NAME:
        # Production: invoke pipeline Lambda asynchronously
        lambda_client = boto3.client("lambda")
        lambda_client.invoke(
            FunctionName=settings.PIPELINE_LAMBDA_NAME,
            InvocationType="Event",
            Payload=json.dumps({"job_id": job.job_id, "arxiv_url": body.arxiv_url}),
        )
        logger.info("Invoked pipeline Lambda for job %s", job.job_id)
    else:
        # Local development: run pipeline in background task
        import asyncio

        from src.agents.orchestrator import run_pipeline

        asyncio.create_task(
            _run_pipeline_task(job.job_id, body.arxiv_url, job_manager, run_pipeline),
        )

    return ConvertResponse(
        job_id=job.job_id,
        status="accepted",
        status_url=f"/api/v1/jobs/{job.job_id}/status",
    )


async def _run_pipeline_task(
    job_id: str,
    arxiv_url: str,
    job_manager: JobManager,
    run_pipeline: Any,
) -> None:
    """Wrapper that runs the pipeline and handles final cleanup.

    Used only in local development mode when no pipeline Lambda is configured.
    """
    try:
        await run_pipeline(
            arxiv_url=arxiv_url,
            job_id=job_id,
            job_manager=job_manager,
        )
    except Exception:
        logger.exception("Pipeline task failed for job %s", job_id)
        job = await job_manager.get_job(job_id)
        if job and job.status != "error":
            await job_manager.set_error(job_id, "Pipeline execution failed")


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

    return FileResponse(
        path=result_path,
        media_type="application/zip",
        filename="output.zip",
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return service health status and version."""
    return HealthResponse(status="healthy", version="0.1.0")
