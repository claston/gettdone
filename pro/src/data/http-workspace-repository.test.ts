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
  it("usa ordem de origem e ID estável na gravação sem expor uma coluna de sequência", async () => {
    const original = structuredClone(batchPayload);
    const rows = original.jobs[0].result!.analysis.preview_transactions.map(
      (row, index) => ({
        ...row,
        transaction_id: `txn-${index}`,
        source_sequence: 2 - index,
        source_subsequence: 0,
      }),
    );
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (url, init) => {
        if (String(url).endsWith("/auth/me")) return jsonResponse({});
        if (init?.method === "POST")
          return jsonResponse({
            updated_at: "saved",
            preview_transactions: rows,
          });
        return jsonResponse({
          ...original,
          jobs: [
            {
              ...original.jobs[0],
              result: {
                ...original.jobs[0].result,
                analysis: {
                  ...original.jobs[0].result!.analysis,
                  preview_transactions: rows,
                },
              },
            },
          ],
        });
      });
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    const displayed = batch.conversions[0].transactions;
    expect(displayed.map((row) => row.id)).toEqual(["txn-1", "txn-0"]);
    expect(batch.period).toBe("01/10/2026–02/10/2026");
    await repository.saveTransactions("job-ready", [
      { ...displayed[0], description: "Corrigida" },
      displayed[1],
    ]);
    const write = fetcher.mock.calls.find(
      ([, init]) => init?.method === "POST",
    )!;
    expect(JSON.parse(String(write[1]?.body)).edits).toEqual([
      expect.objectContaining({ row_id: "txn-1", description: "Corrigida" }),
    ]);
  });

  it("confirma uma inclusão após perda da resposta sem reenviar a inclusão", async () => {
    let operationId = "";
    const rows = batchPayload.jobs[0].result!.analysis.preview_transactions;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (url, init) => {
        if (String(url).endsWith("/auth/me")) return jsonResponse({});
        if (init?.method === "POST") {
          operationId = JSON.parse(String(init.body)).operation_id;
          throw new TypeError("Resposta perdida");
        }
        if (String(url).includes("/operations/"))
          return jsonResponse({
            operation_id: operationId,
            updated_at: "v2",
            preview_transactions: [...rows, rows[0]],
          });
        return jsonResponse(batchPayload);
      });
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    const original = batch.conversions[0].transactions;
    const pending = [...original, { ...original[0], id: "new-row" }];
    await expect(
      repository.saveTransactions("job-ready", pending),
    ).resolves.toHaveLength(3);
    await repository.saveTransactions("job-ready", pending);
    expect(
      fetcher.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
    expect(
      fetcher.mock.calls.some(([url]) =>
        String(url).endsWith(`/operations/${operationId}`),
      ),
    ).toBe(true);
  });

  it("reutiliza a operação pendente antes de gravar novas edições após falha de rede", async () => {
    const writes: Record<string, unknown>[] = [];
    let connected = false;
    const rows = batchPayload.jobs[0].result!.analysis.preview_transactions;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (url, init) => {
        if (String(url).endsWith("/auth/me")) return jsonResponse({});
        if (String(url).includes("/operations/"))
          throw new TypeError("Offline");
        if (init?.method === "POST") {
          const payload = JSON.parse(String(init.body));
          writes.push(payload);
          if (!connected) throw new TypeError("Offline");
          return jsonResponse({
            operation_id: payload.operation_id,
            updated_at: `v${writes.length}`,
            preview_transactions: [...rows, rows[0]],
          });
        }
        return jsonResponse(batchPayload);
      });
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    const original = batch.conversions[0].transactions;
    const added = { ...original[0], id: "new-row" };
    await expect(
      repository.saveTransactions("job-ready", [...original, added]),
    ).rejects.toThrow("Offline");
    connected = true;
    await repository.saveTransactions("job-ready", [
      ...original,
      { ...added, description: "Correção posterior" },
    ]);
    expect(writes[1]).toEqual(writes[0]);
    expect(writes[2].operation_id).not.toBe(writes[0].operation_id);
    expect(writes[2].edits).toEqual([
      expect.objectContaining({
        action: "update",
        description: "Correção posterior",
      }),
    ]);
  });

  it("preserva versões e IDs após inserção no início, ordenação e polling antigo", async () => {
    const original = structuredClone(batchPayload);
    const rows = original.jobs[0].result!.analysis.preview_transactions;
    const stored = [...rows];
    let version = 0;
    const writes: Array<{
      edits: Array<Record<string, unknown>>;
      expected_updated_at: string | null;
    }> = [];
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (url, init) => {
        if (String(url).endsWith("/auth/me")) return jsonResponse({ ok: true });
        if (String(url).includes("/convert-edits/")) {
          const payload = JSON.parse(String(init?.body));
          writes.push(payload);
          for (const edit of payload.edits) {
            const row = {
              date: edit.date,
              description: edit.description,
              amount: edit.credit ?? -edit.debit,
              running_balance: 100,
              warning_types: [],
              is_deleted: false,
            };
            if (edit.action === "insert")
              stored.splice(edit.insert_position, 0, row);
            if (edit.action === "update")
              stored[Number(edit.row_id.replace("row_", "")) - 1] = row;
          }
          version += 1;
          return jsonResponse({
            updated_at: `2026-10-17T20:00:0${version}Z`,
            preview_transactions: stored,
          });
        }
        return jsonResponse(original);
      });
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    const [first, second] = batch.conversions[0].transactions;
    const added = {
      ...first,
      id: "new-manual",
      date: "30/09/2026",
      description: "Nova transação",
    };
    await repository.saveTransactions("job-ready", [added, first, second]);
    expect(writes[0].edits.map((edit) => edit.action)).toEqual(["insert"]);
    const refreshed = await repository.getBatch("batch-real");
    expect(refreshed.conversions[0].transactions[0].id).toBe("new-manual");
    await repository.saveTransactions("job-ready", [
      { ...second, description: "Tarifa corrigida" },
      first,
      added,
    ]);
    expect(writes[1].expected_updated_at).toBe("2026-10-17T20:00:01Z");
    expect(writes[1].edits).toEqual([
      expect.objectContaining({
        action: "update",
        row_id: "row_3",
        description: "Tarifa corrigida",
      }),
    ]);
    expect(stored.map((row) => row.description)).toEqual([
      "Nova transação",
      "PIX recebido",
      "Tarifa corrigida",
    ]);
  });

  it("não reenvia linhas inalteradas, incluindo lançamentos de valor zero", async () => {
    const payload = structuredClone(batchPayload);
    payload.jobs[0].result!.analysis.preview_transactions[0].amount = 0;
    const fetcher = vi
      .fn()
      .mockImplementation(async (url: string) =>
        jsonResponse(url.endsWith("/auth/me") ? {} : payload),
      );
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    await repository.saveTransactions(
      "job-ready",
      batch.conversions[0].transactions,
    );
    expect(fetcher).toHaveBeenCalledTimes(2);
    const rows = batch.conversions[0].transactions;
    await repository.saveTransactions("job-ready", [
      rows[0],
      { ...rows[1], description: "Tarifa corrigida" },
    ]);
    const edits = JSON.parse(String(fetcher.mock.calls[2][1].body)).edits;
    expect(edits).toEqual([
      expect.objectContaining({
        action: "update",
        row_id: "row_2",
        description: "Tarifa corrigida",
      }),
    ]);
  });
  it("cria visualização PDF somente para os dois PDFs de um lote com três OFX", async () => {
    const names = [
      "caixa.pdf",
      "itau.pdf",
      "banrisul.ofx",
      "nordeste.ofx",
      "santander.ofx",
    ];
    const fetcher = vi.fn().mockResolvedValue(
      jsonResponse({
        ...batchPayload,
        jobs: names.map((filename, index) => ({
          ...batchPayload.jobs[0],
          job_id: `job-${index}`,
          filename,
          result: {
            ...batchPayload.jobs[0].result,
            analysis: {
              ...batchPayload.jobs[0].result!.analysis,
              file_type: index < 2 ? "pdf" : "ofx",
            },
          },
        })),
      }),
    );

    const batch = await createHttpWorkspaceRepository({
      apiUrl: "/",
      fetcher,
    }).getBatch("batch-real");

    expect(
      batch.conversions.map((conversion) => Boolean(conversion.pdf)),
    ).toEqual([true, true, false, false, false]);
    expect(
      batch.conversions.every(
        (conversion) => conversion.transactions.length === 2,
      ),
    ).toBe(true);
  });

  it.each(["ofx", "csv", "xlsx"])(
    "não cria iframe para %s ainda em processamento",
    async (extension) => {
      const fetcher = vi.fn().mockResolvedValue(
        jsonResponse({
          ...batchPayload,
          jobs: [
            {
              job_id: "job-pending",
              filename: `original.${extension}`,
              status: "queued",
              result: null,
            },
          ],
        }),
      );
      const batch = await createHttpWorkspaceRepository({
        apiUrl: "/",
        fetcher,
      }).getBatch("batch-real");
      expect(batch.conversions[0].status).toBe("PROCESSING");
      expect(batch.conversions[0].pdf).toBeNull();
    },
  );

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
      { action: "delete", row_id: "row_2" },
      expect.objectContaining({
        action: "insert",
        description: "Novo lançamento",
      }),
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
