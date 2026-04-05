/**
 * Content script injected into arxiv.org paper pages.
 *
 * Detects arxiv.org/abs/* pages and injects a floating "翻訳" button.
 * On click, sends a message to the background service worker to trigger
 * the conversion pipeline for the current paper.
 */

// ── Constants ───────────────────────────────────────────

const BUTTON_ID = "arxiv-translator-btn";
const ARXIV_ABS_PATTERN = /^https?:\/\/(www\.)?arxiv\.org\/abs\/[\d.]+/;

// ── Guard: only run on abs pages ────────────────────────

if (!ARXIV_ABS_PATTERN.test(window.location.href)) {
  // Do nothing on non-abs pages (e.g., PDF pages)
  // We still match pdf/* in manifest for potential future use
}

// ── Prevent duplicate injection ─────────────────────────

if (!document.getElementById(BUTTON_ID)) {
  createFloatingButton();
}

// ── Button Creation ─────────────────────────────────────

function createFloatingButton(): void {
  const button = document.createElement("button");
  button.id = BUTTON_ID;
  button.textContent = "翻訳";
  button.title = "この論文を日本語に翻訳・要約する";

  // Inline styles to avoid conflicts with arxiv's CSS
  Object.assign(button.style, {
    position: "fixed",
    top: "80px",
    right: "20px",
    zIndex: "10000",
    padding: "10px 20px",
    backgroundColor: "#6366f1",
    color: "#ffffff",
    border: "none",
    borderRadius: "8px",
    fontSize: "14px",
    fontWeight: "600",
    cursor: "pointer",
    boxShadow: "0 2px 8px rgba(0, 0, 0, 0.15)",
    transition: "background-color 0.15s ease, transform 0.1s ease",
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
  });

  // Hover effects
  button.addEventListener("mouseenter", () => {
    button.style.backgroundColor = "#4f46e5";
    button.style.transform = "scale(1.05)";
  });
  button.addEventListener("mouseleave", () => {
    button.style.backgroundColor = "#6366f1";
    button.style.transform = "scale(1)";
  });

  // Click handler
  button.addEventListener("click", handleTranslateClick);

  document.body.appendChild(button);
}

// ── Click Handler ───────────────────────────────────────

async function handleTranslateClick(): Promise<void> {
  const button = document.getElementById(BUTTON_ID);
  if (!button) return;

  const paperUrl = window.location.href;

  // Update button state
  button.textContent = "送信中...";
  (button as HTMLButtonElement).disabled = true;
  button.style.opacity = "0.7";
  button.style.cursor = "not-allowed";

  try {
    // Send message to background service worker
    const response = await chrome.runtime.sendMessage({
      type: "START_CONVERSION",
      arxivUrl: paperUrl,
    });

    if (response?.success) {
      button.textContent = "処理中...";
      button.style.backgroundColor = "#059669";

      // Listen for progress updates from background
      chrome.runtime.onMessage.addListener(
        (msg: { type: string; progress?: number; status?: string }) => {
          if (msg.type === "CONVERSION_PROGRESS" && button) {
            const pct = Math.round((msg.progress ?? 0) * 100);
            button.textContent = `${pct}%`;
          }
          if (msg.type === "CONVERSION_COMPLETE" && button) {
            button.textContent = "完了 - クリックでDL";
            button.style.backgroundColor = "#059669";
            button.style.opacity = "1";
            button.style.cursor = "pointer";
            (button as HTMLButtonElement).disabled = false;

            // Replace click handler for download
            button.removeEventListener("click", handleTranslateClick);
            button.addEventListener("click", () => {
              chrome.runtime.sendMessage({
                type: "DOWNLOAD_RESULT",
                jobId: response.jobId,
              });
            });
          }
          if (msg.type === "CONVERSION_ERROR" && button) {
            button.textContent = "エラー - 再試行";
            button.style.backgroundColor = "#dc2626";
            button.style.opacity = "1";
            button.style.cursor = "pointer";
            (button as HTMLButtonElement).disabled = false;
          }
        }
      );
    } else {
      throw new Error(response?.error || "Failed to start conversion");
    }
  } catch (err) {
    console.error("[arXiv Translator] Error:", err);
    button.textContent = "エラー - 再試行";
    button.style.backgroundColor = "#dc2626";
    button.style.opacity = "1";
    button.style.cursor = "pointer";
    (button as HTMLButtonElement).disabled = false;
  }
}
