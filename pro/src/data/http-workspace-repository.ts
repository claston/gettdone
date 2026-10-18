import type {
  Batch,
  Conversion,
  ConversionStatus,
  ExportResult,
  Transaction,
  WorkspaceRepository,
} from "@/domain/workspace";
import { orderTransactionsForReview, countEdited } from "@/domain/workspace";
import { createProApiClient } from "./pro-api-client";

interface HttpWorkspaceRepositoryOptions {
  apiUrl: string;
  fetcher?: typeof fetch;
}

interface ApiTransaction {
  transaction_id?: string | null;
  source_sequence?: number | null;
  source_subsequence?: number;
  source_page?: number | null;
  date?: string;
  description?: string;
  amount?: number;
  running_balance?: number | null;
  warning_types?: string[];
  is_deleted?: boolean;
}

interface ApiAnalysis {
  file_type?: string;
  bank_name?: string | null;
  bank_code?: string | null;
  transactions_total?: number;
  updated_at?: string | null;
  pdf_processing_metrics?: { page_count?: number } | null;
  preview_transactions?: ApiTransaction[];
}

interface ApiJob {
  job_id: string;
  filename: string;
  status: string;
  analysis_id?: string | null;
  reviewed_at?: string | null;
  result?: {
    processing_id?: string;
    quota_limit?: number;
    quota_remaining?: number;
    reviewed_at?: string | null;
    analysis?: ApiAnalysis;
  } | null;
}

interface ApiBatch {
  batch_id: string;
  status: string;
  created_at?: string | null;
  jobs: ApiJob[];
}

interface ConversionContext {
  batchId: string;
  analysisId: string | null;
  updatedAt: string | null;
  rowIds: Map<string, string>;
  rowCount: number;
  persistedTransactions: Transaction[];
  savedTransactions?: Transaction[];
  pendingSave?: PendingSave;
}

interface PendingSave {
  body: string;
  operationId: string;
  transactions: Transaction[];
  serverOrder: Array<string | null>;
}

class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

const originalFieldStates = (): Transaction["fieldStates"] => ({
  date: "ORIGINAL",
  description: "ORIGINAL",
  credit: "ORIGINAL",
  debit: "ORIGINAL",
  balance: "ORIGINAL",
});

export function createHttpWorkspaceRepository(
  options: HttpWorkspaceRepositoryOptions,
): WorkspaceRepository {
  const apiUrl = options.apiUrl.replace(/\/+$/, "");
  const fetcher = options.fetcher ?? fetch;
  const apiClient = createProApiClient({ apiUrl, fetcher });
  const contexts = new Map<string, ConversionContext>();

  const request = async (path: string, init: RequestInit = {}) => {
    const response = await apiClient.request(path, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
    });
    if (!response.ok) {
      let message = `Falha na API (${response.status}).`;
      try {
        const payload = (await response.json()) as { detail?: unknown };
        if (typeof payload.detail === "string") message = payload.detail;
      } catch {
        // Keep the safe status-based message when the response is not JSON.
      }
      if (response.status === 409)
        message =
          "Este documento foi alterado em outra sessão. Suas edições continuam na tela; confira a outra sessão antes de recarregar.";
      throw new ApiRequestError(message, response.status);
    }
    return response;
  };

  const finishSave = async (context: ConversionContext) => {
    const pending = context.pendingSave!;
    const path = `/api/pro/convert-edits/${encodeURIComponent(context.analysisId!)}`;
    let response: Response;
    try {
      response = await request(path, { method: "POST", body: pending.body });
    } catch (error) {
      if (error instanceof ApiRequestError && error.status < 500) {
        context.pendingSave = undefined;
        throw error;
      }
      // A lost response does not tell us whether the write committed. Keep the
      // identical operation until confirmed, even if the user keeps editing.
      try {
        response = await request(`${path}/operations/${pending.operationId}`);
      } catch {
        throw error;
      }
    }
    const result = (await response.json()) as {
      operation_id?: string | null;
      updated_at?: string | null;
      preview_transactions?: ApiTransaction[];
    };
    if (result.operation_id && result.operation_id !== pending.operationId)
      throw new Error(
        "A confirmação da gravação não corresponde à alteração enviada.",
      );
    const { transactions, serverOrder } = pending;
    const currentIds = new Set(transactions.map((row) => row.id));
    context.updatedAt = result.updated_at ?? context.updatedAt;
    if (result.preview_transactions?.length === serverOrder.length) {
      const rows = result.preview_transactions;
      context.rowIds = new Map(
        serverOrder.flatMap((id, index) =>
          id && currentIds.has(id) && !rows[index].is_deleted
            ? [[id, `row_${index + 1}`] as const]
            : [],
        ),
      );
      context.rowCount = rows.length;
      context.savedTransactions = transactions.map((transaction) => ({
        ...transaction,
        sourceSequence:
          rows[serverOrder.indexOf(transaction.id)].source_sequence ?? null,
        sourceSubsequence:
          rows[serverOrder.indexOf(transaction.id)].source_subsequence ?? 0,
        balance:
          rows[serverOrder.indexOf(transaction.id)].running_balance ?? null,
      }));
    } else {
      context.savedTransactions = transactions;
    }
    context.persistedTransactions = context.savedTransactions;
    context.pendingSave = undefined;
  };

  const getBatch = async (batchId: string): Promise<Batch> => {
    const response = await request(
      `/api/pro/conversion-batches/${encodeURIComponent(batchId)}`,
    );
    const payload = (await response.json()) as ApiBatch;
    const conversions = payload.jobs.map((job) =>
      mapConversion({ apiUrl, batchId: payload.batch_id, job, contexts }),
    );
    const limits = payload.jobs
      .map((job) => job.result?.quota_limit)
      .filter((value): value is number => typeof value === "number");
    const remaining = payload.jobs
      .map((job) => job.result?.quota_remaining)
      .filter((value): value is number => typeof value === "number");
    const limit = limits.at(0) ?? Math.max(conversions.length, 1);
    const quotaRemaining = remaining.length
      ? Math.min(...remaining)
      : Math.max(0, limit - conversions.length);
    const period = resolveBatchPeriod(conversions);
    return {
      id: payload.batch_id,
      name: `Lote ${period}`,
      clientName: "Workspace OFX Simples PRO",
      period,
      createdAt: payload.created_at ?? new Date().toISOString(),
      quota: {
        used: Math.max(0, limit - quotaRemaining),
        limit,
        unit: "FILES",
      },
      conversions,
    };
  };

  return {
    getBatch,

    async loadDocument(conversionId, signal) {
      const context = requireContext(contexts, conversionId);
      const response = await request(
        `/api/pro/conversion-batches/${encodeURIComponent(context.batchId)}/jobs/${encodeURIComponent(conversionId)}/document`,
        { signal, headers: { Accept: "application/pdf" } },
      );
      return response.blob();
    },

    async saveTransactions(conversionId: string, transactions: Transaction[]) {
      const context = requireContext(contexts, conversionId);
      if (!context.analysisId)
        throw new Error("A conversão ainda não possui resultado editável.");
      if (context.pendingSave) await finishSave(context);
      const currentIds = new Set(
        transactions.map((transaction) => transaction.id),
      );
      // Existing row IDs are positional: apply all updates/deletes before inserts.
      const originalById = new Map(
        context.persistedTransactions.map((row) => [row.id, row]),
      );
      const fields = ["date", "description", "credit", "debit"] as const;
      const edits: Array<Record<string, unknown>> = transactions
        .filter((row) => {
          if (!context.rowIds.has(row.id)) return false;
          const original = originalById.get(row.id);
          return (
            !original || fields.some((field) => row[field] !== original[field])
          );
        })
        .map((transaction) => {
          const rowId = context.rowIds.get(transaction.id);
          return {
            action: "update",
            row_id: transaction.sourceSequence != null ? transaction.id : rowId,
            date: toIsoDate(transaction.date),
            description: transaction.description,
            credit: transaction.credit,
            debit: transaction.debit,
          };
        });
      for (const [localId, rowId] of context.rowIds) {
        if (!currentIds.has(localId))
          edits.push({
            action: "delete",
            row_id:
              originalById.get(localId)?.sourceSequence != null
                ? localId
                : rowId,
          });
      }
      const serverOrder: Array<string | null> = Array(context.rowCount).fill(
        null,
      );
      for (const [localId, rowId] of context.rowIds) {
        serverOrder[Number(rowId.replace("row_", "")) - 1] = localId;
      }
      const currentById = new Map(transactions.map((row) => [row.id, row]));
      const dateKey = (date: string) => toIsoDate(date);
      transactions.forEach((transaction) => {
        if (context.rowIds.has(transaction.id)) return;
        const nextIndex = serverOrder.findIndex((id) => {
          const row = id ? currentById.get(id) : undefined;
          return row && dateKey(row.date) > dateKey(transaction.date);
        });
        const position = nextIndex < 0 ? serverOrder.length : nextIndex;
        edits.push({
          action: "insert",
          ...(transaction.sourceSequence != null
            ? { transaction_id: transaction.id }
            : {}),
          insert_position: position,
          date: toIsoDate(transaction.date),
          description: transaction.description,
          credit: transaction.credit,
          debit: transaction.debit,
        });
        serverOrder.splice(position, 0, transaction.id);
      });
      if (!edits.length) {
        const savedById = new Map(
          context.persistedTransactions.map((row) => [row.id, row]),
        );
        return transactions.map((row) => ({
          ...row,
          balance: savedById.get(row.id)?.balance ?? row.balance,
        }));
      }
      const operationId = crypto.randomUUID();
      context.pendingSave = {
        operationId,
        transactions: structuredClone(transactions),
        serverOrder,
        body: JSON.stringify({
          operation_id: operationId,
          edits,
          expected_updated_at: context.updatedAt,
          batch_id: context.batchId,
          job_id: conversionId,
        }),
      };
      await finishSave(context);
      return context.savedTransactions!;
    },

    async markReviewed(conversionId: string) {
      const context = requireContext(contexts, conversionId);
      await request(
        `/api/pro/conversion-batches/${encodeURIComponent(context.batchId)}/jobs/${encodeURIComponent(conversionId)}/review`,
        { method: "POST", body: "{}" },
      );
    },

    async retryConversion(conversionId: string) {
      const context = requireContext(contexts, conversionId);
      await request(
        `/api/pro/conversion-batches/${encodeURIComponent(context.batchId)}/jobs/${encodeURIComponent(conversionId)}/retry`,
        { method: "POST", body: "{}" },
      );
    },

    async exportBatch(batchId: string): Promise<ExportResult> {
      const response = await request(
        `/api/pro/conversion-batches/${encodeURIComponent(batchId)}/export`,
        {
          headers: { Accept: "application/zip" },
        },
      );
      const blob = await response.blob();
      const fileName = resolveDownloadName(
        response.headers.get("content-disposition"),
        batchId,
      );
      if (
        typeof document !== "undefined" &&
        typeof URL.createObjectURL === "function"
      ) {
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = fileName;
        anchor.click();
        URL.revokeObjectURL(url);
      }
      return {
        fileName,
        conversionCount: Number(
          response.headers.get("x-conversion-count") ?? 0,
        ),
      };
    },
  };
}

function mapConversion({
  apiUrl,
  batchId,
  job,
  contexts,
}: {
  apiUrl: string;
  batchId: string;
  job: ApiJob;
  contexts: Map<string, ConversionContext>;
}): Conversion {
  const analysis = job.result?.analysis;
  const fileType = (
    analysis?.file_type ??
    job.filename.split(".").pop() ??
    ""
  ).toLowerCase();
  const remoteTransactions = (analysis?.preview_transactions ?? [])
    .map(mapTransaction)
    .filter((_, index) => !analysis?.preview_transactions?.[index].is_deleted);
  const analysisId = job.analysis_id ?? job.result?.processing_id ?? null;
  let context = contexts.get(job.job_id);
  // A loaded document is an editing snapshot. Polling other jobs must not
  // advance its optimistic-lock version or replace positional row mappings.
  const keepContext =
    context?.analysisId === analysisId &&
    (context.rowCount > 0 || remoteTransactions.length === 0);
  if (!keepContext) {
    context = {
      batchId,
      analysisId,
      updatedAt: analysis?.updated_at ?? null,
      rowIds: new Map(
        (analysis?.preview_transactions ?? []).flatMap((row, index) =>
          row.is_deleted
            ? []
            : [[row.transaction_id ?? `row_${index + 1}`, `row_${index + 1}`]],
        ),
      ),
      rowCount: analysis?.preview_transactions?.length ?? 0,
      persistedTransactions: remoteTransactions,
    };
    contexts.set(job.job_id, context);
  }
  const transactions = orderTransactionsForReview(
    context?.savedTransactions ??
      context?.persistedTransactions ??
      remoteTransactions,
  );
  const suspectedCount = transactions.filter((transaction) =>
    Object.values(transaction.fieldStates).includes("SUSPECTED"),
  ).length;
  const status = mapStatus(job, suspectedCount);
  return {
    id: job.job_id,
    batchId,
    fileName: job.filename,
    bankName: analysis?.bank_name?.trim() || "Instituição não identificada",
    bankCode: analysis?.bank_code?.trim() || undefined,
    period: resolveTransactionPeriod(transactions),
    status,
    reviewStatus: status === "REVIEWED" ? "REVIEWED" : "PENDING",
    transactionCount: transactions.length,
    suspectedCount,
    editedCount: countEdited(transactions),
    pdf:
      fileType !== "pdf" || job.status === "failed" || job.status === "expired"
        ? null
        : {
            url: `${apiUrl}/api/pro/conversion-batches/${encodeURIComponent(batchId)}/jobs/${encodeURIComponent(job.job_id)}/document`,
            pageCount: analysis?.pdf_processing_metrics?.page_count ?? 1,
            fileName: job.filename,
          },
    downloadUrl: analysisId
      ? `${apiUrl}/convert-report/${encodeURIComponent(analysisId)}?format=ofx`
      : undefined,
    transactions,
  };
}

function mapTransaction(row: ApiTransaction, index: number): Transaction {
  const amount = Number(row.amount ?? 0);
  const fieldStates = originalFieldStates();
  if ((row.warning_types ?? []).length > 0) {
    fieldStates.description = "SUSPECTED";
    fieldStates[amount >= 0 ? "credit" : "debit"] = "SUSPECTED";
  }
  return {
    id: row.transaction_id ?? `row_${index + 1}`,
    date: toBrazilianDate(String(row.date ?? "")),
    description: String(row.description ?? ""),
    credit: amount > 0 ? amount : null,
    debit: amount < 0 ? Math.abs(amount) : null,
    balance: row.running_balance == null ? null : Number(row.running_balance),
    sourcePage: row.source_page ?? null,
    sourceSequence: row.source_sequence ?? null,
    sourceSubsequence: row.source_subsequence ?? 0,
    fieldStates,
  };
}

function mapStatus(job: ApiJob, suspectedCount: number): ConversionStatus {
  if (job.status === "failed" || job.status === "expired") return "FAILED";
  if (job.status !== "completed") return "PROCESSING";
  if (job.reviewed_at || job.result?.reviewed_at) return "REVIEWED";
  return suspectedCount > 0 ? "NEEDS_REVIEW" : "READY";
}

function requireContext(
  contexts: Map<string, ConversionContext>,
  conversionId: string,
): ConversionContext {
  const context = contexts.get(conversionId);
  if (!context) throw new Error("Conversão não encontrada no lote carregado.");
  return context;
}

function toBrazilianDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value;
}

function toIsoDate(value: string): string {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
  return match ? `${match[3]}-${match[2]}-${match[1]}` : value;
}

function resolveTransactionPeriod(transactions: Transaction[]): string {
  if (!transactions.length) return "Período indisponível";
  const dates = transactions
    .map((row) => row.date)
    .sort((a, b) => toIsoDate(a).localeCompare(toIsoDate(b)));
  return `${dates[0]}–${dates.at(-1)}`;
}

function resolveBatchPeriod(conversions: Conversion[]): string {
  const ready = conversions.find(
    (conversion) => conversion.transactions.length > 0,
  );
  return ready?.period ?? "em processamento";
}

function resolveDownloadName(header: string | null, batchId: string): string {
  const match = /filename="([^"]+)"/i.exec(header ?? "");
  return match?.[1] ?? `lote-${batchId}.zip`;
}
