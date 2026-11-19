import { createProApiClient, readApiError } from "./pro-api-client";

export interface ShadowSource {
  id: string;
  version: number;
  kind: "movement" | "invoice";
  label: string;
}
export interface ShadowRun {
  id: string;
  state: "queued" | "ready" | "failed";
  stale: boolean;
  engine_version: string;
  expires_at: string;
  items: Array<{
    id: string;
    description: string;
    label: string;
    status: string;
    evidence: string;
    reported_code_check?: string;
    original?: { ncm?: string; cst?: string; cClassTrib?: string };
    candidates: Array<{
      code: string;
      cst: string;
      label: string;
      foundations: string[];
    }>;
  }>;
  feedback: Array<{ item_id: string; verdict: string; comment: string }>;
  metrics: Record<"agree" | "disagree" | "unknown", number>;
}

const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
export async function shadowRequest(
  client: string,
  path: string,
  body?: object,
  signal?: AbortSignal,
): Promise<ShadowRun> {
  const response = await api.request(
    `/api/pro/financial-center/clients/${encodeURIComponent(client)}/classification-shadow${path}`,
    {
      method: body ? "POST" : "GET",
      body: body ? JSON.stringify(body) : undefined,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      signal,
      cache: "no-store",
    },
  );
  if (!response.ok)
    throw new Error(
      await readApiError(response, "Não foi possível consultar a avaliação."),
    );
  return response.json();
}
