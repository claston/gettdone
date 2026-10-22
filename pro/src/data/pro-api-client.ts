import {
  expireProSession,
  hasVerifiedProSession,
  proSessionVerifiedAt,
} from "./pro-session-cache";

export const PRO_CLIENT_HEADER_NAME = "X-OFX-Pro-Client";
export const PRO_CLIENT_HEADER_VALUE = "web";

export interface ProApiClient {
  request(path: string, init?: RequestInit): Promise<Response>;
}

interface ProApiClientOptions {
  apiUrl: string;
  fetcher?: typeof fetch;
}

export function createProApiClient(options: ProApiClientOptions): ProApiClient {
  const apiUrl = options.apiUrl.replace(/\/+$/, "");
  const fetcher = options.fetcher ?? fetch;
  let bootstrapPromise: Promise<void> | null = null;
  let bootstrapExpiresAt = 0;
  let refreshPromise: Promise<void> | null = null;
  let sessionEstablished = false;
  let bootstrapPending = false;

  const bootstrap = async (force = false) => {
    if (bootstrapPending && bootstrapPromise) return bootstrapPromise;
    if (
      !force &&
      hasVerifiedProSession() &&
      Date.now() < proSessionVerifiedAt() + 10 * 60 * 1000
    ) {
      sessionEstablished = true;
      bootstrapExpiresAt = proSessionVerifiedAt() + 10 * 60 * 1000;
      bootstrapPromise ??= Promise.resolve();
      return bootstrapPromise;
    }
    // Access cookies expire after 15 minutes. Recheck before the next write
    // instead of keeping a successful bootstrap for the lifetime of the tab.
    if (force || Date.now() >= bootstrapExpiresAt) bootstrapPromise = null;
    if (!bootstrapPromise) {
      bootstrapPending = true;
      bootstrapExpiresAt = Date.now() + 10 * 60 * 1000;
      bootstrapPromise = fetcher(`${apiUrl}/auth/me`, {
        method: "GET",
        credentials: "include",
        headers: {
          Accept: "application/json",
          [PRO_CLIENT_HEADER_NAME]: PRO_CLIENT_HEADER_VALUE,
        },
      })
        .then(async (response) => {
          if (response.ok) return;
          if (response.status !== 401) {
            bootstrapPromise = null;
            throw new Error(
              await readApiError(
                response,
                "Não foi possível verificar a sessão segura.",
              ),
            );
          }

          const refreshResponse = await fetcher(
            `${apiUrl}/auth/session/refresh`,
            {
              method: "POST",
              credentials: "include",
              headers: {
                Accept: "application/json",
                [PRO_CLIENT_HEADER_NAME]: PRO_CLIENT_HEADER_VALUE,
              },
            },
          );
          if (refreshResponse.ok) return;
          if (refreshResponse.status !== 401) {
            bootstrapPromise = null;
            throw new Error(
              await readApiError(
                refreshResponse,
                "Não foi possível renovar a sessão segura.",
              ),
            );
          }

          expireProSession();
          const anonymousResponse = await fetcher(
            `${apiUrl}/auth/pro/anonymous-session`,
            {
              method: "POST",
              credentials: "include",
              headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
                [PRO_CLIENT_HEADER_NAME]: PRO_CLIENT_HEADER_VALUE,
              },
              body: "{}",
            },
          );
          if (!anonymousResponse.ok) {
            bootstrapPromise = null;
            throw new Error(
              await readApiError(
                anonymousResponse,
                "Não foi possível iniciar a sessão segura.",
              ),
            );
          }
        })
        .then(() => {
          sessionEstablished = true;
        })
        .catch((error: unknown) => {
          bootstrapPromise = null;
          throw error;
        })
        .finally(() => {
          bootstrapPending = false;
        });
    }
    return bootstrapPromise;
  };

  const execute = (path: string, init: RequestInit) => {
    const headers = new Headers(init.headers);
    headers.set(PRO_CLIENT_HEADER_NAME, PRO_CLIENT_HEADER_VALUE);
    return fetcher(`${apiUrl}${path}`, {
      ...init,
      credentials: "include",
      headers,
    });
  };

  return {
    async request(path: string, init: RequestInit = {}) {
      const checkAbort = () => {
        if (init.signal?.aborted)
          throw new DOMException("Aborted", "AbortError");
      };
      checkAbort();
      const isRead = ["GET", "HEAD"].includes(
        (init.method ?? "GET").toUpperCase(),
      );
      // Reads are authorized by their own endpoint. Avoid serializing them
      // behind /auth/me after idle; an expired cookie still triggers renewal.
      if (!isRead || (!sessionEstablished && !hasVerifiedProSession()))
        await bootstrap();
      checkAbort();
      let response = await execute(path, init);
      checkAbort();
      if (
        response.status === 401 ||
        (response.status === 404 &&
          (path.startsWith("/api/pro/conversion-batches") ||
            path.startsWith("/api/pro/convert-edits/")) &&
          !(await response.clone().text()).includes(
            "Edit operation not found.",
          )) ||
        (response.status === 400 &&
          path.startsWith("/api/pro/convert-edits/") &&
          (await response.clone().text()).includes(
            "Missing or invalid identity context",
          )) ||
        (response.status === 403 &&
          path.startsWith("/api/pro/convert-edits/") &&
          (await response.clone().text()).includes(
            "Access denied for this analysis.",
          ))
      ) {
        checkAbort();
        if (!refreshPromise) {
          refreshPromise = bootstrap(true).finally(() => {
            refreshPromise = null;
          });
        }
        await refreshPromise;
        checkAbort();
        response = await execute(path, init);
      }
      return response;
    },
  };
}

export async function readApiError(
  response: Response,
  fallback: string,
): Promise<string> {
  try {
    const payload = (await response.json()) as {
      detail?: string | { message?: string };
    };
    if (typeof payload.detail === "string" && payload.detail.trim()) {
      return payload.detail;
    }
    if (
      payload.detail &&
      typeof payload.detail === "object" &&
      typeof payload.detail.message === "string"
    ) {
      return payload.detail.message;
    }
  } catch {
    // A mensagem segura local é suficiente quando o backend não retorna JSON.
  }
  return fallback;
}
