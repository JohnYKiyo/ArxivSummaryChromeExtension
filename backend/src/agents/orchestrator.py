"""Pipeline orchestrator for the arXiv translation workflow.

Coordinates the execution of all agents in the translation pipeline:
fetch_arxiv_paper -> Tex2MarkdownAgent -> TranslationAgent -> SummaryAgent.
Reports progress via DynamoDB updates at each stage transition.

Each agent is run independently with its own InMemoryRunner so that
DynamoDB progress writes can be interleaved between stages and
non-LLM steps (arXiv fetch, ZIP packaging, S3 upload) can be mixed
into the same flow.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from src.agents.summary import create_summary_agent
from src.agents.tex2markdown import create_tex2markdown_agent
from src.agents.translation import create_translation_agent
from src.config import get_settings
from src.models.job import JobStatus
from src.tools.arxiv import fetch_arxiv_paper
from src.tools.packaging import create_zip_package, upload_to_s3

if TYPE_CHECKING:
    from src.services.job_manager import JobManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline stages used for progress reporting
# ---------------------------------------------------------------------------

STAGES = [
    {"name": "tex_fetch", "label": "arXivからTeXソースを取得中...", "progress": 10, "status": JobStatus.TEX_FETCH},
    {"name": "tex2markdown", "label": "Markdown変換中...", "progress": 30, "status": JobStatus.TEX2MARKDOWN},
    {"name": "translation", "label": "日本語翻訳中...", "progress": 60, "status": JobStatus.TRANSLATION},
    {"name": "summary", "label": "要約生成中...", "progress": 80, "status": JobStatus.SUMMARY},
    {"name": "packaging", "label": "ZIP作成中...", "progress": 95, "status": JobStatus.PACKAGING},
]


# ---------------------------------------------------------------------------
# Pipeline execution with DynamoDB progress
# ---------------------------------------------------------------------------


async def _extract_final_text_async(event_stream: Any) -> str:
    """Walk an async event stream and return the last text content produced."""
    final_text = ""
    async for event in event_stream:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    final_text = part.text
    return final_text


async def _run_single_agent(
    agent: LlmAgent,
    user_message: str,
) -> str:
    """Run a single agent with the given message and return its text output.

    Creates an ephemeral :class:`InMemoryRunner` and session for the call.

    Args:
        agent: The agent to execute.
        user_message: The prompt / content to send.

    Returns:
        The agent's final text response.
    """
    runner = InMemoryRunner(agent=agent)
    user_id = "pipeline"
    session_id = str(uuid.uuid4())

    content = genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_message)],
    )

    event_stream = runner.run_async(
        user_id=user_id,
        session_id=session_id,
        new_message=content,
    )

    return await _extract_final_text_async(event_stream)


async def _publish_progress(
    job_manager: JobManager | None,
    job_id: str,
    stage_index: int,
) -> None:
    """Update job progress in DynamoDB.

    Args:
        job_manager: Optional job manager for progress updates.
        job_id: The job identifier.
        stage_index: Index into :data:`STAGES`.
    """
    stage = STAGES[stage_index]

    if job_manager is not None:
        await job_manager.update_progress(
            job_id=job_id,
            status=stage["status"],
            current_step=stage["name"],
            progress=stage["progress"],
            message=stage["label"],
        )

    logger.info("Pipeline [%s] stage=%s progress=%d%%", job_id, stage["name"], stage["progress"])


async def run_pipeline(
    arxiv_url: str,
    job_id: str,
    job_manager: JobManager | None = None,
) -> dict[str, Any]:
    """Execute the full translation pipeline with DynamoDB progress reporting.

    Runs each agent in sequence, updating progress in DynamoDB between stages.
    The frontend polls the ``GET /api/v1/jobs/{job_id}/status`` endpoint to
    read the latest progress.

    Args:
        arxiv_url: The arXiv paper URL to process.
        job_id: Unique job identifier for progress tracking.
        job_manager: Optional job manager for progress updates.

    Returns:
        A dict containing:
          - ``markdown_en``: English Markdown content.
          - ``markdown_ja``: Japanese Markdown content.
          - ``summary_ja``: Japanese summary.
          - ``download_url``: Presigned S3 URL for the output ZIP (or ``None``).
    """
    settings = get_settings()
    model = settings.LLM_MODEL

    results: dict[str, Any] = {
        "markdown_en": None,
        "markdown_ja": None,
        "summary_ja": None,
        "download_url": None,
    }

    try:
        # ---- Stage 0: Fetch TeX ----
        await _publish_progress(job_manager, job_id, 0)

        # Fetch directly — no LLM needed for downloading/extracting files.
        tex_content, image_paths, work_dir = fetch_arxiv_paper(arxiv_url)
        image_paths_str = "\n".join(str(p) for p in image_paths)

        # ---- Stage 1: TeX -> Markdown ----
        await _publish_progress(job_manager, job_id, 1)
        tex2md_agent = create_tex2markdown_agent(model)
        markdown_en = await _run_single_agent(
            tex2md_agent,
            (
                f"Convert the following TeX content to Markdown.\n\n"
                f"Image paths available:\n{image_paths_str}\n\n"
                f"TeX content:\n{tex_content}"
            ),
        )
        results["markdown_en"] = markdown_en

        # ---- Stage 2: Translation ----
        await _publish_progress(job_manager, job_id, 2)
        translation_agent = create_translation_agent(model)
        markdown_ja = await _run_single_agent(
            translation_agent,
            f"Translate the following English Markdown to Japanese:\n\n{markdown_en}",
        )
        results["markdown_ja"] = markdown_ja

        # ---- Stage 3: Summary ----
        await _publish_progress(job_manager, job_id, 3)
        summary_agent = create_summary_agent(model)
        summary_ja = await _run_single_agent(
            summary_agent,
            (f"Create a summary for the following paper.\narXiv URL: {arxiv_url}\n\nPaper content:\n{markdown_ja}"),
        )
        results["summary_ja"] = summary_ja

        # ---- Stage 4: Packaging ----
        await _publish_progress(job_manager, job_id, 4)
        zip_path = create_zip_package(
            paper_en_md=markdown_en,
            paper_ja_md=markdown_ja,
            summary_ja_md=summary_ja,
            image_paths=image_paths,
            work_dir=work_dir,
        )

        # Upload to S3 and get presigned URL
        download_url = upload_to_s3(
            zip_path=zip_path,
            job_id=job_id,
            bucket_name=settings.S3_BUCKET_NAME,
            presigned_url_expiry=settings.S3_PRESIGNED_URL_EXPIRY,
        )
        results["download_url"] = download_url

        # ---- Done ----
        if job_manager is not None:
            await job_manager.set_result(job_id, download_url)

        logger.info("Pipeline [%s] completed successfully", job_id)

    except Exception:
        logger.exception("Pipeline [%s] failed", job_id)

        if job_manager is not None:
            await job_manager.set_error(job_id, "Pipeline execution failed")

        raise

    return results
