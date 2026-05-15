"""Tests for the real ``api.routes`` module.

Earlier this file used a hand-written mock FastAPI app because ``routes.py``
was a stub. Both the mock and the asyncio fixture wiring fell out of date,
so we now drive the actual router with mocked ``JobManager`` and
``PipelineDispatcher`` injected via ``init_routes``.

Auth is bypassed automatically when ``APP_ENV != "production"`` (see
``api/auth.py``); the ``settings_override`` fixture in ``conftest.py``
sets ``APP_ENV=test`` so no token handling is needed here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import init_routes, router
from src.models.job import Job, JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_job(
    job_id: str = "abc123",
    status: JobStatus = JobStatus.ACCEPTED,
    *,
    progress: int = 0,
    current_step: str | None = None,
    message: str | None = None,
    error: str | None = None,
    download_url: str | None = None,
    local_result_path: str | None = None,
) -> Job:
    """Build a Job instance for tests with sensible defaults."""
    return Job(
        job_id=job_id,
        status=status,
        arxiv_url="https://arxiv.org/abs/2301.00001",
        created_at=datetime.now(UTC),
        progress=progress,
        current_step=current_step,
        message=message,
        error=error,
        download_url=download_url,
        local_result_path=local_result_path,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_job_manager() -> MagicMock:
    """A fully-mocked JobManager with async methods stubbed."""
    mgr = MagicMock(name="MockJobManager")
    mgr.create_job = AsyncMock(return_value=_make_job())
    mgr.get_job = AsyncMock(return_value=None)
    mgr.update_progress = AsyncMock()
    mgr.set_result = AsyncMock()
    mgr.set_error = AsyncMock()
    return mgr


@pytest.fixture()
def mock_dispatcher() -> MagicMock:
    """Pipeline dispatcher whose ``dispatch`` is a no-op coroutine."""
    disp = MagicMock(name="MockPipelineDispatcher")
    disp.dispatch = AsyncMock()
    return disp


@pytest.fixture()
def app(
    settings_override: dict[str, str],  # noqa: ARG001 — sets APP_ENV=test for auth bypass
    mock_job_manager: MagicMock,
    mock_dispatcher: MagicMock,
) -> FastAPI:
    """Build a FastAPI app wired to the real router and mocked services."""
    init_routes(mock_job_manager, mock_dispatcher)
    application = FastAPI()
    application.include_router(router)
    return application


@pytest_asyncio.fixture
async def client(app: FastAPI) -> Any:
    """An httpx AsyncClient bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """GET /api/v1/health"""

    async def test_health_returns_healthy(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data


# ---------------------------------------------------------------------------
# POST /convert
# ---------------------------------------------------------------------------


class TestConvertEndpoint:
    """POST /api/v1/convert"""

    async def test_valid_url_returns_202(
        self, client: AsyncClient, mock_dispatcher: MagicMock
    ) -> None:
        response = await client.post(
            "/api/v1/convert",
            json={"arxiv_url": "https://arxiv.org/abs/2301.00001"},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["job_id"] == "abc123"
        assert data["status"] == "accepted"
        assert data["status_url"] == "/api/v1/jobs/abc123/status"
        # The pipeline should have been dispatched exactly once.
        mock_dispatcher.dispatch.assert_awaited_once_with("abc123", "https://arxiv.org/abs/2301.00001")

    async def test_non_arxiv_url_returns_422(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/convert",
            json={"arxiv_url": "https://example.com/not-arxiv"},
        )
        assert response.status_code == 422

    async def test_missing_field_returns_422(self, client: AsyncClient) -> None:
        response = await client.post("/api/v1/convert", json={})
        assert response.status_code == 422

    async def test_malformed_arxiv_id_returns_422(self, client: AsyncClient) -> None:
        """arXiv ID without the YYMM.NNNNN form should fail validation."""
        response = await client.post(
            "/api/v1/convert",
            json={"arxiv_url": "https://arxiv.org/abs/not-a-real-id"},
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /jobs/{id}/status
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    """GET /api/v1/jobs/{job_id}/status"""

    async def test_nonexistent_job_returns_404(
        self, client: AsyncClient, mock_job_manager: MagicMock
    ) -> None:
        mock_job_manager.get_job.return_value = None
        response = await client.get("/api/v1/jobs/missing/status")
        assert response.status_code == 404

    async def test_existing_job_returns_status(
        self, client: AsyncClient, mock_job_manager: MagicMock
    ) -> None:
        mock_job_manager.get_job.return_value = _make_job(
            status=JobStatus.TRANSLATION,
            progress=60,
            current_step="translation",
            message="日本語翻訳中...",
        )
        response = await client.get("/api/v1/jobs/abc123/status")
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "abc123"
        assert data["status"] == "translation"
        assert data["progress"] == 60
        assert data["current_step"] == "translation"

    async def test_completed_job_includes_download_url(
        self, client: AsyncClient, mock_job_manager: MagicMock
    ) -> None:
        mock_job_manager.get_job.return_value = _make_job(
            status=JobStatus.COMPLETED,
            progress=100,
            download_url="/api/v1/jobs/abc123/download",
        )
        response = await client.get("/api/v1/jobs/abc123/status")
        assert response.status_code == 200
        assert response.json()["download_url"] == "/api/v1/jobs/abc123/download"


# ---------------------------------------------------------------------------
# GET /jobs/{id}/download
# ---------------------------------------------------------------------------


class TestDownloadEndpoint:
    """GET /api/v1/jobs/{job_id}/download (local development only)."""

    async def test_nonexistent_job_returns_404(
        self, client: AsyncClient, mock_job_manager: MagicMock
    ) -> None:
        mock_job_manager.get_job.return_value = None
        response = await client.get("/api/v1/jobs/missing/download")
        assert response.status_code == 404

    async def test_completed_job_without_local_path_returns_404(
        self, client: AsyncClient, mock_job_manager: MagicMock
    ) -> None:
        mock_job_manager.get_job.return_value = _make_job(
            status=JobStatus.COMPLETED,
            progress=100,
            download_url="/api/v1/jobs/abc123/download",
            local_result_path=None,
        )
        response = await client.get("/api/v1/jobs/abc123/download")
        assert response.status_code == 404

    async def test_returns_file_when_local_path_exists(
        self,
        client: AsyncClient,
        mock_job_manager: MagicMock,
        tmp_path: Any,
    ) -> None:
        zip_path = tmp_path / "output.zip"
        zip_path.write_bytes(b"PK\x03\x04dummy")
        mock_job_manager.get_job.return_value = _make_job(
            status=JobStatus.COMPLETED,
            progress=100,
            download_url=f"/api/v1/jobs/abc123/download",
            local_result_path=str(zip_path),
        )
        response = await client.get("/api/v1/jobs/abc123/download")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        assert response.content == b"PK\x03\x04dummy"
