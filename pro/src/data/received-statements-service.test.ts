import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.resetModules();
});

describe("busca de clientes", () => {
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
