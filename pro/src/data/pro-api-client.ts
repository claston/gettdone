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

  const bootstrap = async (force = false) => {
    if (force) bootstrapPromise = null;
    if (!bootstrapPromise) {
      bootstrapPromise = fetcher(`${apiUrl}/auth/pro/anonymous-session`, {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          [PRO_CLIENT_HEADER_NAME]: PRO_CLIENT_HEADER_VALUE,
        },
        body: "{}",
      }).then(async (response) => {
        if (!response.ok) {
          bootstrapPromise = null;
          throw new Error(
            await readApiError(
              response,
              "Não foi possível iniciar a sessão segura.",
            ),
          );
        }
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
      if (response.status === 401) {
        await bootstrap(true);
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
