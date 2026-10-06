import { describe, expect, it } from "vitest";

import { createMockWorkspaceRepository } from "./mock-workspace-repository";

describe("MockWorkspaceRepository", () => {
  it("persiste alterações por conversão sem acoplar a UI ao formato do backend", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const batch = await repository.getBatch("batch-setembro-2026");
    const conversion = batch.conversions.find(
      (item) => item.transactions.length > 0,
    )!;
    const transaction = {
      ...conversion.transactions[0],
      description: "Descrição corrigida",
    };

    await repository.saveTransactions(conversion.id, [transaction]);
    const refreshed = await repository.getBatch(batch.id);

    expect(
      refreshed.conversions.find((item) => item.id === conversion.id)
        ?.transactions[0].description,
    ).toBe("Descrição corrigida");
  });

  it("permite simular uma falha e tentar novamente", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const batch = await repository.getBatch("batch-setembro-2026");
    const conversion = batch.conversions.find(
      (item) => item.transactions.length > 0,
    )!;

    repository.failNextSave();
    await expect(
      repository.saveTransactions(conversion.id, conversion.transactions),
    ).rejects.toThrow("Falha simulada");
    await expect(
      repository.saveTransactions(conversion.id, conversion.transactions),
    ).resolves.toBeUndefined();
  });
});
