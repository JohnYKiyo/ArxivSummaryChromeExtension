"""FastAPI application entrypoint.

Initializes the FastAPI app, configures CORS middleware,
and registers API route handlers for the arXiv Translator service.
Supports both uvicorn (local dev) and Mangum (AWS Lambda) execution.
"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import init_routes, router
from src.config import get_settings
from src.services.job_manager import JobManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan handler for startup and shutdown logic."""
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)

    # Initialize DynamoDB-backed JobManager
    job_manager = JobManager(
        table_name=settings.DYNAMODB_TABLE_NAME,
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL,
        ttl_seconds=settings.JOB_TTL_SECONDS,
    )
    init_routes(job_manager)

    logger.info(
        "arXiv Translator started (env=%s, log_level=%s)",
        settings.APP_ENV,
        settings.LOG_LEVEL,
    )
    yield

    logger.info("arXiv Translator shut down")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance.

    Returns:
        A fully configured :class:`FastAPI` application.
    """
    settings = get_settings()

    app = FastAPI(
        title="arXiv Translator API",
        description="Translate and summarize arXiv papers",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(router)

    return app


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging(level: str) -> None:
    """Set up structured logging for the application.

    Args:
        level: The log level string (e.g. ``"INFO"``, ``"DEBUG"``).
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Entrypoints
# ---------------------------------------------------------------------------

app = create_app()

# AWS Lambda handler via Mangum (used when deployed to Lambda)
try:
    from mangum import Mangum

    handler = Mangum(app, lifespan="off")
except ImportError:
    # Mangum not installed — running locally with uvicorn
    handler = None  # type: ignore[assignment]

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        reload=not settings.is_production,
        log_level=settings.LOG_LEVEL.lower(),
    )
