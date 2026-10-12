import type {
  Batch,
  Conversion,
  ConversionStatus,
  ExportResult,
  Transaction,
  WorkspaceRepository,
} from "@/domain/workspace";
import { createProApiClient } from "./pro-api-client";

interface HttpWorkspaceRepositoryOptions {
  apiUrl: string;
  fetcher?: typeof fetch;
}

interface ApiTransaction {
  date?: string;
  description?: string;
  amount?: number;
  running_balance?: number | null;
  warning_types?: string[];
  is_deleted?: boolean;
}

interface ApiAnalysis {
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
      throw new Error(message);
    }
    return response;
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

    async saveTransactions(conversionId: string, transactions: Transaction[]) {
      const context = requireContext(contexts, conversionId);
      if (!context.analysisId)
        throw new Error("A conversão ainda não possui resultado editável.");
      const currentIds = new Set(
        transactions.map((transaction) => transaction.id),
      );
      const edits: Array<Record<string, unknown>> = transactions.map(
        (transaction, index) => {
          const rowId = context.rowIds.get(transaction.id);
          return {
            action: rowId ? "update" : "insert",
            ...(rowId ? { row_id: rowId } : { insert_position: index }),
            date: toIsoDate(transaction.date),
            description: transaction.description,
            credit: transaction.credit,
            debit: transaction.debit,
          };
        },
      );
      for (const [localId, rowId] of context.rowIds) {
        if (!currentIds.has(localId))
          edits.push({ action: "delete", row_id: rowId });
      }
      const response = await request(
        `/api/pro/convert-edits/${encodeURIComponent(context.analysisId)}`,
        {
          method: "POST",
          body: JSON.stringify({
            edits,
            expected_updated_at: context.updatedAt,
          }),
        },
      );
      const result = (await response.json()) as {
        updated_at?: string | null;
        preview_transactions?: ApiTransaction[];
      };
      context.updatedAt = result.updated_at ?? context.updatedAt;
      const activeRows = (result.preview_transactions ?? [])
        .map((row, index) => ({ row, id: `row_${index + 1}` }))
        .filter(({ row }) => !row.is_deleted);
      if (activeRows.length === transactions.length) {
        context.rowIds = new Map(
          transactions.map((transaction, index) => [
            transaction.id,
            activeRows[index].id,
          ]),
        );
      }
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
  const transactions = (analysis?.preview_transactions ?? [])
    .map(mapTransaction)
    .filter((_, index) => !analysis?.preview_transactions?.[index].is_deleted);
  const suspectedCount = transactions.filter((transaction) =>
    Object.values(transaction.fieldStates).includes("SUSPECTED"),
  ).length;
  const status = mapStatus(job, suspectedCount);
  const analysisId = job.analysis_id ?? job.result?.processing_id ?? null;
  contexts.set(job.job_id, {
    batchId,
    analysisId,
    updatedAt: analysis?.updated_at ?? null,
    rowIds: new Map(
      transactions.map((transaction) => [transaction.id, transaction.id]),
    ),
  });
  return {
    id: job.job_id,
    batchId,
    fileName: job.filename,
    bankName: analysis?.bank_name?.trim() || "Instituição não identificada",
    bankCode: analysis?.bank_code?.trim() || undefined,
    period: resolveTransactionPeriod(transactions),
    status,
    reviewStatus: status === "REVIEWED" ? "REVIEWED" : "PENDING",
    transactionCount: analysis?.transactions_total ?? transactions.length,
    suspectedCount,
    editedCount: 0,
    pdf:
      job.status === "failed" || job.status === "expired"
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
    id: `row_${index + 1}`,
    date: toBrazilianDate(String(row.date ?? "")),
    description: String(row.description ?? ""),
    credit: amount > 0 ? amount : null,
    debit: amount < 0 ? Math.abs(amount) : null,
    balance: row.running_balance == null ? null : Number(row.running_balance),
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
  return `${transactions[0].date}–${transactions.at(-1)?.date ?? transactions[0].date}`;
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
