import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const html = readFileSync(resolve(process.cwd(), "../frontend/ofx-convert.html"), "utf8");
const script = readFileSync(resolve(process.cwd(), "../frontend/ofx-convert.js"), "utf8");

describe("avisos objetivos dos extratos recuperados", () => {
  it.each([
    ["date_year_from_statement_period", "período impresso"],
    ["native_amount_recovered_from_balances", "dígitos visíveis"],
    ["scheduled_transactions_excluded", "agendados"],
    ["pending_deposits_excluded", "bloqueados"],
    ["ocr_duplicate_sign_normalized", "sinal duplicado"],
    ["amount_sign_from_printed_balances", "saldos impressos"],
    ["opening_balance_ambiguous", "saldos iniciais diferentes"],
    ["ocr_sign_stroke_overlap_confirmed", "caractere junto ao sinal"],
    ["ocr_trailing_mark_confirmed", "marca após o valor"],
    ["printed_totals_differ_from_extracted_movements", "soma dos movimentos"],
  ])("explica %s no revisor", async (warning, expected) => {
    const dom = new JSDOM(html, { url: "https://ofx.test/ofx-convert.html", runScripts: "outside-only" });
    const rows = [{ date: "2026-10-01", description: "EXEMPLO", amount: 20 }];
    dom.window.fetch = vi.fn(async () => new Response("[]"));
    dom.window.OfxSession = { getCurrentUser: vi.fn(async () => ({ email: "owner@test", quota_remaining: 10 })) };
    Object.defineProperty(dom.window.performance, "getEntriesByType", { value: () => [{ type: "reload" }] });
    dom.window.sessionStorage.setItem("ofxsimples_ofx_convert_view_state_ofx_v1", JSON.stringify({
      processing_id: "an_followup", analysis: {
        analysis_id: "an_followup", preview_transactions: rows, transactions_total: 1,
        pdf_processing_metrics: {
          canonical_warning_types_list: [warning],
          opening_balance_ambiguous: Number(warning === "opening_balance_ambiguous"),
          opening_balance_ambiguity_reason: "conflicting_printed_balances",
        },
      }, preview_rows: rows, original_rows: rows,
    }));
    try {
      dom.window.eval(script);
      await vi.waitFor(() => expect(dom.window.document.body.textContent).toContain(expected));
      if (warning === "opening_balance_ambiguous") {
        expect(dom.window.document.body.textContent).not.toContain("pode incluir aplicações");
      }
    } finally {
      dom.window.close();
    }
  });
});
