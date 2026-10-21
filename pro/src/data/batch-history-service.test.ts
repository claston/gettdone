import { describe, expect, it, vi } from "vitest";
import { createBatchHistoryService } from "./batch-history-service";
import { invalidateProSessionCaches } from "./pro-session-cache";

const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });

describe("BatchHistoryService", () => {
  it("usa sessão e cursor sem cache e não gera link para lote expirado", async () => {
    const summary = {
      batch_id: "batch-1",
      status: "completed",
      created_at: "2026-10-17T00:00:00Z",
      expires_at: "2026-10-18T00:00:00Z",
      is_expired: false,
      files_count: 2,
      completed_count: 2,
      failed_count: 0,
      pending_count: 0,
      first_job_id: "job-1",
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(json({}))
      .mockResolvedValueOnce(
        json({
          items: [
            summary,
            { ...summary, batch_id: "expired", is_expired: true },
          ],
          next_cursor: "next",
        }),
      );
    const service = createBatchHistoryService({ apiUrl: "/", fetcher });
    const result = await service.list("cursor+value");
    expect(fetcher.mock.calls[1][0]).toBe(
      "/api/pro/conversion-batches?limit=20&cursor=cursor%2Bvalue",
    );
    expect(fetcher.mock.calls[1][1]).toMatchObject({
      credentials: "include",
      cache: "no-store",
    });
    expect(result.items[0].reviewUrl).toBe("/review/batch-1/job-1");
    expect(result.items[1].reviewUrl).toBeNull();
    expect(result.nextCursor).toBe("next");
    expect(service.getCachedPage?.("cursor+value")).toEqual(result);
    invalidateProSessionCaches();
    expect(service.getCachedPage?.("cursor+value")).toBeUndefined();
  });
});
