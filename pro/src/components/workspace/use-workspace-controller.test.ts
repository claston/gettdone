import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import { useWorkspaceController } from "./use-workspace-controller";

afterEach(() => vi.useRealTimers());

describe("edição e autosave", () => {
  it("mantém a edição mais recente após uma falha e a envia na nova tentativa", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    let rejectSave: ((reason: Error) => void) | undefined;
    const save = vi
      .spyOn(repository, "saveTransactions")
      .mockImplementationOnce(
        () =>
          new Promise<void>((_resolve, reject) => {
            rejectSave = reject;
          }),
      )
      .mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useWorkspaceController("batch-setembro-2026", repository),
    );
    await waitFor(() => expect(result.current.loadState).toBe("READY"));
    vi.useFakeTimers();
    const id = result.current.activeConversion!.transactions[0].id;
    act(() => result.current.updateField(id, "description", "Primeira edição"));
    await act(async () => vi.advanceTimersByTimeAsync(701));
    act(() =>
      result.current.updateField(id, "description", "Edição preservada"),
    );
    await act(async () => rejectSave!(new Error("Falha de rede")));
    expect(result.current.saveStatus).toBe("ERROR");
    expect(
      result.current.activeConversion!.transactions.find((row) => row.id === id)
        ?.description,
    ).toBe("Edição preservada");
    await act(async () => result.current.retrySave());
    expect(
      save.mock.calls[1][1].find((row) => row.id === id)?.description,
    ).toBe("Edição preservada");
    expect(result.current.saveStatus).toBe("SAVED");
  });
  it("ignora foco/blur e mudanças apenas na formatação monetária", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const save = vi.spyOn(repository, "saveTransactions");
    const { result } = renderHook(() =>
      useWorkspaceController("batch-setembro-2026", repository),
    );
    await waitFor(() => expect(result.current.loadState).toBe("READY"));
    const row = result.current.activeConversion!.transactions.find(
      (item) => item.credit !== null,
    )!;
    act(() =>
      result.current.updateField(
        row.id,
        "credit",
        row.credit!.toLocaleString("pt-BR", { minimumFractionDigits: 2 }),
      ),
    );
    expect(result.current.canUndo).toBe(false);
    expect(result.current.saveStatus).toBe("IDLE");
    expect(save).not.toHaveBeenCalled();
  });

  it("serializa gravações e mantém pendentes as edições feitas durante uma requisição", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    let finish: (() => void) | undefined;
    const save = vi
      .spyOn(repository, "saveTransactions")
      .mockImplementationOnce(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useWorkspaceController("batch-setembro-2026", repository),
    );
    await waitFor(() => expect(result.current.loadState).toBe("READY"));
    vi.useFakeTimers();
    const id = result.current.activeConversion!.transactions[0].id;
    act(() => result.current.updateField(id, "description", "Primeira edição"));
    await act(async () => vi.advanceTimersByTimeAsync(701));
    act(() =>
      result.current.updateField(id, "description", "Edição mais recente"),
    );
    await act(async () => vi.advanceTimersByTimeAsync(701));
    expect(save).toHaveBeenCalledTimes(1);
    await act(async () => finish!());
    expect(save).toHaveBeenCalledTimes(2);
    expect(
      save.mock.calls[1][1].find((row) => row.id === id)?.description,
    ).toBe("Edição mais recente");
    expect(result.current.saveStatus).toBe("SAVED");
  });
});
