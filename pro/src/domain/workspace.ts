export type ConversionStatus =
  "PROCESSING" | "READY" | "NEEDS_REVIEW" | "REVIEWED" | "EXPORTED" | "FAILED";

export type ReviewStatus = "PENDING" | "IN_REVIEW" | "REVIEWED";
export type TransactionFieldState = "ORIGINAL" | "SUSPECTED" | "EDITED";
export type TransactionFilter = "ALL" | "SUSPECTED" | "EDITED";
export type SaveStatus = "IDLE" | "DIRTY" | "SAVING" | "SAVED" | "ERROR";
export type TransactionField =
  "date" | "description" | "credit" | "debit" | "balance";

export interface PdfDocument {
  url: string;
  pageCount: number;
  fileName?: string;
}

export interface Transaction {
  id: string;
  date: string;
  description: string;
  credit: number | null;
  debit: number | null;
  balance: number | null;
  sourcePage?: number | null;
  sourceSequence?: number | null;
  sourceSubsequence?: number;
  sourceRegions?: readonly SourceRegion[];
  reviewRegions?: readonly SourceRegion[];
  isDeleted?: boolean;
  fieldStates: Record<TransactionField, TransactionFieldState>;
}

/** Unit coordinates on the original visible page, including intrinsic rotation/crop. */
export interface SourceRegion {
  readonly page: number;
  readonly left: number;
  readonly top: number;
  readonly width: number;
  readonly height: number;
}

export interface Conversion {
  id: string;
  batchId: string;
  fileName: string;
  bankName: string;
  bankCode?: string;
  period: string;
  status: ConversionStatus;
  reviewStatus: ReviewStatus;
  transactionCount: number;
  suspectedCount: number;
  editedCount: number;
  pdf: PdfDocument | null;
  downloadUrl?: string;
  noActivity?: boolean;
  transactions: Transaction[];
}

export interface QuotaSummary {
  used: number;
  limit: number;
  unit: "FILES";
}

export interface Batch {
  id: string;
  name: string;
  clientName: string;
  period: string;
  createdAt: string;
  conversions: Conversion[];
  quota: QuotaSummary;
}

export interface ExportResult {
  fileName: string;
  conversionCount: number;
}

export interface WorkspaceRepository {
  loadDocument?(conversionId: string, signal?: AbortSignal): Promise<Blob>;
  getBatch(batchId: string): Promise<Batch>;
  saveTransactions(
    conversionId: string,
    transactions: Transaction[],
  ): Promise<Transaction[] | void>;
  markReviewed(conversionId: string): Promise<void>;
  retryConversion(conversionId: string): Promise<void>;
  exportBatch(batchId: string): Promise<ExportResult>;
}

export type TransactionDraft = Pick<
  Transaction,
  "date" | "description" | "credit" | "debit"
>;

export function sortTransactionsByDate(
  transactions: Transaction[],
): Transaction[] {
  const key = (date: string) => date.split("/").reverse().join("-");
  return [...transactions].sort((a, b) =>
    key(a.date).localeCompare(key(b.date)),
  );
}

export function orderTransactionsForReview(
  transactions: Transaction[],
): Transaction[] {
  if (
    !transactions.length ||
    transactions.some((row) => row.sourceSequence == null)
  )
    return sortTransactionsByDate(transactions);
  return [...transactions].sort(
    (a, b) =>
      a.sourceSequence! - b.sourceSequence! ||
      (a.sourceSubsequence ?? 0) - (b.sourceSubsequence ?? 0),
  );
}

export function sortConversionsForReview(
  conversions: Conversion[],
): Conversion[] {
  const rank: Record<ConversionStatus, number> = {
    NEEDS_REVIEW: 0,
    READY: 1,
    PROCESSING: 2,
    FAILED: 3,
    REVIEWED: 4,
    EXPORTED: 5,
  };

  return [...conversions].sort((a, b) => rank[a.status] - rank[b.status]);
}

export function nextPendingConversion(
  conversions: Conversion[],
  activeId: string,
): Conversion | undefined {
  return sortConversionsForReview(conversions).find(
    (item) =>
      item.id !== activeId &&
      (item.status === "NEEDS_REVIEW" || item.status === "READY"),
  );
}

export function transactionHasState(
  transaction: Transaction,
  state: TransactionFieldState,
): boolean {
  if (state === "SUSPECTED" && transaction.isDeleted) return false;
  if (state === "EDITED" && transaction.isDeleted) return true;
  return Object.values(transaction.fieldStates).includes(state);
}

export function filterTransactions(
  transactions: Transaction[],
  filter: TransactionFilter,
): Transaction[] {
  if (filter === "ALL") return transactions;
  return transactions.filter((transaction) =>
    transactionHasState(transaction, filter),
  );
}

function isValidDate(value: string): boolean {
  const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
  if (!match) return false;
  const [, day, month, year] = match.map(Number);
  const date = new Date(year, month - 1, day);
  return (
    date.getFullYear() === year &&
    date.getMonth() === month - 1 &&
    date.getDate() === day
  );
}

export function parseBrazilianCurrency(value: string): number | null {
  if (!value.trim()) return null;
  const normalized = value.replace(/\./g, "").replace(",", ".");
  if (!/^-?\d+(\.\d{1,2})?$/.test(normalized)) return Number.NaN;
  return Number(normalized);
}

export function validateTransactionField(
  field: TransactionField,
  value: string,
): string | null {
  if (field === "date") {
    return isValidDate(value)
      ? null
      : "Informe uma data válida no formato DD/MM/AAAA.";
  }
  if (field === "description") {
    return value.trim().length >= 2 ? null : "Informe uma descrição.";
  }
  const parsed = parseBrazilianCurrency(value);
  return Number.isNaN(parsed) ? "Informe um valor monetário válido." : null;
}

export function formatCurrency(value: number | null): string {
  if (value === null) return "";
  return value.toLocaleString("pt-BR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

export function countSuspected(transactions: Transaction[]): number {
  return transactions.filter((item) => transactionHasState(item, "SUSPECTED"))
    .length;
}

export function countEdited(transactions: Transaction[]): number {
  return transactions.filter((item) => transactionHasState(item, "EDITED"))
    .length;
}
