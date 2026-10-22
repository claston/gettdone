import type { Batch, WorkspaceRepository } from "@/domain/workspace";

export const WORKSPACE_READ_TIMEOUT_MS = 30_000;

export function loadWorkspaceBatch(
  repository: WorkspaceRepository,
  batchId: string,
  signal?: AbortSignal,
) {
  const abort = new AbortController();
  const cancel = () => abort.abort(signal?.reason);
  let rejectAborted!: () => void;
  const cancelled = new Promise<never>((_resolve, reject) => {
    rejectAborted = () =>
      reject(abort.signal.reason ?? new DOMException("Aborted", "AbortError"));
  });
  abort.signal.addEventListener("abort", rejectAborted, { once: true });
  const timer = setTimeout(() => {
    const error = new Error(
      "A consulta do lote demorou demais. Suas edições foram mantidas; tente novamente.",
    );
    error.name = "TimeoutError";
    abort.abort(error);
  }, WORKSPACE_READ_TIMEOUT_MS);
  signal?.addEventListener("abort", cancel, { once: true });
  if (signal?.aborted) cancel();

  // Racing also bounds adapters that ignore AbortSignal. Their late result can
  // never update the controller; the HTTP adapter guards its own cache as well.
  let read: Promise<Batch>;
  try {
    if (abort.signal.aborted) throw abort.signal.reason;
    read = repository.getBatch(batchId, abort.signal);
  } catch (error) {
    read = Promise.reject(error);
  }
  return Promise.race([read, cancelled]).finally(() => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
    abort.signal.removeEventListener("abort", rejectAborted);
  });
}
