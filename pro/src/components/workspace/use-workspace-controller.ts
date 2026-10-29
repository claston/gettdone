"use client";
import { requiresDocumentReview } from "@/domain/review-warnings";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { validSourceRegions } from "@/domain/source-regions";
import { emitWorkspaceEvent } from "@/domain/workspace-events";
import { loadWorkspaceBatch } from "@/data/load-workspace-batch";

import {
  countEdited,
  countSuspected,
  nextPendingConversion,
  parseBrazilianCurrency,
  sortConversionsForReview,
  orderTransactionsForReview,
  validateTransactionField,
} from "@/domain/workspace";
import type {
  Batch,
  Conversion,
  ExportResult,
  SaveStatus,
  Transaction,
  TransactionField,
  TransactionDraft,
  WorkspaceRepository,
  SourceRegion,
} from "@/domain/workspace";

type LoadState = "LOADING" | "READY" | "ERROR";
type RefreshState = "IDLE" | "REFRESHING" | "ERROR";

export interface WorkspaceController {
  batch: Batch | null;
  activeConversion: Conversion | null;
  loadState: LoadState;
  loadError: string | null;
  refreshState: RefreshState;
  refreshError: string | null;
  saveStatus: SaveStatus;
  saveError: string | null;
  canUndo: boolean;
  exportState: "IDLE" | "EXPORTING" | "DONE" | "ERROR";
  exportResult: ExportResult | null;
  exportError: string | null;
  actionError: string | null;
  actionBusy: boolean;
  retryLoad(): void;
  retryAction(): void;
  dismissExport(): void;
  selectConversion(id: string): void;
  updateField(
    transactionId: string,
    field: TransactionField,
    value: string,
  ): string | null;
  addTransaction(draft: TransactionDraft): void;
  setReviewRegions(
    transactionId: string,
    regions: SourceRegion[],
  ): string | null;
  removeTransaction(transactionId: string): void;
  restoreTransaction(transactionId: string): void;
  retrySave(): void;
  undo(): void;
  markReviewed(): Promise<void>;
  goToNextPending(): void;
  retryConversion(): Promise<void>;
  exportBatch(): Promise<void>;
  refreshBatch(): Promise<void>;
}

const emptyStates: Transaction["fieldStates"] = {
  date: "EDITED",
  description: "EDITED",
  credit: "EDITED",
  debit: "EDITED",
  balance: "EDITED",
};

export function useWorkspaceController(
  batchId: string,
  repository: WorkspaceRepository,
  initialConversionId?: string,
  reviewBasePath = "/review",
  pollIntervalMs = 2500,
): WorkspaceController {
  const [batch, setBatch] = useState<Batch | null>(null);
  const [activeId, setActiveId] = useState(initialConversionId ?? "");
  const [loadState, setLoadState] = useState<LoadState>("LOADING");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [refreshState, setRefreshState] = useState<RefreshState>("IDLE");
  const [refreshError, setRefreshError] = useState<string | null>(null);
  const [saveStatuses, setSaveStatuses] = useState<Record<string, SaveStatus>>(
    {},
  );
  const [saveErrors, setSaveErrors] = useState<Record<string, string | null>>(
    {},
  );
  const [history, setHistory] = useState<Record<string, Transaction[][]>>({});
  const [exportState, setExportState] = useState<
    "IDLE" | "EXPORTING" | "DONE" | "ERROR"
  >("IDLE");
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const actionLock = useRef(false);
  const lastAction = useRef<"review" | "retry">("review");
  const exportPromise = useRef<Promise<void> | null>(null);
  const openedBatch = useRef<string | null>(null);
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const batchRef = useRef<Batch | null>(null);
  const saveStatusesRef = useRef<Record<string, SaveStatus>>({});
  const refreshPromiseRef = useRef<Promise<void> | null>(null);
  const refreshAbortRef = useRef<AbortController | null>(null);
  const pending = useRef(new Map<string, Transaction[]>());
  const saving = useRef(new Map<string, Promise<boolean>>());

  const setStatus = useCallback((id: string, status: SaveStatus) => {
    saveStatusesRef.current = { ...saveStatusesRef.current, [id]: status };
    setSaveStatuses(saveStatusesRef.current);
  }, []);

  const changeRows = useCallback((id: string, transactions: Transaction[]) => {
    const current = batchRef.current;
    if (!current) return;
    const next: Batch = {
      ...current,
      conversions: current.conversions.map((item) =>
        item.id === id
          ? {
              ...item,
              transactions,
              transactionCount: transactions.filter((row) => !row.isDeleted)
                .length,
              editedCount: countEdited(transactions),
              suspectedCount: countSuspected(transactions),
              reviewStatus: "IN_REVIEW",
              status:
                item.status === "REVIEWED" || item.status === "EXPORTED"
                  ? countSuspected(transactions) || requiresDocumentReview(item)
                    ? "NEEDS_REVIEW"
                    : "READY"
                  : item.status,
            }
          : item,
      ),
    };
    batchRef.current = next;
    setBatch(next);
  }, []);

  useEffect(() => {
    let active = true;
    const abort = new AbortController();
    const activeTimers = timers.current;
    const cached = repository.getCachedBatch?.(batchId);
    const showBatch = (loadedBatch: Batch) => {
      const current = batchRef.current;
      const merged =
        current?.id === batchId
          ? mergeRefreshedBatch(current, loadedBatch, saveStatusesRef.current)
          : loadedBatch;
      setBatch(merged);
      batchRef.current = merged;
      const initial =
        loadedBatch.conversions.find(
          (item) => item.id === initialConversionId,
        ) ?? sortConversionsForReview(loadedBatch.conversions)[0];
      setActiveId((id) =>
        loadedBatch.conversions.some((item) => item.id === id)
          ? id
          : (initial?.id ?? ""),
      );
      setLoadState("READY");
      if (openedBatch.current !== batchId) {
        openedBatch.current = batchId;
        emitWorkspaceEvent(repository, "workspace_opened");
        if (initial) emitWorkspaceEvent(repository, "conversion_selected");
      }
    };
    setLoadError(null);
    setRefreshError(null);
    if (cached) {
      showBatch(cached);
      setRefreshState("REFRESHING");
    } else setLoadState("LOADING");
    loadWorkspaceBatch(repository, batchId, abort.signal)
      .then((loadedBatch) => {
        if (!active) return;
        showBatch(loadedBatch);
        setRefreshState("IDLE");
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (cached) {
          setRefreshError(
            error instanceof Error
              ? error.message
              : "Não foi possível atualizar o lote.",
          );
          setRefreshState("ERROR");
          return;
        }
        setLoadError(
          error instanceof Error
            ? error.message
            : "Não foi possível abrir o lote.",
        );
        setLoadState("ERROR");
      });
    return () => {
      active = false;
      abort.abort();
      Object.values(activeTimers).forEach(clearTimeout);
    };
  }, [batchId, initialConversionId, repository, loadAttempt]);

  useEffect(() => {
    batchRef.current = batch;
  }, [batch]);

  useEffect(() => {
    saveStatusesRef.current = saveStatuses;
  }, [saveStatuses]);

  useEffect(() => {
    const preventLoss = (event: BeforeUnloadEvent) => {
      if (
        Object.values(saveStatusesRef.current).some((status) =>
          ["DIRTY", "SAVING", "ERROR"].includes(status),
        )
      ) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", preventLoss);
    return () => window.removeEventListener("beforeunload", preventLoss);
  }, []);

  const refreshBatch = useCallback((): Promise<void> => {
    if (refreshPromiseRef.current) return refreshPromiseRef.current;

    setRefreshState("REFRESHING");
    setRefreshError(null);
    const abort = new AbortController();
    refreshAbortRef.current = abort;
    const refreshPromise = loadWorkspaceBatch(repository, batchId, abort.signal)
      .then((loadedBatch) => {
        if (abort.signal.aborted) return;
        setBatch((current) => {
          const merged = mergeRefreshedBatch(
            current,
            loadedBatch,
            saveStatusesRef.current,
          );
          batchRef.current = merged;
          return merged;
        });
        setActiveId((current) => {
          if (loadedBatch.conversions.some((item) => item.id === current)) {
            return current;
          }
          return sortConversionsForReview(loadedBatch.conversions)[0]?.id ?? "";
        });
        setRefreshState("IDLE");
      })
      .catch((error: unknown) => {
        if (abort.signal.aborted) return;
        setRefreshError(
          error instanceof Error
            ? error.message
            : "Não foi possível atualizar o lote.",
        );
        setRefreshState("ERROR");
      })
      .finally(() => {
        if (refreshAbortRef.current === abort) {
          refreshAbortRef.current = null;
          refreshPromiseRef.current = null;
        }
      });
    refreshPromiseRef.current = refreshPromise;
    return refreshPromise;
  }, [batchId, repository]);

  useEffect(
    () => () => {
      refreshAbortRef.current?.abort();
      refreshAbortRef.current = null;
      refreshPromiseRef.current = null;
    },
    [batchId, repository],
  );

  const hasProcessing = Boolean(
    batch?.conversions.some((item) => item.status === "PROCESSING"),
  );

  useEffect(() => {
    const expirations = (batch?.conversions ?? [])
      .flatMap((item) => (item.expiresAt ? [Date.parse(item.expiresAt)] : []))
      .filter(Number.isFinite);
    if (!expirations.length) return;
    const expire = () =>
      setBatch((current) =>
        current
          ? {
              ...current,
              conversions: current.conversions.filter(
                (item) =>
                  !item.expiresAt || Date.parse(item.expiresAt) > Date.now(),
              ),
            }
          : current,
      );
    const timer = setTimeout(
      expire,
      Math.min(
        2_147_483_647,
        Math.max(0, Math.min(...expirations) - Date.now()),
      ),
    );
    return () => clearTimeout(timer);
  }, [batch]);

  useEffect(() => {
    if (!hasProcessing) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const schedule = () => {
      timer = setTimeout(() => {
        void refreshBatch().then(() => {
          const stillProcessing = batchRef.current?.conversions.some(
            (item) => item.status === "PROCESSING",
          );
          if (active && stillProcessing) schedule();
        });
      }, pollIntervalMs);
    };

    schedule();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [hasProcessing, pollIntervalMs, refreshBatch]);

  const activeConversion =
    batch?.conversions.find((item) => item.id === activeId) ?? null;

  const persist = useCallback(
    (conversionId: string): Promise<boolean> => {
      const inFlight = saving.current.get(conversionId);
      if (inFlight) return inFlight;
      clearTimeout(timers.current[conversionId]);
      setStatus(conversionId, "SAVING");
      setSaveErrors((current) => ({ ...current, [conversionId]: null }));
      const promise = (async () => {
        try {
          while (pending.current.has(conversionId)) {
            const transactions = pending.current.get(conversionId)!;
            pending.current.delete(conversionId);
            let saved: Transaction[] | void;
            try {
              saved = await repository.saveTransactions(
                conversionId,
                transactions,
              );
            } catch (error) {
              if (!pending.current.has(conversionId))
                pending.current.set(conversionId, transactions);
              throw error;
            }
            clearTimeout(timers.current[conversionId]);
            if (!pending.current.has(conversionId))
              changeRows(
                conversionId,
                orderTransactionsForReview(saved ?? transactions),
              );
          }
          setStatus(conversionId, "SAVED");
          return true;
        } catch (error) {
          clearTimeout(timers.current[conversionId]);
          setStatus(conversionId, "ERROR");
          setSaveErrors((current) => ({
            ...current,
            [conversionId]:
              error instanceof Error ? error.message : "Falha ao salvar.",
          }));
          return false;
        }
      })().finally(() => {
        saving.current.delete(conversionId);
      });
      saving.current.set(conversionId, promise);
      return promise;
    },
    [repository, changeRows, setStatus],
  );

  const queueSave = useCallback(
    (conversionId: string, transactions: Transaction[]) => {
      clearTimeout(timers.current[conversionId]);
      pending.current.set(conversionId, transactions);
      setStatus(
        conversionId,
        saving.current.has(conversionId) ? "SAVING" : "DIRTY",
      );
      timers.current[conversionId] = setTimeout(() => {
        void persist(conversionId);
      }, 700);
    },
    [persist, setStatus],
  );

  const replaceTransactions = useCallback(
    (
      conversionId: string,
      transactions: Transaction[],
      previous: Transaction[],
    ) => {
      if (
        !pending.current.has(conversionId) &&
        !saving.current.has(conversionId)
      )
        emitWorkspaceEvent(repository, "transaction_edited");
      setHistory((current) => ({
        ...current,
        [conversionId]: [
          ...(current[conversionId] ?? []).slice(-9),
          structuredClone(previous),
        ],
      }));
      changeRows(conversionId, transactions);
      queueSave(conversionId, transactions);
    },
    [queueSave, changeRows, repository],
  );

  const updateField = useCallback(
    (transactionId: string, field: TransactionField, value: string) => {
      if (!activeConversion) return "Nenhuma conversão ativa.";
      const validationError = validateTransactionField(field, value);
      if (validationError) return validationError;

      const previous =
        batchRef.current?.conversions.find(
          (item) => item.id === activeConversion.id,
        )?.transactions ?? activeConversion.transactions;
      const parsedValue =
        field === "date" || field === "description"
          ? value
          : parseBrazilianCurrency(value);
      const existing = previous.find(
        (transaction) => transaction.id === transactionId,
      );
      if (!existing || existing[field] === parsedValue) return null;
      if (existing.isDeleted) return "Restaure o lançamento antes de editar.";
      if (field === "balance") return "O saldo é recalculado após a gravação.";
      if (
        (field === "credit" || field === "debit") &&
        parsedValue !== null &&
        Number(parsedValue) < 0
      )
        return "Use um valor positivo para crédito ou débito.";
      if (field === "credit" || field === "debit") {
        const other = field === "credit" ? "debit" : "credit";
        if (
          parsedValue === 0 ||
          (parsedValue === null && existing[other] === null)
        )
          return "Informe um valor positivo em crédito ou débito.";
      }
      const transactions = previous.map((transaction) => {
        if (transaction.id !== transactionId) return transaction;
        return {
          ...transaction,
          [field]: parsedValue,
          ...((field === "credit" || field === "debit") && parsedValue !== null
            ? { [field === "credit" ? "debit" : "credit"]: null }
            : {}),
          fieldStates: { ...transaction.fieldStates, [field]: "EDITED" },
        };
      });
      replaceTransactions(activeConversion.id, transactions, previous);
      return null;
    },
    [activeConversion, replaceTransactions],
  );

  const setReviewRegions = useCallback(
    (transactionId: string, regions: SourceRegion[]) => {
      if (!activeConversion?.pdf) return "Selecione um arquivo PDF.";
      const previous =
        batchRef.current?.conversions.find(
          (item) => item.id === activeConversion.id,
        )?.transactions ?? activeConversion.transactions;
      const row = previous.find((item) => item.id === transactionId);
      if (!row || row.isDeleted) return "Selecione um lançamento ativo.";
      if (regions.length > 50) return "Use até 50 marcações por lançamento.";
      const valid = validSourceRegions(regions);
      if (valid.length !== regions.length)
        return "Marcação fora dos limites do PDF.";
      regions = valid.filter(
        (region, index) =>
          valid.findIndex(
            (item) => JSON.stringify(item) === JSON.stringify(region),
          ) === index,
      );
      if (JSON.stringify(regions) === JSON.stringify(row.reviewRegions ?? []))
        return null;
      replaceTransactions(
        activeConversion.id,
        previous.map((item) =>
          item.id === transactionId
            ? { ...item, reviewRegions: regions }
            : item,
        ),
        previous,
      );
      return null;
    },
    [activeConversion, replaceTransactions],
  );

  const addTransaction = useCallback(
    (draft: TransactionDraft) => {
      if (!activeConversion) return;
      const previous = activeConversion.transactions;
      const transactions = [
        {
          id: `new-${crypto.randomUUID()}`,
          ...draft,
          balance: null,
          sourcePage: null,
          isDeleted: false,
          ...(previous.some((row) => row.sourceSequence != null)
            ? {
                sourceSequence: 0,
                sourceSubsequence:
                  1 +
                  Math.max(
                    0,
                    ...previous
                      .filter((row) => row.sourceSequence === 0)
                      .map((row) => row.sourceSubsequence ?? 0),
                  ),
              }
            : {}),
          fieldStates: { ...emptyStates },
        },
        ...previous,
      ];
      replaceTransactions(activeConversion.id, transactions, previous);
    },
    [activeConversion, replaceTransactions],
  );

  const removeTransaction = useCallback(
    (transactionId: string) => {
      if (!activeConversion) return;
      const previous = activeConversion.transactions;
      replaceTransactions(
        activeConversion.id,
        previous.map((item) =>
          item.id === transactionId ? { ...item, isDeleted: true } : item,
        ),
        previous,
      );
    },
    [activeConversion, replaceTransactions],
  );

  const restoreTransaction = useCallback(
    (transactionId: string) => {
      if (!activeConversion) return;
      const previous = activeConversion.transactions;
      replaceTransactions(
        activeConversion.id,
        previous.map((item) =>
          item.id === transactionId ? { ...item, isDeleted: false } : item,
        ),
        previous,
      );
    },
    [activeConversion, replaceTransactions],
  );

  const retrySave = useCallback(() => {
    if (activeConversion) {
      pending.current.set(activeConversion.id, activeConversion.transactions);
      void persist(activeConversion.id);
    }
  }, [activeConversion, persist]);

  const undo = useCallback(() => {
    if (!activeConversion) return;
    const entries = history[activeConversion.id] ?? [];
    const previous = entries.at(-1);
    if (!previous) return;
    setHistory((current) => ({
      ...current,
      [activeConversion.id]: entries.slice(0, -1),
    }));
    changeRows(activeConversion.id, previous);
    queueSave(activeConversion.id, previous);
  }, [activeConversion, history, queueSave, changeRows]);

  const selectConversion = useCallback(
    (id: string) => {
      if (id !== activeId)
        emitWorkspaceEvent(repository, "conversion_selected");
      setActiveId(id);
      setActionError(null);
      if (typeof window !== "undefined") {
        window.history.replaceState(
          null,
          "",
          `${reviewBasePath}/${batchId}/${id}`,
        );
      }
    },
    [batchId, reviewBasePath, activeId, repository],
  );

  const goToNextPending = useCallback(() => {
    if (!batch || !activeConversion) return;
    const next = nextPendingConversion(batch.conversions, activeConversion.id);
    if (next) selectConversion(next.id);
  }, [activeConversion, batch, selectConversion]);

  const markReviewed = useCallback(async () => {
    if (
      !activeConversion ||
      actionLock.current ||
      exportPromise.current ||
      !["READY", "NEEDS_REVIEW"].includes(activeConversion.status)
    )
      return;
    const status = saveStatusesRef.current[activeConversion.id] ?? "IDLE";
    if (["DIRTY", "SAVING", "ERROR"].includes(status)) return;
    actionLock.current = true;
    lastAction.current = "review";
    setActionBusy(true);
    setActionError(null);
    try {
      await repository.markReviewed(activeConversion.id);
      emitWorkspaceEvent(repository, "conversion_reviewed");
      const current = batchRef.current;
      if (current) {
        const next: Batch = {
          ...current,
          conversions: current.conversions.map((item) =>
            item.id === activeConversion.id
              ? {
                  ...item,
                  status: "REVIEWED",
                  reviewStatus: "REVIEWED",
                  suspectedCount: 0,
                }
              : item,
          ),
        };
        batchRef.current = next;
        setBatch(next);
      }
      const currentBatch = batchRef.current;
      if (currentBatch) {
        const next = nextPendingConversion(
          currentBatch.conversions,
          activeConversion.id,
        );
        if (next) selectConversion(next.id);
      }
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Não foi possível confirmar a revisão.",
      );
    } finally {
      actionLock.current = false;
      setActionBusy(false);
    }
  }, [activeConversion, repository, selectConversion]);

  const retryConversion = useCallback(async () => {
    if (
      !activeConversion ||
      activeConversion.status !== "FAILED" ||
      actionLock.current ||
      exportPromise.current
    )
      return;
    actionLock.current = true;
    lastAction.current = "retry";
    setActionBusy(true);
    setActionError(null);
    try {
      await repository.retryConversion(activeConversion.id);
      setBatch((current) =>
        current
          ? {
              ...current,
              conversions: current.conversions.map((item) =>
                item.id === activeConversion.id
                  ? { ...item, status: "PROCESSING" }
                  : item,
              ),
            }
          : current,
      );
    } catch (error) {
      setActionError(
        error instanceof Error
          ? error.message
          : "Não foi possível reprocessar o arquivo.",
      );
    } finally {
      actionLock.current = false;
      setActionBusy(false);
    }
  }, [activeConversion, repository]);

  const flushSaves = useCallback(async () => {
    while (pending.current.size || saving.current.size) {
      const ids = new Set([
        ...pending.current.keys(),
        ...saving.current.keys(),
      ]);
      for (const id of ids) {
        if (!(await persist(id))) {
          selectConversion(id);
          throw new Error(
            "Não foi possível salvar todas as alterações. Confira o arquivo selecionado e tente exportar novamente.",
          );
        }
      }
    }
  }, [persist, selectConversion]);

  const exportBatch = useCallback((): Promise<void> => {
    if (exportPromise.current) return exportPromise.current;
    if (actionLock.current) return Promise.resolve();
    setExportState("EXPORTING");
    setExportResult(null);
    setExportError(null);
    emitWorkspaceEvent(repository, "batch_export_requested");
    const promise = (async () => {
      try {
        await flushSaves();
        const result = await repository.exportBatch(batchId);
        setExportResult(result);
        setExportState("DONE");
      } catch (error) {
        setExportState("ERROR");
        setExportError(
          error instanceof Error
            ? error.message
            : "Não foi possível exportar o lote.",
        );
      }
    })().finally(() => {
      exportPromise.current = null;
    });
    exportPromise.current = promise;
    return promise;
  }, [batchId, repository, flushSaves]);

  const retryLoad = useCallback(() => setLoadAttempt((value) => value + 1), []);
  const retryAction = useCallback(() => {
    void (lastAction.current === "review" ? markReviewed() : retryConversion());
  }, [markReviewed, retryConversion]);
  const dismissExport = useCallback(() => {
    setExportState("IDLE");
    setExportResult(null);
    setExportError(null);
  }, []);

  return useMemo(
    () => ({
      batch,
      activeConversion,
      loadState,
      loadError,
      refreshState,
      refreshError,
      saveStatus: activeConversion
        ? (saveStatuses[activeConversion.id] ?? "IDLE")
        : "IDLE",
      saveError: activeConversion
        ? (saveErrors[activeConversion.id] ?? null)
        : null,
      canUndo: activeConversion
        ? Boolean(history[activeConversion.id]?.length)
        : false,
      exportState,
      exportResult,
      exportError,
      actionError,
      actionBusy,
      retryLoad,
      retryAction,
      dismissExport,
      selectConversion,
      updateField,
      setReviewRegions,
      addTransaction,
      removeTransaction,
      restoreTransaction,
      retrySave,
      undo,
      markReviewed,
      goToNextPending,
      retryConversion,
      exportBatch,
      refreshBatch,
    }),
    [
      activeConversion,
      addTransaction,
      batch,
      exportBatch,
      exportResult,
      exportState,
      exportError,
      actionError,
      actionBusy,
      retryLoad,
      retryAction,
      dismissExport,
      goToNextPending,
      history,
      loadError,
      loadState,
      markReviewed,
      removeTransaction,
      restoreTransaction,
      refreshBatch,
      refreshError,
      refreshState,
      retryConversion,
      retrySave,
      saveErrors,
      saveStatuses,
      selectConversion,
      undo,
      updateField,
      setReviewRegions,
    ],
  );
}

function mergeRefreshedBatch(
  current: Batch | null,
  refreshed: Batch,
  saveStatuses: Record<string, SaveStatus>,
): Batch {
  if (!current) return refreshed;
  const currentById = new Map(
    current.conversions.map((conversion) => [conversion.id, conversion]),
  );
  return {
    ...refreshed,
    conversions: refreshed.conversions.map((conversion) => {
      const local = currentById.get(conversion.id);
      const saveStatus = saveStatuses[conversion.id] ?? "IDLE";
      if (
        !local ||
        !["DIRTY", "SAVING", "ERROR", "SAVED"].includes(saveStatus)
      ) {
        return conversion;
      }
      return {
        ...conversion,
        transactions: local.transactions,
        transactionCount: local.transactionCount,
        suspectedCount: local.suspectedCount,
        editedCount: local.editedCount,
        reviewStatus: local.reviewStatus,
      };
    }),
  };
}
