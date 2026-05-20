# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

arXiv Translator is a service that fetches arXiv paper TeX sources, converts them to Markdown, translates to Japanese, and produces a structured summary. It ships as both a **React web UI** and a **Chrome extension**, backed by a Python FastAPI service deployed to AWS Lambda.

## Branch strategy

**Branch from `develop`, not `main`** (Git Flow). `main` is reserved for releases.

## Common commands

### Full-stack local dev (recommended)
```bash
cp .env.example .env                # set GOOGLE_API_KEY
docker compose up --build           # backend + web + DynamoDB Local
```
- Web UI: http://localhost:5173 — Backend: http://localhost:8000 — DynamoDB Local: http://localhost:8100
- Hot reload: backend (`backend/src/`) via uvicorn `--reload`, frontend (`frontend/web/src/`) via Vite

**⚠ Docker Desktop on macOS — uvicorn `--reload` quietly misses file edits.** The
bind mount `./backend/src:/app/src` does not propagate inotify events to the
container reliably, so `WatchFiles` never sees the change and the Python
process keeps the old module in memory — even though `docker exec ... md5sum`
shows the in-container file is up to date. Symptom: a code change to
`backend/src/...` does not affect the next pipeline run, and the logs are
silent (no `Detected change`/`Reloading` line). Fix:
`docker compose restart backend`. Verify it picked up the edit by checking
for a fresh `Started reloader process` line in `docker logs backend` after
you saved the file. Do NOT trust the file mtime or md5 alone — they update,
but the running interpreter does not.

### Backend (Python 3.12+, FastAPI)
```bash
cd backend
pip install -e ".[dev]"                          # editable install with dev deps
uvicorn src.main:app --reload --port 8000        # standalone run
pytest tests/ -v                                  # all tests
pytest tests/test_agents/ -v                      # one suite
pytest tests/test_agents/test_orchestrator.py::test_name -v   # one test
pytest tests/ --cov=src --cov-report=html        # coverage
ruff check src/ tests/                            # lint (configured in pyproject.toml)
ruff format src/ tests/                           # format
mypy src/                                         # strict type check
```
LLM evaluation tests hit the real Gemini API (require `GOOGLE_API_KEY`). Only the LLM-driven stages have evals — TeX→Markdown is now deterministic via pandoc and doesn't need one:
```bash
python -m tests.eval.eval_translation
python -m tests.eval.eval_summary
```

### Frontend web (React 18 + Vite + TS + Tailwind)
```bash
cd frontend/web
npm install
npm run dev                  # vite dev server (proxies /api/* to :8000)
npm run build                # tsc -b && vite build
npm run lint                 # eslint
npx tsc --noEmit             # type check only
```

### Chrome extension (esbuild + TS)
```bash
cd frontend/chrome-extension
npm install
npm run build                # bundle to dist/
npm run watch                # rebuild on change
npx tsc --noEmit             # type check
```
Load `frontend/chrome-extension/dist/` as an unpacked extension in `chrome://extensions/`. The manifest references `dist/<path>.js`, so you must rebuild after editing TS sources.

### Infrastructure (AWS CDK, Python)
```bash
cd infrastructure
pip install -r requirements.txt
cdk synth                    # generate CloudFormation
cdk deploy --all             # deploy all stacks
```

## Architecture

### Backend pipeline (key reading: `backend/src/agents/orchestrator.py`)

The conversion pipeline is a linear async function — **not** an ADK `SequentialAgent`. This is intentional: each stage writes progress to DynamoDB between calls, and the source-to-Markdown step is a deterministic library transform (not an LLM agent), so a SequentialAgent doesn't fit the shape.

`run_pipeline()` in `orchestrator.py` is the single source of truth:

```
fetch_arxiv_paper (src/tools/arxiv.py)
  ├ kind="html" → html_to_markdown   (src/tools/html_to_markdown.py — markdownify)
  └ kind="tex"  → tex_to_markdown    (src/tools/tex_to_markdown.py  — pandoc subprocess)
                              ↓
                  TranslationAgent   (src/agents/translation.py — LLM)
                              ↓
                  SummaryAgent       (src/agents/summary.py     — LLM)
                              ↓
                  create_zip_package (src/tools/packaging.py)
                              ↓
                  upload_to_s3 (prod) OR keep local (dev)
```

Source → Markdown is **deterministic** (library, no LLM): `markdownify` for HTML, `pandoc` for TeX. Only translation and summary call the LLM. Each LLM agent runs via an ephemeral `InMemoryRunner` inside `_run_single_agent()`; progress is reported via `JobManager.update_progress()` between stages using the `STAGES` table at the top of `orchestrator.py`.

### Source fetching (`backend/src/tools/arxiv.py`)

`fetch_arxiv_paper()` returns a `PaperSource` dataclass with `kind: "html" | "tex"`:

1. **HTML-first** — try `arxiv.org/html/<id>`; if 200 HTML, use it. arxiv's HTML is LaTeXML-generated and converts well via `markdownify` with the `<article class="ltx_document">` selector and ``alttext`` extraction for math.
2. **TeX fallback** — fetch `arxiv.org/e-print/<id>`. For multi-file submissions, `\input{...}` / `\include{...}` directives are recursively expanded so pandoc operates on a single self-contained document. Main file is picked by preferring files containing both `\documentclass` and `\begin{document}`.
3. **PDF-only** — when the e-print is a PDF (no real TeX source) or just a `\includepdf` wrapper, raise `PdfOnlyPaperError`. The orchestrator catches this and surfaces a clear Japanese error message to the user.

### Agent output extraction

`_extract_final_text_async` **concatenates every non-thought text part** across all ADK events. Earlier strategies (last-event-only, longest-text-only) silently dropped most of the answer because Gemini streams long outputs as many small `Part` objects rather than one big text. The current rule:

- Skip parts where `part.thought` is `True` (Gemini's internal reasoning).
- Concatenate every other `part.text` in arrival order.

Translation outputs in particular arrive as 15–25 parts for a typical paper; picking any single one loses the rest.

### Two-Lambda deployment (`infrastructure/cdk/stacks/backend_stack.py`)

- **API Lambda** (`src.main:handler` via Mangum) — 256 MB, 29 s timeout. Handles HTTP requests. On `POST /convert` it creates a job in DynamoDB and asynchronously invokes the pipeline Lambda. **Never runs the pipeline itself in production** (29 s API Gateway limit).
- **Pipeline Lambda** (`src.pipeline_handler:handler`) — 1024 MB, 15 min timeout, 2 GB ephemeral storage. Runs `run_pipeline()` to completion.

In **local development** (`PIPELINE_LAMBDA_NAME` unset), `routes.py` runs the pipeline as an `asyncio.create_task` instead — see `_run_pipeline_task()`.

### Polling, not SSE

Progress used to stream via SSE; this was removed for Lambda compatibility. The frontend polls `GET /api/v1/jobs/{job_id}/status` every 3 s. Progress is an **integer 0–100** (not float 0–1). The endpoint `/jobs/{job_id}/stream` no longer exists.

### Local vs production: S3 conditional

`run_pipeline()` checks `settings.S3_BUCKET_NAME`:
- **Set** (production): upload ZIP to S3, return presigned URL as `download_url`
- **Empty** (local dev): keep ZIP on disk, set `download_url = /api/v1/jobs/{job_id}/download`, store path in `Job.local_result_path`. The `GET /jobs/{job_id}/download` endpoint serves the file via FileResponse and is **only available when `S3_BUCKET_NAME` is empty** (returns 404 otherwise).

Cognito auth is similarly bypassed when `APP_ENV=development` — see `src/api/auth.py`.

### Job state (`backend/src/services/job_manager.py`)

`JobManager` is a thin DynamoDB wrapper. All state mutations are atomic DynamoDB `update_item` calls — no application-level locking needed. TTL is set on the `ttl` attribute for auto-cleanup. The same class works against DynamoDB Local (via `endpoint_url`) and real DynamoDB.

### Frontend status polling (`frontend/web/src/features/job-status/hooks/useJobPolling.ts`)

Single `setInterval` at 3 s. Stops when `status === 'completed' | 'error'`. Same pattern is duplicated in the Chrome extension's `background/service-worker.ts` (`startPolling`/`pollJobStatus`) and `popup/popup.tsx`.

### Chrome extension structure

- `background/service-worker.ts` — runs polling, owns the `activeJob` state, broadcasts `CONVERSION_PROGRESS|COMPLETE|ERROR` to all tabs
- `content/content.ts` — injected on `arxiv.org/abs/*` pages only; renders the floating "翻訳" button
- `popup/popup.tsx` — toolbar popup; on open, calls `GET_STATUS` on the service worker to resume in-progress jobs

The extension stores `apiUrl` and `lastCompletedJob` in `chrome.storage.local`. `download_url` from the status response (presigned S3 URL in prod, local `/download` path in dev) is used directly for the download — do not reconstruct it.

## Design principles

Follow **SOLID, YAGNI, KISS, DRY, SoC**. These aren't decorations — they map to concrete rules below. When in doubt, prefer the simpler option and call it out.

- **YAGNI** — Do not add config flags, abstract base classes, plugin hooks, or "for future use" parameters. If a need is one paper away, don't build the framework now. Recent removals along this axis: the `SequentialAgent` / `TexFetchAgent` / `create_orchestrator_agent()`, and the `Tex2MarkdownAgent` (replaced by a pandoc subprocess once we realised a deterministic transform handled every case the LLM was doing).
- **KISS** — Prefer a flat function over a class hierarchy. Prefer one file over five. `run_pipeline()` is intentionally a linear async function, not a state machine. When a deterministic library transform (markdownify, pandoc) replaces an LLM, take that trade — fewer calls, lower cost, more consistent output.
- **DRY** — But don't deduplicate things that merely look similar. The polling logic in `useJobPolling.ts` (web) and `service-worker.ts` (extension) is duplicated *on purpose* — they run in different runtimes with different lifecycle constraints. Shared types/constants → extract; shared coincidence → leave alone.
- **SoC** — Keep the layer boundaries strict:
  - `agents/` = LLM prompt construction + ADK runner glue. Currently `translation`, `summary`, and `orchestrator`. **No** DynamoDB, **no** filesystem, **no** HTTP.
  - `tools/` = pure I/O and deterministic transforms. `arxiv` (fetch + TeX extraction), `html_to_markdown` (markdownify), `tex_to_markdown` (pandoc), `packaging` (ZIP + S3). **No** LLM calls.
  - `services/job_manager.py` = the *only* code that talks to DynamoDB.
  - `services/pipeline_dispatcher.py` = the *only* code that decides between Lambda-invoke (prod) and in-process asyncio (dev).
  - `api/routes.py` = HTTP shape only; delegates to `JobManager` and the dispatcher.
  - `agents/orchestrator.py` = the only place that wires `tools/` and `agents/` together.
  Adding a DynamoDB import to `agents/` or an LLM call to `tools/` is a layering violation — push it up to the orchestrator.
- **SOLID**:
  - **SRP** — One reason to change per module. If a PR touches both prompt text and DynamoDB schema, it's probably two PRs.
  - **OCP / LSP / ISP** — Rarely relevant in this codebase (few inheritance hierarchies). Don't invent abstractions to satisfy them.
  - **DIP** — `JobManager` and the `PipelineDispatcher` are injected into routes via `init_routes()`. `JobManager` is also passed by parameter to `run_pipeline()`. Keep it that way; don't reach for module-level singletons in new code.

When a change tempts you to break one of these (e.g., "I'll just import `boto3` here for one call"), stop and route it through the proper layer instead.

## Conventions to keep

- **Ruff**: line length 120, target py312, rule set `E F I N W UP B A SIM`. Run `ruff check` before committing.
- **Mypy**: strict mode is on. New code must type-check.
- **Pytest**: `asyncio_mode = "auto"` — async tests don't need `@pytest.mark.asyncio`.
- **Progress is int 0–100**, never float 0–1. The `* 100` conversion in old code was a bug from the SSE era.
- **Don't add a `SequentialAgent`** to replace `run_pipeline()` — it was tried and removed because progress writes can't interleave inside an ADK sequential run, *and* because half the stages are deterministic library calls (not LLMs) that don't belong in an agent flow at all.
- **ADK prompt placeholders**: agent `instruction` strings have `{var_name}` substituted from session state when `var_name` is a valid Python identifier. If you write `{filename}` or `{figure}` as a literal example in a prompt, ADK will raise `KeyError`. Either (a) use a non-identifier inside the braces — `{...}` is the easiest — or (b) describe the syntax in prose. The existing prompts in `agents/translation.py` and `agents/summary.py` follow this rule.
- **Agent output extraction**: concatenate every non-thought text part across all events (`_extract_final_text_async`). For long outputs (translation of a full paper) Gemini emits 15–25 short parts; picking only one — last, first, or longest — drops most of the answer.
- **Long outputs need `max_output_tokens`**: Gemini 2.5 Pro defaults to ~8K output tokens which silently truncates a typical paper translation. Set `max_output_tokens=65536` in the agent's `generate_content_config` whenever the output can be book-length.
