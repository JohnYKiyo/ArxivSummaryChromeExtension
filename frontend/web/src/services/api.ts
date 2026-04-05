/**
 * API client service.
 *
 * Provides typed functions for interacting with the backend API.
 */

import { getToken } from "./auth";

const BASE_URL = import.meta.env.VITE_API_BASE_URL as string | undefined ?? "";

interface SubmitResponse {
  job_id: string;
  stream_url: string;
}

function getHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}

export async function submitUrl(arxivUrl: string): Promise<SubmitResponse> {
  const response = await fetch(`${BASE_URL}/api/v1/convert`, {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify({ url: arxivUrl }),
  });

  if (!response.ok) {
    const errorBody = await response.text();
    throw new Error(
      `API error (${response.status}): ${errorBody || response.statusText}`,
    );
  }

  const data: unknown = await response.json();
  const result = data as SubmitResponse;
  if (!result.job_id) {
    throw new Error("Invalid response: missing job_id");
  }
  return result;
}

export function getDownloadUrl(jobId: string): string {
  return `${BASE_URL}/api/v1/jobs/${jobId}/download`;
}

export function getStreamUrl(jobId: string): string {
  return `${BASE_URL}/api/v1/jobs/${jobId}/stream`;
}
