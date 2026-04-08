/**
 * Authentication service.
 *
 * Placeholder for future AWS Cognito integration.
 * In development mode, returns mock values.
 */

const DEV_MODE = import.meta.env.DEV;

const MOCK_TOKEN = "dev-mock-token";

export async function login(): Promise<void> {
  if (DEV_MODE) {
    localStorage.setItem("auth_token", MOCK_TOKEN);
    return;
  }
  // TODO: Implement Cognito hosted UI redirect
  throw new Error("Cognito login not yet implemented");
}

export async function logout(): Promise<void> {
  localStorage.removeItem("auth_token");
  if (DEV_MODE) {
    return;
  }
  // TODO: Implement Cognito logout
  throw new Error("Cognito logout not yet implemented");
}

export function getToken(): string | null {
  if (DEV_MODE) {
    return MOCK_TOKEN;
  }
  return localStorage.getItem("auth_token");
}

export function isAuthenticated(): boolean {
  if (DEV_MODE) {
    return true;
  }
  return localStorage.getItem("auth_token") !== null;
}
