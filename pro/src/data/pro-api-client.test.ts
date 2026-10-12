import { describe, expect, it, vi } from "vitest";

import { createProApiClient } from "./pro-api-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ProApiClient", () => {
  it("inicializa a sessão anônima uma única vez antes das chamadas", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = createProApiClient({ apiUrl: "/", fetcher });

    await client.request("/api/pro/conversion-batches/batch-1");
    await client.request("/api/pro/conversion-batches/batch-1");

    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      "/auth/pro/anonymous-session",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "X-OFX-Pro-Client": "web",
        }),
      }),
    );
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("refaz o bootstrap e repete a chamada somente uma vez após 401", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = createProApiClient({ apiUrl: "/", fetcher });

    const response = await client.request(
      "/api/pro/conversion-batches/batch-1",
    );

    expect(response.status).toBe(200);
    expect(
      fetcher.mock.calls.filter(([url]) =>
        String(url).endsWith("/auth/pro/anonymous-session"),
      ),
    ).toHaveLength(2);
    expect(fetcher).toHaveBeenCalledTimes(4);
  });
});
