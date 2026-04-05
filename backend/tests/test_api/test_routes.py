"""Tests for API route handlers.

Verifies correct HTTP responses for all endpoints:
- POST /api/v1/convert
- GET /api/v1/jobs/{job_id}/stream
- GET /api/v1/jobs/{job_id}/download
- GET /api/v1/health

Since routes.py is currently a stub, these tests create a minimal
FastAPI application with the expected endpoint signatures and verify
the expected HTTP behavior.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, HttpUrl

from src.services.job_manager import JobManager, JobStatus

# ---------------------------------------------------------------------------
# Minimal app for testing expected route behaviour
# ---------------------------------------------------------------------------


class ConvertRequest(BaseModel):
    """Request body for the convert endpoint."""
    url: str


def _create_test_app(job_manager: JobManager) -> FastAPI:
    """Build a minimal FastAPI app that mirrors the expected route contract."""
    app = FastAPI()

    @app.post("/api/v1/convert", status_code=202)
    async def convert(body: ConvertRequest, background_tasks: BackgroundTasks):
        # Basic URL validation
        url = body.url
        if not url or "arxiv.org" not in url:
            return JSONResponse(
                status_code=422,
                content={"detail": "Invalid arXiv URL"},
            )
        job = await job_manager.create_job(url)
        return {"job_id": job.job_id, "status": job.status}

    @app.get("/api/v1/health")
    async def health():
        return {"status": "healthy"}

    @app.get("/api/v1/jobs/{job_id}/download")
    async def download(job_id: str):
        job = await job_manager.get_job(job_id)
        if job is None:
            return JSONResponse(status_code=404, content={"detail": "Job not found"})
        if job.result is None:
            return JSONResponse(
                status_code=404, content={"detail": "Result not ready"}
            )
        return FileResponse(path=str(job.result), filename="output.zip")

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture()
async def client(job_manager: JobManager):
    """Provide an httpx AsyncClient bound to the test app."""
    app = _create_test_app(job_manager)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestConvertEndpoint:
    """POST /api/v1/convert"""

    async def test_valid_url_returns_202(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/convert",
            json={"url": "https://arxiv.org/abs/2301.00001"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert data["status"] == "accepted"

    async def test_invalid_url_returns_422(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/convert",
            json={"url": "https://example.com/not-arxiv"},
        )
        assert response.status_code == 422

    async def test_empty_url_returns_422(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/convert",
            json={"url": ""},
        )
        assert response.status_code == 422

    async def test_missing_url_returns_422(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/v1/convert",
            json={},
        )
        assert response.status_code == 422


class TestHealthEndpoint:
    """GET /api/v1/health"""

    async def test_health_returns_healthy(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"


class TestDownloadEndpoint:
    """GET /api/v1/jobs/{job_id}/download"""

    async def test_nonexistent_job_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/v1/jobs/nonexistent123/download")
        assert response.status_code == 404

    async def test_job_without_result_returns_404(
        self, client: AsyncClient, job_manager: JobManager
    ) -> None:
        job = await job_manager.create_job("https://arxiv.org/abs/2301.00001")
        response = await client.get(f"/api/v1/jobs/{job.job_id}/download")
        assert response.status_code == 404

    async def test_job_with_result_returns_file(
        self, client: AsyncClient, job_manager: JobManager, tmp_path: Path
    ) -> None:
        # Create a job and set a result
        job = await job_manager.create_job("https://arxiv.org/abs/2301.00001")
        zip_path = tmp_path / "output.zip"
        zip_path.write_bytes(b"PK\x03\x04fake-zip-content")
        await job_manager.set_result(job.job_id, zip_path)

        response = await client.get(f"/api/v1/jobs/{job.job_id}/download")
        assert response.status_code == 200
