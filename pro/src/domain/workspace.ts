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
  fieldStates: Record<TransactionField, TransactionFieldState>;
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
  getBatch(batchId: string): Promise<Batch>;
  saveTransactions(
    conversionId: string,
    transactions: Transaction[],
  ): Promise<void>;
  markReviewed(conversionId: string): Promise<void>;
  retryConversion(conversionId: string): Promise<void>;
  exportBatch(batchId: string): Promise<ExportResult>;
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
