import { act, renderHook } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createMockWorkspaceRepository } from "@/data/mock-workspace-repository";
import type { Batch } from "@/domain/workspace";
import { useWorkspaceController } from "./use-workspace-controller";

afterEach(() => vi.useRealTimers());

it("libera o retry após timeout do polling e descarta a resposta antiga sem perder edição ou seleção", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  const original = await repository.getBatch("batch-setembro-2026");
  const remote = structuredClone(original);
  remote.conversions = remote.conversions.map((item) => ({
    ...item,
    status: "READY",
  }));
  let finish!: (batch: Batch) => void;
  let signal: AbortSignal | undefined;
  const read = vi
    .fn()
    .mockResolvedValueOnce(original)
    .mockImplementationOnce((_id: string, nextSignal?: AbortSignal) => {
      signal = nextSignal;
      return new Promise<Batch>((resolve) => {
        finish = resolve;
      });
    })
    .mockResolvedValue(remote);
  repository.getBatch = read;
  repository.saveTransactions = vi.fn(() => new Promise<void>(() => {}));
  vi.useFakeTimers();
  const { result, unmount } = renderHook(() =>
    useWorkspaceController(original.id, repository),
  );
  await act(async () => {
    await Promise.resolve();
  });
  expect(result.current.loadState).toBe("READY");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2501);
  });
  expect(read).toHaveBeenCalledTimes(2);
  const conversion = result.current.activeConversion!;
  act(() => {
    result.current.updateField(
      conversion.transactions[0].id,
      "description",
      "Edição preservada no timeout",
    );
  });
  const other = original.conversions.find((item) => item.id !== conversion.id)!;
  act(() => result.current.selectConversion(other.id));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_001);
  });
  expect(result.current.refreshState).toBe("ERROR");
  expect(result.current.refreshError).toMatch(/demorou.*tente novamente/i);
  expect(signal?.aborted).toBe(true);
  expect(result.current.activeConversion!.id).toBe(other.id);
  expect(
    result.current.batch!.conversions.find((item) => item.id === conversion.id)!
      .transactions[0].description,
  ).toBe("Edição preservada no timeout");
  await act(async () => {
    await result.current.refreshBatch();
  });
  expect(read).toHaveBeenCalledTimes(3);
  expect(result.current.refreshState).toBe("IDLE");
  expect(
    result.current.batch!.conversions.every(
      (item) => item.status !== "PROCESSING",
    ),
  ).toBe(true);
  await act(async () => {
    finish(original);
  });
  expect(
    result.current.batch!.conversions.every(
      (item) => item.status !== "PROCESSING",
    ),
  ).toBe(true);
  expect(
    result.current.batch!.conversions.find((item) => item.id === conversion.id)!
      .transactions[0].description,
  ).toBe("Edição preservada no timeout");
  unmount();
});

it("cancela a consulta inicial ao desmontar e permite recarregar após timeout de abertura", async () => {
  const repository = createMockWorkspaceRepository({ latencyMs: 0 });
  const batch = await repository.getBatch("batch-setembro-2026");
  let signal: AbortSignal | undefined;
  repository.getBatch = vi
    .fn()
    .mockImplementationOnce((_id: string, nextSignal?: AbortSignal) => {
      signal = nextSignal;
      return new Promise<Batch>(() => {});
    })
    .mockResolvedValue(batch);
  vi.useFakeTimers();
  const { result, unmount } = renderHook(() =>
    useWorkspaceController(batch.id, repository),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30_001);
  });
  expect(result.current.loadState).toBe("ERROR");
  expect(signal?.aborted).toBe(true);
  act(() => result.current.retryLoad());
  await act(async () => {
    await Promise.resolve();
  });
  expect(result.current.loadState).toBe("READY");
  unmount();

  repository.getBatch = vi.fn((_id: string, nextSignal?: AbortSignal) => {
    signal = nextSignal;
    return new Promise<Batch>(() => {});
  });
  const second = renderHook(() => useWorkspaceController(batch.id, repository));
  await act(async () => {
    await Promise.resolve();
  });
  second.unmount();
  expect(signal?.aborted).toBe(true);
});
