import { expect, it, vi } from "vitest";
import { createHttpWorkspaceRepository } from "./http-workspace-repository";

it("envia somente o nome permitido do evento e a rota fixa, sem conteúdo financeiro", async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValue(new Response(null, { status: 202 }));
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  repository.recordEvent?.("transaction_edited");
  await vi.waitFor(() =>
    expect(
      fetcher.mock.calls.some(([path]) => path === "/telemetry/events"),
    ).toBe(true),
  );
  const [, init] = fetcher.mock.calls.find(
    ([path]) => path === "/telemetry/events",
  )!;
  expect(JSON.parse(init.body)).toEqual({
    event_type: "transaction_edited",
    page_path: "/review",
  });
  expect(init.credentials).toBe("include");
});

it("falhas de telemetria não geram rejeição sem tratamento", async () => {
  const fetcher = vi.fn().mockRejectedValue(new Error("offline"));
  const repository = createHttpWorkspaceRepository({ apiUrl: "/", fetcher });
  expect(() => repository.recordEvent?.("workspace_opened")).not.toThrow();
  await new Promise((resolve) => setTimeout(resolve, 10));
});
