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
