import { createProApiClient, readApiError } from "./pro-api-client";

export interface ReceivedClient {
  id: string;
  name: string;
  tax_id: string | null;
  trade_name: string;
  updated_at: string;
  historical_count: number;
  available_count: number;
  expired_count: number;
  attention_count: number;
  processing_count?: number;
  accounts: Array<{
    id: string;
    bank_code: string;
    account_number: string;
    statement_count: number;
  }>;
}
export interface PendingStatement {
  job_id: string;
  batch_id: string;
  filename: string;
  version: number;
  source: {
    holder_name?: string;
    holder_tax_id?: string;
    confidence?: string;
    bank_code?: string;
    branch?: string;
    account_number?: string;
    identity_issues?: string[];
    references?: Array<{ page: number; line: number }>;
  };
}
export interface ReceivedPage {
  totals: { statements: number; ready: number; attention: number };
  clients: ReceivedClient[];
  pending: PendingStatement[];
  next_cursor: string | null;
  pending_next_cursor?: string | null;
  client_count?: number;
  all_client_count?: number;
  active_client_count?: number;
  server_time?: string;
  next_expiration_at?: string | null;
  pending_count?: number;
}
export interface ClientEdit {
  name: string;
  tax_id: string | null;
  expected_updated_at: string;
}
export interface Confirmation {
  name: string;
  tax_id: string | null;
  expected_version: number;
  client_id?: string | null;
}
export interface RecoverySummary {
  unavailable: number;
  review_required: number;
  pending: number;
}
export interface ReceivedService {
  list(
    cursor?: string,
    signal?: AbortSignal,
    pendingCursor?: string,
    query?: string,
    activeOnly?: boolean,
  ): Promise<ReceivedPage>;
  refresh(signal?: AbortSignal): Promise<RecoverySummary | void>;
  edit(id: string, value: ClientEdit): Promise<unknown>;
  confirm(id: string, value: Confirmation): Promise<unknown>;
}

const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});

export async function loadReceivedDocument(
  batchId: string,
  jobId: string,
  signal: AbortSignal,
): Promise<Blob> {
  const response = await api
    .request(
      `/api/pro/conversion-batches/${encodeURIComponent(batchId)}/jobs/${encodeURIComponent(jobId)}/document`,
      { signal, cache: "no-store", headers: { Accept: "application/pdf" } },
    )
    .catch(() => {
      throw new Error(
        "Não foi possível carregar o PDF. Tente novamente; os dados preenchidos continuam na tela.",
      );
    });
  if (!response.ok) {
    throw new Error(
      response.status === 404
        ? "PDF indisponível ou expirado. Você pode continuar preenchendo os dados."
        : "Não foi possível carregar o PDF. Tente novamente; os dados preenchidos continuam na tela.",
    );
  }
  return response.blob();
}

async function read(path: string, init?: RequestInit) {
  const response = await api.request(`/api/pro/receipts${path}`, init);
  if (!response.ok)
    throw new Error(
      await readApiError(
        response,
        "Não foi possível carregar os extratos. O Conversor continua disponível.",
      ),
    );
  return response.json();
}
export const receivedService: ReceivedService = {
  list: (cursor, signal, pendingCursor, search = "", activeOnly = false) => {
    if (search.trim())
      return read("/search", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: search.trim(),
          cursor: cursor ?? "",
          pending_cursor: pendingCursor ?? "",
          sort: "name",
          ...(activeOnly ? { active_only: true } : {}),
        }),
        signal,
        cache: "no-store",
      });
    const query = new URLSearchParams();
    query.set("sort", "name");
    if (activeOnly) query.set("active_only", "true");
    if (cursor) query.set("cursor", cursor);
    if (pendingCursor) query.set("pending_cursor", pendingCursor);
    return read(query.size ? `?${query}` : "", {
      signal,
      cache: "no-store",
    });
  },
  async refresh(signal) {
    let cursor: string | null = null;
    const summary: RecoverySummary = {
      unavailable: 0,
      review_required: 0,
      pending: 0,
    };
    do {
      const result = await read(
        `/refresh${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
        { method: "POST", signal },
      );
      for (const issue of result.recovery_issues ?? []) {
        if (issue.status === "unavailable") summary.unavailable += 1;
        if (issue.status === "review_required") summary.review_required += 1;
        if (issue.status === "pending") summary.pending += 1;
      }
      cursor = result.next_cursor;
    } while (cursor != null && !signal?.aborted);
    return summary;
  },
  edit: (id, value) =>
    read(`/clients/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    }),
  confirm: (id, value) =>
    read(`/documents/${encodeURIComponent(id)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(value),
    }),
};
