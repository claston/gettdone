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
    references?: Array<{ page: number; line: number }>;
  };
}
export interface ReceivedPage {
  totals: { statements: number; ready: number; attention: number };
  clients: ReceivedClient[];
  pending: PendingStatement[];
  next_cursor: string | null;
  pending_next_cursor?: string | null;
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
export interface ReceivedService {
  list(
    cursor?: string,
    signal?: AbortSignal,
    pendingCursor?: string,
  ): Promise<ReceivedPage>;
  refresh(signal?: AbortSignal): Promise<void>;
  edit(id: string, value: ClientEdit): Promise<unknown>;
  confirm(id: string, value: Confirmation): Promise<unknown>;
}

const api = createProApiClient({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
async function read(path: string, init?: RequestInit) {
  const response = await api.request(`/api/pro/receipts${path}`, init);
  if (!response.ok)
    throw new Error(
      await readApiError(
        response,
        "Não foi possível carregar os extratos. Conversões continua disponível.",
      ),
    );
  return response.json();
}
export const receivedService: ReceivedService = {
  list: (cursor, signal, pendingCursor) => {
    const query = new URLSearchParams();
    query.set("sort", "name");
    if (cursor) query.set("cursor", cursor);
    if (pendingCursor) query.set("pending_cursor", pendingCursor);
    return read(query.size ? `?${query}` : "", {
      signal,
      cache: "no-store",
    });
  },
  async refresh(signal) {
    let cursor: string | null = null;
    do {
      const result = await read(
        `/refresh${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
        { method: "POST", signal },
      );
      cursor = result.next_cursor;
    } while (cursor != null && !signal?.aborted);
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
