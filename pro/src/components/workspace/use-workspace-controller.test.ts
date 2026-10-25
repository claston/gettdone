import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import { useWorkspaceController } from "./use-workspace-controller";

afterEach(() => vi.useRealTimers());

describe("edição e autosave", () => {
  it("mantém a revisão documental necessária ao editar um arquivo já revisado", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const batch = await repository.getBatch("batch-setembro-2026");
    repository.getBatch = vi.fn().mockResolvedValue(batch);
    const conversion = batch.conversions[0];
    conversion.status = "REVIEWED";
    conversion.reviewWarnings = ["printed_summary_balance_mismatch"];
    conversion.transactions = conversion.transactions.map((row) => ({
      ...row,
      fieldStates: {
        date: "ORIGINAL",
        description: "ORIGINAL",
        credit: "ORIGINAL",
        debit: "ORIGINAL",
        balance: "ORIGINAL",
      },
    }));
    const { result } = renderHook(() =>
      useWorkspaceController(batch.id, repository),
    );
    await waitFor(() => expect(result.current.loadState).toBe("READY"));
    act(() => result.current.selectConversion(conversion.id));
    act(() =>
      result.current.updateField(
        conversion.transactions[0].id,
        "description",
        "Descrição revisada",
      ),
    );
    expect(result.current.activeConversion?.status).toBe("NEEDS_REVIEW");
  });
  it("reabre o lote da memória enquanto atualiza sem apagar edição ou seleção recentes", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const batch = await repository.getBatch("batch-setembro-2026");
    let finish!: (value: typeof batch) => void;
    repository.getCachedBatch = () => batch;
    repository.getBatch = vi.fn(
      () =>
        new Promise<typeof batch>((resolve) => {
          finish = resolve;
        }),
    );
    const { result } = renderHook(() =>
      useWorkspaceController(batch.id, repository),
    );
    expect(result.current.loadState).toBe("READY");
    const row = result.current.activeConversion!.transactions[0];
    const conversionId = result.current.activeConversion!.id;
    act(() =>
      result.current.updateField(
        row.id,
        "description",
        "Edição durante atualização",
      ),
    );
    const other = batch.conversions.find((item) => item.id !== conversionId)!;
    act(() => result.current.selectConversion(other.id));
    await act(async () => finish(batch));
    expect(result.current.activeConversion!.id).toBe(other.id);
    expect(
      result.current.batch!.conversions.find(
        (item) => item.id === conversionId,
      )!.transactions[0].description,
    ).toBe("Edição durante atualização");
  });

  it("mantém o lote já aberto disponível quando a atualização demora ou falha", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const batch = await repository.getBatch("batch-setembro-2026");
    repository.getCachedBatch = () => batch;
    repository.getBatch = vi
      .fn()
      .mockRejectedValue(new Error("Servidor temporariamente indisponível"));
    const { result } = renderHook(() =>
      useWorkspaceController(batch.id, repository),
    );
    await waitFor(() => expect(result.current.refreshState).toBe("ERROR"));
    expect(result.current.loadState).toBe("READY");
    expect(result.current.activeConversion).not.toBeNull();
  });
  it("salva e desfaz marcações sem alterar campos financeiros", async () => {
    const repository = createMockWorkspaceRepository({ latencyMs: 0 });
    const save = vi
      .spyOn(repository, "saveTransactions")
      .mockResolvedValue(undefined);
    const { result } = renderHook(() =>
      useWorkspaceController("batch-setembro-2026", repository),
    );
    await waitFor(() => expect(result.current.loadState).toBe("READY"));
    const row = structuredClone(
      result.current.activeConversion!.transactions[0],
    );
    vi.useFakeTimers();
    const region = { page: 1, left: 0.1, top: 0.2, width: 0.7, height: 0.03 };
    act(() => {
      expect(result.current.setReviewRegions(row.id, [region])).toBeNull();
    });
    expect(result.current.activeConversion!.transactions[0]).toEqual({
      ...row,
      reviewRegions: [region],
    });
    await act(async () => vi.advanceTimersByTimeAsync(701));
    expect(save).toHaveBeenCalledTimes(1);
    expect(save.mock.calls[0][1][0].reviewRegions).toEqual([region]);
    act(() => result.current.undo());
    expect(
      result.current.activeConversion!.transactions.find(
        (item) => item.id === row.id,
      )?.reviewRegions ?? [],
    ).toEqual([]);
    await act(async () => vi.advanceTimersByTimeAsync(701));
    expect(result.current.saveStatus).toBe("SAVED");
  });
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
