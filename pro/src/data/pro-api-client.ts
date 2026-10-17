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
  let refreshPromise: Promise<void> | null = null;

  const bootstrap = async (force = false) => {
    if (force) bootstrapPromise = null;
    if (!bootstrapPromise) {
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
        .catch((error: unknown) => {
          bootstrapPromise = null;
          throw error;
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
      await bootstrap();
      let response = await execute(path, init);
      if (
        response.status === 401 ||
        (response.status === 404 &&
          path.startsWith("/api/pro/conversion-batches/")) ||
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
        if (!refreshPromise) {
          refreshPromise = bootstrap(true).finally(() => {
            refreshPromise = null;
          });
        }
        await refreshPromise;
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
