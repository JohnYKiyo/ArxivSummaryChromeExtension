/**
 * Root application component for arXiv Translator.
 *
 * Manages the main UI flow: URL input -> progress view -> download.
 * Coordinates state between UrlInput, ProgressView, and DownloadButton components.
 */

import { useState, useCallback, useEffect } from "react";
import UrlInput from "./components/UrlInput";
import ProgressView from "./components/ProgressView";
import DownloadButton from "./components/DownloadButton";
import { useSSE } from "./hooks/useSSE";
import { submitUrl } from "./services/api";

type AppState = "idle" | "processing" | "completed";

export default function App() {
  const [appState, setAppState] = useState<AppState>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const sse = useSSE(jobId);

  // Transition to completed when SSE reports completion
  useEffect(() => {
    if (sse.isComplete && appState === "processing") {
      setAppState("completed");
    }
  }, [sse.isComplete, appState]);

  const handleSubmit = useCallback(async (url: string) => {
    setSubmitError(null);
    setAppState("processing");
    try {
      const result = await submitUrl(url);
      setJobId(result.job_id);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "送信中にエラーが発生しました";
      setSubmitError(message);
      setAppState("idle");
    }
  }, []);

  const handleReset = useCallback(() => {
    setAppState("idle");
    setJobId(null);
    setSubmitError(null);
  }, []);

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-3xl mx-auto px-4 py-12">
        {/* Header */}
        <header className="text-center mb-10">
          <h1 className="text-3xl font-bold text-gray-900">
            arXiv Paper Translator
          </h1>
          <p className="mt-2 text-gray-600">
            arXiv論文を日本語に翻訳・要約します
          </p>
        </header>

        <main className="space-y-8">
          {/* URL Input */}
          <UrlInput
            onSubmit={handleSubmit}
            disabled={appState === "processing"}
          />

          {/* Submit error */}
          {submitError && (
            <div className="w-full max-w-2xl mx-auto bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
              {submitError}
            </div>
          )}

          {/* Progress view */}
          {appState === "processing" && (
            <ProgressView
              currentStep={sse.currentStep}
              progress={sse.progress}
              isError={sse.isError}
              errorMessage={sse.errorMessage}
            />
          )}

          {/* Download button */}
          {appState === "completed" && sse.downloadUrl && (
            <DownloadButton downloadUrl={sse.downloadUrl} />
          )}

          {/* Reset button */}
          {(appState === "completed" || sse.isError) && (
            <div className="text-center">
              <button
                onClick={handleReset}
                className="text-sm text-blue-600 hover:text-blue-800 underline"
              >
                別の論文を翻訳する
              </button>
            </div>
          )}
        </main>

        {/* Footer */}
        <footer className="mt-16 text-center text-xs text-gray-400">
          arXiv Paper Translator
        </footer>
      </div>
    </div>
  );
}
