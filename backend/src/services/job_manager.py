"""Job lifecycle manager.

Manages in-memory job state for conversion tasks.
Handles job creation, status tracking, progress updates,
and result storage. Jobs are transient and not persisted to a database.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class JobStatus(StrEnum):
    """Pipeline stage identifiers for a conversion job."""

    ACCEPTED = "accepted"
    TEX_FETCH = "tex_fetch"
    TEX2MARKDOWN = "tex2markdown"
    TRANSLATION = "translation"
    SUMMARY = "summary"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    ERROR = "error"


@dataclass
class Job:
    """Represents a single arXiv paper conversion job."""

    job_id: str
    status: JobStatus
    arxiv_url: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    result: Path | None = None
    error: str | None = None


class JobManager:
    """Thread-safe in-memory store for conversion jobs.

    All mutation methods are guarded by an ``asyncio.Lock`` to ensure
    consistency when accessed from multiple concurrent coroutines.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = asyncio.Lock()

    async def create_job(self, arxiv_url: str) -> Job:
        """Create and register a new job for the given arXiv URL.

        Args:
            arxiv_url: The arXiv paper URL to process.

        Returns:
            The newly created :class:`Job` instance.
        """
        job_id = uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, status=JobStatus.ACCEPTED, arxiv_url=arxiv_url)
        async with self._lock:
            self._jobs[job_id] = job
        logger.info("Created job %s for %s", job_id, arxiv_url)
        return job

    async def get_job(self, job_id: str) -> Job | None:
        """Look up a job by its ID.

        Args:
            job_id: The unique job identifier.

        Returns:
            The :class:`Job` if found, otherwise ``None``.
        """
        async with self._lock:
            return self._jobs.get(job_id)

    async def update_status(self, job_id: str, status: JobStatus) -> None:
        """Transition a job to a new status.

        Args:
            job_id: The unique job identifier.
            status: The new :class:`JobStatus` to set.

        Raises:
            KeyError: If no job with the given ID exists.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            job.status = status
        logger.info("Job %s -> %s", job_id, status)

    async def set_result(self, job_id: str, result_path: Path) -> None:
        """Mark a job as completed and store its result path.

        Args:
            job_id: The unique job identifier.
            result_path: Path to the output artifact (ZIP file).

        Raises:
            KeyError: If no job with the given ID exists.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            job.status = JobStatus.COMPLETED
            job.result = result_path
        logger.info("Job %s completed: %s", job_id, result_path)

    async def set_error(self, job_id: str, error: str) -> None:
        """Mark a job as failed with an error message.

        Args:
            job_id: The unique job identifier.
            error: Human-readable error description.

        Raises:
            KeyError: If no job with the given ID exists.
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise KeyError(f"Job not found: {job_id}")
            job.status = JobStatus.ERROR
            job.error = error
        logger.error("Job %s error: %s", job_id, error)

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        """Remove jobs older than *ttl_seconds*.

        Args:
            ttl_seconds: Maximum age of a job in seconds.

        Returns:
            The number of jobs removed.
        """
        now = datetime.now(UTC)
        expired_ids: list[str] = []

        async with self._lock:
            for job_id, job in self._jobs.items():
                age = (now - job.created_at).total_seconds()
                if age > ttl_seconds:
                    expired_ids.append(job_id)

            for job_id in expired_ids:
                del self._jobs[job_id]

        if expired_ids:
            logger.info("Cleaned up %d expired jobs", len(expired_ids))
        return len(expired_ids)
