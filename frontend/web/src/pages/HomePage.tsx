/**
 * Home page — the main conversion workflow.
 *
 * Coordinates state between paper-convert and job-status features:
 * URL input -> progress view -> download.
 */

import { useCallback, useEffect, useState } from "react";

import { UrlInput, submitUrl } from "../features/paper-convert";
import {
  DownloadButton,
  ProgressView,
  cancelJob,
  useJobPolling,
} from "../features/job-status";

type AppState = "idle" | "processing" | "completed";

export default function HomePage() {
  const [appState, setAppState] = useState<AppState>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  // ``cancelInFlight`` is the local "the user clicked cancel and we're
  // waiting for the backend" flag — distinct from the polled
  // ``job.isCancelled`` which only flips after the pipeline acknowledges.
  // Disables the cancel button while the POST is on the wire.
  const [cancelInFlight, setCancelInFlight] = useState(false);

  const job = useJobPolling(jobId);

  // Transition to completed when polling reports completion
  useEffect(() => {
    if (job.isComplete && appState === "processing") {
      setAppState("completed");
    }
  }, [job.isComplete, appState]);

  const handleSubmit = useCallback(async (url: string) => {
    setSubmitError(null);
    setCancelInFlight(false);
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

  const handleCancel = useCallback(async () => {
    if (!jobId || cancelInFlight) return;
    setCancelInFlight(true);
    try {
      await cancelJob(jobId);
      // Pipeline picks up the cancel at its next checkpoint; polling will
      // drive the UI to the cancelled terminal state. Keep the button in
      // its "キャンセル中..." disabled state until then.
    } catch (err) {
      setCancelInFlight(false);
      const message =
        err instanceof Error ? err.message : "キャンセルに失敗しました";
      setSubmitError(message);
    }
  }, [jobId, cancelInFlight]);

  const handleReset = useCallback(() => {
    setAppState("idle");
    setJobId(null);
    setSubmitError(null);
    setCancelInFlight(false);
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
          isCancelled={job.isCancelled}
          errorMessage={job.errorMessage}
          onCancel={handleCancel}
          cancelDisabled={cancelInFlight}
        />
      )}

      {/* Download button */}
      {appState === "completed" && job.downloadUrl && (
        <DownloadButton downloadUrl={job.downloadUrl} />
      )}

      {/* Reset button */}
      {(appState === "completed" || job.isError || job.isCancelled) && (
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
