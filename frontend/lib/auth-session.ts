import "server-only";

export const ACCESS_TOKEN_COOKIE = "dealflow_access_token";
export const REFRESH_TOKEN_COOKIE = "dealflow_refresh_token";
export const VERIFICATION_ID_COOKIE = "dealflow_verification_id";
export const VERIFICATION_METHOD_COOKIE = "dealflow_verification_method";

export const authProvider = process.env.AUTH_PROVIDER ?? "demo";
export const phoneLoginEnabled = process.env.PHONE_LOGIN_ENABLED === "true";

export function safeReturnPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  try {
    const parsed = new URL(value, "https://dealflow-radar.invalid");
    if (parsed.origin !== "https://dealflow-radar.invalid") return "/";
    return `${parsed.pathname}${parsed.search}${parsed.hash}`;
  } catch {
    return "/";
  }
}

export function sessionCookieOptions(maxAge: number) {
  return {
    httpOnly: true,
    maxAge,
    path: "/",
    sameSite: "strict" as const,
    secure: process.env.NODE_ENV === "production",
  };
}
