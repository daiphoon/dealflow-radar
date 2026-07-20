import "server-only";

export type AuthTokens = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  token_type: "Bearer";
};

export type VerificationChallenge = {
  verification_id: string;
  expires_in: number;
};

export class AuthenticationApiError extends Error {
  constructor(public readonly status: number) {
    super(`Authentication API request failed with status ${status}`);
  }
}

const apiBaseUrl = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

async function postAuthentication<T>(
  path: string,
  body: unknown,
  accessToken?: string,
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: "POST",
    cache: "no-store",
    headers,
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new AuthenticationApiError(response.status);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function requestEmailVerification(email: string): Promise<VerificationChallenge> {
  return postAuthentication("/api/v1/auth/email/verification", { email });
}

export function loginWithEmailCode(
  verificationId: string,
  verificationCode: string,
): Promise<AuthTokens> {
  return postAuthentication("/api/v1/auth/email/login", {
    verification_id: verificationId,
    verification_code: verificationCode,
  });
}

export function refreshAuthentication(refreshToken: string): Promise<AuthTokens> {
  return postAuthentication("/api/v1/auth/token/refresh", {
    refresh_token: refreshToken,
  });
}

export function revokeAuthentication(accessToken: string): Promise<void> {
  return postAuthentication("/api/v1/auth/logout", {}, accessToken);
}
