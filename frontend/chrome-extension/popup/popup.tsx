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

interface StartConversionResponse {
  success: boolean;
  jobId?: string;
  error?: string;
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
const apiKeyInput = $<HTMLInputElement>("api-key");
const saveSettingsBtn = $<HTMLButtonElement>("save-settings");

// ── State ───────────────────────────────────────────────

let currentJobId: string | null = null;
let pollingInterval: ReturnType<typeof setInterval> | null = null;
let apiBaseUrl: string = DEFAULT_API_URL;
// Google API key supplied by the user via the settings panel. The Chrome
// extension does NOT inherit the backend's .env credential — translations
// can only start when this is non-empty. Sent on every /convert call as the
// ``X-Google-Api-Key`` header.
let googleApiKey: string = "";

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
  submitBtn.textContent = "翻訳を開始";
  updateSubmitEnabled();
  progressBar.style.width = "0%";
  progressPercent.textContent = "0%";
  progressStep.textContent = "";
}

function showError(msg: string): void {
  errorMessage.textContent = msg;
  show(errorSection);
  hide(progressSection);
  submitBtn.textContent = "翻訳を開始";
  updateSubmitEnabled();
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

async function getStoredApiKey(): Promise<string> {
  return new Promise((resolve) => {
    chrome.storage.local.get(["googleApiKey"], (result) => {
      resolve(typeof result.googleApiKey === "string" ? result.googleApiKey : "");
    });
  });
}

/** Ask the service worker to start a conversion.
 *
 * Routing through the SW (instead of fetching ``/convert`` from the
 * popup) is what lets the popup recover from being closed: the SW owns
 * the in-flight ``activeJob`` and persists it to ``chrome.storage.local``,
 * so a later popup reopen — even after the SW itself was idle-killed and
 * respawned — can restore the progress / download-button UI via
 * ``GET_STATUS``. A direct popup-to-backend fetch leaves the SW unaware
 * of the job, so when the popup closes its state is gone for good.
 */
async function startConversionViaServiceWorker(
  arxivUrl: string
): Promise<StartConversionResponse> {
  return (await chrome.runtime.sendMessage({
    type: "START_CONVERSION",
    arxivUrl,
  })) as StartConversionResponse;
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
  submitBtn.textContent = "翻訳を開始";
  updateSubmitEnabled();

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
  // The extension's whole credential model is "user supplies their own
  // key" — block here rather than letting the request hit the backend with
  // an empty header.
  if (!googleApiKey) {
    showError("設定から Google API Key を入力してください");
    settingsPanel.classList.remove("hidden");
    apiKeyInput.focus();
    return;
  }

  resetUI();
  submitBtn.disabled = true;
  submitBtn.textContent = "処理中...";
  show(progressSection);
  hide(errorSection);

  try {
    const response = await startConversionViaServiceWorker(arxivUrl);
    if (!response?.success || !response.jobId) {
      showError(response?.error || "翻訳の開始に失敗しました");
      return;
    }
    currentJobId = response.jobId;
    // The SW is already polling this job from its own context — the popup
    // also polls so the open UI updates in real time. The SW's polling
    // is what keeps state alive across popup close / SW idle-kill.
    startPolling(currentJobId);
  } catch (err) {
    const message = err instanceof Error ? err.message : "不明なエラー";
    showError(message);
  }
}

function handleUrlInput(): void {
  const value = urlInput.value.trim();
  if (value && isArxivUrl(value)) {
    urlHint.textContent = googleApiKey
      ? "有効なarXiv URLです"
      : "有効なarXiv URLです (設定から API Key を入力してください)";
    urlHint.classList.add("detected");
  } else if (value) {
    urlHint.textContent = "arXiv URLの形式で入力してください";
    urlHint.classList.remove("detected");
  } else {
    urlHint.textContent = "";
    urlHint.classList.remove("detected");
  }
  updateSubmitEnabled();
}

/** Enable the submit button only when both URL is valid and API key is set. */
function updateSubmitEnabled(): void {
  const value = urlInput.value.trim();
  submitBtn.disabled = !(value && isArxivUrl(value) && googleApiKey);
}

// ── Initialization ──────────────────────────────────────

async function init(): Promise<void> {
  // Load saved API URL and API key
  apiBaseUrl = await getApiUrl();
  apiUrlInput.value = apiBaseUrl;
  googleApiKey = await getStoredApiKey();
  apiKeyInput.value = googleApiKey;

  // If the user has never entered a key, surface the settings panel up-front
  // so the requirement is obvious instead of failing silently at submit time.
  if (!googleApiKey) {
    settingsPanel.classList.remove("hidden");
  }

  // Try to auto-fill from the current tab
  try {
    const [tab] = await chrome.tabs.query({
      active: true,
      currentWindow: true,
    });
    if (tab?.url && isArxivUrl(tab.url)) {
      urlInput.value = tab.url;
      urlHint.textContent = googleApiKey
        ? "現在のタブから検出しました"
        : "現在のタブから検出しました (設定から API Key を入力してください)";
      urlHint.classList.add("detected");
    }
  } catch {
    // Not in a context where we can query tabs; ignore
  }
  updateSubmitEnabled();

  // Restore the active job for the current tab's URL. The SW tracks
  // jobs per-URL, so a popup opened on tab A sees A's translation
  // status independently of any other tab also running its own
  // translation. ``GET_STATUS`` returns ``null`` when the active tab's
  // URL has no associated job (fresh UI).
  //
  // Limitation: if the user manually pasted some other arxiv URL into a
  // different tab and submitted, then came back to a tab whose URL
  // doesn't match anything in the SW table, the popup shows fresh UI.
  // Submission key = the tab's URL is the easiest mental model; the
  // alternative (track "last submitted from this popup") would mask
  // the per-tab behaviour the user actually wants.
  const lookupUrl = urlInput.value.trim();
  if (lookupUrl && isArxivUrl(lookupUrl)) {
    try {
      const response = await chrome.runtime.sendMessage({
        type: "GET_STATUS",
        arxivUrl: lookupUrl,
      });
      if (response?.jobId) {
        const restoredJobId: string = response.jobId;
        currentJobId = restoredJobId;

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
      // No active job for this URL — fresh UI.
    }
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
    const newKey = apiKeyInput.value.trim();
    const updates: Record<string, string> = {};
    if (newUrl) {
      apiBaseUrl = newUrl;
      updates.apiUrl = newUrl;
    }
    // Always persist the key (including the empty string) so the user can
    // explicitly clear it from the UI.
    googleApiKey = newKey;
    updates.googleApiKey = newKey;
    await chrome.storage.local.set(updates);
    updateSubmitEnabled();
    if (newKey) {
      settingsPanel.classList.add("hidden");
    }
  });
}

document.addEventListener("DOMContentLoaded", init);
