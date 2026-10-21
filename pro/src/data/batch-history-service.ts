import { createProApiClient } from "./pro-api-client";
import { proSessionCacheVersion } from "./pro-session-cache";

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
  getCachedPage?(cursor?: string): BatchHistoryPage | undefined;
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
  const pages = new Map<string, BatchHistoryPage>();
  let version = proSessionCacheVersion();
  const synchronizeSession = () => {
    if (version !== proSessionCacheVersion()) {
      pages.clear();
      version = proSessionCacheVersion();
    }
  };
  return {
    getCachedPage(cursor) {
      synchronizeSession();
      return pages.get(cursor ?? "");
    },
    async list(cursor, signal) {
      synchronizeSession();
      const requestVersion = version;
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
      if (signal?.aborted || requestVersion !== proSessionCacheVersion())
        throw new DOMException("Aborted", "AbortError");
      const page: BatchHistoryPage = {
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
      pages.delete(cursor ?? "");
      pages.set(cursor ?? "", page);
      while (pages.size > 5) pages.delete(pages.keys().next().value!);
      return page;
    },
  };
}

export const batchHistoryService = createBatchHistoryService({
  apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "/",
});
