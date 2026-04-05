"""OrchestratorAgent - Sequential pipeline controller.

Coordinates the execution of all agents in the translation pipeline:
TexFetchAgent -> Tex2MarkdownAgent -> TranslationAgent -> SummaryAgent.
Reports progress via SSE events at each stage transition.
Implemented as a Google ADK SequentialAgent with sub-agents, plus a
``run_pipeline`` helper for programmatic invocation with SSE support.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google.adk.agents import LlmAgent
from google.adk.agents.sequential_agent import SequentialAgent
from google.adk.runners import InMemoryRunner
from google.genai import types as genai_types

from src.agents.summary import create_summary_agent
from src.agents.tex2markdown import create_tex2markdown_agent
from src.agents.tex_fetch import create_tex_fetch_agent
from src.agents.translation import create_translation_agent
from src.config import get_settings
from src.services.job_manager import JobStatus
from src.services.sse import SSEEvent
from src.tools.arxiv import fetch_arxiv_paper
from src.tools.packaging import create_zip_package

if TYPE_CHECKING:
    from src.services.job_manager import JobManager
    from src.services.sse import EventBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pipeline stages used for SSE progress reporting
# ---------------------------------------------------------------------------

STAGES = [
    {"name": "tex_fetch", "label": "arXivからTeXソースを取得中...", "progress": 10, "status": JobStatus.TEX_FETCH},
    {"name": "tex2markdown", "label": "Markdown変換中...", "progress": 30, "status": JobStatus.TEX2MARKDOWN},
    {"name": "translation", "label": "日本語翻訳中...", "progress": 60, "status": JobStatus.TRANSLATION},
    {"name": "summary", "label": "要約生成中...", "progress": 80, "status": JobStatus.SUMMARY},
    {"name": "packaging", "label": "ZIP作成中...", "progress": 95, "status": JobStatus.PACKAGING},
]


def create_orchestrator_agent(model: str) -> SequentialAgent:
    """Create the top-level orchestrator as a :class:`SequentialAgent`.

    The orchestrator runs four sub-agents in sequence.  Each sub-agent
    stores its output in the shared session state via ``output_key``,
    so downstream agents can reference upstream results using
    ``{output_key}`` placeholders in their instructions.

    Args:
        model: The LLM model identifier.

    Returns:
        A configured :class:`SequentialAgent`.
    """
    tex_fetch = create_tex_fetch_agent(model)
    tex2md = create_tex2markdown_agent(model)
    translation = create_translation_agent(model)
    summary = create_summary_agent(model)

    return SequentialAgent(
        name="OrchestratorAgent",
        description=(
            "Orchestrates the full arXiv paper translation pipeline: "
            "fetch TeX, convert to Markdown, translate to Japanese, "
            "and generate a summary."
        ),
        sub_agents=[tex_fetch, tex2md, translation, summary],
    )


# ---------------------------------------------------------------------------
# Programmatic pipeline execution with SSE progress
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
    event_bus: EventBus | None,
    job_manager: JobManager | None,
    job_id: str,
    stage_index: int,
) -> None:
    """Publish an SSE progress event and update job status.

    Args:
        event_bus: Optional SSE event bus for real-time progress.
        job_manager: Optional job manager for status updates.
        job_id: The job identifier.
        stage_index: Index into :data:`STAGES`.
    """
    stage = STAGES[stage_index]

    if job_manager is not None:
        await job_manager.update_status(job_id, stage["status"])

    if event_bus is not None:
        event = SSEEvent(
            event=stage["name"],
            step=stage["name"],
            message=stage["label"],
            progress=stage["progress"],
        )
        await event_bus.publish(job_id, event)

    logger.info("Pipeline [%s] stage=%s progress=%d%%", job_id, stage["name"], stage["progress"])


async def run_pipeline(
    arxiv_url: str,
    job_id: str,
    event_bus: EventBus | None = None,
    job_manager: JobManager | None = None,
) -> dict[str, Any]:
    """Execute the full translation pipeline with SSE progress reporting.

    Runs each agent in sequence, publishing progress events between stages.
    The TeX fetch stage calls the arXiv tool directly (no LLM needed),
    while subsequent stages use LLM-based agents.

    Args:
        arxiv_url: The arXiv paper URL to process.
        job_id: Unique job identifier for progress tracking.
        event_bus: Optional SSE event bus for real-time progress.
        job_manager: Optional job manager for status updates.

    Returns:
        A dict containing:
          - ``markdown_en``: English Markdown content.
          - ``markdown_ja``: Japanese Markdown content.
          - ``summary_ja``: Japanese summary.
          - ``zip_path``: Path to the output ZIP archive (or ``None``).
    """
    settings = get_settings()
    model = settings.LLM_MODEL

    results: dict[str, Any] = {
        "markdown_en": None,
        "markdown_ja": None,
        "summary_ja": None,
        "zip_path": None,
    }

    try:
        # ---- Stage 0: Fetch TeX ----
        await _publish_progress(event_bus, job_manager, job_id, 0)

        # Fetch directly — no LLM needed for downloading/extracting files.
        tex_content, image_paths, work_dir = fetch_arxiv_paper(arxiv_url)
        image_paths_str = "\n".join(str(p) for p in image_paths)

        # ---- Stage 1: TeX -> Markdown ----
        await _publish_progress(event_bus, job_manager, job_id, 1)
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
        await _publish_progress(event_bus, job_manager, job_id, 2)
        translation_agent = create_translation_agent(model)
        markdown_ja = await _run_single_agent(
            translation_agent,
            f"Translate the following English Markdown to Japanese:\n\n{markdown_en}",
        )
        results["markdown_ja"] = markdown_ja

        # ---- Stage 3: Summary ----
        await _publish_progress(event_bus, job_manager, job_id, 3)
        summary_agent = create_summary_agent(model)
        summary_ja = await _run_single_agent(
            summary_agent,
            (
                f"Create a summary for the following paper.\n"
                f"arXiv URL: {arxiv_url}\n\n"
                f"Paper content:\n{markdown_ja}"
            ),
        )
        results["summary_ja"] = summary_ja

        # ---- Stage 4: Packaging ----
        await _publish_progress(event_bus, job_manager, job_id, 4)
        zip_path = create_zip_package(
            paper_en_md=markdown_en,
            paper_ja_md=markdown_ja,
            summary_ja_md=summary_ja,
            image_paths=image_paths,
            work_dir=work_dir,
        )
        results["zip_path"] = str(zip_path)

        # ---- Done ----
        if job_manager is not None:
            await job_manager.set_result(job_id, zip_path)

        if event_bus is not None:
            complete_event = SSEEvent(
                event="complete",
                step="done",
                message="処理が完了しました",
                progress=100,
                data={"download_url": f"/api/v1/jobs/{job_id}/download"},
            )
            await event_bus.publish(job_id, complete_event)
            await event_bus.remove(job_id)

        logger.info("Pipeline [%s] completed successfully", job_id)

    except Exception:
        logger.exception("Pipeline [%s] failed", job_id)

        if job_manager is not None:
            await job_manager.set_error(job_id, "Pipeline execution failed")

        if event_bus is not None:
            error_event = SSEEvent(
                event="error",
                step="error",
                message="処理中にエラーが発生しました",
                progress=0,
                data={"message": "Pipeline execution failed"},
            )
            await event_bus.publish(job_id, error_event)
            await event_bus.remove(job_id)

        raise

    return results
