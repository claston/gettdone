import { describe, expect, it, vi } from "vitest";

import { createBatchUploadService } from "./batch-upload-service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

it("envia um PDF PRO e confere páginas antes de submeter ao worker", async () => {
  const request = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("conversion-runtime"))
      return jsonResponse({
        direct_batch_enabled: true,
        upload_mode: "direct_s3",
        execution_mode: "sqs_lambda",
        batch_max_files: 12,
      });
    if (url.endsWith("pro-estimate"))
      return jsonResponse({
        pages: 4,
        remaining_pages: 20,
        remaining_after: 16,
        monthly_pages: 20,
      });
    if (url.endsWith("submit"))
      return jsonResponse({ batch_id: "pro-test" }, 202);
    expect(JSON.parse(String(init?.body)).ocr_mode).toBe("pro");
    return jsonResponse({
      batch_id: "pro-test",
      uploads: [
        {
          job_id: "job-pro",
          url: "https://uploads.example.test/pro",
          fields: {},
        },
      ],
    });
  });
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValue(new Response(null, { status: 204 }));
  const service = createBatchUploadService({
    apiUrl: "/",
    apiClient: { request },
    fetcher,
    digestFile: async () => "a".repeat(64),
    createIdempotencyKey: () => "pro-key",
  });
  const estimate = await service.preparePro!(new File(["scan"], "asaas.pdf"));
  expect(estimate).toMatchObject({
    batchId: "pro-test",
    pages: 4,
    remaining_after: 16,
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(request.mock.calls.some(([url]) => url.endsWith("submit"))).toBe(
    false,
  );
  await service.submitPro!(estimate.batchId);
  expect(
    request.mock.calls.filter(([url]) => url.endsWith("submit")),
  ).toHaveLength(1);
});

describe("BatchUploadService", () => {
  it.each(["http", "network", "all"])(
    "submete o lote e identifica somente uploads com falha: %s",
    async (failure) => {
      let finishLastUpload!: () => void;
      const lastUpload = new Promise<Response>((resolve) => {
        finishLastUpload = () =>
          resolve(
            new Response(null, { status: failure === "all" ? 500 : 204 }),
          );
      });
      const request = vi.fn<
        (url: string, init?: RequestInit) => Promise<Response>
      >(async (url) => {
        if (url.includes("runtime")) {
          return jsonResponse({
            direct_batch_enabled: true,
            upload_mode: "direct_s3",
            execution_mode: "sqs_lambda",
            batch_max_files: 12,
          });
        }
        if (url.endsWith("/submit")) {
          return jsonResponse({ batch_id: "batch-partial" }, 202);
        }
        return jsonResponse({
          batch_id: "batch-partial",
          uploads: ["job-1", "job-2", "job-3"].map((job_id) => ({
            job_id,
            url: `https://uploads.example.test/${job_id}`,
            fields: {},
          })),
        });
      });
      const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => {
        if (String(url).endsWith("job-3")) return lastUpload;
        if (failure === "all" || String(url).endsWith("job-1")) {
          if (failure === "network") throw new TypeError("Failed to fetch");
          return new Response(null, { status: 500 });
        }
        return new Response(null, { status: 204 });
      });
      const service = createBatchUploadService({
        apiUrl: "/",
        apiClient: { request },
        fetcher,
        digestFile: async () => "a".repeat(64),
        createIdempotencyKey: () => "request-partial",
      });

      const submitted = service.createAndSubmit([
        new File(["1"], "falha.pdf"),
        new File(["2"], "ok.pdf"),
        new File(["3"], "ultimo.pdf"),
      ]);
      // Attach rejection handling before the intentionally delayed upload finishes.
      const result = submitted.then(
        (value) => ({ value, error: undefined }),
        (error: unknown) => ({ value: undefined, error }),
      );
      await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
      expect(request.mock.calls.some(([url]) => url.endsWith("/submit"))).toBe(
        false,
      );
      finishLastUpload();
      expect(await result).toEqual({
        value: { batchId: "batch-partial" },
        error: undefined,
      });
      const submitCall = request.mock.calls.find(([url]) =>
        url.endsWith("/submit"),
      )!;
      expect(JSON.parse(String((submitCall[1] as RequestInit).body))).toEqual({
        failed_upload_job_ids:
          failure === "all" ? ["job-1", "job-2", "job-3"] : ["job-1"],
      });
    },
  );

  it("usa exclusivamente upload direto e submissão ao worker", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ email: "canario@example.com" }))
      .mockResolvedValueOnce(
        jsonResponse({
          direct_batch_enabled: true,
          upload_mode: "direct_s3",
          execution_mode: "sqs_lambda",
          batch_max_files: 12,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          {
            batch_id: "batch-real",
            uploads: [
              {
                job_id: "job-1",
                filename: "extrato.ofx",
                url: "https://uploads.example.test",
                fields: { key: "document.pdf" },
              },
            ],
            jobs: [],
          },
          201,
        ),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ batch_id: "batch-real" }, 202));
    const service = createBatchUploadService({
      apiUrl: "/",
      fetcher,
      digestFile: vi.fn().mockResolvedValue("a".repeat(64)),
      createIdempotencyKey: () => "request-123",
    });

    const result = await service.createAndSubmit([
      new File(["conteúdo"], "extrato.ofx"),
    ]);

    expect(result).toEqual({ batchId: "batch-real" });
    expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([
      "/auth/me",
      "/api/conversion-runtime",
      "/api/pro/conversion-batches",
      "https://uploads.example.test",
      "/api/pro/conversion-batches/batch-real/submit",
    ]);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).includes("/api/conversions/upload"),
      ),
    ).toBe(false);
    const createRequest = fetcher.mock.calls[2][1] as RequestInit;
    expect(JSON.parse(String(createRequest.body))).toMatchObject({
      files: [
        {
          filename: "extrato.ofx",
          content_type: "application/x-ofx",
        },
      ],
    });
  });

  it("bloqueia o envio quando o runtime não usa o worker", async () => {
    const digestFile = vi.fn().mockResolvedValue("a".repeat(64));
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ email: "canario@example.com" }))
      .mockResolvedValueOnce(
        jsonResponse({
          direct_batch_enabled: false,
          upload_mode: "proxy",
          execution_mode: "inline_legacy",
          batch_max_files: 12,
        }),
      );
    const service = createBatchUploadService({
      apiUrl: "/",
      fetcher,
      digestFile,
    });

    await expect(
      service.createAndSubmit([
        new File(["conteúdo"], "extrato.pdf", { type: "application/pdf" }),
      ]),
    ).rejects.toThrow("processamento assíncrono está indisponível");

    expect(digestFile).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
