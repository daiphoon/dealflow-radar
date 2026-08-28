import "server-only";

function configuredPublicOrigin(): URL | null {
  const value = process.env.APP_PUBLIC_ORIGIN?.trim();
  if (!value) return null;

  const origin = new URL(value);
  const isLocalHttp =
    origin.protocol === "http:" &&
    ["localhost", "127.0.0.1", "::1"].includes(origin.hostname);
  if (
    (origin.protocol !== "https:" && !isLocalHttp) ||
    origin.username ||
    origin.password ||
    origin.pathname !== "/" ||
    origin.search ||
    origin.hash
  ) {
    throw new Error("APP_PUBLIC_ORIGIN must be a safe HTTPS origin");
  }
  return origin;
}

export function publicRedirectUrl(path: string, requestUrl: string): URL {
  return new URL(path, configuredPublicOrigin() ?? new URL(requestUrl).origin);
}
