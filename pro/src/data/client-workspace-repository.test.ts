import { describe, expect, it, vi } from "vitest";
import { createHttpWorkspaceRepository } from "./http-workspace-repository";

describe("workspace por cliente", () => {
  it("não reabre um documento em cache no limite exato de expiração", async () => {
    const clock = vi.spyOn(Date, "now");
    const expires = Date.parse("2026-10-29T12:00:00Z");
    clock.mockReturnValue(expires - 1);
    try {
      const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => {
        if (String(url).endsWith("/document")) return new Response("pdf");
        return Response.json({
          batch_id: "client_1",
          jobs: [
            {
              job_id: "j1",
              source_batch_id: "b1",
              filename: "extrato.pdf",
              status: "completed",
              expires_at: new Date(expires).toISOString(),
              result: {
                analysis: { file_type: "pdf", preview_transactions: [] },
              },
            },
          ],
        });
      });
      const repository = createHttpWorkspaceRepository({
        apiUrl: "/",
        fetcher,
        clientId: "client_1",
      });
      await repository.getBatch("client_1");
      await repository.loadDocument!("j1");
      const calls = fetcher.mock.calls.length;
      clock.mockReturnValue(expires);
      expect(repository.getCachedBatch!("client_1")?.conversions).toHaveLength(
        0,
      );
      await expect(repository.loadDocument!("j1")).rejects.toThrow("expirou");
      expect(fetcher).toHaveBeenCalledTimes(calls);
    } finally {
      clock.mockRestore();
    }
  });
  it("carrega múltiplas páginas e preserva lote original para revisão e PDF", async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => {
      const second = String(url).includes("cursor=j1");
      return Response.json({
        batch_id: "client_1",
        client_name: "Cliente",
        next_cursor: second ? null : "j1",
        jobs: [
          {
            job_id: second ? "j2" : "j1",
            source_batch_id: second ? "b2" : "b1",
            filename: "extrato.pdf",
            status: "completed",
            expires_at: "2099-01-01T00:00:00Z",
            analysis_id: second ? "an2" : "an1",
            result: {
              analysis: { file_type: "pdf", preview_transactions: [] },
            },
          },
        ],
      });
    });
    const repository = createHttpWorkspaceRepository({
      apiUrl: "/",
      fetcher,
      clientId: "client_1",
    });
    const result = await repository.getBatch("client_1");
    expect(result.conversions.map((row) => row.batchId)).toEqual(["b1", "b2"]);
    expect(result.conversions[1].pdf?.url).toContain("/b2/jobs/j2/document");
    expect(result.clientName).toBe("Cliente");
    expect(result.conversions).toHaveLength(2);
    fetcher.mockResolvedValueOnce(Response.json({}));
    await repository.markReviewed("j2");
    expect(fetcher.mock.calls.at(-1)?.[0]).toContain("/b2/jobs/j2/review");
  });
});
