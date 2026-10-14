import { describe, expect, it, vi } from "vitest";

import { createProApiClient } from "./pro-api-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ProApiClient", () => {
  it("reutiliza a sessão autenticada sem criar identidade anônima", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ email: "canario@example.com" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = createProApiClient({ apiUrl: "/", fetcher });

    await client.request("/api/pro/conversion-batches/batch-1");
    await client.request("/api/pro/conversion-batches/batch-1");

    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      "/auth/me",
      expect.objectContaining({
        method: "GET",
        credentials: "include",
        headers: expect.objectContaining({
          "X-OFX-Pro-Client": "web",
        }),
      }),
    );
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).endsWith("/auth/pro/anonymous-session"),
      ),
    ).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("cria uma identidade anônima somente quando não existe sessão autenticada", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401))
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = createProApiClient({ apiUrl: "/", fetcher });

    await client.request("/api/pro/conversion-batches/batch-1");

    expect(fetcher).toHaveBeenNthCalledWith(
      3,
      "/auth/pro/anonymous-session",
      expect.objectContaining({
        method: "POST",
        credentials: "include",
      }),
    );
  });

  it("renova a sessão antes de considerar o navegador anônimo", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ email: "canario@example.com" }))
      .mockResolvedValueOnce(jsonResponse({ ok: true }));
    const client = createProApiClient({ apiUrl: "/", fetcher });

    await client.request("/api/pro/conversion-batches/batch-1");

    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/auth/session/refresh",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).endsWith("/auth/pro/anonymous-session"),
      ),
    ).toBe(false);
  });

  it("refaz o bootstrap e repete a chamada somente uma vez após 401", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ email: "canario@example.com" }))
      .mockResolvedValueOnce(jsonResponse({ detail: "expired" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401))
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
    ).toHaveLength(1);
    expect(fetcher).toHaveBeenCalledTimes(6);
  });
});
