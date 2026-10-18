"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
  selectConversion(id: string): void;
  updateField(
    transactionId: string,
    field: TransactionField,
    value: string,
  ): string | null;
  addTransaction(draft: TransactionDraft): void;
  removeTransaction(transactionId: string): void;
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
  const timers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const batchRef = useRef<Batch | null>(null);
  const saveStatusesRef = useRef<Record<string, SaveStatus>>({});
  const refreshPromiseRef = useRef<Promise<void> | null>(null);
  const pending = useRef(new Map<string, Transaction[]>());
  const saving = useRef(new Set<string>());

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
              transactionCount: transactions.length,
              editedCount: countEdited(transactions),
              suspectedCount: countSuspected(transactions),
              reviewStatus: "IN_REVIEW",
              status:
                item.status === "REVIEWED" || item.status === "EXPORTED"
                  ? countSuspected(transactions)
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
    const activeTimers = timers.current;
    setLoadState("LOADING");
    repository
      .getBatch(batchId)
      .then((loadedBatch) => {
        if (!active) return;
        setBatch(loadedBatch);
        batchRef.current = loadedBatch;
        const initial =
          loadedBatch.conversions.find(
            (item) => item.id === initialConversionId,
          ) ?? sortConversionsForReview(loadedBatch.conversions)[0];
        setActiveId(initial?.id ?? "");
        setLoadState("READY");
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(
          error instanceof Error
            ? error.message
            : "Não foi possível abrir o lote.",
        );
        setLoadState("ERROR");
      });
    return () => {
      active = false;
      Object.values(activeTimers).forEach(clearTimeout);
    };
  }, [batchId, initialConversionId, repository]);

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
    const refreshPromise = repository
      .getBatch(batchId)
      .then((loadedBatch) => {
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
        setRefreshError(
          error instanceof Error
            ? error.message
            : "Não foi possível atualizar o lote.",
        );
        setRefreshState("ERROR");
      })
      .finally(() => {
        refreshPromiseRef.current = null;
      });
    refreshPromiseRef.current = refreshPromise;
    return refreshPromise;
  }, [batchId, repository]);

  const hasProcessing = Boolean(
    batch?.conversions.some((item) => item.status === "PROCESSING"),
  );

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
    async (conversionId: string) => {
      if (saving.current.has(conversionId)) return;
      saving.current.add(conversionId);
      clearTimeout(timers.current[conversionId]);
      setStatus(conversionId, "SAVING");
      setSaveErrors((current) => ({ ...current, [conversionId]: null }));
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
      } catch (error) {
        clearTimeout(timers.current[conversionId]);
        setStatus(conversionId, "ERROR");
        setSaveErrors((current) => ({
          ...current,
          [conversionId]:
            error instanceof Error ? error.message : "Falha ao salvar.",
        }));
      } finally {
        saving.current.delete(conversionId);
      }
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
    [queueSave, changeRows],
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
        previous.filter((item) => item.id !== transactionId),
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
      setActiveId(id);
      if (typeof window !== "undefined") {
        window.history.replaceState(
          null,
          "",
          `${reviewBasePath}/${batchId}/${id}`,
        );
      }
    },
    [batchId, reviewBasePath],
  );

  const goToNextPending = useCallback(() => {
    if (!batch || !activeConversion) return;
    const next = nextPendingConversion(batch.conversions, activeConversion.id);
    if (next) selectConversion(next.id);
  }, [activeConversion, batch, selectConversion]);

  const markReviewed = useCallback(async () => {
    if (!activeConversion) return;
    const status = saveStatuses[activeConversion.id] ?? "IDLE";
    if (["DIRTY", "SAVING", "ERROR"].includes(status)) return;
    await repository.markReviewed(activeConversion.id);
    setBatch((current) =>
      current
        ? {
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
          }
        : current,
    );
    const currentBatch = batchRef.current;
    if (currentBatch) {
      const next = nextPendingConversion(
        currentBatch.conversions,
        activeConversion.id,
      );
      if (next) selectConversion(next.id);
    }
  }, [activeConversion, repository, saveStatuses, selectConversion]);

  const retryConversion = useCallback(async () => {
    if (!activeConversion) return;
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
  }, [activeConversion, repository]);

  const exportBatch = useCallback(async () => {
    setExportState("EXPORTING");
    try {
      const result = await repository.exportBatch(batchId);
      setExportResult(result);
      setExportState("DONE");
    } catch {
      setExportState("ERROR");
    }
  }, [batchId, repository]);

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
      selectConversion,
      updateField,
      addTransaction,
      removeTransaction,
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
      goToNextPending,
      history,
      loadError,
      loadState,
      markReviewed,
      removeTransaction,
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
