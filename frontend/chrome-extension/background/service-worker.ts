/**
 * Background service worker for the Chrome extension.
 *
 * Owns conversion state for the extension. Tracks **multiple in-flight
 * jobs in parallel** keyed by arXiv URL — each tab can be running its
 * own translation simultaneously, and the popup looks up "the job for
 * this tab" when it opens.
 *
 * Progress tracking uses polling (GET /api/v1/jobs/{jobId}/status every
 * 3 s) instead of SSE, matching the backend's Lambda-compatible
 * polling design.
 */

// ── Constants ───────────────────────────────────────────

const DEFAULT_API_URL = "http://localhost:8000";
const POLL_INTERVAL_MS = 3000;
// Map serialised to chrome.storage.local under this key (replaces the
// historical singular ``activeJob`` entry; we migrate at startup).
const ACTIVE_JOBS_KEY = "activeJobs";
const LEGACY_ACTIVE_JOB_KEY = "activeJob";
// Drop persisted jobs older than this. Compared against ``startedAt`` (when the
// user initiated the conversion), so this needs to be generous enough that a
// user who walks away for a few hours still sees the download button when they
// return. The backend's presigned-URL/DDB-TTL boundaries are tighter (~1 h from
// upload time), so the link itself may expire before this threshold — but a
// stale-link 404 on click is a clearer failure than a vanished UI that hides
// the fact a translation ever ran.
const STALE_JOB_THRESHOLD_MS = 24 * 60 * 60 * 1000;

// ── State ───────────────────────────────────────────────

interface JobState {
  jobId: string;
  arxivUrl: string;
  status: "processing" | "complete" | "error";
  progress: number;
  downloadUrl: string | null;
  error: string | null;
  startedAt: number;
}

// In-memory job table. Keyed by ``arxivUrl`` so the popup — which knows
// the active tab's URL but not the backend job id — can do an O(1)
// lookup on open.
const activeJobs: Map<string, JobState> = new Map();

// One poll loop per job. Keyed by ``jobId`` because that's what the
// backend identifies polls with; multiple jobs poll independently.
const pollingIntervals: Map<string, ReturnType<typeof setInterval>> = new Map();

// ── Helpers ─────────────────────────────────────────────

async function getApiUrl(): Promise<string> {
  return new Promise((resolve) => {
    chrome.storage.local.get(["apiUrl"], (result) => {
      resolve(result.apiUrl || DEFAULT_API_URL);
    });
  });
}

/** Return the user-supplied Google API key from storage (or empty). */
async function getStoredApiKey(): Promise<string> {
  return new Promise((resolve) => {
    chrome.storage.local.get(["googleApiKey"], (result) => {
      resolve(typeof result.googleApiKey === "string" ? result.googleApiKey : "");
    });
  });
}

function setBadgeText(text: string): void {
  chrome.action.setBadgeText({ text });
  if (text) {
    chrome.action.setBadgeBackgroundColor({ color: "#6366f1" });
  }
}

/**
 * Recompute the badge based on the aggregate state of every tracked
 * job. The badge is a single global indicator (not per-tab), so:
 *
 *   - any errored job  → ``"!"`` (highest priority)
 *   - any processing   → count of in-flight jobs
 *   - otherwise        → cleared
 *
 * Per-job progress (the old ``"NN%"`` form) doesn't generalise to
 * multiple concurrent jobs; the popup is the right place for that
 * detail.
 */
function recomputeBadge(): void {
  let processing = 0;
  let hasError = false;
  for (const job of activeJobs.values()) {
    if (job.status === "error") {
      hasError = true;
    } else if (job.status === "processing") {
      processing += 1;
    }
  }
  if (hasError) {
    setBadgeText("!");
  } else if (processing > 0) {
    setBadgeText(String(processing));
  } else {
    setBadgeText("");
  }
}

function stopPolling(jobId: string): void {
  const interval = pollingIntervals.get(jobId);
  if (interval !== undefined) {
    clearInterval(interval);
    pollingIntervals.delete(jobId);
  }
}

/**
 * Persist the full job table to ``chrome.storage.local`` so popup
 * re-opens (and SW restarts) can recover state.
 *
 * MV3 service workers are killed after idle — especially once polling
 * stops on completion — wiping in-memory ``activeJobs``. Without
 * persistence, the next popup open would show a fresh UI even though
 * translations succeeded.
 *
 * ``Map`` doesn't JSON-serialise, so we round-trip through ``Object``.
 */
async function persistActiveJobs(): Promise<void> {
  const serialised = Object.fromEntries(activeJobs);
  await chrome.storage.local.set({ [ACTIVE_JOBS_KEY]: serialised });
}

/**
 * Restore the job table from storage on SW startup. Drops jobs older
 * than ``STALE_JOB_THRESHOLD_MS``, migrates the legacy single-job
 * schema (``activeJob`` → first entry of ``activeJobs``), and
 * restarts polling for anything still ``processing``.
 */
async function restoreActiveJobs(): Promise<void> {
  const result = await chrome.storage.local.get([
    ACTIVE_JOBS_KEY,
    LEGACY_ACTIVE_JOB_KEY,
  ]);

  // ── Migration from the historical singular schema ──
  // Earlier builds stored exactly one in-flight conversion under the
  // ``activeJob`` key. Carry that forward into the new map so users
  // who upgrade mid-translation don't lose their in-progress work.
  if (result[LEGACY_ACTIVE_JOB_KEY] && !result[ACTIVE_JOBS_KEY]) {
    const legacy = result[LEGACY_ACTIVE_JOB_KEY] as JobState;
    if (Date.now() - legacy.startedAt <= STALE_JOB_THRESHOLD_MS) {
      activeJobs.set(legacy.arxivUrl, legacy);
    }
    await chrome.storage.local.remove(LEGACY_ACTIVE_JOB_KEY);
    await persistActiveJobs();
  } else {
    const stored = (result[ACTIVE_JOBS_KEY] || {}) as Record<string, JobState>;
    const now = Date.now();
    let pruned = false;
    for (const [url, job] of Object.entries(stored)) {
      if (now - job.startedAt > STALE_JOB_THRESHOLD_MS) {
        pruned = true;
        continue;
      }
      activeJobs.set(url, job);
    }
    if (pruned) {
      await persistActiveJobs();
    }
  }

  for (const job of activeJobs.values()) {
    if (job.status === "processing") {
      startPolling(job.jobId, job.arxivUrl);
    }
  }
  recomputeBadge();
}

/**
 * Resolve a download URL into an absolute URL the browser can open from
 * the extension context. The backend returns either an absolute S3 presigned
 * URL (production) or a path-only string like ``/api/v1/jobs/.../download``
 * (local dev). A path-only string opened via ``chrome.tabs.create`` would
 * resolve against ``chrome-extension://<id>`` and 404 — prefix the configured
 * apiBaseUrl in that case.
 */
function resolveDownloadUrl(
  url: string | null,
  apiBaseUrl: string,
  jobId: string
): string {
  if (!url) return `${apiBaseUrl}/api/v1/jobs/${jobId}/download`;
  if (/^https?:\/\//i.test(url)) return url;
  return `${apiBaseUrl}${url.startsWith("/") ? "" : "/"}${url}`;
}

function findJobByJobId(jobId: string): JobState | undefined {
  for (const job of activeJobs.values()) {
    if (job.jobId === jobId) return job;
  }
  return undefined;
}

function snapshotJob(job: JobState): {
  jobId: string;
  arxivUrl: string;
  status: JobState["status"];
  progress: number;
  downloadUrl: string | null;
  error: string | null;
} {
  return {
    jobId: job.jobId,
    arxivUrl: job.arxivUrl,
    status: job.status,
    progress: job.progress,
    downloadUrl: job.downloadUrl,
    error: job.error,
  };
}

// ── API Calls ───────────────────────────────────────────

async function startConversion(
  arxivUrl: string
): Promise<{ success: boolean; jobId?: string; error?: string }> {
  const apiUrl = await getApiUrl();
  const apiKey = await getStoredApiKey();

  // The extension does not inherit the backend's .env credential — every
  // conversion must carry the user's own key.
  if (!apiKey) {
    return {
      success: false,
      error: "設定から Google API Key を入力してください",
    };
  }

  try {
    const res = await fetch(`${apiUrl}/api/v1/convert`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Google-Api-Key": apiKey,
      },
      body: JSON.stringify({ arxiv_url: arxivUrl }),
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: `API error (${res.status}): ${text}` };
    }

    const data = await res.json();
    const jobId: string = data.job_id;

    // If a job for this URL already exists (typical: retry from the
    // error UI), tear down its poll so the new one runs cleanly.
    const previous = activeJobs.get(arxivUrl);
    if (previous) {
      stopPolling(previous.jobId);
    }

    activeJobs.set(arxivUrl, {
      jobId,
      arxivUrl,
      status: "processing",
      progress: 0,
      downloadUrl: null,
      error: null,
      startedAt: Date.now(),
    });
    await persistActiveJobs();
    recomputeBadge();

    startPolling(jobId, arxivUrl);

    return { success: true, jobId };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return { success: false, error: message };
  }
}

/** Poll GET /api/v1/jobs/{jobId}/status every POLL_INTERVAL_MS milliseconds. */
function startPolling(jobId: string, arxivUrl: string): void {
  stopPolling(jobId);

  const interval = setInterval(() => {
    pollJobStatus(jobId, arxivUrl).catch((err) => {
      console.error("[service-worker] polling error:", err);
    });
  }, POLL_INTERVAL_MS);
  pollingIntervals.set(jobId, interval);

  // Kick off an immediate first poll instead of waiting one full interval.
  pollJobStatus(jobId, arxivUrl).catch((err) => {
    console.error("[service-worker] polling error:", err);
  });
}

async function pollJobStatus(jobId: string, arxivUrl: string): Promise<void> {
  // Guard: stop if the job under this URL has been replaced (e.g.
  // a retry kicked off a new jobId) or removed.
  const tracked = activeJobs.get(arxivUrl);
  if (!tracked || tracked.jobId !== jobId) {
    stopPolling(jobId);
    return;
  }

  const apiUrl = await getApiUrl();

  try {
    const res = await fetch(`${apiUrl}/api/v1/jobs/${jobId}/status`);
    if (!res.ok) {
      throw new Error(`Status fetch failed: ${res.status}`);
    }

    const data: {
      job_id: string;
      status: string;
      current_step: string | null;
      progress: number;
      message: string | null;
      error: string | null;
      download_url: string | null;
    } = await res.json();

    // Re-check after the await — the job may have been superseded.
    const current = activeJobs.get(arxivUrl);
    if (!current || current.jobId !== jobId) return;

    if (data.status === "completed") {
      stopPolling(jobId);
      await handleConversionComplete(jobId, arxivUrl, data.download_url);
      return;
    }

    if (data.status === "error") {
      stopPolling(jobId);
      current.status = "error";
      current.error = data.error || "変換中にエラーが発生しました";
      await persistActiveJobs();
      recomputeBadge();
      return;
    }

    // Still in progress — update state.
    handleStatusUpdate(data, jobId, arxivUrl);
  } catch (err) {
    console.error("[service-worker] poll error:", err);
    // Don't stop polling on transient network errors; keep retrying.
  }
}

function handleStatusUpdate(
  data: { current_step?: string | null; progress?: number; message?: string | null },
  jobId: string,
  arxivUrl: string
): void {
  const job = activeJobs.get(arxivUrl);
  if (!job || job.jobId !== jobId) return;

  // progress is already an integer 0–100 (matches API response).
  job.progress = data.progress ?? job.progress;
  // Per-job progress isn't shown on the badge in the multi-job world
  // (the badge shows aggregate state); the popup uses ``GET_STATUS``
  // for live values.
}

async function handleConversionComplete(
  jobId: string,
  arxivUrl: string,
  downloadUrl: string | null
): Promise<void> {
  const job = activeJobs.get(arxivUrl);
  if (job && job.jobId === jobId) {
    job.status = "complete";
    job.progress = 100;
    job.downloadUrl = downloadUrl;
    // Await persistence: MV3 SW can be idle-killed shortly after polling
    // stops, and a fire-and-forget set would race that shutdown — leaving
    // chrome.storage in the previous "processing" state so the next popup
    // reopen never sees the download button.
    await persistActiveJobs();
  }

  recomputeBadge();
}

// ── Message Listener ────────────────────────────────────

chrome.runtime.onMessage.addListener(
  (
    message: { type: string; [key: string]: unknown },
    _sender: chrome.runtime.MessageSender,
    sendResponse: (response: unknown) => void
  ) => {
    switch (message.type) {
      case "START_CONVERSION": {
        const arxivUrl = message.arxivUrl as string;
        startConversion(arxivUrl).then(sendResponse);
        return true; // async response
      }

      case "GET_STATUS": {
        // The popup passes the URL it cares about (typically the active
        // tab's URL). Wait for the post-startup restore so a freshly
        // respawned SW responds with the persisted snapshot, not its
        // empty in-memory state.
        const arxivUrl = message.arxivUrl as string | undefined;
        restorePromise.then(() => {
          if (!arxivUrl) {
            sendResponse(null);
            return;
          }
          const job = activeJobs.get(arxivUrl);
          sendResponse(job ? snapshotJob(job) : null);
        });
        return true; // async response
      }

      case "UPDATE_BADGE": {
        // Legacy hook from the popup. With the multi-job model the
        // badge is computed from aggregate state instead, so we just
        // recompute and ignore the requested text.
        recomputeBadge();
        sendResponse({ success: true });
        return false;
      }

      case "DOWNLOAD_RESULT": {
        const jobId = message.jobId as string;
        (async () => {
          // Ensure the job table is restored from storage so we use the
          // persisted download_url (presigned S3 URL or path-only local
          // URL) when the SW was killed and respawned between completion
          // and click.
          await restorePromise;
          const job = findJobByJobId(jobId);
          const storedUrl = job?.downloadUrl ?? null;
          const apiUrl = await getApiUrl();
          const url = resolveDownloadUrl(storedUrl, apiUrl, jobId);
          chrome.tabs.create({ url });
          sendResponse({ success: true });
        })();
        return true; // async response
      }

      default:
        sendResponse({ error: "Unknown message type" });
        return false;
    }
  }
);

// ── Install / Update ────────────────────────────────────

chrome.runtime.onInstalled.addListener(() => {
  console.log("[arXiv Translator] Extension installed/updated");
  setBadgeText("");
});

// ── Startup restore ─────────────────────────────────────
// Fire-and-forget restoration kicked off at module evaluation. Avoids
// top-level await, which can fail MV3 service-worker registration on some
// Chrome versions (status code 3). Handlers that need the restored state
// (GET_STATUS, DOWNLOAD_RESULT) await this promise before responding.
const restorePromise: Promise<void> = restoreActiveJobs().catch((err) => {
  console.error("[service-worker] restore failed:", err);
});
