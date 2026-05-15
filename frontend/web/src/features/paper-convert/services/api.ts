/**
 * Paper conversion API client.
 */

import { BASE_URL, getHeaders } from "../../../shared/services/api";
import type { SubmitResponse } from "../types";

export async function submitUrl(arxivUrl: string): Promise<SubmitResponse> {
  const response = await fetch(`${BASE_URL}/api/v1/convert`, {
    method: "POST",
    headers: getHeaders(),
    body: JSON.stringify({ arxiv_url: arxivUrl }),
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
