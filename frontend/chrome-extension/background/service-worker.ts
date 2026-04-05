/**
 * Background service worker for the Chrome extension.
 *
 * Handles message passing between content scripts and the popup.
 * Manages API communication with the backend service and
 * coordinates the conversion workflow.
 */

// ── Constants ───────────────────────────────────────────

const DEFAULT_API_URL = "http://localhost:8000";

// ── State ───────────────────────────────────────────────

interface JobState {
  jobId: string;
  arxivUrl: string;
  status: "processing" | "complete" | "error";
  progress: number;
}

let activeJob: JobState | null = null;

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
    };

    // Start listening to SSE in the background
    listenToSSE(jobId);

    return { success: true, jobId };
  } catch (err) {
    const message = err instanceof Error ? err.message : "Unknown error";
    return { success: false, error: message };
  }
}

async function listenToSSE(jobId: string): Promise<void> {
  const apiUrl = await getApiUrl();
  const url = `${apiUrl}/api/v1/jobs/${jobId}/stream`;

  try {
    const res = await fetch(url);
    if (!res.ok || !res.body) {
      throw new Error(`SSE connection failed: ${res.status}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const dataStr = line.slice(6).trim();
          if (!dataStr) continue;

          try {
            const data = JSON.parse(dataStr);
            handleSSEData(data, jobId);
          } catch {
            // skip malformed JSON
          }
        } else if (line.startsWith("event: ")) {
          const eventType = line.slice(7).trim();
          if (eventType === "complete") {
            handleConversionComplete(jobId);
            reader.cancel();
            return;
          }
        }
      }
    }
  } catch (err) {
    console.error("[service-worker] SSE error:", err);
    if (activeJob?.jobId === jobId) {
      activeJob.status = "error";
      broadcastToTabs({
        type: "CONVERSION_ERROR",
        jobId,
        error: err instanceof Error ? err.message : "SSE connection failed",
      });
      setBadgeText("!");
    }
  }
}

function handleSSEData(
  data: { step?: string; progress?: number; message?: string },
  jobId: string
): void {
  if (activeJob?.jobId !== jobId) return;

  const progress = data.progress ?? activeJob.progress;
  activeJob.progress = progress;

  const pct = Math.round(progress * 100);
  setBadgeText(`${pct}%`);

  broadcastToTabs({
    type: "CONVERSION_PROGRESS",
    jobId,
    progress,
    step: data.step || "",
    message: data.message || "",
  });
}

function handleConversionComplete(jobId: string): void {
  if (activeJob?.jobId === jobId) {
    activeJob.status = "complete";
    activeJob.progress = 1;
  }

  setBadgeText("");

  broadcastToTabs({
    type: "CONVERSION_COMPLETE",
    jobId,
  });

  // Store completed job for popup access
  chrome.storage.local.set({
    lastCompletedJob: { jobId, timestamp: Date.now() },
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
        getApiUrl().then((apiUrl) => {
          const downloadUrl = `${apiUrl}/api/v1/jobs/${jobId}/download`;
          chrome.tabs.create({ url: downloadUrl });
          sendResponse({ success: true });
        });
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
