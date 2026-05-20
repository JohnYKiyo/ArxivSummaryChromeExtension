"""Job domain model and status enumeration."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


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
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.COMPLETED, JobStatus.ERROR, JobStatus.CANCELLED})


@dataclass
class Job:
    """Represents a single arXiv paper conversion job."""

    job_id: str
    status: JobStatus
    arxiv_url: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    current_step: str | None = None
    progress: int = 0
    message: str | None = None
    download_url: str | None = None
    local_result_path: str | None = None  # ローカル開発時のみ使用
    error: str | None = None
    # Cooperative-cancellation flag. The API sets this via JobManager.request_cancel();
    # the orchestrator checks it between stages and transitions to CANCELLED when seen.
    # Kept separate from ``status`` so a pipeline progress write doesn't race-overwrite
    # the cancel intent.
    cancel_requested: bool = False
