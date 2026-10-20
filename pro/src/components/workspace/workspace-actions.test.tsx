import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import { useWorkspaceController } from "./use-workspace-controller";
import { WorkspaceScreen } from "./workspace-screen";

afterEach(() => vi.useRealTimers());

it("atualiza o processamento sem sobrescrever uma correção pendente", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  const original = await repository.getBatch("batch-setembro-2026");
  const remote = structuredClone(original);
  const waiting = original.conversions.find(
    (row) => row.status === "PROCESSING",
  )!;
  remote.conversions.find((row) => row.id === waiting.id)!.status = "FAILED";
  vi.spyOn(repository, "getBatch")
    .mockResolvedValueOnce(original)
    .mockResolvedValue(remote);
  const { result } = renderHook(() =>
    useWorkspaceController("batch-setembro-2026", repository),
  );
  await waitFor(() => expect(result.current.loadState).toBe("READY"));
  vi.useFakeTimers();
  const id = result.current.activeConversion!.transactions[0].id;
  act(() =>
    result.current.updateField(id, "description", "Correção durante o polling"),
  );
  await act(async () => {
    await result.current.refreshBatch();
  });
  expect(
    result.current.activeConversion!.transactions.find((row) => row.id === id)
      ?.description,
  ).toBe("Correção durante o polling");
  expect(
    result.current.batch!.conversions.find((row) => row.id === waiting.id)
      ?.status,
  ).toBe("FAILED");
  expect(result.current.saveStatus).toBe("DIRTY");
});

it("salva todos os documentos antes de exportar, inclusive o documento que saiu da tela", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  const order: string[] = [];
  const save = vi
    .spyOn(repository, "saveTransactions")
    .mockImplementation(async (id) => {
      order.push(id);
    });
  const exportBatch = vi
    .spyOn(repository, "exportBatch")
    .mockImplementation(async () => {
      order.push("export");
      return { fileName: "review.zip", conversionCount: 2 };
    });
  const { result } = renderHook(() =>
    useWorkspaceController("batch-setembro-2026", repository),
  );
  await waitFor(() => expect(result.current.loadState).toBe("READY"));
  vi.useFakeTimers();
  const first = result.current.activeConversion!;
  act(() =>
    result.current.updateField(
      first.transactions[0].id,
      "description",
      "Primeiro corrigido",
    ),
  );
  const second = result.current.batch!.conversions.find(
    (c) => c.id !== first.id && c.transactions.length,
  )!;
  act(() => result.current.selectConversion(second.id));
  act(() =>
    result.current.updateField(
      second.transactions[0].id,
      "description",
      "Segundo corrigido",
    ),
  );
  await act(async () => {
    await result.current.exportBatch();
  });
  expect(save).toHaveBeenCalledTimes(2);
  expect(new Set(order.slice(0, 2))).toEqual(new Set([first.id, second.id]));
  expect(order.at(-1)).toBe("export");
  expect(exportBatch).toHaveBeenCalledOnce();
});

it("aguarda o autosave em andamento e a edição mais recente antes do ZIP", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  let finish!: () => void;
  const save = vi
    .spyOn(repository, "saveTransactions")
    .mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    )
    .mockResolvedValue(undefined);
  const exportBatch = vi
    .spyOn(repository, "exportBatch")
    .mockResolvedValue({ fileName: "review.zip", conversionCount: 1 });
  const { result } = renderHook(() =>
    useWorkspaceController("batch-setembro-2026", repository),
  );
  await waitFor(() => expect(result.current.loadState).toBe("READY"));
  vi.useFakeTimers();
  const id = result.current.activeConversion!.transactions[0].id;
  act(() => result.current.updateField(id, "description", "Primeira edição"));
  await act(async () => vi.advanceTimersByTimeAsync(701));
  act(() => result.current.updateField(id, "description", "Última edição"));
  let exporting!: Promise<void>;
  act(() => {
    exporting = result.current.exportBatch();
  });
  expect(exportBatch).not.toHaveBeenCalled();
  await act(async () => {
    finish();
    await exporting;
  });
  expect(save).toHaveBeenCalledTimes(2);
  expect(save.mock.calls[1][1].find((row) => row.id === id)?.description).toBe(
    "Última edição",
  );
  expect(exportBatch).toHaveBeenCalledOnce();
});

it("não exporta quando uma gravação falha e conserva a correção para nova tentativa", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  vi.spyOn(repository, "saveTransactions").mockRejectedValue(
    new Error("Rede indisponível"),
  );
  const exportBatch = vi
    .spyOn(repository, "exportBatch")
    .mockResolvedValue({ fileName: "review.zip", conversionCount: 1 });
  const { result } = renderHook(() =>
    useWorkspaceController("batch-setembro-2026", repository),
  );
  await waitFor(() => expect(result.current.loadState).toBe("READY"));
  vi.useFakeTimers();
  const id = result.current.activeConversion!.transactions[0].id;
  act(() =>
    result.current.updateField(id, "description", "Correção preservada"),
  );
  await act(async () => {
    await result.current.exportBatch();
  });
  expect(exportBatch).not.toHaveBeenCalled();
  expect(result.current.exportState).toBe("ERROR");
  expect(
    result.current.activeConversion!.transactions.find((row) => row.id === id)
      ?.description,
  ).toBe("Correção preservada");
});

it("mostra a falha de exportação e permite repetir sem recarregar o workspace", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  const exportBatch = vi
    .spyOn(repository, "exportBatch")
    .mockRejectedValueOnce(new Error("Não foi possível gerar o ZIP."))
    .mockResolvedValue({ fileName: "review.zip", conversionCount: 1 });
  render(
    <WorkspaceScreen batchId="batch-setembro-2026" repository={repository} />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Exportar lote" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Não foi possível gerar o ZIP.",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Tentar exportar novamente" }),
  );
  expect(await screen.findByText("Exportação concluída")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Fechar aviso" }));
  expect(screen.queryByText("Exportação concluída")).not.toBeInTheDocument();
  expect(exportBatch).toHaveBeenCalledTimes(2);
});

it("mostra falhas ao marcar revisado e permite repetir a ação", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  vi.spyOn(repository, "markReviewed")
    .mockRejectedValueOnce(new Error("Revisão indisponível"))
    .mockResolvedValue(undefined);
  render(
    <WorkspaceScreen batchId="batch-setembro-2026" repository={repository} />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Marcar revisado" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Revisão indisponível",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Tentar ação novamente" }),
  );
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
});
