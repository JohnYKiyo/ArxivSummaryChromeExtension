/**
 * Shared API client configuration.
 *
 * Provides the base URL and common header construction
 * used by all feature-specific API services.
 */

import { getToken } from "./auth";

export const BASE_URL =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

export function getHeaders(): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  const token = getToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  return headers;
}
