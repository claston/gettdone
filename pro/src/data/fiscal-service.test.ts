import { webcrypto } from "node:crypto";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { uploadFiscalNote } from "./fiscal-service";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("./pro-api-client", () => ({
  createProApiClient: () => ({ request }),
  readApiError: (_response: Response, fallback: string) =>
    Promise.resolve(fallback),
}));
const file = () => {
  const result = new File(["<NFe/>"], "arroz.xml", { type: "application/xml" });
  Object.defineProperty(result, "arrayBuffer", {
    value: async () => new TextEncoder().encode("<NFe/>").buffer,
  });
  return result;
};
const prepared = {
  id: "i1",
  state: "uploading",
  upload: { url: "https://upload.example.test", fields: { key: "synthetic" } },
};
beforeEach(() => {
  request.mockReset();
  vi.stubGlobal("crypto", webcrypto);
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
  );
});
afterEach(() => vi.unstubAllGlobals());

it("só confirma o arquivo após enviar os bytes e confirmar o recebimento", async () => {
  request
    .mockResolvedValueOnce(Response.json(prepared))
    .mockResolvedValueOnce(Response.json({ id: "i1", state: "queued" }));
  const controller = new AbortController();
  expect(await uploadFiscalNote("c1", file(), controller.signal)).toEqual({
    id: "i1",
    state: "queued",
  });
  expect(fetch).toHaveBeenCalledWith(
    prepared.upload.url,
    expect.objectContaining({
      method: "POST",
      signal: controller.signal,
      credentials: "omit",
    }),
  );
  expect(request).toHaveBeenNthCalledWith(
    1,
    "/api/pro/financial-center/clients/c1/fiscal/imports",
    expect.any(Object),
  );
  expect(request).toHaveBeenLastCalledWith(
    "/api/pro/financial-center/clients/c1/fiscal/imports/i1/submit",
    expect.objectContaining({ signal: controller.signal }),
  );
});
it("retorna a nota já importada sem enviar ou processar uma cópia", async () => {
  request.mockResolvedValueOnce(
    Response.json({ id: "i1", state: "ready", upload: null }),
  );
  expect(await uploadFiscalNote("c1", file())).toEqual({
    id: "i1",
    state: "ready",
  });
  expect(fetch).not.toHaveBeenCalled();
  expect(request).toHaveBeenCalledTimes(1);
});
it("não transforma uma tentativa anterior com falha em sucesso", async () => {
  request.mockResolvedValueOnce(
    Response.json({ id: "i1", state: "failed", upload: null }),
  );
  await expect(uploadFiscalNote("c1", file())).rejects.toThrow(
    "não pôde ser processado",
  );
  expect(fetch).not.toHaveBeenCalled();
});
it("não confirma um envio sem destino ou comprovante de recebimento", async () => {
  request.mockResolvedValueOnce(
    Response.json({ id: "i1", state: "uploading", upload: null }),
  );
  await expect(uploadFiscalNote("c1", file())).rejects.toThrow(
    "Não foi possível confirmar o envio",
  );
});
it.each([false, true])(
  "informa falha no envio dos bytes sem confirmar a importação (%s)",
  async (networkFailure) => {
    request.mockResolvedValueOnce(Response.json(prepared));
    if (networkFailure)
      vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    else
      vi.mocked(fetch).mockResolvedValueOnce(
        new Response(null, { status: 403 }),
      );
    await expect(uploadFiscalNote("c1", file())).rejects.toThrow(
      "Não foi possível enviar a nota",
    );
    expect(request).toHaveBeenCalledTimes(1);
  },
);
