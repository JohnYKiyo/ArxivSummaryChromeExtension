/**
 * Chrome extension popup logic.
 *
 * Displays the extension UI when the toolbar icon is clicked.
 * Auto-fills the current tab's arXiv URL, shows conversion progress
 * via SSE, and provides a download link for completed jobs.
 */

// ── Constants ───────────────────────────────────────────

const DEFAULT_API_URL = "http://localhost:8000";
const ARXIV_URL_PATTERN = /^https?:\/\/(www\.)?arxiv\.org\/(abs|pdf)\/[\d.]+/;

// ── Types ───────────────────────────────────────────────

interface ConvertResponse {
  job_id: string;
}

interface SSEProgressEvent {
  step: string;
  progress: number;
  message: string;
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
let eventSource: EventSource | null = null;
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

function connectSSE(jobId: string): void {
  if (eventSource) {
    eventSource.close();
  }

  const url = `${apiBaseUrl}/api/v1/jobs/${jobId}/stream`;
  eventSource = new EventSource(url);

  eventSource.addEventListener("progress", (event: MessageEvent) => {
    const data: SSEProgressEvent = JSON.parse(event.data);
    updateProgress(data);
  });

  eventSource.addEventListener("complete", (_event: MessageEvent) => {
    eventSource?.close();
    eventSource = null;
    onConversionComplete(jobId);
  });

  eventSource.addEventListener("error", (event: MessageEvent) => {
    eventSource?.close();
    eventSource = null;
    let msg = "接続エラーが発生しました";
    try {
      const data = JSON.parse(event.data);
      msg = data.message || msg;
    } catch {
      // use default message
    }
    showError(msg);
  });

  eventSource.onerror = () => {
    eventSource?.close();
    eventSource = null;
    showError("サーバーとの接続が切断されました");
  };
}

// ── UI Updates ──────────────────────────────────────────

function updateProgress(data: SSEProgressEvent): void {
  const pct = Math.round(data.progress * 100);
  progressBar.style.width = `${pct}%`;
  progressPercent.textContent = `${pct}%`;
  progressLabel.textContent = data.step;
  progressStep.textContent = data.message;

  // Update badge via background script
  chrome.runtime.sendMessage({
    type: "UPDATE_BADGE",
    text: `${pct}%`,
  });
}

function onConversionComplete(jobId: string): void {
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
    const downloadUrl = `${apiBaseUrl}/api/v1/jobs/${jobId}/download`;
    chrome.tabs.create({ url: downloadUrl });
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
    connectSSE(currentJobId);
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

  // Check if there's an active job from the background
  try {
    const response = await chrome.runtime.sendMessage({ type: "GET_STATUS" });
    if (response?.jobId && response?.status === "processing") {
      currentJobId = response.jobId;
      urlInput.value = response.arxivUrl || urlInput.value;
      submitBtn.disabled = true;
      submitBtn.textContent = "処理中...";
      show(progressSection);
      connectSSE(currentJobId);
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
