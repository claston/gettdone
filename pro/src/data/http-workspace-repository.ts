import { validSourceRegions } from "@/domain/source-regions";
import {
  documentReviewWarnings,
  fieldReviewWarnings,
  requiresDocumentReview,
} from "@/domain/review-warnings";
import { workspaceEvents } from "@/domain/workspace-events";
import type {
  Batch,
  BalanceCheck,
  Conversion,
  ConversionStatus,
  ExportResult,
  FinancialDocument,
  Transaction,
  WorkspaceRepository,
} from "@/domain/workspace";
import {
  orderTransactionsForReview,
  countEdited,
  countSuspected,
} from "@/domain/workspace";
import { createProApiClient } from "./pro-api-client";
import { proSessionCacheVersion } from "./pro-session-cache";

interface HttpWorkspaceRepositoryOptions {
  apiUrl: string;
  fetcher?: typeof fetch;
  clientId?: string;
}

interface ApiTransaction {
  balance_check?: unknown;
  transaction_id?: string | null;
  source_sequence?: number | null;
  source_subsequence?: number;
  source_page?: number | null;
  source_line?: number | null;
  source_regions?: unknown;
  review_regions?: unknown;
  date?: string;
  description?: string;
  amount?: number;
  running_balance?: number | null;
  warning_types?: string[];
  is_deleted?: boolean;
}

interface ApiAnalysis {
  statement_balance_check?: unknown;
  financial_document?: FinancialDocument | null;
  file_type?: string;
  bank_name?: string | null;
  bank_code?: string | null;
  transactions_total?: number;
  updated_at?: string | null;
  pdf_processing_metrics?: {
    page_count?: number;
    statement_no_activity?: number;
    partial_statement?: number;
  } | null;
  preview_transactions?: ApiTransaction[];
}

interface ApiJob {
  source_batch_id?: string;
  expires_at?: string;
  job_id: string;
  filename: string;
  status: string;
  error_code?: string | null;
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
  client_name?: string;
  next_cursor?: string | null;
  batch_id: string;
  status: string;
  created_at?: string | null;
  jobs: ApiJob[];
}

interface ConversionContext {
  expiresAt?: string;
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
  const documents = new Map<string, Blob>();
  const batches = new Map<string, Batch>();
  let cacheVersion = proSessionCacheVersion();

  const synchronizeSession = () => {
    if (cacheVersion === proSessionCacheVersion()) return;
    contexts.clear();
    documents.clear();
    batches.clear();
    cacheVersion = proSessionCacheVersion();
  };
  const updateCachedConversion = (
    id: string,
    update: (conversion: Conversion) => Conversion,
  ) => {
    for (const [batchId, batch] of batches)
      batches.set(batchId, {
        ...batch,
        conversions: batch.conversions.map((item) =>
          item.id === id ? update(item) : item,
        ),
      });
  };
  const cacheDocument = (id: string, blob: Blob) => {
    if (blob.size > 64 * 1024 * 1024) return;
    documents.delete(id);
    documents.set(id, blob);
    while (
      documents.size > 12 ||
      [...documents.values()].reduce((total, item) => total + item.size, 0) >
        64 * 1024 * 1024
    )
      documents.delete(documents.keys().next().value!);
  };

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
    const originalById = new Map(
      context.persistedTransactions.map((row) => [row.id, row]),
    );
    const manualRows = transactions.filter(
      (row) =>
        (row.reviewRegions?.length ?? 0) > 0 ||
        JSON.stringify(row.reviewRegions ?? []) !==
          JSON.stringify(originalById.get(row.id)?.reviewRegions ?? []),
    );
    if (
      manualRows.some((row) => {
        const server =
          result.preview_transactions?.[serverOrder.indexOf(row.id)];
        return (
          !Array.isArray(server?.review_regions) ||
          JSON.stringify(validSourceRegions(server.review_regions)) !==
            JSON.stringify(row.reviewRegions ?? [])
        );
      })
    )
      throw new Error(
        "Não foi possível confirmar as marcações. Verifique se a API está atualizada e tente salvar novamente.",
      );
    const currentIds = new Set(transactions.map((row) => row.id));
    context.updatedAt = result.updated_at ?? context.updatedAt;
    if (result.preview_transactions?.length === serverOrder.length) {
      const rows = result.preview_transactions;
      context.rowIds = new Map(
        serverOrder.flatMap((id, index) =>
          id && currentIds.has(id) ? [[id, `row_${index + 1}`] as const] : [],
        ),
      );
      context.rowCount = rows.length;
      context.savedTransactions = transactions.map((transaction) => ({
        ...transaction,
        isDeleted: Boolean(
          rows[serverOrder.indexOf(transaction.id)].is_deleted,
        ),
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
    updateCachedConversion(
      [...contexts].find(([, value]) => value === context)?.[0] ?? "",
      (item) => ({
        ...item,
        transactions: context.savedTransactions!,
        transactionCount: context.savedTransactions!.filter(
          (row) => !row.isDeleted,
        ).length,
        suspectedCount: countSuspected(context.savedTransactions!),
        editedCount: countEdited(context.savedTransactions!),
        reviewStatus: "IN_REVIEW",
        status:
          item.status === "REVIEWED" || item.status === "EXPORTED"
            ? countSuspected(context.savedTransactions!) ||
              requiresDocumentReview(item)
              ? "NEEDS_REVIEW"
              : "READY"
            : item.status,
      }),
    );
  };

  const getBatch = async (
    batchId: string,
    signal?: AbortSignal,
  ): Promise<Batch> => {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    synchronizeSession();
    const version = cacheVersion;
    const readPath = options.clientId
      ? `/api/pro/receipts/clients/${encodeURIComponent(options.clientId)}/workspace`
      : `/api/pro/conversion-batches/${encodeURIComponent(batchId)}`;
    const response = await request(readPath, { signal });
    const payload = (await response.json()) as ApiBatch;
    while (options.clientId && payload.next_cursor != null) {
      const next = await request(
        `${readPath}?cursor=${encodeURIComponent(payload.next_cursor)}`,
        {
          signal,
        },
      );
      const page = (await next.json()) as ApiBatch;
      payload.jobs.push(...page.jobs);
      payload.next_cursor = page.next_cursor;
    }
    if (signal?.aborted || version !== proSessionCacheVersion())
      throw new DOMException("Session changed", "AbortError");
    const conversions = payload.jobs
      .filter(
        (job) => !job.expires_at || Date.parse(job.expires_at) > Date.now(),
      )
      .map((job) =>
        mapConversion({
          apiUrl,
          batchId: job.source_batch_id ?? payload.batch_id,
          job,
          contexts,
        }),
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
    const batch: Batch = {
      id: payload.batch_id,
      name: options.clientId ? "Extratos do cliente" : `Lote ${period}`,
      clientName: payload.client_name ?? "Workspace OFX Simples PRO",
      period,
      createdAt: payload.created_at ?? new Date().toISOString(),
      quota: {
        used: Math.max(0, limit - quotaRemaining),
        limit,
        unit: "FILES",
      },
      conversions,
    };
    batches.delete(batchId);
    batches.set(batchId, batch);
    while (batches.size > 3) batches.delete(batches.keys().next().value!);
    return batch;
  };

  return {
    getBatch,
    getCachedBatch(batchId) {
      synchronizeSession();
      const batch = batches.get(batchId);
      if (!batch) return undefined;
      const conversions = batch.conversions.filter(
        (row) => !row.expiresAt || Date.parse(row.expiresAt) > Date.now(),
      );
      return conversions.length === batch.conversions.length
        ? batch
        : { ...batch, conversions };
    },
    recordEvent(event) {
      if (!workspaceEvents.includes(event)) return;
      void request("/telemetry/events", {
        method: "POST",
        body: JSON.stringify({ event_type: event, page_path: "/review" }),
      }).catch(() => {});
    },

    async loadDocument(conversionId, signal, options) {
      synchronizeSession();
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      const context = requireContext(contexts, conversionId);
      if (options?.reload) documents.delete(conversionId);
      const cached = documents.get(conversionId);
      if (cached) {
        documents.delete(conversionId);
        documents.set(conversionId, cached);
        return cached;
      }
      const version = cacheVersion;
      const response = await request(
        `/api/pro/conversion-batches/${encodeURIComponent(context.batchId)}/jobs/${encodeURIComponent(conversionId)}/document`,
        { signal, headers: { Accept: "application/pdf" } },
      );
      const blob = await response.blob();
      if (signal?.aborted || version !== proSessionCacheVersion())
        throw new DOMException("Aborted", "AbortError");
      cacheDocument(conversionId, blob);
      return blob;
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
            !original ||
            fields.some((field) => row[field] !== original[field]) ||
            JSON.stringify(row.reviewRegions ?? []) !==
              JSON.stringify(original.reviewRegions ?? []) ||
            Boolean(row.isDeleted) !== Boolean(original.isDeleted)
          );
        })
        .map((transaction) => {
          const rowId = context.rowIds.get(transaction.id);
          const original = originalById.get(transaction.id);
          const regionsChanged =
            JSON.stringify(transaction.reviewRegions ?? []) !==
            JSON.stringify(original?.reviewRegions ?? []);
          const annotationsOnly =
            regionsChanged &&
            original &&
            !transaction.isDeleted &&
            !original.isDeleted &&
            fields.every((field) => transaction[field] === original[field]);
          return {
            action: annotationsOnly
              ? "annotate"
              : transaction.isDeleted
                ? "delete"
                : originalById.get(transaction.id)?.isDeleted
                  ? "restore"
                  : "update",
            row_id: transaction.sourceSequence != null ? transaction.id : rowId,
            date: toIsoDate(transaction.date),
            description: transaction.description,
            credit: transaction.credit,
            debit: transaction.debit,
            ...(regionsChanged
              ? { review_regions: transaction.reviewRegions ?? [] }
              : {}),
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
          ...(transaction.reviewRegions?.length
            ? { review_regions: transaction.reviewRegions }
            : {}),
        });
        serverOrder.splice(position, 0, transaction.id);
        if (transaction.isDeleted)
          edits.push({ action: "delete", row_id: `row_${position + 1}` });
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
      updateCachedConversion(conversionId, (item) => ({
        ...item,
        status: "REVIEWED",
        reviewStatus: "REVIEWED",
      }));
    },

    async retryConversion(conversionId: string) {
      const context = requireContext(contexts, conversionId);
      await request(
        `/api/pro/conversion-batches/${encodeURIComponent(context.batchId)}/jobs/${encodeURIComponent(conversionId)}/retry`,
        { method: "POST", body: "{}" },
      );
      documents.delete(conversionId);
      batches.delete(context.batchId);
    },

    async exportBatch(batchId: string): Promise<ExportResult> {
      const response = await request(
        options.clientId
          ? `/api/pro/receipts/clients/${encodeURIComponent(options.clientId)}/export`
          : `/api/pro/conversion-batches/${encodeURIComponent(batchId)}/export`,
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
  const remoteTransactions = (analysis?.preview_transactions ?? []).map(
    mapTransaction,
  );
  const analysisId = job.analysis_id ?? job.result?.processing_id ?? null;
  let context = contexts.get(job.job_id);
  // A loaded document is an editing snapshot. Polling other jobs must not
  // advance its optimistic-lock version or replace positional row mappings.
  const keepContext =
    context?.analysisId === analysisId &&
    (context.rowCount > 0 || remoteTransactions.length === 0);
  if (!keepContext) {
    context = {
      expiresAt: job.expires_at,
      batchId,
      analysisId,
      updatedAt: analysis?.updated_at ?? null,
      rowIds: new Map(
        (analysis?.preview_transactions ?? []).map((row, index) => [
          row.transaction_id ?? `row_${index + 1}`,
          `row_${index + 1}`,
        ]),
      ),
      rowCount: analysis?.preview_transactions?.length ?? 0,
      persistedTransactions: remoteTransactions,
    };
    contexts.set(job.job_id, context);
  }
  if (context) context.expiresAt = job.expires_at;
  const transactions = orderTransactionsForReview(
    context?.savedTransactions ??
      context?.persistedTransactions ??
      remoteTransactions,
  );
  const suspectedCount = transactions.filter(
    (transaction) =>
      !transaction.isDeleted &&
      Object.values(transaction.fieldStates).includes("SUSPECTED"),
  ).length;
  const partialStatement =
    analysis?.pdf_processing_metrics?.partial_statement === 1 ||
    (analysis?.preview_transactions ?? []).some((row) =>
      row.warning_types?.includes("partial_statement"),
    );
  const reviewWarnings = [
    ...new Set(
      (analysis?.preview_transactions ?? []).flatMap(
        (row) => row.warning_types ?? [],
      ),
    ),
  ];
  const statementBalanceCheck = mapBalanceCheck(
    analysis?.statement_balance_check,
  );
  const status = mapStatus(
    job,
    suspectedCount,
    partialStatement ||
      Boolean(statementBalanceCheck) ||
      reviewWarnings.some((code) => documentReviewWarnings.has(code)),
  );
  return {
    id: job.job_id,
    expiresAt: job.expires_at,
    batchId,
    fileName: job.filename,
    bankName: analysis?.bank_name?.trim() || "Instituição não identificada",
    bankCode: analysis?.bank_code?.trim() || undefined,
    period: resolveTransactionPeriod(transactions),
    status,
    errorCode: job.error_code,
    reviewStatus: status === "REVIEWED" ? "REVIEWED" : "PENDING",
    transactionCount: transactions.filter((row) => !row.isDeleted).length,
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
      ? `${apiUrl}/convert-report/${encodeURIComponent(analysisId)}?format=${analysis?.financial_document ? "xlsx" : "ofx"}`
      : undefined,
    financialDocument: analysis?.financial_document,
    noActivity: analysis?.pdf_processing_metrics?.statement_no_activity === 1,
    partialStatement,
    reviewWarnings,
    statementBalanceCheck,
    transactions,
  };
}

function mapTransaction(row: ApiTransaction, index: number): Transaction {
  const amount = Number(row.amount ?? 0);
  const fieldStates = originalFieldStates();
  const warningTypes = row.warning_types ?? [];
  const fieldWarnings = fieldReviewWarnings(warningTypes);
  if (fieldWarnings.includes("balance_consistency_failed"))
    fieldStates.balance = "SUSPECTED";
  if (fieldWarnings.some((code) => code !== "balance_consistency_failed")) {
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
    sourceLine: row.source_line ?? null,
    sourceRegions: validSourceRegions(row.source_regions),
    reviewRegions: validSourceRegions(row.review_regions),
    sourceSequence: row.source_sequence ?? null,
    sourceSubsequence: row.source_subsequence ?? 0,
    isDeleted: Boolean(row.is_deleted),
    fieldStates,
    warningTypes,
    balanceCheck: mapBalanceCheck(row.balance_check),
  };
}

function mapBalanceCheck(value: unknown): BalanceCheck | undefined {
  if (!value || typeof value !== "object") return;
  const check = value as Record<string, unknown>;
  if (
    ![
      "reference_balance",
      "credits_total",
      "debits_total",
      "expected_balance",
      "reported_balance",
      "difference",
      "movements_count",
    ].every(
      (key) => typeof check[key] === "number" && Number.isFinite(check[key]),
    ) ||
    !["ascending", "descending"].includes(String(check.order))
  )
    return;
  return {
    referenceBalance: check.reference_balance as number,
    creditsTotal: check.credits_total as number,
    debitsTotal: check.debits_total as number,
    expectedBalance: check.expected_balance as number,
    reportedBalance: check.reported_balance as number,
    difference: check.difference as number,
    movementsCount: check.movements_count as number,
    order: check.order as BalanceCheck["order"],
    referenceDate:
      typeof check.reference_date === "string"
        ? toBrazilianDate(check.reference_date)
        : undefined,
    referencePage:
      typeof check.reference_page === "number"
        ? check.reference_page
        : undefined,
    referenceLine:
      typeof check.reference_line === "number"
        ? check.reference_line
        : undefined,
  };
}

function mapStatus(
  job: ApiJob,
  suspectedCount: number,
  hasDocumentWarning: boolean,
): ConversionStatus {
  if (job.status === "failed" || job.status === "expired") return "FAILED";
  if (job.status !== "completed") return "PROCESSING";
  if (job.reviewed_at || job.result?.reviewed_at) return "REVIEWED";
  return suspectedCount > 0 || hasDocumentWarning ? "NEEDS_REVIEW" : "READY";
}

function requireContext(
  contexts: Map<string, ConversionContext>,
  conversionId: string,
): ConversionContext {
  const context = contexts.get(conversionId);
  if (!context) throw new Error("Conversão não encontrada no lote carregado.");
  if (context.expiresAt && Date.parse(context.expiresAt) <= Date.now())
    throw new Error("Este extrato expirou e não está mais disponível.");
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
