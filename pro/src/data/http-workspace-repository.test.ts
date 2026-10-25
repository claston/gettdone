import { describe, expect, it, vi } from "vitest";

import { createHttpWorkspaceRepository } from "./http-workspace-repository";
import { invalidateProSessionCaches } from "./pro-session-cache";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("documentos e lotes já abertos", () => {
  it("preserva o motivo de falha de upload para orientar o reenvio", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(
        jsonResponse({
          batch_id: "batch-partial",
          status: "processing",
          jobs: [
            {
              job_id: "job-failed",
              filename: "falha.pdf",
              status: "failed",
              error_code: "upload_failed",
            },
          ],
        }),
      );
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-partial");
    expect(batch.conversions[0]).toMatchObject({
      status: "FAILED",
      errorCode: "upload_failed",
      pdf: null,
    });
  });

  it("não substitui o snapshot com uma resposta de lote cancelada em trânsito", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      );
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const original = await repository.getBatch("batch-real");
    const abort = new AbortController();
    const loading = repository.getBatch("batch-real", abort.signal);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    abort.abort();
    const stale = structuredClone(batchPayload);
    stale.jobs[0].filename = "resposta-antiga.pdf";
    finish(jsonResponse(stale));
    await expect(loading).rejects.toMatchObject({ name: "AbortError" });
    expect(repository.getCachedBatch?.("batch-real")).toBe(original);
  });
  it("reabre o PDF depois de vinte minutos sem baixar nem verificar a sessão novamente", async () => {
    const now = vi.spyOn(Date, "now").mockReturnValue(1000);
    try {
      const fetcher = vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse({}))
        .mockResolvedValueOnce(jsonResponse(batchPayload))
        .mockResolvedValueOnce(new Response("%PDF-1.4"));
      const repository = createHttpWorkspaceRepository({
        apiUrl: "/",
        fetcher,
      });
      await repository.getBatch("batch-real");
      const first = await repository.loadDocument!("job-ready");
      now.mockReturnValue(1000 + 20 * 60 * 1000);
      expect(await repository.loadDocument!("job-ready")).toBe(first);
      expect(fetcher).toHaveBeenCalledTimes(3);
      expect(repository.getCachedBatch?.("batch-real")?.id).toBe("batch-real");
    } finally {
      now.mockRestore();
    }
  });

  it("refaz o download quando o usuário pede para recarregar o PDF", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockResolvedValueOnce(new Response("first"))
      .mockResolvedValueOnce(new Response("second"));
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    await repository.getBatch("batch-real");
    const first = await repository.loadDocument!("job-ready");
    const second = await repository.loadDocument!("job-ready", undefined, {
      reload: true,
    });
    expect(second).not.toBe(first);
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("não devolve documentos nem lotes da sessão anterior", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockResolvedValueOnce(new Response("%PDF-1.4"));
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    await repository.getBatch("batch-real");
    await repository.loadDocument!("job-ready");
    invalidateProSessionCaches();
    expect(repository.getCachedBatch?.("batch-real")).toBeUndefined();
    await expect(repository.loadDocument!("job-ready")).rejects.toThrow();
  });

  it("não usa uma resposta cancelada como documento disponível", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValueOnce(new Response("%PDF-1.4"));
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    await repository.getBatch("batch-real");
    const abort = new AbortController();
    const loading = repository.loadDocument!("job-ready", abort.signal);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    abort.abort();
    finish(new Response("stale document"));
    await expect(loading).rejects.toMatchObject({ name: "AbortError" });
    await repository.loadDocument!("job-ready");
    expect(fetcher).toHaveBeenCalledTimes(4);
  });

  it("mostra as edições confirmadas quando o lote é reaberto da memória", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockResolvedValueOnce(jsonResponse({ updated_at: "saved" }));
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    await repository.saveTransactions(
      "job-ready",
      batch.conversions[0].transactions.map((row, index) =>
        index ? row : { ...row, description: "Descrição salva" },
      ),
    );
    expect(
      repository.getCachedBatch?.("batch-real")?.conversions[0].transactions[0]
        .description,
    ).toBe("Descrição salva");
  });

  it("limita os PDFs em memória e remove primeiro o menos recentemente usado", async () => {
    const payload = {
      ...batchPayload,
      jobs: Array.from({ length: 13 }, (_, i) => ({
        ...batchPayload.jobs[0],
        job_id: `job-${i}`,
      })),
    };
    let downloads = 0;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async (url) => {
      if (String(url).endsWith("/auth/me")) return jsonResponse({});
      if (String(url).endsWith("/document")) {
        downloads++;
        return new Response("%PDF-1.4");
      }
      return jsonResponse(payload);
    });
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    await repository.getBatch("batch-real");
    for (let i = 0; i < 13; i++) await repository.loadDocument!(`job-${i}`);
    await repository.loadDocument!("job-12");
    expect(downloads).toBe(13);
    await repository.loadDocument!("job-0");
    expect(downloads).toBe(14);
  });
});

it("preserva eventos especializados e escolhe Excel em vez de OFX bancário", async () => {
  const payload = structuredClone(batchPayload);
  const report = {
    document_type: "fiscal_summary",
    origin: "GDOOR",
    notice: "Emissão não é recebimento.",
    events: [],
    validations: [],
    export: {
      available_formats: ["csv", "xlsx"],
      requires_destination_account: false,
    },
  };
  Object.assign(payload.jobs[0].result!.analysis, {
    financial_document: report,
  });
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse({}))
    .mockResolvedValueOnce(jsonResponse(payload));
  const repository = createHttpWorkspaceRepository({
    apiUrl: "https://api.example.test",
    fetcher,
  });
  const batch = await repository.getBatch("batch-real");
  expect(batch.conversions[0].financialDocument).toEqual(report);
  expect(batch.conversions[0].downloadUrl).toBe(
    "https://api.example.test/convert-report/an_real?format=xlsx",
  );
});

it("preserva regiões de origem ao salvar sem enviá-las como campos editáveis", async () => {
  const payload = structuredClone(batchPayload);
  const region = { page: 1, left: 0.1, top: 0.4, width: 0.6, height: 0.02 };
  Object.assign(payload.jobs[0].result!.analysis.preview_transactions[0], {
    source_regions: [region, { ...region, width: -1 }],
  });
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse({}))
    .mockResolvedValueOnce(jsonResponse(payload))
    .mockResolvedValueOnce(jsonResponse({ updated_at: "saved" }));
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const batch = await repository.getBatch("batch-real");
  const rows = batch.conversions[0].transactions;
  expect(rows[0].sourceRegions).toEqual([region]);
  const saved = await repository.saveTransactions("job-ready", [
    { ...rows[0], description: "Editado" },
    ...rows.slice(1),
  ]);
  expect(saved?.[0].sourceRegions).toEqual([region]);
  expect(fetcher.mock.calls.at(-1)?.[1]?.body).not.toContain("source_regions");
  expect(fetcher.mock.calls.at(-1)?.[1]?.body).not.toContain("sourceRegions");
});

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

it("salva e limpa marcações separadamente da origem e confirma o eco da API", async () => {
  const region = { page: 1, left: 0.1, top: 0.5, width: 0.7, height: 0.03 };
  const markedRows =
    batchPayload.jobs[0].result!.analysis.preview_transactions.map(
      (row, index) => ({ ...row, review_regions: index ? [] : [region] }),
    );
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse({}))
    .mockResolvedValueOnce(jsonResponse(batchPayload))
    .mockResolvedValueOnce(
      jsonResponse({ updated_at: "marked", preview_transactions: markedRows }),
    )
    .mockResolvedValueOnce(
      jsonResponse({
        updated_at: "cleared",
        preview_transactions: markedRows.map((row) => ({
          ...row,
          review_regions: [],
        })),
      }),
    );
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const batch = await repository.getBatch("batch-real");
  const rows = batch.conversions[0].transactions;
  const marked = await repository.saveTransactions("job-ready", [
    { ...rows[0], reviewRegions: [region] },
    ...rows.slice(1),
  ]);
  const patch = JSON.parse(String(fetcher.mock.calls.at(-1)?.[1]?.body))
    .edits[0];
  expect(patch.action).toBe("annotate");
  expect(patch.review_regions).toEqual([region]);
  expect(patch).not.toHaveProperty("source_regions");
  expect(marked?.[0].reviewRegions).toEqual([region]);
  expect(marked?.[0].credit).toBe(rows[0].credit);
  const cleared = await repository.saveTransactions(
    "job-ready",
    marked!.map((row) => ({ ...row, reviewRegions: [] })),
  );
  expect(cleared?.[0].reviewRegions).toEqual([]);
  expect(
    JSON.parse(String(fetcher.mock.calls.at(-1)?.[1]?.body)).edits[0]
      .review_regions,
  ).toEqual([]);
});

it("não informa sucesso se uma API antiga descartar a marcação", async () => {
  const fetcher = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(jsonResponse({}))
    .mockResolvedValueOnce(jsonResponse(batchPayload))
    .mockResolvedValueOnce(jsonResponse({ updated_at: "old" }));
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const batch = await repository.getBatch("batch-real");
  const rows = batch.conversions[0].transactions;
  await expect(
    repository.saveTransactions("job-ready", [
      {
        ...rows[0],
        reviewRegions: [{ page: 1, left: 0, top: 0, width: 0.5, height: 0.1 }],
      },
      ...rows.slice(1),
    ]),
  ).rejects.toThrow(/confirmar as marcações/);
});

describe("HttpWorkspaceRepository", () => {
  it("preserva a indicação de extrato parcial recebida do worker", async () => {
    const payload = structuredClone(batchPayload);
    const analysis = {
      ...payload.jobs[0].result!.analysis,
      pdf_processing_metrics: { partial_statement: 1 },
      preview_transactions: [
        {
          ...payload.jobs[0].result!.analysis.preview_transactions[0],
          warning_types: ["manual_review_recommended", "partial_statement"],
        },
      ],
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(
        jsonResponse({
          ...payload,
          jobs: [{ ...payload.jobs[0], result: { analysis } }],
        }),
      );
    const batch = await createHttpWorkspaceRepository({
      apiUrl: "/",
      fetcher,
    }).getBatch("batch-real");
    expect(batch.conversions[0].partialStatement).toBe(true);
    expect(batch.conversions[0].transactions.length).toBeGreaterThan(0);
    expect(batch.conversions[0].status).toBe("NEEDS_REVIEW");
    expect(batch.conversions[0].suspectedCount).toBe(0);
    expect(
      Object.values(batch.conversions[0].transactions[0].fieldStates),
    ).not.toContain("SUSPECTED");
  });
  it("mantém alertas do lançamento e o estado revisado em extratos parciais", async () => {
    const payload = structuredClone(batchPayload);
    const job = payload.jobs[0];
    job.result!.analysis.preview_transactions[0].warning_types = [
      "manual_review_recommended",
      "partial_statement",
      "balance_consistency_failed",
    ];
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(
        jsonResponse({
          ...payload,
          jobs: [{ ...job, reviewed_at: "2026-10-24T12:00:00Z" }],
        }),
      );
    const batch = await createHttpWorkspaceRepository({
      apiUrl: "/",
      fetcher,
    }).getBatch("batch-real");
    expect(batch.conversions[0]).toMatchObject({
      partialStatement: true,
      status: "REVIEWED",
      suspectedCount: 2,
    });
  });
  it("mapeia a declaração de ausência de movimentação sem marcar falha", async () => {
    const payload = structuredClone(batchPayload);
    payload.jobs = [payload.jobs[0]];
    const analysis = {
      ...payload.jobs[0].result!.analysis,
      transactions_total: 0,
      preview_transactions: [],
      pdf_processing_metrics: { statement_no_activity: 1 },
    };
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(
        jsonResponse({
          ...payload,
          jobs: [{ ...payload.jobs[0], result: { analysis } }],
        }),
      );
    const batch = await createHttpWorkspaceRepository({
      apiUrl: "/",
      fetcher,
    }).getBatch("batch-real");
    expect(batch.conversions[0]).toMatchObject({
      status: "READY",
      noActivity: true,
      transactionCount: 0,
      transactions: [],
    });
    expect(batch.conversions[0].downloadUrl).toContain("format=ofx");
  });
  it("mantém excluída a linha nova removida antes do primeiro autosave", async () => {
    const rows = batchPayload.jobs[0].result!.analysis.preview_transactions;
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({}))
      .mockResolvedValueOnce(jsonResponse(batchPayload))
      .mockResolvedValueOnce(
        jsonResponse({
          updated_at: "saved",
          preview_transactions: [
            rows[0],
            { ...rows[0], is_deleted: true },
            rows[1],
          ],
        }),
      );
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    const original = batch.conversions[0].transactions;
    const saved = await repository.saveTransactions("job-ready", [
      ...original,
      { ...original[0], id: "new-deleted", isDeleted: true },
    ]);
    expect(saved).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: "new-deleted", isDeleted: true }),
      ]),
    );
    const payload = JSON.parse(String(fetcher.mock.calls[2][1]?.body));
    expect(
      payload.edits.map((edit: { action: string }) => edit.action),
    ).toEqual(["insert", "delete"]);
  });

  it("persiste exclusão e restauração com os mesmos IDs sem reinserir a linha", async () => {
    const original = structuredClone(batchPayload);
    const rows = original.jobs[0].result!.analysis.preview_transactions;
    const writes: Array<{ edits: Array<Record<string, unknown>> }> = [];
    const fetcher = vi
      .fn<typeof fetch>()
      .mockImplementation(async (url, init) => {
        if (String(url).endsWith("/auth/me")) return jsonResponse({});
        if (init?.method === "POST") {
          const payload = JSON.parse(String(init.body));
          writes.push(payload);
          for (const edit of payload.edits)
            rows[Number(edit.row_id.replace("row_", "")) - 1].is_deleted =
              edit.action === "delete";
          return jsonResponse({
            updated_at: `v${writes.length}`,
            preview_transactions: rows,
          });
        }
        return jsonResponse(original);
      });
    const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
    const batch = await repository.getBatch("batch-real");
    const [first, second] = batch.conversions[0].transactions;
    const deleted = await repository.saveTransactions("job-ready", [
      { ...first, isDeleted: true },
      second,
    ]);
    expect(deleted?.[0].isDeleted).toBe(true);
    expect(
      (await repository.getBatch("batch-real")).conversions[0].transactionCount,
    ).toBe(1);
    await repository.saveTransactions("job-ready", [first, second]);
    expect(writes.map((write) => write.edits[0].action)).toEqual([
      "delete",
      "restore",
    ]);
    expect(writes.every((write) => write.edits[0].row_id === "row_1")).toBe(
      true,
    );
    expect(
      (await repository.getBatch("batch-real")).conversions[0].transactionCount,
    ).toBe(2);
  });

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
    const [deleted, first, second] = batch.conversions[0].transactions;

    expect(deleted.isDeleted).toBe(true);
    expect(first.id).toBe("row_2");
    await repository.saveTransactions("job-ready", [
      deleted,
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
