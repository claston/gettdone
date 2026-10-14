import {
  PRO_CLIENT_HEADER_NAME,
  PRO_CLIENT_HEADER_VALUE,
  readApiError,
} from "./pro-api-client";

export interface ProUser {
  user_id: string;
  name: string;
  email: string;
  plan_code?: string | null;
  plan_name?: string | null;
  quota_remaining?: number;
  quota_limit?: number;
  quota_mode?: string;
}

export interface ProAuthService {
  currentUser(): Promise<ProUser | null>;
  login(email: string, password: string): Promise<ProUser>;
  logout(): Promise<void>;
}

interface ProAuthServiceOptions {
  apiUrl: string;
  fetcher?: typeof fetch;
}

export function createProAuthService(
  options: ProAuthServiceOptions,
): ProAuthService {
  const apiUrl = options.apiUrl.replace(/\/+$/, "");
  const fetcher = options.fetcher ?? fetch;
  const headers = {
    Accept: "application/json",
    [PRO_CLIENT_HEADER_NAME]: PRO_CLIENT_HEADER_VALUE,
  };

  return {
    async currentUser() {
      let response = await fetcher(`${apiUrl}/auth/me`, {
        method: "GET",
        credentials: "include",
        headers,
      });
      if (response.status === 401) {
        response = await fetcher(`${apiUrl}/auth/session/refresh`, {
          method: "POST",
          credentials: "include",
          headers,
        });
        if (response.status === 401) return null;
      }
      if (!response.ok) {
        throw new Error(
          await readApiError(
            response,
            "Não foi possível verificar sua sessão.",
          ),
        );
      }
      return (await response.json()) as ProUser;
    },

    async login(email, password) {
      const response = await fetcher(`${apiUrl}/auth/pro/session/login`, {
        method: "POST",
        credentials: "include",
        headers: {
          ...headers,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      if (!response.ok) {
        throw new Error(
          await readApiError(
            response,
            response.status === 401
              ? "E-mail ou senha inválidos."
              : "Não foi possível entrar no OFX Simples PRO.",
          ),
        );
      }
      return (await response.json()) as ProUser;
    },

    async logout() {
      const response = await fetcher(`${apiUrl}/auth/session/logout`, {
        method: "POST",
        credentials: "include",
        headers,
      });
      if (!response.ok) {
        throw new Error(
          await readApiError(response, "Não foi possível encerrar sua sessão."),
        );
      }
    },
  };
}
