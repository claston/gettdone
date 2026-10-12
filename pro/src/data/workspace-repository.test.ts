import { describe, expect, it, vi } from "vitest";

import { createMockWorkspaceRepository } from "./mock-workspace-repository";
import { resolveWorkspaceRepository } from "./workspace-repository";

describe("resolveWorkspaceRepository", () => {
  it("força o repository de demonstração sem acessar o repository configurado", async () => {
    const configuredRepository = createMockWorkspaceRepository({
      latencyMs: 0,
    });
    const configuredGetBatch = vi.spyOn(configuredRepository, "getBatch");

    const repository = resolveWorkspaceRepository("demo", configuredRepository);
    const batch = await repository.getBatch("batch-setembro-2026");

    expect(batch.id).toBe("batch-setembro-2026");
    expect(configuredGetBatch).not.toHaveBeenCalled();
  });

  it("mantém o repository configurado no modo API", () => {
    const configuredRepository = createMockWorkspaceRepository({
      latencyMs: 0,
    });

    expect(resolveWorkspaceRepository("api", configuredRepository)).toBe(
      configuredRepository,
    );
  });
});
