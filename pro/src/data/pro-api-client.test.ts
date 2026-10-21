import { describe, expect, it, vi } from "vitest";

import { createProApiClient } from "./pro-api-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("ProApiClient", () => {
  it("verifica novamente a sessão antes de gravar depois de dez minutos", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockImplementation(async () => jsonResponse({}));
      const client = createProApiClient({ apiUrl: "/", fetcher });
      await client.request("/api/pro/conversion-batches/batch-1", {
        method: "POST",
      });
      now.mockReturnValue(1000 + 10 * 60 * 1000);
      await client.request("/api/pro/conversion-batches/batch-1", {
        method: "POST",
      });
      expect(
        fetcher.mock.calls.filter(([url]) => url === "/auth/me"),
      ).toHaveLength(2);
    } finally {
      now.mockRestore();
    }
  });
  it("consulta diretamente o servidor após inatividade sem uma verificação de sessão na frente da leitura", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockImplementation(async () => jsonResponse({}));
      const client = createProApiClient({ apiUrl: "/", fetcher });
      await client.request("/api/pro/conversion-batches/batch-1");
      now.mockReturnValue(1000 + 20 * 60 * 1000);
      await client.request("/api/pro/conversion-batches/batch-1");
      expect(
        fetcher.mock.calls.filter(([url]) => url === "/auth/me"),
      ).toHaveLength(1);
      expect(fetcher).toHaveBeenCalledTimes(3);
    } finally {
      now.mockRestore();
    }
  });
  it.each([
    [
      400,
      "Missing or invalid identity context. Start an anonymous session or authenticate.",
    ],
    [403, "Access denied for this analysis."],
    [404, "Conversion job not found."],
    [404, "Analysis not found"],
  ])(
    "recupera a sessão expirada antes de repetir uma gravação com %s",
    async (status, detail) => {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse({ email: "owner@test" }))
        .mockResolvedValueOnce(jsonResponse({ detail }, status))
        .mockResolvedValueOnce(jsonResponse({}, 401))
        .mockResolvedValueOnce(jsonResponse({ email: "owner@test" }))
        .mockResolvedValueOnce(jsonResponse({ updated_at: "saved" }));
      const client = createProApiClient({ apiUrl: "/", fetcher });
      expect(
        (
          await client.request("/api/pro/convert-edits/an_owned", {
            method: "POST",
            body: "{}",
          })
        ).status,
      ).toBe(200);
      expect(
        fetcher.mock.calls.filter(([url]) => url === "/auth/session/refresh"),
      ).toHaveLength(1);
      expect(fetcher.mock.calls[1][1]?.body).toBe(
        fetcher.mock.calls[4][1]?.body,
      );
    },
  );

  it("preserva a rejeição de origem sem repetir a gravação", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(
        jsonResponse({ detail: "Origin is not allowed for PRO writes." }, 403),
      );
    const client = createProApiClient({ apiUrl: "/", fetcher });
    expect(
      (
        await client.request("/api/pro/convert-edits/an_owned", {
          method: "POST",
        })
      ).status,
    ).toBe(403);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("renova a sessão também quando um documento fica oculto por 404", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ email: "owner@test" }))
      .mockResolvedValueOnce(
        jsonResponse({ detail: "Conversion batch not found." }, 404),
      )
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({ email: "owner@test" }))
      .mockResolvedValueOnce(
        new Response("%PDF-1.4", {
          headers: { "Content-Type": "application/pdf" },
        }),
      );
    const client = createProApiClient({ apiUrl: "/", fetcher });
    const response = await client.request(
      "/api/pro/conversion-batches/batch-1/jobs/job-1/document",
    );
    expect(response.status).toBe(200);
    expect(
      fetcher.mock.calls.some(([url]) => url === "/auth/session/refresh"),
    ).toBe(true);
    expect(fetcher).toHaveBeenCalledTimes(5);
  });
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
