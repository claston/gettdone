import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("busca de clientes", () => {
  it("mostra uma mensagem em português quando a rede falha", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch")),
    );
    const { loadReceivedDocument } =
      await import("./received-statements-service");
    await expect(
      loadReceivedDocument("batch", "job", new AbortController().signal),
    ).rejects.toThrow("Não foi possível carregar o PDF.");
  });
  it("busca apenas o PDF original com sessão, cancelamento e sem cache", async () => {
    const fetcher = vi.fn().mockImplementation(async (path) =>
      path === "/auth/me"
        ? Response.json({})
        : new Response("%PDF-example", {
            headers: { "Content-Type": "application/pdf" },
          }),
    );
    vi.stubGlobal("fetch", fetcher);
    const { loadReceivedDocument } =
      await import("./received-statements-service");
    const abort = new AbortController();
    const blob = await loadReceivedDocument("batch/1", "job/1", abort.signal);
    expect(blob.type).toBe("application/pdf");
    expect(fetcher).toHaveBeenLastCalledWith(
      "/api/pro/conversion-batches/batch%2F1/jobs/job%2F1/document",
      expect.objectContaining({
        credentials: "include",
        signal: abort.signal,
        cache: "no-store",
      }),
    );
    expect(
      fetcher.mock.calls.every(
        ([path]) => path === "/auth/me" || path.endsWith("/document"),
      ),
    ).toBe(true);
  });
  it("informa quando o PDF não está mais disponível", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockImplementation(async (path) =>
          path === "/auth/me"
            ? Response.json({})
            : new Response(null, { status: 404 }),
        ),
    );
    const { loadReceivedDocument } =
      await import("./received-statements-service");
    await expect(
      loadReceivedDocument("batch", "job", new AbortController().signal),
    ).rejects.toThrow(/indisponível ou expirado/);
  });
  it("envia identificadores no corpo, preserva cursores e não os inclui em URLs ou telemetria", async () => {
    const fetcher = vi
      .fn()
      .mockImplementation(async () =>
        Response.json({ clients: [], pending: [] }),
      );
    vi.stubGlobal("fetch", fetcher);
    const { receivedService } = await import("./received-statements-service");
    const abort = new AbortController();
    await receivedService.list("c2", abort.signal, "p2", "12.345.678/0001-90");
    const [url, init] = fetcher.mock.calls.find(
      ([path]) => path === "/api/pro/receipts/search",
    )!;
    expect(url).not.toContain("12345");
    expect(init.method).toBe("POST");
    expect(init.signal).toBe(abort.signal);
    expect(JSON.parse(init.body)).toEqual({
      query: "12.345.678/0001-90",
      cursor: "c2",
      pending_cursor: "p2",
      sort: "name",
    });
    expect(
      fetcher.mock.calls.every(
        ([path]) => !path.includes("telemetry") && !path.includes("12.345"),
      ),
    ).toBe(true);
    await receivedService.list(undefined, abort.signal, undefined, "");
    expect(fetcher.mock.calls.at(-1)?.[0]).toBe("/api/pro/receipts?sort=name");
  });
});
