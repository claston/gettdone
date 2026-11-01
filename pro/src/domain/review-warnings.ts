export const documentReviewWarnings = new Set([
  "partial_statement",
  "document_period_unavailable",
  "statement_blocks_not_contiguous",
  "printed_summary_balance_mismatch",
  "financial_validation_failed",
  "ocr_control_confidence_unconfirmed",
  "ocr_controls_unavailable",
  "ocr_structure_review_required",
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
  const fields = fieldReviewWarnings(warnings);
  if (fields.length && fields.every((code) => ocrFieldWarnings.has(code))) {
    const date = fields.includes("ocr_date_confidence_unconfirmed");
    const amount = fields.includes("ocr_amount_confidence_unconfirmed");
    return `Confiança da leitura abaixo do limite. Confira ${date && amount ? "a data e o valor" : date ? "a data" : "o valor"} no PDF.`;
  }
  return "Confira a data, a descrição e o valor deste lançamento com o PDF antes de marcar como revisado.";
}

export const ocrFieldWarnings = new Set([
  "ocr_date_confidence_unconfirmed",
  "ocr_amount_confidence_unconfirmed",
]);

export function reviewFields(
  warnings: readonly string[],
  amount: number,
): TransactionField[] {
  const fields = new Set<TransactionField>();
  for (const code of fieldReviewWarnings(warnings)) {
    if (code === "balance_consistency_failed") fields.add("balance");
    else if (code === "ocr_date_confidence_unconfirmed") fields.add("date");
    else if (code === "ocr_amount_confidence_unconfirmed")
      fields.add(amount >= 0 ? "credit" : "debit");
    else {
      fields.add("description");
      fields.add(amount >= 0 ? "credit" : "debit");
    }
  }
  return [...fields];
}

import type { Conversion, TransactionField } from "./workspace";
