export const documentReviewWarnings = new Set([
  "partial_statement",
  "document_period_unavailable",
  "printed_summary_balance_mismatch",
  "financial_validation_failed",
]);

export function fieldReviewWarnings(warnings: readonly string[]): string[] {
  const hasDocumentWarning = warnings.some((code) =>
    documentReviewWarnings.has(code),
  );
  return warnings.filter(
    (code) =>
      !documentReviewWarnings.has(code) &&
      !(code === "manual_review_recommended" && hasDocumentWarning),
  );
}

export function requiresDocumentReview(conversion: Conversion): boolean {
  return Boolean(
    conversion.partialStatement ||
    conversion.statementBalanceCheck ||
    conversion.reviewWarnings?.some((code) => documentReviewWarnings.has(code)),
  );
}

export function reviewWarningText(warnings: readonly string[]): string {
  if (warnings.includes("balance_consistency_failed"))
    return "Saldo não confere. Confira o valor deste lançamento e os saldos de referência no PDF.";
  return "Confira a data, a descrição e o valor deste lançamento com o PDF antes de marcar como revisado.";
}
import type { Conversion } from "./workspace";
