"""Job lifecycle manager backed by DynamoDB.

Manages job state for conversion tasks using DynamoDB as persistent storage.
Handles job creation, status tracking, progress updates, and result storage.
DynamoDB TTL handles automatic cleanup of expired jobs.
"""

import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3

from src.models.job import Job, JobStatus

logger = logging.getLogger(__name__)


class JobManager:
    """DynamoDB-backed store for conversion jobs.

    All mutations use atomic DynamoDB operations, so no application-level
    locking is required.
    """

    def __init__(self, table_name: str, endpoint_url: str = "", ttl_seconds: int = 3600) -> None:
        self._table_name = table_name
        self._ttl_seconds = ttl_seconds

        kwargs: dict[str, Any] = {}
        if endpoint_url:
            kwargs["endpoint_url"] = endpoint_url

        dynamodb = boto3.resource("dynamodb", **kwargs)
        self._table = dynamodb.Table(table_name)

    def _make_ttl(self) -> int:
        """Return epoch seconds for the TTL expiration."""
        return int(time.time()) + self._ttl_seconds

    async def create_job(self, arxiv_url: str) -> Job:
        """Create and store a new job for the given arXiv URL.

        Args:
            arxiv_url: The arXiv paper URL to process.

        Returns:
            The newly created :class:`Job` instance.
        """
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now(UTC)

        self._table.put_item(
            Item={
                "job_id": job_id,
                "status": JobStatus.ACCEPTED.value,
                "arxiv_url": arxiv_url,
                "created_at": now.isoformat(),
                "current_step": None,
                "progress": 0,
                "message": None,
                "download_url": None,
                "error": None,
                "ttl": self._make_ttl(),
            }
        )

        job = Job(job_id=job_id, status=JobStatus.ACCEPTED, arxiv_url=arxiv_url, created_at=now)
        logger.info("Created job %s for %s", job_id, arxiv_url)
        return job

    async def get_job(self, job_id: str) -> Job | None:
        """Look up a job by its ID.

        Args:
            job_id: The unique job identifier.

        Returns:
            The :class:`Job` if found, otherwise ``None``.
        """
        response = self._table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        if item is None:
            return None

        return Job(
            job_id=item["job_id"],
            status=JobStatus(item["status"]),
            arxiv_url=item["arxiv_url"],
            created_at=datetime.fromisoformat(item["created_at"]),
            current_step=item.get("current_step"),
            progress=int(item.get("progress", 0)),
            message=item.get("message"),
            download_url=item.get("download_url"),
            error=item.get("error"),
        )

    async def update_status(self, job_id: str, status: JobStatus) -> None:
        """Transition a job to a new status.

        Args:
            job_id: The unique job identifier.
            status: The new :class:`JobStatus` to set.
        """
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :status, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status.value,
                ":now": datetime.now(UTC).isoformat(),
            },
        )
        logger.info("Job %s -> %s", job_id, status)

    async def update_progress(
        self,
        job_id: str,
        status: JobStatus,
        current_step: str,
        progress: int,
        message: str,
    ) -> None:
        """Atomically update job status, step, progress, and message.

        This is the primary method called by the pipeline orchestrator
        to report progress, replacing the old EventBus publish mechanism.

        Args:
            job_id: The unique job identifier.
            status: The new :class:`JobStatus`.
            current_step: The current pipeline step name.
            progress: Completion percentage (0--100).
            message: Human-readable progress message.
        """
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #s = :status, current_step = :step, progress = :prog, "
                "message = :msg, updated_at = :now"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": status.value,
                ":step": current_step,
                ":prog": progress,
                ":msg": message,
                ":now": datetime.now(UTC).isoformat(),
            },
        )
        logger.info("Job %s stage=%s progress=%d%%", job_id, current_step, progress)

    async def set_result(self, job_id: str, download_url: str) -> None:
        """Mark a job as completed and store its download URL.

        Args:
            job_id: The unique job identifier.
            download_url: Presigned S3 URL for the output ZIP.
        """
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #s = :status, progress = :prog, current_step = :step, "
                "message = :msg, download_url = :url, updated_at = :now"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": JobStatus.COMPLETED.value,
                ":prog": 100,
                ":step": "done",
                ":msg": "処理が完了しました",
                ":url": download_url,
                ":now": datetime.now(UTC).isoformat(),
            },
        )
        logger.info("Job %s completed: %s", job_id, download_url)

    async def set_error(self, job_id: str, error: str) -> None:
        """Mark a job as failed with an error message.

        Args:
            job_id: The unique job identifier.
            error: Human-readable error description.
        """
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #s = :status, progress = :prog, "
                "error = :err, message = :msg, updated_at = :now"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": JobStatus.ERROR.value,
                ":prog": 0,
                ":err": error,
                ":msg": "処理中にエラーが発生しました",
                ":now": datetime.now(UTC).isoformat(),
            },
        )
        logger.error("Job %s error: %s", job_id, error)
