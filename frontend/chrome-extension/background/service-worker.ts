/**
 * Background service worker for the Chrome extension.
 *
 * Handles message passing between content scripts and the popup.
 * Manages API communication with the backend service and
 * coordinates the conversion workflow.
 *
 * Progress tracking uses polling (GET /api/v1/jobs/{jobId}/status every 3 s)
 * instead of SSE, matching the backend's Lambda-compatible polling design.
 */

// ── Constants ───────────────────────────────────────────

const DEFAULT_API_URL = "http://localhost:8000";
const POLL_INTERVAL_MS = 3000;
const ACTIVE_JOB_KEY = "activeJob";
// Drop persisted jobs older than this — the backend's S3 presigned URL
// (S3_PRESIGNED_URL_EXPIRY) and DynamoDB job TTL (JOB_TTL_SECONDS) are both
// 1 h, so older entries can't be downloaded or re-queried anyway.
const STALE_JOB_THRESHOLD_MS = 60 * 60 * 1000;

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

let activeJob: JobState | null = null;
let pollingInterval: ReturnType<typeof setInterval> | null = null;

// ── Helpers ─────────────────────────────────────────────

async function getApiUrl(): Promise<string> {
  return new Promise((resolve) => {
    chrome.storage.local.get(["apiUrl"], (result) => {
      resolve(result.apiUrl || DEFAULT_API_URL);
    });
  });
}

function setBadgeText(text: string): void {
  chrome.action.setBadgeText({ text });
  if (text) {
    chrome.action.setBadgeBackgroundColor({ color: "#6366f1" });
  }
}

function stopPolling(): void {
  if (pollingInterval !== null) {
    clearInterval(pollingInterval);
    pollingInterval = null;
  }
}

/**
 * Persist activeJob to chrome.storage.local so popup re-opens (and SW
 * restarts) can recover the in-flight / completed state.
 *
 * MV3 service workers are killed after idle (especially after polling
 * stops on completion), wiping the in-memory ``activeJob``. Without
 * persistence, reopening the popup after completion shows a fresh UI
 * even though the translation finished successfully.
 */
async function persistActiveJob(): Promise<void> {
  if (activeJob) {
    await chrome.storage.local.set({ [ACTIVE_JOB_KEY]: activeJob });
  } else {
    await chrome.storage.local.remove(ACTIVE_JOB_KEY);
  }
}

/** Restore activeJob from storage on SW startup. */
async function restoreActiveJob(): Promise<void> {
  const result = await chrome.storage.local.get([ACTIVE_JOB_KEY]);
  const stored: JobState | undefined = result[ACTIVE_JOB_KEY];
  if (!stored) return;

  if (Date.now() - stored.startedAt > STALE_JOB_THRESHOLD_MS) {
    await chrome.storage.local.remove(ACTIVE_JOB_KEY);
    return;
  }

  activeJob = stored;
  if (stored.status === "processing") {
    startPolling(stored.jobId);
  } else if (stored.status === "error") {
    setBadgeText("!");
  } else {
    setBadgeText("");
  }
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

// ── API Calls ───────────────────────────────────────────

async function startConversion(
  arxivUrl: string
): Promise<{ success: boolean; jobId?: string; error?: string }> {
  const apiUrl = await getApiUrl();

  try {
    const res = await fetch(`${apiUrl}/api/v1/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ arxiv_url: arxivUrl }),
    });

    if (!res.ok) {
      const text = await res.text();
      return { success: false, error: `API error (${res.status}): ${text}` };
    }

    const data = await res.json();
    const jobId: string = data.job_id;

    activeJob = {
      jobId,
      arxivUrl,
      status: "processing",
      progress: 0,
      downloadUrl: null,
      error: null,
      startedAt: Date.now(),
    };
    await persistActiveJob();

    startPolling(jobId);

    return { success: true, jobId };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return { success: false, error: message };
  }
}

/** Poll GET /api/v1/jobs/{jobId}/status every POLL_INTERVAL_MS milliseconds. */
function startPolling(jobId: string): void {
  stopPolling();

  pollingInterval = setInterval(() => {
    pollJobStatus(jobId).catch((err) => {
      console.error("[service-worker] polling error:", err);
    });
  }, POLL_INTERVAL_MS);

  // Kick off an immediate first poll instead of waiting one full interval.
  pollJobStatus(jobId).catch((err) => {
    console.error("[service-worker] polling error:", err);
  });
}

async function pollJobStatus(jobId: string): Promise<void> {
  // Guard: stop if the active job changed while we were awaiting.
  if (activeJob?.jobId !== jobId) {
    stopPolling();
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

    if (activeJob?.jobId !== jobId) return;

    if (data.status === "completed") {
      stopPolling();
      handleConversionComplete(jobId, data.download_url);
      return;
    }

    if (data.status === "error") {
      stopPolling();
      activeJob.status = "error";
      activeJob.error = data.error || "変換中にエラーが発生しました";
      await persistActiveJob();
      setBadgeText("!");
      return;
    }

    // Still in progress — update state.
    handleStatusUpdate(data, jobId);
  } catch (err) {
    console.error("[service-worker] poll error:", err);
    // Don't stop polling on transient network errors; keep retrying.
  }
}

function handleStatusUpdate(
  data: { current_step?: string | null; progress?: number; message?: string | null },
  jobId: string
): void {
  if (activeJob?.jobId !== jobId) return;

  // progress is already an integer 0–100 (matches API response).
  const progress = data.progress ?? activeJob.progress;
  activeJob.progress = progress;

  setBadgeText(`${progress}%`);
}

function handleConversionComplete(jobId: string, downloadUrl: string | null): void {
  if (activeJob?.jobId === jobId) {
    activeJob.status = "complete";
    activeJob.progress = 100;
    activeJob.downloadUrl = downloadUrl;
    persistActiveJob().catch((err) =>
      console.error("[service-worker] persist failed:", err)
    );
  }

  setBadgeText("");
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
        // Wait for the post-startup restore to finish so the popup gets the
        // persisted state (not just whatever is in memory) on SW respawn.
        restorePromise.then(() => {
          sendResponse(
            activeJob
              ? {
                  jobId: activeJob.jobId,
                  arxivUrl: activeJob.arxivUrl,
                  status: activeJob.status,
                  progress: activeJob.progress,
                  downloadUrl: activeJob.downloadUrl,
                  error: activeJob.error,
                }
              : null
          );
        });
        return true; // async response
      }

      case "UPDATE_BADGE": {
        const text = (message.text as string) || "";
        setBadgeText(text);
        sendResponse({ success: true });
        return false;
      }

      case "DOWNLOAD_RESULT": {
        const jobId = message.jobId as string;
        (async () => {
          // Ensure activeJob is restored from storage so we use the persisted
          // download_url (presigned S3 URL or path-only local URL) when SW was
          // killed and respawned between completion and click.
          await restorePromise;
          const storedUrl =
            activeJob?.jobId === jobId ? activeJob.downloadUrl : null;
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
const restorePromise: Promise<void> = restoreActiveJob().catch((err) => {
  console.error("[service-worker] restore failed:", err);
});
