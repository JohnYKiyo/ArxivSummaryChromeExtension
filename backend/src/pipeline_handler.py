"""AWS Lambda handler for the pipeline runner.

This module is the entry point for the pipeline Lambda function.
It receives a job event (job_id + arxiv_url) via async Lambda invocation
and runs the full translation pipeline, updating progress in DynamoDB.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda handler for the pipeline runner.

    Args:
        event: Dict with ``job_id`` and ``arxiv_url`` keys.
        context: Lambda context (unused).

    Returns:
        Dict with execution status.
    """
    job_id = event["job_id"]
    arxiv_url = event["arxiv_url"]

    logger.info("Pipeline Lambda invoked for job %s: %s", job_id, arxiv_url)

    # Import here to avoid cold-start overhead when loading the handler module
    from src.agents.orchestrator import run_pipeline
    from src.config import get_settings
    from src.services.job_manager import JobManager

    settings = get_settings()
    job_manager = JobManager(
        table_name=settings.DYNAMODB_TABLE_NAME,
        endpoint_url=settings.DYNAMODB_ENDPOINT_URL,
        ttl_seconds=settings.JOB_TTL_SECONDS,
    )

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    try:
        results = asyncio.run(
            run_pipeline(
                arxiv_url=arxiv_url,
                job_id=job_id,
                job_manager=job_manager,
            )
        )
        logger.info("Pipeline Lambda completed for job %s", job_id)
        return {"status": "success", "job_id": job_id, "download_url": results.get("download_url")}

    except Exception:
        logger.exception("Pipeline Lambda failed for job %s", job_id)
        return {"status": "error", "job_id": job_id}
