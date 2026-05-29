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
                "cancel_requested": False,
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
            local_result_path=item.get("local_result_path"),
            error=item.get("error"),
            cancel_requested=bool(item.get("cancel_requested", False)),
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
                "SET #s = :status, current_step = :step, progress = :prog, message = :msg, updated_at = :now"
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

    async def set_result(
        self,
        job_id: str,
        download_url: str,
        local_result_path: str | None = None,
    ) -> None:
        """Mark a job as completed and store its download URL.

        Args:
            job_id: The unique job identifier.
            download_url: Presigned S3 URL or local API path for the output ZIP.
            local_result_path: Absolute local file path (local dev only).
        """
        update_expr = (
            "SET #s = :status, progress = :prog, current_step = :step, "
            "message = :msg, download_url = :url, updated_at = :now"
        )
        values: dict[str, Any] = {
            ":status": JobStatus.COMPLETED.value,
            ":prog": 100,
            ":step": "done",
            ":msg": "処理が完了しました",
            ":url": download_url,
            ":now": datetime.now(UTC).isoformat(),
        }

        if local_result_path is not None:
            update_expr += ", local_result_path = :local_path"
            values[":local_path"] = local_result_path

        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues=values,
        )
        logger.info("Job %s completed: %s", job_id, download_url)

    async def set_error(self, job_id: str, error: str) -> None:
        """Mark a job as failed with an error message.

        Args:
            job_id: The unique job identifier.
            error: Human-readable error description.
        """
        # NOTE: `error` is a DynamoDB reserved word — must alias via ExpressionAttributeNames.
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=("SET #s = :status, progress = :prog, #e = :err, message = :msg, updated_at = :now"),
            ExpressionAttributeNames={"#s": "status", "#e": "error"},
            ExpressionAttributeValues={
                ":status": JobStatus.ERROR.value,
                ":prog": 0,
                ":err": error,
                ":msg": "処理中にエラーが発生しました",
                ":now": datetime.now(UTC).isoformat(),
            },
        )
        logger.error("Job %s error: %s", job_id, error)

    async def request_cancel(self, job_id: str) -> bool:
        """Mark a job for cooperative cancellation.

        Sets ``cancel_requested = True`` only when the job is still in a
        non-terminal state. The running pipeline checks this flag between
        stages and exits cleanly via :meth:`set_cancelled` when it sees it.

        Status is intentionally NOT flipped here — a progress write from the
        in-flight pipeline would race-overwrite it back to the current stage.
        The pipeline owns the eventual CANCELLED transition.

        Args:
            job_id: The unique job identifier.

        Returns:
            ``True`` if the cancel flag was set (or was already set).
            ``False`` if the job has already finished (completed / error /
            cancelled) — cancellation is a no-op in that case.
        """
        # Conditional update: only set the flag when the status is still
        # in-flight. ``attribute_not_exists`` covers the legacy rows that
        # predate this attribute.
        try:
            self._table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET cancel_requested = :true, updated_at = :now",
                ConditionExpression=("#s <> :completed AND #s <> :error AND #s <> :cancelled"),
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={
                    ":true": True,
                    ":completed": JobStatus.COMPLETED.value,
                    ":error": JobStatus.ERROR.value,
                    ":cancelled": JobStatus.CANCELLED.value,
                    ":now": datetime.now(UTC).isoformat(),
                },
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        logger.info("Job %s cancel requested", job_id)
        return True

    async def is_cancel_requested(self, job_id: str) -> bool:
        """Return ``True`` if a cancel has been requested for the job.

        Used by the pipeline orchestrator at each stage boundary to decide
        whether to abort the run.
        """
        response = self._table.get_item(
            Key={"job_id": job_id},
            ProjectionExpression="cancel_requested",
        )
        item = response.get("Item")
        if item is None:
            return False
        return bool(item.get("cancel_requested", False))

    async def set_cancelled(self, job_id: str) -> None:
        """Mark a job as cancelled (terminal state).

        Called by the orchestrator after it observes ``cancel_requested``
        between stages and stops the pipeline.
        """
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :status, message = :msg, updated_at = :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":status": JobStatus.CANCELLED.value,
                ":msg": "処理がキャンセルされました",
                ":now": datetime.now(UTC).isoformat(),
            },
        )
        logger.info("Job %s cancelled", job_id)
