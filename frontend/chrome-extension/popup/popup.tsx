/**
 * Chrome extension popup logic.
 *
 * Displays the extension UI when the toolbar icon is clicked.
 * Auto-fills the current tab's arXiv URL, shows conversion progress
 * via polling (GET /api/v1/jobs/{jobId}/status every 3 s), and provides
 * a download link for completed jobs.
 */

// ── Constants ───────────────────────────────────────────

const DEFAULT_API_URL = "http://localhost:8000";
const ARXIV_URL_PATTERN = /^https?:\/\/(www\.)?arxiv\.org\/(abs|pdf)\/[\d.]+/;
const POLL_INTERVAL_MS = 3000;

// ── Types ───────────────────────────────────────────────

interface ConvertResponse {
  job_id: string;
}

interface StatusResponse {
  job_id: string;
  status: string;
  current_step: string | null;
  progress: number; // integer 0–100
  message: string | null;
  error: string | null;
  download_url: string | null;
}

// ── DOM Elements ────────────────────────────────────────

const $ = <T extends HTMLElement>(id: string): T =>
  document.getElementById(id) as T;

const urlInput = $<HTMLInputElement>("arxiv-url");
const urlHint = $<HTMLParagraphElement>("url-hint");
const submitBtn = $<HTMLButtonElement>("submit-btn");
const progressSection = $<HTMLDivElement>("progress-section");
const progressLabel = $<HTMLSpanElement>("progress-label");
const progressPercent = $<HTMLSpanElement>("progress-percent");
const progressBar = $<HTMLDivElement>("progress-bar");
const progressStep = $<HTMLParagraphElement>("progress-step");
const downloadSection = $<HTMLDivElement>("download-section");
const downloadBtn = $<HTMLButtonElement>("download-btn");
const errorSection = $<HTMLDivElement>("error-section");
const errorMessage = $<HTMLParagraphElement>("error-message");
const retryBtn = $<HTMLButtonElement>("retry-btn");
const settingsToggle = $<HTMLButtonElement>("settings-toggle");
const settingsPanel = $<HTMLDivElement>("settings-panel");
const apiUrlInput = $<HTMLInputElement>("api-url");
const saveSettingsBtn = $<HTMLButtonElement>("save-settings");

// ── State ───────────────────────────────────────────────

let currentJobId: string | null = null;
let pollingInterval: ReturnType<typeof setInterval> | null = null;
let apiBaseUrl: string = DEFAULT_API_URL;

// ── Helpers ─────────────────────────────────────────────

function isArxivUrl(url: string): boolean {
  return ARXIV_URL_PATTERN.test(url);
}

function show(el: HTMLElement): void {
  el.classList.remove("hidden");
}

function hide(el: HTMLElement): void {
  el.classList.add("hidden");
}

function resetUI(): void {
  hide(progressSection);
  hide(downloadSection);
  hide(errorSection);
  submitBtn.disabled = false;
  submitBtn.textContent = "翻訳を開始";
  progressBar.style.width = "0%";
  progressPercent.textContent = "0%";
  progressStep.textContent = "";
}

function showError(msg: string): void {
  errorMessage.textContent = msg;
  show(errorSection);
  hide(progressSection);
  submitBtn.disabled = false;
  submitBtn.textContent = "翻訳を開始";
}

function stopPolling(): void {
  if (pollingInterval !== null) {
    clearInterval(pollingInterval);
    pollingInterval = null;
  }
}

/**
 * Resolve a download URL into an absolute URL the browser can open from
 * the extension context. Backend returns either an absolute S3 presigned
 * URL (production) or a path-only string like ``/api/v1/jobs/.../download``
 * (local dev). A path-only string opened via ``chrome.tabs.create`` resolves
 * against ``chrome-extension://<id>`` and 404s — prefix apiBaseUrl in that case.
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

// ── API ─────────────────────────────────────────────────

async function getApiUrl(): Promise<string> {
  return new Promise((resolve) => {
    chrome.storage.local.get(["apiUrl"], (result) => {
      resolve(result.apiUrl || DEFAULT_API_URL);
    });
  });
}

async function submitConversion(arxivUrl: string): Promise<ConvertResponse> {
  const url = `${apiBaseUrl}/api/v1/convert`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ arxiv_url: arxivUrl }),
  });

  if (!res.ok) {
    const text = await res.text();
    throw new Error(`API error (${res.status}): ${text}`);
  }

  return res.json();
}

/** Start polling GET /status for the given job every POLL_INTERVAL_MS ms. */
function startPolling(jobId: string): void {
  stopPolling();

  // Immediate first poll, then repeat.
  pollJobStatus(jobId);
  pollingInterval = setInterval(() => {
    pollJobStatus(jobId);
  }, POLL_INTERVAL_MS);
}

async function pollJobStatus(jobId: string): Promise<void> {
  // Guard: abort if another job was started while we were awaiting.
  if (currentJobId !== jobId) {
    stopPolling();
    return;
  }

  try {
    const res = await fetch(`${apiBaseUrl}/api/v1/jobs/${jobId}/status`);
    if (!res.ok) {
      throw new Error(`Status fetch failed: ${res.status}`);
    }

    const data: StatusResponse = await res.json();

    if (currentJobId !== jobId) return;

    if (data.status === "completed") {
      stopPolling();
      onConversionComplete(jobId, data.download_url);
      return;
    }

    if (data.status === "error") {
      stopPolling();
      showError(data.error || "変換中にエラーが発生しました");
      return;
    }

    // Still running — update progress UI.
    updateProgress(data);
  } catch (err) {
    // Transient network error — keep polling; don't show error yet.
    console.error("[popup] poll error:", err);
  }
}

// ── UI Updates ──────────────────────────────────────────

function updateProgress(data: StatusResponse): void {
  // progress is already an integer 0–100 (API change from float 0–1).
  const pct = data.progress;
  progressBar.style.width = `${pct}%`;
  progressPercent.textContent = `${pct}%`;
  progressLabel.textContent = data.current_step || "";
  progressStep.textContent = data.message || "";

  // Update badge via background script
  chrome.runtime.sendMessage({
    type: "UPDATE_BADGE",
    text: `${pct}%`,
  });
}

function onConversionComplete(jobId: string, downloadUrl: string | null): void {
  progressBar.style.width = "100%";
  progressPercent.textContent = "100%";
  progressLabel.textContent = "完了";
  progressStep.textContent = "翻訳が完了しました";

  show(downloadSection);
  submitBtn.disabled = false;
  submitBtn.textContent = "翻訳を開始";

  chrome.runtime.sendMessage({
    type: "UPDATE_BADGE",
    text: "",
  });

  downloadBtn.onclick = () => {
    // Use the download_url returned by the API (presigned S3 URL in production,
    // path-only /download endpoint in development — needs apiBaseUrl prefix).
    const url = resolveDownloadUrl(downloadUrl, apiBaseUrl, jobId);
    chrome.tabs.create({ url });
  };
}

// ── Event Handlers ──────────────────────────────────────

async function handleSubmit(): Promise<void> {
  const arxivUrl = urlInput.value.trim();
  if (!arxivUrl) {
    showError("URLを入力してください");
    return;
  }
  if (!isArxivUrl(arxivUrl)) {
    showError("有効なarXiv URLを入力してください");
    return;
  }

  resetUI();
  submitBtn.disabled = true;
  submitBtn.textContent = "処理中...";
  show(progressSection);
  hide(errorSection);

  try {
    const response = await submitConversion(arxivUrl);
    currentJobId = response.job_id;
    startPolling(currentJobId);
  } catch (err) {
    const message = err instanceof Error ? err.message : "不明なエラー";
    showError(message);
  }
}

function handleUrlInput(): void {
  const value = urlInput.value.trim();
  if (value && isArxivUrl(value)) {
    submitBtn.disabled = false;
    urlHint.textContent = "有効なarXiv URLです";
    urlHint.classList.add("detected");
  } else if (value) {
    submitBtn.disabled = true;
    urlHint.textContent = "arXiv URLの形式で入力してください";
    urlHint.classList.remove("detected");
  } else {
    submitBtn.disabled = true;
    urlHint.textContent = "";
    urlHint.classList.remove("detected");
  }
}

// ── Initialization ──────────────────────────────────────

async function init(): Promise<void> {
  // Load saved API URL
  apiBaseUrl = await getApiUrl();
  apiUrlInput.value = apiBaseUrl;

  // Try to auto-fill from the current tab
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (tab?.url && isArxivUrl(tab.url)) {
      urlInput.value = tab.url;
      urlHint.textContent = "現在のタブから検出しました";
      urlHint.classList.add("detected");
      submitBtn.disabled = false;
    }
  } catch {
    // Not in a context where we can query tabs; ignore
  }

  // Restore the active job from the background service worker so closing
  // and reopening the popup does not lose in-progress, completed, or errored
  // jobs. The SW persists activeJob to chrome.storage.local, so this works
  // even if the worker was killed between popup opens.
  try {
    const response = await chrome.runtime.sendMessage({ type: "GET_STATUS" });
    if (response?.jobId) {
      const restoredJobId: string = response.jobId;
      currentJobId = restoredJobId;
      if (response.arxivUrl) {
        urlInput.value = response.arxivUrl;
        urlHint.textContent = "前回の翻訳ジョブを復元しました";
        urlHint.classList.add("detected");
      }

      if (response.status === "processing") {
        submitBtn.disabled = true;
        submitBtn.textContent = "処理中...";
        show(progressSection);
        const pct: number = response.progress ?? 0;
        progressBar.style.width = `${pct}%`;
        progressPercent.textContent = `${pct}%`;
        startPolling(restoredJobId);
      } else if (response.status === "complete") {
        show(progressSection);
        onConversionComplete(restoredJobId, response.downloadUrl ?? null);
      } else if (response.status === "error") {
        showError(response.error || "変換中にエラーが発生しました");
      }
    }
  } catch {
    // No active job
  }

  // Event listeners
  submitBtn.addEventListener("click", handleSubmit);
  urlInput.addEventListener("input", handleUrlInput);
  retryBtn.addEventListener("click", () => {
    resetUI();
    handleSubmit();
  });

  settingsToggle.addEventListener("click", () => {
    settingsPanel.classList.toggle("hidden");
  });

  saveSettingsBtn.addEventListener("click", async () => {
    const newUrl = apiUrlInput.value.trim().replace(/\/+$/, "");
    if (newUrl) {
      apiBaseUrl = newUrl;
      await chrome.storage.local.set({ apiUrl: newUrl });
      settingsPanel.classList.add("hidden");
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
