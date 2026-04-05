/**
 * Custom hook for Server-Sent Events (SSE) connection.
 *
 * Manages an EventSource connection to the backend SSE endpoint.
 * Provides reactive state for progress updates, error handling,
 * and automatic reconnection logic.
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { getStreamUrl, getDownloadUrl } from "../services/api";

export type StepName =
  | "tex_fetch"
  | "tex2markdown"
  | "translation"
  | "summary"
  | "packaging";

export interface ProgressEvent {
  step: StepName;
  progress: number;
  message: string;
}

interface SSEState {
  events: ProgressEvent[];
  currentStep: StepName | null;
  progress: number;
  isComplete: boolean;
  isError: boolean;
  errorMessage: string | null;
  downloadUrl: string | null;
}

const INITIAL_STATE: SSEState = {
  events: [],
  currentStep: null,
  progress: 0,
  isComplete: false,
  isError: false,
  errorMessage: null,
  downloadUrl: null,
};

const MAX_RETRIES = 3;
const RETRY_DELAY_MS = 2000;

export function useSSE(jobId: string | null): SSEState {
  const [state, setState] = useState<SSEState>(INITIAL_STATE);
  const eventSourceRef = useRef<EventSource | null>(null);
  const retryCountRef = useRef(0);

  const cleanup = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, []);

  useEffect(() => {
    if (!jobId) {
      setState(INITIAL_STATE);
      return;
    }

    setState(INITIAL_STATE);
    retryCountRef.current = 0;

    function connect() {
      cleanup();

      const url = getStreamUrl(jobId as string);
      const es = new EventSource(url);
      eventSourceRef.current = es;

      es.addEventListener("progress", (e: MessageEvent) => {
        retryCountRef.current = 0;
        try {
          const data = JSON.parse(e.data as string) as ProgressEvent;
          setState((prev) => ({
            ...prev,
            events: [...prev.events, data],
            currentStep: data.step,
            progress: data.progress,
            isError: false,
            errorMessage: null,
          }));
        } catch {
          // Ignore malformed events
        }
      });

      es.addEventListener("complete", () => {
        setState((prev) => ({
          ...prev,
          isComplete: true,
          progress: 100,
          downloadUrl: getDownloadUrl(jobId as string),
        }));
        cleanup();
      });

      es.addEventListener("error", (e: MessageEvent) => {
        let message = "処理中にエラーが発生しました";
        try {
          if (e.data) {
            const parsed = JSON.parse(e.data as string) as {
              message?: string;
            };
            if (parsed.message) {
              message = parsed.message;
            }
          }
        } catch {
          // Use default message
        }
        setState((prev) => ({
          ...prev,
          isError: true,
          errorMessage: message,
        }));
        cleanup();
      });

      es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {
          return;
        }
        cleanup();

        if (retryCountRef.current < MAX_RETRIES) {
          retryCountRef.current += 1;
          setTimeout(connect, RETRY_DELAY_MS);
        } else {
          setState((prev) => ({
            ...prev,
            isError: true,
            errorMessage: "サーバーとの接続が切断されました。再度お試しください。",
          }));
        }
      };
    }

    connect();

    return cleanup;
  }, [jobId, cleanup]);

  return state;
}
