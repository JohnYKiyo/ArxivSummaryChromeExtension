/**
 * Job status API client.
 */

import { BASE_URL } from "../../../shared/services/api";

export function getDownloadUrl(jobId: string): string {
  return `${BASE_URL}/api/v1/jobs/${jobId}/download`;
}

export function getStreamUrl(jobId: string): string {
  return `${BASE_URL}/api/v1/jobs/${jobId}/stream`;
}
