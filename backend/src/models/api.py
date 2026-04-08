"""API request/response models."""

import re

from pydantic import BaseModel, field_validator

_ARXIV_URL_PATTERN = re.compile(
    r"^https?://(www\.)?arxiv\.org/(abs|pdf|html)/\d{4}\.\d{4,5}(v\d+)?$"
)


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
