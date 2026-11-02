import { expect, it, vi } from "vitest";
import { createHttpWorkspaceRepository } from "./http-workspace-repository";

const id = "550e8400-e29b-41d4-a716-446655440000";
const row = {
  transaction_id: "stable",
  date: "2026-04-01",
  description: "Original",
  amount: 10,
};
const analysis = {
  authority: "postgres_v1",
  version: 1,
  updated_at: "first",
  movement_ids: { stable: id },
  movement_versions: { stable: 1 },
  preview_transactions: [row],
};
const batch = {
  batch_id: "batch",
  jobs: [
    {
      job_id: "job",
      status: "completed",
      filename: "file.csv",
      analysis_id: "analysis",
      result: { analysis },
    },
  ],
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });

it("recovers a client document from a later page through its source batch", async () => {
  let saved = false;
  const latest = {
    ...analysis,
    version: 2,
    movement_versions: { stable: 2 },
    preview_transactions: [{ ...row, description: "Outra sessão" }],
  };
  const sourceJob = { ...batch.jobs[0], source_batch_id: "source-batch" };
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    const path = String(url);
    if (path.includes("convert-edits")) {
      const body = JSON.parse(String(init?.body));
      if (body.expected_version === 1) return json({}, 409);
      expect(body.batch_id).toBe("source-batch");
      expect(body.expected_version).toBe(2);
      saved = true;
      return json({ ...latest, version: 3, operation_id: body.operation_id });
    }
    if (path.includes("/conversion-batches/source-batch"))
      return json({
        ...batch,
        jobs: [{ ...sourceJob, result: { analysis: latest } }],
      });
    if (path.includes("cursor=second"))
      return json({ ...batch, jobs: [sourceJob] });
    if (path.includes("/workspace"))
      return json({ ...batch, jobs: [], next_cursor: "second" });
    return json({});
  });
  const repository = createHttpWorkspaceRepository({
    apiUrl: "/",
    fetcher,
    clientId: "client",
  });
  const first = (await repository.getBatch("client")).conversions[0];
  const draft = [{ ...first.transactions[0], description: "Rascunho" }];
  await expect(repository.saveTransactions("job", draft)).rejects.toThrow(
    "outra sessão",
  );
  const comparison = await repository.prepareConflict!("job");
  expect(comparison.baseline[0].description).toBe("Original");
  expect(comparison.latest[0].description).toBe("Outra sessão");
  expect(draft[0].description).toBe("Rascunho");
  repository.acceptConflict!("job");
  await repository.saveTransactions("job", draft);
  expect(saved).toBe(true);
});

it("unlocks a reverted local edit so refresh adopts remote rows and download version", async () => {
  let current = analysis;
  const fetcher = vi.fn<typeof fetch>(async (url) => {
    expect(String(url)).not.toContain("convert-edits");
    return json({
      ...batch,
      jobs: [{ ...batch.jobs[0], result: { analysis: current } }],
    });
  });
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const first = (await repository.getBatch("batch")).conversions[0];
  repository.lockSnapshot!("job");
  await repository.saveTransactions("job", first.transactions);
  current = {
    ...analysis,
    version: 2,
    preview_transactions: [{ ...row, description: "Remoto" }],
  };
  const refreshed = (await repository.getBatch("batch")).conversions[0];
  expect(refreshed.transactions[0].description).toBe("Remoto");
  expect(refreshed.downloadUrl).toContain("expected_version=2");
});

it("uses movement UUID and durable versions even without PDF source sequence, then refreshes after save", async () => {
  let saved = analysis;
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).includes("convert-edits")) {
      const body = JSON.parse(String(init?.body));
      expect(body.expected_version).toBe(1);
      expect(body.expected_movement_versions).toEqual({ [id]: 1 });
      expect(body.edits[0].movement_id).toBe(id);
      expect(body.edits[0].row_id).toBeUndefined();
      saved = {
        ...analysis,
        version: 2,
        updated_at: "second",
        movement_versions: { stable: 2 },
        preview_transactions: [{ ...row, description: "Corrigido" }],
      };
      return json({ ...saved, operation_id: body.operation_id });
    }
    if (String(url).includes("conversion-batches"))
      return json({
        ...batch,
        jobs: [{ ...batch.jobs[0], result: { analysis: saved } }],
      });
    return json({});
  });
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const first = (await repository.getBatch("batch")).conversions[0];
  await repository.saveTransactions("job", [
    { ...first.transactions[0], description: "Corrigido" },
  ]);
  const reopened = await createHttpWorkspaceRepository({
    apiUrl: "/",
    fetcher,
  }).getBatch("batch");
  expect(reopened.conversions[0].transactions[0].description).toBe("Corrigido");
  expect(reopened.conversions[0].downloadUrl).toContain("expected_version=2");
});

it("preserves a conflicted draft and requires explicit comparison before retrying", async () => {
  const fetcher = vi.fn<typeof fetch>(async (url) =>
    String(url).includes("convert-edits")
      ? json({ detail: "conflict" }, 409)
      : String(url).includes("conversion-batches")
        ? json(batch)
        : json({}),
  );
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  const first = (await repository.getBatch("batch")).conversions[0];
  const draft = [{ ...first.transactions[0], description: "Rascunho" }];
  await expect(repository.saveTransactions("job", draft)).rejects.toThrow(
    "outra sessão",
  );
  await expect(repository.saveTransactions("job", draft)).rejects.toThrow(
    "Compare",
  );
  const comparison = await repository.prepareConflict!("job");
  expect(comparison.latest[0].description).toBe("Original");
  expect(draft[0].description).toBe("Rascunho");
  repository.acceptConflict!("job");
  await expect(repository.saveTransactions("job", draft)).rejects.toThrow(
    "outra sessão",
  );
});
