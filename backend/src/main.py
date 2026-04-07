"""FastAPI application entrypoint.

Initializes the FastAPI app, configures CORS middleware,
and registers API route handlers for the arXiv Translator service.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes import init_routes, router
from src.config import get_settings
from src.services.job_manager import JobManager
from src.services.sse import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared service instances
# ---------------------------------------------------------------------------

job_manager = JobManager()
event_bus = EventBus()

# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

_cleanup_task: asyncio.Task[None] | None = None


async def _periodic_cleanup() -> None:
    """Background task that removes expired jobs on a regular interval."""
    settings = get_settings()
    while True:
        try:
            await asyncio.sleep(300)  # Run every 5 minutes
            removed = await job_manager.cleanup_expired(settings.JOB_TTL_SECONDS)
            if removed:
                logger.info("Periodic cleanup removed %d expired jobs", removed)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Error during periodic cleanup")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan handler for startup and shutdown logic."""
    global _cleanup_task  # noqa: PLW0603

    # Startup
    settings = get_settings()
    _configure_logging(settings.LOG_LEVEL)
    init_routes(job_manager, event_bus)
    _cleanup_task = asyncio.create_task(_periodic_cleanup())
    logger.info(
        "arXiv Translator started (env=%s, log_level=%s)",
        settings.APP_ENV,
        settings.LOG_LEVEL,
    )
    yield

    # Shutdown
    if _cleanup_task is not None:
        _cleanup_task.cancel()
        with suppress(asyncio.CancelledError):
            await _cleanup_task
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
# Entrypoint
# ---------------------------------------------------------------------------

app = create_app()

if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",  # noqa: S104
        port=8000,
        reload=not settings.is_production,
        log_level=settings.LOG_LEVEL.lower(),
    )
