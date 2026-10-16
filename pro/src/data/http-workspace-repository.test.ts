import { describe, expect, it, vi } from "vitest";

import { createHttpWorkspaceRepository } from "./http-workspace-repository";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const batchPayload = {
  batch_id: "batch-real",
  status: "completed",
  jobs: [
    {
      job_id: "job-ready",
      filename: "Itau_102026.pdf",
      status: "completed",
      analysis_id: "an_real",
      reviewed_at: null,
      result: {
        processing_id: "an_real",
        quota_limit: 50,
        quota_remaining: 47,
        analysis: {
          bank_name: "Itaú",
          transactions_total: 2,
          preview_transactions: [
            {
              date: "2026-10-01",
              description: "PIX recebido",
              amount: 150,
              running_balance: 1150,
              warning_types: [],
              is_deleted: false,
            },
            {
              date: "2026-10-02",
              description: "Tarifa",
              amount: -12.5,
              running_balance: 1137.5,
              warning_types: ["manual_review_recommended"],
              is_deleted: false,
            },
          ],
        },
      },
    },
    {
      job_id: "job-failed",
      filename: "Caixa_102026.pdf",
      status: "failed",
      analysis_id: null,
      reviewed_at: null,
      result: null,
    },
  ],
};

describe("HttpWorkspaceRepository", () => {
  it("mantém instituição, páginas e PDF separados para os três arquivos do lote", async () => {
    const institutions = [
      { name: "Itaú", code: "341", pages: 1 },
      { name: "Santander", code: "033", pages: 1 },
      { name: "Bradesco", code: "237", pages: 6 },
    ];
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse({
        ...batchPayload,
        jobs: institutions.map((institution, index) => ({
          ...batchPayload.jobs[0],
          job_id: `job-${index}`,
          filename: `extrato-${index}.pdf`,
          result: {
            ...batchPayload.jobs[0].result,
            analysis: {
              ...batchPayload.jobs[0].result!.analysis,
              bank_name: institution.name,
              bank_code: institution.code,
              pdf_processing_metrics: { page_count: institution.pages },
            },
          },
        })),
      }),
    );
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });

    const batch = await repository.getBatch("batch-real");

    expect(batch.conversions).toHaveLength(3);
    batch.conversions.forEach((conversion, index) => {
      expect(conversion).toMatchObject({
        id: `job-${index}`,
        bankName: institutions[index].name,
        bankCode: institutions[index].code,
        pdf: {
          url: `/api/pro/conversion-batches/batch-real/jobs/job-${index}/document`,
          pageCount: institutions[index].pages,
          fileName: `extrato-${index}.pdf`,
        },
      });
    });
  });

  it("usa rotas relativas quando configurado para o proxy same-origin", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(batchPayload));
    const repository = createHttpWorkspaceRepository({
      apiUrl: "/",
      fetcher,
    });

    await repository.getBatch("batch-real");

    expect(fetcher).toHaveBeenCalledWith(
      "/api/pro/conversion-batches/batch-real",
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("mapeia lote real sem expor o contrato bruto do backend", async () => {
    const fetcher = vi.fn().mockResolvedValue(jsonResponse(batchPayload));
    const repository = createHttpWorkspaceRepository({
      apiUrl: "https://api.example.test/",
      fetcher,
    });

    const batch = await repository.getBatch("batch-real");

    expect(fetcher).toHaveBeenCalledWith(
      "https://api.example.test/api/pro/conversion-batches/batch-real",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(batch.quota).toEqual({ used: 3, limit: 50, unit: "FILES" });
    expect(batch.conversions[0]).toMatchObject({
      id: "job-ready",
      bankName: "Itaú",
      status: "NEEDS_REVIEW",
      suspectedCount: 1,
      transactionCount: 2,
    });
    expect(batch.conversions[0].transactions[0]).toMatchObject({
      id: "row_1",
      date: "01/10/2026",
      credit: 150,
      debit: null,
      balance: 1150,
    });
    expect(batch.conversions[0].pdf?.url).toBe(
      "https://api.example.test/api/pro/conversion-batches/batch-real/jobs/job-ready/document",
    );
    expect(batch.conversions[0].downloadUrl).toBe(
      "https://api.example.test/convert-report/an_real?format=ofx",
    );
    expect(batch.conversions[1].status).toBe("FAILED");
  });

  it("traduz alterações, inserções e remoções para a API de edições existente", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockResolvedValueOnce(
        jsonResponse({
          processing_id: "an_real",
          preview_transactions: [],
          updated_at: "2026-10-11T20:00:00Z",
        }),
      );
    const repository = createHttpWorkspaceRepository({
      apiUrl: "https://api.example.test",
      fetcher,
    });
    const batch = await repository.getBatch("batch-real");
    const [first] = batch.conversions[0].transactions;

    await repository.saveTransactions("job-ready", [
      { ...first, description: "PIX corrigido" },
      {
        ...first,
        id: "local-new",
        description: "Novo lançamento",
      },
    ]);

    const [url, request] = fetcher.mock.calls[2] as [string, RequestInit];
    expect(url).toBe("https://api.example.test/api/pro/convert-edits/an_real");
    const payload = JSON.parse(String(request.body));
    expect(payload.edits).toEqual([
      expect.objectContaining({
        action: "update",
        row_id: "row_1",
        description: "PIX corrigido",
        date: "2026-10-01",
      }),
      expect.objectContaining({
        action: "insert",
        description: "Novo lançamento",
      }),
      { action: "delete", row_id: "row_2" },
    ]);
  });

  it("preserva o identificador original quando há linhas excluídas", async () => {
    const payloadWithDeletedRow = structuredClone(batchPayload);
    payloadWithDeletedRow.jobs[0].result!.analysis.preview_transactions = [
      {
        date: "2026-09-30",
        description: "Linha excluída",
        amount: -1,
        running_balance: 1000,
        warning_types: [],
        is_deleted: true,
      },
      ...payloadWithDeletedRow.jobs[0].result!.analysis.preview_transactions,
    ];
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse(payloadWithDeletedRow))
      .mockResolvedValueOnce(
        jsonResponse({
          processing_id: "an_real",
          preview_transactions:
            payloadWithDeletedRow.jobs[0].result!.analysis.preview_transactions,
          updated_at: "2026-10-11T20:00:00Z",
        }),
      );
    const repository = createHttpWorkspaceRepository({
      apiUrl: "https://api.example.test",
      fetcher,
    });
    const batch = await repository.getBatch("batch-real");
    const [first, second] = batch.conversions[0].transactions;

    expect(first.id).toBe("row_2");
    await repository.saveTransactions("job-ready", [
      { ...first, description: "PIX corrigido" },
      second,
    ]);

    const [, request] = fetcher.mock.calls[2] as [string, RequestInit];
    const payload = JSON.parse(String(request.body));
    expect(payload.edits[0]).toEqual(
      expect.objectContaining({
        row_id: "row_2",
        description: "PIX corrigido",
      }),
    );
  });

  it("persiste revisão e solicita retry no contexto do lote", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ status: "ready" }))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockResolvedValueOnce(
        jsonResponse({ job_id: "job-ready", reviewed_at: "now" }),
      )
      .mockResolvedValueOnce(jsonResponse(batchPayload));
    const repository = createHttpWorkspaceRepository({
      apiUrl: "https://api.example.test",
      fetcher,
    });
    await repository.getBatch("batch-real");

    await repository.markReviewed("job-ready");
    await repository.retryConversion("job-failed");

    expect(fetcher.mock.calls[2][0]).toContain(
      "/batch-real/jobs/job-ready/review",
    );
    expect(fetcher.mock.calls[3][0]).toContain(
      "/batch-real/jobs/job-failed/retry",
    );
  });
});
