import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

// Exercise the actual free converter function without its upload/session DOM.
const source = readFileSync(
  resolve(process.cwd(), "../frontend/ofx-convert.js"),
  "utf8",
);
const start = source.indexOf("  function collectReviewWarnings(analysis) {");
const end = source.indexOf("  function toPositiveMoneyString(", start);
if (start < 0 || end < 0)
  throw new Error("Free converter warning function missing");
const context = {
  isPayablesReport: () => false,
  mapWarningTypeExplanation: (code: string) => `finding:${code}`,
};
const collect = runInNewContext(
  `${source.slice(start, end)}; collectReviewWarnings;`,
  context,
) as (analysis: Record<string, unknown>) => string[];

function statement(metrics: Record<string, unknown> = {}) {
  return {
    transactions_total: 24,
    layout_inference_name:
      "banco_safra_extrato_movimentacao_lancamentos_realizados_v1",
    pdf_processing_metrics: {
      selected_parser: "tabular",
      confidence_band: "medium",
      export_recommendation: "review_recommended",
      balance_consistency_failed: 0,
      ...metrics,
    },
  };
}

describe("free converter review findings", () => {
  it.each(["high", "medium", "low"])(
    "shows no warning for %s confidence without findings, including old responses",
    (band) => {
      expect(collect(statement({ confidence_band: band }))).toHaveLength(0);
    },
  );

  it("keeps the specific balance finding without a duplicate generic warning", () => {
    const warnings = collect(statement({ balance_consistency_failed: 2 }));
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("2 lançamento(s)");
    expect(warnings[0]).toContain("não bateram no cálculo de saldo");
  });

  it("preserves unreadable rows and ambiguous opening balances", () => {
    const warnings = collect(
      statement({
        ocr_unresolved_rows: 3,
        opening_balance_ambiguous: 1,
      }),
    );
    expect(warnings).toHaveLength(2);
    expect(warnings[0]).toContain("3 linha(s)");
    expect(warnings[1]).toContain("saldo inicial");
  });

  it("preserves document incompleteness and financial validation warnings", () => {
    const warnings = collect(
      statement({
        canonical_warning_types_list: [
          "partial_statement",
          "financial_validation_failed",
        ],
      }),
    );
    expect(Array.from(warnings)).toEqual([
      "finding:partial_statement",
      "finding:financial_validation_failed",
    ]);
  });

  it("explains unavailable document periods without a financial error", () => {
    expect(
      Array.from(
        collect(
          statement({
            canonical_warning_types_list: ["document_period_unavailable"],
          }),
        ),
      ),
    ).toEqual(["finding:document_period_unavailable"]);
  });

  it("explains independent statement blocks without inventing a global balance", () => {
    expect(
      Array.from(
        collect(
          statement({
            canonical_warning_types_list: ["statement_blocks_not_contiguous"],
          }),
        ),
      ),
    ).toEqual(["finding:statement_blocks_not_contiguous"]);
  });
});
