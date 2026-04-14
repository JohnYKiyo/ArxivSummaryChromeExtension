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
