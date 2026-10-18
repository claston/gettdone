import { createProApiClient } from "./pro-api-client";

export interface BatchHistoryItem {
  id: string;
  status: string;
  createdAt: string;
  expiresAt: string;
  isExpired: boolean;
  fileCount: number;
  completedCount: number;
  failedCount: number;
  pendingCount: number;
  reviewUrl: string | null;
}

export interface BatchHistoryPage {
  items: BatchHistoryItem[];
  nextCursor: string | null;
}

export interface BatchHistoryService {
  list(cursor?: string, signal?: AbortSignal): Promise<BatchHistoryPage>;
}

interface ApiSummary {
  batch_id: string;
  status: string;
  created_at: string;
  expires_at: string;
  is_expired: boolean;
  files_count: number;
  completed_count: number;
  failed_count: number;
  pending_count: number;
  first_job_id: string | null;
}

export function createBatchHistoryService(options: {
  apiUrl: string;
  fetcher?: typeof fetch;
}): BatchHistoryService {
  const client = createProApiClient(options);
  return {
    async list(cursor, signal) {
      const query = new URLSearchParams({ limit: "20" });
      if (cursor) query.set("cursor", cursor);
      const response = await client.request(
        `/api/pro/conversion-batches?${query}`,
        {
          signal,
          cache: "no-store",
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok)
        throw new Error(
          response.status === 401
            ? "Entre novamente para consultar seus lotes."
            : "Não foi possível carregar os lotes. Tente novamente.",
        );
      const payload = (await response.json()) as {
        items: ApiSummary[];
        next_cursor: string | null;
      };
      return {
        nextCursor: payload.next_cursor,
        items: payload.items.map((item) => ({
          id: item.batch_id,
          status: item.status,
          createdAt: item.created_at,
          expiresAt: item.expires_at,
          isExpired: item.is_expired,
          fileCount: item.files_count,
          completedCount: item.completed_count,
          failedCount: item.failed_count,
          pendingCount: item.pending_count,
          reviewUrl:
            item.is_expired || !item.first_job_id
              ? null
              : `/review/${encodeURIComponent(item.batch_id)}/${encodeURIComponent(item.first_job_id)}`,
        })),
      };
    },
  };
}
