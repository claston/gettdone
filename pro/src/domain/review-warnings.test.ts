import { expect, it } from "vitest";
import {
  reviewWarningText,
  reviewFields,
  fieldReviewWarnings,
  documentReviewWarnings,
} from "./review-warnings";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";

it("Free renderiza 42 linhas sem alertas repetidos e conserva uma divergência localizada", () => {
  const source = readFileSync(
    resolve(process.cwd(), "../frontend/ofx-convert.js"),
    "utf8",
  );
  const rows = Array.from({ length: 42 }, (_, i) => ({
    rowId: `row_${i}`,
    description: `Movimento ${i}`,
    amount: 10,
    warning_types: ["manual_review_recommended"],
  }));
  const reviewRows = document.createElement("tbody");
  const render = runInNewContext(
    `${source.slice(
      source.indexOf("  function renderRows()"),
      source.indexOf("  function mapWarningTypeLabel("),
    )}; renderRows;`,
    {
      state: { previewRows: rows },
      reviewRows,
      addRowBtn: null,
      syncRowEditor: () => {},
      financialDocument: () => null,
      isOpeningBalanceRow: () => false,
      buildDisplayedRunningBalances: () => rows.map(() => null),
      isRowChanged: () => false,
      getCreditAmount: () => 10,
      getDebitAmount: () => null,
      formatCurrency: String,
      formatDate: String,
      escapeAttr: String,
      isDraftRowId: () => false,
      mapWarningTypeExplanation: String,
      mapWarningTypeLabel: String,
    },
  );
  render();
  expect(reviewRows.querySelectorAll("tr")).toHaveLength(42);
  expect(reviewRows.querySelectorAll(".row-has-warning")).toHaveLength(0);
  rows[8].warning_types.push("balance_consistency_failed");
  render();
  expect(reviewRows.querySelectorAll(".row-has-warning")).toHaveLength(1);
  expect(reviewRows.querySelector(".row-has-warning")).toHaveTextContent(
    "Movimento 8",
  );
});

it("mantém a recomendação genérica no documento sem suspeitar de campos", () => {
  expect(reviewFields(["manual_review_recommended"], 10)).toEqual([]);
  expect(
    reviewFields(
      ["manual_review_recommended", "ocr_amount_confidence_unconfirmed"],
      -10,
    ),
  ).toEqual(["debit"]);
  expect(
    reviewFields(
      ["manual_review_recommended", "balance_consistency_failed"],
      10,
    ),
  ).toEqual(["balance"]);
});

it("Free e PRO distinguem avisos do documento de alertas concretos da mesma forma", () => {
  const source = readFileSync(
    resolve(process.cwd(), "../frontend/ofx-convert.js"),
    "utf8",
  );
  const start = source.indexOf("  function fieldReviewWarnings(");
  expect(start).toBeGreaterThan(-1);
  const end = source.indexOf("  function mapWarningTypeLabel(", start);
  const free = runInNewContext(
    `${source.slice(start, end)}; fieldReviewWarnings;`,
  ) as (codes: string[]) => string[];
  for (const code of [
    ...documentReviewWarnings,
    "manual_review_recommended",
    "balance_consistency_failed",
    "ocr_date_confidence_unconfirmed",
    "ocr_amount_confidence_unconfirmed",
    "ambiguous_amount",
    "unknown_warning",
  ]) {
    const warnings = ["manual_review_recommended", code];
    expect(Array.from(free(warnings))).toEqual(fieldReviewWarnings(warnings));
  }
});

it("explica referência compartilhada sem sugerir que o valor está errado", () => {
  const message = reviewWarningText([
    "manual_review_recommended",
    "shared_operation_reference",
  ]);
  expect(message).toContain("não comprova duplicidade");
  expect(message).toContain("preservados");
});

it("prioriza divergência real mesmo com referência compartilhada", () => {
  expect(
    reviewWarningText([
      "shared_operation_reference",
      "balance_consistency_failed",
    ]),
  ).toContain("Saldo não confere");
});
