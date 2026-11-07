import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";

// Exercise the actual free converter function without its upload/session DOM.
const source = readFileSync(
  resolve(process.cwd(), "../frontend/ofx-convert.js"),
  "utf8",
);
const start = source.indexOf("  function isNoActivityStatement(analysis) {");
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
  it("shows explicit guidance when legacy recommendation metrics are absent", () => {
    expect(
      collect({
        review_guidance: {
          category: "financial_discrepancy",
          title: "Divergência financeira encontrada",
          message: "Saldo final divergente.",
        },
      }),
    ).toEqual(["Saldo final divergente."]);
  });
  it("explains incomplete validation instead of a generic suspected error", () => {
    const result = collect({
      ...statement(),
      review_guidance: {
        category: "validation_incomplete",
        title: "Validação automática incompleta",
        message:
          "164 verificações entre saldos conferiram. A cobertura do período ainda precisa de conferência.",
      },
    });
    expect(result).toHaveLength(1);
    expect(result[0]).toContain("164 verificações");
  });

  it("keeps a balance discrepancy even with informational guidance", () => {
    const result = collect({
      ...statement({ balance_consistency_failed: 1 }),
      review_guidance: {
        category: "information",
        title: "Referências compartilhadas",
        message: "Controles conferiram.",
      },
    });
    expect(result.join(" ")).toContain("inconsistências");
    expect(result.join(" ")).not.toContain("Controles conferiram");
  });
  it.each([2, 4])(
    "does not label a confirmed empty %s-page statement as partial",
    (pageCount) => {
      expect(
        collect({
          ...statement({
            statement_no_activity: 1,
            selected_parser: "explicit_no_activity",
            page_count: pageCount,
            export_recommendation: "",
          }),
          transactions_total: 0,
          layout_inference_name: "generic_statement_ptbr",
        }),
      ).toHaveLength(0);
    },
  );

  it("preserves explicit warnings on confirmed empty statements", () => {
    expect(
      Array.from(
        collect({
          ...statement({
            statement_no_activity: 1,
            selected_parser: "explicit_no_activity",
            page_count: 2,
            canonical_warning_types_list: ["financial_validation_failed"],
          }),
          transactions_total: 0,
          layout_inference_name: "generic_statement_ptbr",
        }),
      ),
    ).toEqual(["finding:financial_validation_failed"]);
  });

  it("keeps the partial-reading heuristic when absence of activity is not confirmed", () => {
    const warnings = collect({
      ...statement({ page_count: 2, export_recommendation: "" }),
      transactions_total: 0,
      layout_inference_name: "generic_statement_ptbr",
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("Leitura parcial detectada");
  });

  it("keeps manual review alongside an informational receipt attachment", () => {
    const warnings = collect({
      ...statement(),
      document_parts: [
        {
          document_type: "bank_receipt",
          pages: [2],
          included_in_statement: false,
          requires_review: false,
        },
      ],
    });
    expect(warnings).toHaveLength(2);
    expect(warnings[0]).toContain("Páginas 2: comprovantes");
    expect(warnings[1]).toContain("recomendação de revisão manual");
  });

  it("does not add generic review for an attachment without a backend recommendation", () => {
    const warnings = collect({
      ...statement({ export_recommendation: "safe_to_export" }),
      document_parts: [
        {
          document_type: "bank_receipt",
          pages: [2],
          included_in_statement: false,
          requires_review: false,
        },
      ],
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("Páginas 2: comprovantes");
  });

  it("keeps a concrete attachment review instruction without a generic duplicate", () => {
    const warnings = collect({
      ...statement(),
      document_parts: [
        {
          document_type: "unknown",
          pages: [2],
          included_in_statement: false,
          requires_review: true,
        },
      ],
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("Confira essas páginas no PDF original");
  });

  it("preserves informational attachments and specific findings without generic duplication", () => {
    const warnings = collect({
      ...statement({
        canonical_warning_types_list: ["financial_validation_failed"],
      }),
      document_parts: [
        {
          document_type: "bank_receipt",
          pages: [2],
          included_in_statement: false,
          requires_review: false,
        },
      ],
    });
    expect(warnings).toHaveLength(2);
    expect(warnings[1]).toBe("finding:financial_validation_failed");
  });

  it.each(["high", "medium", "low"])(
    "does not treat %s confidence alone as a review recommendation",
    (band) => {
      expect(
        collect(
          statement({ confidence_band: band, export_recommendation: "" }),
        ),
      ).toHaveLength(0);
    },
  );

  it("shows an explicit review recommendation even without specific findings", () => {
    const warnings = collect(statement());
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("recomendação de revisão manual");
  });

  it("shows the legacy review insight when PDF metrics are absent", () => {
    const warnings = collect({
      insights: [{ type: "pdf_export_review_recommended" }],
    });
    expect(warnings).toHaveLength(1);
    expect(warnings[0]).toContain("recomendação de revisão manual");
  });

  it("keeps OCR document findings without repeating the general recommendation", () => {
    expect(
      Array.from(
        collect(
          statement({
            canonical_warning_types_list: [
              "ocr_control_confidence_unconfirmed",
              "ocr_structure_review_required",
            ],
          }),
        ),
      ),
    ).toEqual([
      "finding:ocr_control_confidence_unconfirmed",
      "finding:ocr_structure_review_required",
    ]);
  });

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
