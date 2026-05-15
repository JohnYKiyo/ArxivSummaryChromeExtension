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

// ── State ───────────────────────────────────────────────

interface JobState {
  jobId: string;
  arxivUrl: string;
  status: "processing" | "complete" | "error";
  progress: number;
  downloadUrl: string | null;
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
    };

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
      broadcastToTabs({
        type: "CONVERSION_ERROR",
        jobId,
        error: data.error || "変換中にエラーが発生しました",
      });
      setBadgeText("!");
      return;
    }

    // Still in progress — update state and broadcast.
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

  broadcastToTabs({
    type: "CONVERSION_PROGRESS",
    jobId,
    progress,
    step: data.current_step || "",
    message: data.message || "",
  });
}

function handleConversionComplete(jobId: string, downloadUrl: string | null): void {
  if (activeJob?.jobId === jobId) {
    activeJob.status = "complete";
    activeJob.progress = 100;
    activeJob.downloadUrl = downloadUrl;
  }

  setBadgeText("");

  broadcastToTabs({
    type: "CONVERSION_COMPLETE",
    jobId,
    downloadUrl,
  });

  // Store completed job for popup access
  chrome.storage.local.set({
    lastCompletedJob: { jobId, downloadUrl, timestamp: Date.now() },
  });
}

async function broadcastToTabs(message: Record<string, unknown>): Promise<void> {
  try {
    const tabs = await chrome.tabs.query({});
    for (const tab of tabs) {
      if (tab.id) {
        chrome.tabs.sendMessage(tab.id, message).catch(() => {
          // Tab might not have content script; ignore
        });
      }
    }
  } catch {
    // Ignore broadcast errors
  }
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
        sendResponse(
          activeJob
            ? {
                jobId: activeJob.jobId,
                arxivUrl: activeJob.arxivUrl,
                status: activeJob.status,
                progress: activeJob.progress,
                downloadUrl: activeJob.downloadUrl,
              }
            : null
        );
        return false;
      }

      case "UPDATE_BADGE": {
        const text = (message.text as string) || "";
        setBadgeText(text);
        sendResponse({ success: true });
        return false;
      }

      case "DOWNLOAD_RESULT": {
        const jobId = message.jobId as string;
        // Prefer the download_url from the completed job status response.
        // Fall back to constructing the local-dev endpoint URL if unavailable.
        const storedUrl =
          activeJob?.jobId === jobId ? activeJob.downloadUrl : null;

        if (storedUrl) {
          chrome.tabs.create({ url: storedUrl });
          sendResponse({ success: true });
        } else {
          getApiUrl().then((apiUrl) => {
            const fallbackUrl = `${apiUrl}/api/v1/jobs/${jobId}/download`;
            chrome.tabs.create({ url: fallbackUrl });
            sendResponse({ success: true });
          });
          return true; // async response
        }
        return false;
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
