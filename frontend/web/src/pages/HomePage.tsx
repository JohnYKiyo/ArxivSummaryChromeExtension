/**
 * Home page — the main conversion workflow.
 *
 * Coordinates state between paper-convert and job-status features:
 * URL input -> progress view -> download.
 */

import { useCallback, useEffect, useState } from "react";

import { UrlInput, submitUrl } from "../features/paper-convert";
import { DownloadButton, ProgressView, useJobPolling } from "../features/job-status";

type AppState = "idle" | "processing" | "completed";

export default function HomePage() {
  const [appState, setAppState] = useState<AppState>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const job = useJobPolling(jobId);

  // Transition to completed when polling reports completion
  useEffect(() => {
    if (job.isComplete && appState === "processing") {
      setAppState("completed");
    }
  }, [job.isComplete, appState]);

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
          currentStep={job.currentStep}
          progress={job.progress}
          isError={job.isError}
          errorMessage={job.errorMessage}
        />
      )}

      {/* Download button */}
      {appState === "completed" && job.downloadUrl && (
        <DownloadButton downloadUrl={job.downloadUrl} />
      )}

      {/* Reset button */}
      {(appState === "completed" || job.isError) && (
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
