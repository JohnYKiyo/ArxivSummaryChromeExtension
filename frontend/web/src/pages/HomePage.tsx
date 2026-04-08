/**
 * Home page — the main conversion workflow.
 *
 * Coordinates state between paper-convert and job-status features:
 * URL input -> progress view -> download.
 */

import { useCallback, useEffect, useState } from "react";

import { UrlInput, submitUrl } from "../features/paper-convert";
import { DownloadButton, ProgressView, useSSE } from "../features/job-status";

type AppState = "idle" | "processing" | "completed";

export default function HomePage() {
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
  );
}
