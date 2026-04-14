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
    error: str | None = None
