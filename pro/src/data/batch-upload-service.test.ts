import { describe, expect, it, vi } from "vitest";

import { createBatchUploadService } from "./batch-upload-service";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("BatchUploadService", () => {
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
