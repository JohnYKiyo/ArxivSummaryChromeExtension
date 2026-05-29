/**
 * Job status API client.
 */

import { BASE_URL, getHeaders } from "../../../shared/services/api";
import type { StatusResponse } from "../types";

export async function fetchJobStatus(
  jobId: string,
): Promise<StatusResponse> {
  const response = await fetch(`${BASE_URL}/api/v1/jobs/${jobId}/status`, {
    headers: getHeaders(),
  });

  if (!response.ok) {
    throw new Error(`Status fetch failed: ${response.status}`);
  }

  const data: unknown = await response.json();
  return data as StatusResponse;
}

/**
 * Request cooperative cancellation of a running job.
 *
 * Returns the updated status snapshot. The backend flips ``status`` to
 * ``cancelled`` at the next pipeline checkpoint (not synchronously), so
 * polling continues to drive the UI to the terminal state.
 *
 * Throws on 4xx/5xx — in particular 409 if the job has already finished.
 */
export async function cancelJob(jobId: string): Promise<StatusResponse> {
  const response = await fetch(`${BASE_URL}/api/v1/jobs/${jobId}/cancel`, {
    method: "POST",
    headers: getHeaders(),
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(
      `Cancel failed (${response.status}): ${errorBody || response.statusText}`,
    );
  }

  const data: unknown = await response.json();
  return data as StatusResponse;
}
