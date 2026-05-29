/**
 * Custom hook for polling job status.
 *
 * Replaces the SSE-based useSSE hook with a polling mechanism.
 * Polls the backend status endpoint at a fixed interval and
 * provides reactive state for progress updates and error handling.
 */

import { useEffect, useRef, useState, useCallback } from "react";

import { fetchJobStatus } from "../services/api";
import type { JobState, ProgressEvent, StepName } from "../types";

const INITIAL_STATE: JobState = {
  events: [],
  currentStep: null,
  progress: 0,
  isComplete: false,
  isError: false,
  isCancelled: false,
  errorMessage: null,
  downloadUrl: null,
};

const POLL_INTERVAL_MS = 3000;

export function useJobPolling(jobId: string | null): JobState {
  const [state, setState] = useState<JobState>(INITIAL_STATE);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const cleanup = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!jobId) {
      setState(INITIAL_STATE);
      return;
    }

    setState(INITIAL_STATE);

    async function poll() {
      try {
        const status = await fetchJobStatus(jobId as string);

        if (status.status === "completed") {
          setState((prev) => ({
            ...prev,
            events: buildEventsList(prev.events, status.current_step, status.progress, status.message),
            currentStep: status.current_step,
            progress: 100,
            isComplete: true,
            downloadUrl: status.download_url,
          }));
          cleanup();
          return;
        }

        if (status.status === "error") {
          setState((prev) => ({
            ...prev,
            isError: true,
            errorMessage: status.error ?? "処理中にエラーが発生しました",
          }));
          cleanup();
          return;
        }

        if (status.status === "cancelled") {
          setState((prev) => ({
            ...prev,
            isCancelled: true,
            errorMessage: status.message ?? "処理がキャンセルされました",
          }));
          cleanup();
          return;
        }

        // In progress
        setState((prev) => ({
          ...prev,
          events: buildEventsList(prev.events, status.current_step, status.progress, status.message),
          currentStep: status.current_step,
          progress: status.progress,
          isError: false,
          errorMessage: null,
        }));
      } catch {
        // Network error — keep polling, do not mark as error yet
      }
    }

    // Initial poll immediately
    poll();

    // Then poll at interval
    intervalRef.current = setInterval(poll, POLL_INTERVAL_MS);

    return cleanup;
  }, [jobId, cleanup]);

  return state;
}

/**
 * Append a new event to the list if the step changed.
 */
function buildEventsList(
  prevEvents: ProgressEvent[],
  currentStep: StepName | null,
  progress: number,
  message: string | null,
): ProgressEvent[] {
  if (!currentStep || !message) {
    return prevEvents;
  }

  const lastEvent = prevEvents[prevEvents.length - 1];
  if (lastEvent && lastEvent.step === currentStep) {
    return prevEvents;
  }

  return [
    ...prevEvents,
    { step: currentStep, progress, message },
  ];
}
