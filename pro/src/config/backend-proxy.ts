export interface BackendProxyRewrite {
  source: string;
  destination: string;
}

const workspaceRoutes = [
  "/telemetry/events",
  "/api/conversion-runtime",
  "/api/pro/conversion-batches/:path*",
  "/api/pro/conversions",
  "/api/pro/receipts/:path*",
  "/api/pro/works/:path*",
  "/api/pro/organization/catalog",
  "/api/pro/organization/clients/:path*",
  "/api/pro/organization/accounts/:path*",
  "/api/pro/organization/periods/:path*",
  "/api/pro/organization/conversions",
  "/api/pro/organization/batches/:path*",
  "/api/pro/organization/contexts/bulk",
  "/api/pro/convert-edits/:path*",
  "/api/conversion-batches/:path*",
  "/auth/me",
  "/auth/pro/anonymous-session",
  "/auth/pro/session/login",
  "/auth/session/refresh",
  "/auth/session/logout",
  "/convert-edits/:path*",
  "/convert-report/:path*",
] as const;

export function buildBackendProxyRewrites(
  rawBackendUrl: string | undefined,
): BackendProxyRewrite[] {
  const configuredUrl = rawBackendUrl?.trim();
  if (!configuredUrl) return [];

  let backendUrl: URL;
  try {
    backendUrl = new URL(configuredUrl);
  } catch {
    throw new Error("BACKEND_API_URL deve ser uma URL absoluta válida.");
  }
  if (!new Set(["http:", "https:"]).has(backendUrl.protocol)) {
    throw new Error("BACKEND_API_URL deve usar http ou https.");
  }
  if (backendUrl.username || backendUrl.password) {
    throw new Error("BACKEND_API_URL não pode conter credenciais.");
  }
  if (backendUrl.search || backendUrl.hash) {
    throw new Error(
      "BACKEND_API_URL não pode conter query string ou fragmento.",
    );
  }

  const baseUrl = backendUrl.toString().replace(/\/+$/, "");
  return workspaceRoutes.map((route) => ({
    source: route,
    destination: `${baseUrl}${route}`,
  }));
}
