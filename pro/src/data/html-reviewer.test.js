import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const html = readFileSync(
  resolve(process.cwd(), "../frontend/ofx-convert.html"),
  "utf8",
);
const script = readFileSync(
  resolve(process.cwd(), "../frontend/ofx-convert.js"),
  "utf8",
);

describe("revisor HTML do OFX Simples", () => {
  it.each([
    "receivables_report",
    "investment_statement",
    "fiscal_summary",
    "investment_period_summary",
  ])(
    "preserva a semântica e os formatos ao reabrir %s",
    async (documentType) => {
      const dom = new JSDOM(html, {
        url: "https://ofx.test/ofx-convert.html",
        runScripts: "outside-only",
        pretendToBeVisual: true,
      });
      const receipt = documentType === "receivables_report";
      const summary = documentType === "investment_period_summary";
      const rows = summary
        ? []
        : [
            {
              date: "2026-09-01",
              description: "Documento de exemplo",
              amount: 100,
            },
          ];
      const report = {
        document_type: documentType,
        notice: "Campos de origem preservados.",
        validations: [],
        events: [
          {
            description: "Documento de exemplo",
            event_type: receipt ? "receipt" : "fiscal_document",
            amount: "100.00",
            status: "issued",
            reference: "0001",
            source_refs: [{ page: 1, line: 4 }],
          },
        ],
        export: {
          available_formats: receipt ? ["csv", "xlsx", "ofx"] : ["csv", "xlsx"],
          requires_destination_account: receipt,
          reason: "OFX indisponível.",
        },
      };
      if (summary) {
        report.document_type = "investment_statement";
        report.events = [];
        report.observations = [
          {
            dates: { period_end: "2025-01-31" },
            amounts: { applications: "200.00" },
            attributes: { label: "Aplicações" },
            source_refs: [{ page: 1, line: 8 }],
          },
          {
            dates: { period_end: "2025-01-31" },
            amounts: { net_redemptions: "100.00" },
            attributes: { label: "Resgates líquidos" },
            source_refs: [{ page: 1, line: 9 }],
          },
        ];
      }
      dom.window.fetch = vi.fn(
        async () =>
          new Response(
            JSON.stringify({ banks: [{ code: "237", label: "Bradesco" }] }),
          ),
      );
      dom.window.OfxSession = {
        getCurrentUser: vi.fn(async () => ({
          email: "owner@test",
          quota_remaining: 10,
          quota_limit: 50,
        })),
        request: vi.fn(
          async () =>
            new Response(JSON.stringify({ detail: "Resposta de teste" }), {
              status: 400,
            }),
        ),
      };
      Object.defineProperty(dom.window.performance, "getEntriesByType", {
        value: () => [{ type: "reload" }],
      });
      dom.window.sessionStorage.setItem(
        "ofxsimples_ofx_convert_view_state_ofx_v1",
        JSON.stringify({
          processing_id: "an_report",
          analysis: {
            analysis_id: "an_report",
            document_type: documentType,
            financial_document: report,
            preview_transactions: rows,
            total_inflows: receipt ? 100 : 0,
            total_outflows: 0,
          },
          preview_rows: rows,
          original_rows: rows,
        }),
      );
      try {
        dom.window.eval(script);
        await vi.waitFor(() =>
          expect(
            dom.window.document.querySelector("#kpis").textContent,
          ).toContain(report.notice),
        );
        const document = dom.window.document;
        const headers = [
          ...document.querySelectorAll("#review-section thead th"),
        ].map((cell) => cell.textContent);
        expect(headers).toContain("Valor");
        expect(headers).not.toContain("Crédito");
        expect(document.querySelector("#closing-balance-input")).toBeNull();
        expect(document.querySelector('[data-action="edit-row"]')).toBeNull();
        expect(document.querySelector("#add-row-btn").hidden).toBe(true);
        if (summary) {
          expect(document.querySelector("#kpis").textContent).toContain(
            "Campos do resumo",
          );
          expect(document.querySelector("#kpis").textContent).toContain(
            "Aplicações no período",
          );
          expect(
            document.querySelector("#review-section tbody").textContent,
          ).toContain("Resgates líquidos");
          expect(
            document.querySelector("#review-section tbody").textContent,
          ).toContain("Sem data de movimento individual");
        }
        const ofxButton = document.querySelector("#download-ofx-btn");
        expect(ofxButton.disabled).toBe(!receipt);
        expect(document.querySelector("#download-excel-btn").disabled).toBe(
          false,
        );
        if (receipt) {
          for (const [id, value] of [
            ["bank-code-select", "237"],
            ["bank-branch-input", "1234"],
            ["account-number-input", "56789"],
          ]) {
            const input = document.getElementById(id);
            input.value = value;
            input.dispatchEvent(
              new dom.window.Event("change", { bubbles: true }),
            );
          }
          ofxButton.click();
          const call = await vi.waitFor(() => {
            const result = dom.window.OfxSession.request.mock.calls.find(
              ([url]) => url.includes("/convert-report/"),
            );
            expect(result).toBeDefined();
            return result;
          });
          expect(call[0]).toContain("account_number=56789");
          expect(call[0]).not.toContain("closing_balance");
          expect(
            dom.window.OfxSession.request.mock.calls.some(([url]) =>
              url.includes("/convert-edits/"),
            ),
          ).toBe(false);
        }
      } finally {
        dom.window.close();
      }
    },
  );
  it("informa extrato sem movimentação e mantém o download habilitado após reabrir", async () => {
    const dom = new JSDOM(html, {
      url: "https://ofx.test/ofx-convert.html",
      runScripts: "outside-only",
      pretendToBeVisual: true,
    });
    dom.window.fetch = vi.fn(async () => new Response("[]"));
    dom.window.OfxSession = {
      getCurrentUser: vi.fn(async () => ({
        email: "owner@test",
        quota_remaining: 10,
        quota_limit: 50,
      })),
      request: vi.fn(async () => new Response("{}")),
    };
    Object.defineProperty(dom.window.performance, "getEntriesByType", {
      value: () => [{ type: "reload" }],
    });
    dom.window.sessionStorage.setItem(
      "ofxsimples_ofx_convert_view_state_ofx_v1",
      JSON.stringify({
        processing_id: "an_empty",
        analysis: {
          analysis_id: "an_empty",
          transactions_total: 0,
          preview_transactions: [],
          pdf_processing_metrics: { statement_no_activity: 1, page_count: 1 },
        },
        preview_rows: [],
        original_rows: [],
      }),
    );
    try {
      dom.window.eval(script);
      await vi.waitFor(() =>
        expect(
          dom.window.document.querySelector("#kpis").textContent,
        ).toContain("Extrato sem movimentação no período"),
      );
      expect(dom.window.document.querySelector("#kpis").textContent).toContain(
        "Você pode baixar o OFX sem lançamentos",
      );
      const download = dom.window.document.querySelector(
        "#download-ofx-btn, #download-report-btn",
      );
      expect(download.disabled).toBe(false);
      expect(
        dom.window.document.querySelector("#opening-balance-input").value,
      ).toBe("");
      expect(
        dom.window.document.querySelector("#closing-balance-input").value,
      ).toBe("");
      dom.window.OfxSession.request.mockResolvedValueOnce(
        new Response("{}", { status: 409 }),
      );
      download.click();
      const settingsCall = await vi.waitFor(() => {
        const call = dom.window.OfxSession.request.mock.calls.find(
          ([url, init]) =>
            url.includes("/convert-edits/") && init?.method === "POST",
        );
        expect(call).toBeDefined();
        return call;
      });
      const settings = JSON.parse(settingsCall[1].body);
      expect(settings.opening_balance).toBeNull();
      expect(settings.closing_balance).toBeNull();
      expect(
        dom.window.document
          .querySelector("#download-section")
          .classList.contains("hidden"),
      ).toBe(false);
    } finally {
      dom.window.close();
    }
  });
  it.each([true, false])(
    "preserva edição e saldos com ordem de origem disponível: %s",
    async (newConversion) => {
      const dom = new JSDOM(html, {
        url: "https://ofx.test/ofx-convert.html",
        runScripts: "outside-only",
        pretendToBeVisual: true,
      });
      const rows = [
        {
          date: "2026-04-30",
          description: "SEGUNDO",
          amount: 10,
          running_balance: 100,
          ...(newConversion
            ? { transaction_id: "txn-second", source_sequence: 2 }
            : {}),
        },
        {
          date: "2026-04-30",
          description: "PRIMEIRO",
          amount: -10,
          running_balance: 90,
          ...(newConversion
            ? { transaction_id: "txn-first", source_sequence: 1 }
            : {}),
        },
      ];
      const saved = {
        analysis_id: "an_html",
        preview_transactions: rows,
        opening_balance: 100,
        transactions_total: 2,
      };
      const writes = [];
      dom.window.fetch = vi.fn(async () => new Response("[]"));
      dom.window.OfxSession = {
        getCurrentUser: vi.fn(async () => ({
          email: "owner@test",
          quota_remaining: 10,
          quota_limit: 50,
        })),
        request: vi.fn(async (_url, init) => {
          if (init?.method !== "POST") return new Response("{}");
          const patch = JSON.parse(init.body).edits[0];
          writes.push(patch);
          const index = rows.findIndex(
            (row, i) => (row.transaction_id ?? `row_${i + 1}`) === patch.row_id,
          );
          if (index >= 0) rows[index].description = patch.description;
          return new Response(
            JSON.stringify({
              ...saved,
              preview_transactions: rows,
              updated_at: "v2",
            }),
          );
        }),
      };
      Object.defineProperty(dom.window.performance, "getEntriesByType", {
        value: () => [{ type: "reload" }],
      });
      dom.window.sessionStorage.setItem(
        "ofxsimples_ofx_convert_view_state_ofx_v1",
        JSON.stringify({
          processing_id: "an_html",
          analysis: saved,
          preview_rows: rows,
          original_rows: rows,
        }),
      );
      try {
        dom.window.eval(script);
        const document = dom.window.document;
        await vi.waitFor(() =>
          expect(document.querySelectorAll("#review-rows tr")).toHaveLength(2),
        );
        const displayed = [...document.querySelectorAll("#review-rows tr")];
        expect(displayed[0].textContent).toContain(
          newConversion ? "PRIMEIRO" : "SEGUNDO",
        );
        expect(displayed[0].textContent).toContain(
          newConversion ? "90,00" : "100,00",
        );
        expect(
          document.querySelector("#review-section").textContent,
        ).not.toMatch(/subsequência|source_sequence|sequência interna/i);
        displayed[0].querySelector('[data-action="edit-row"]').click();
        const description = document.querySelector(
          '[data-edit-field="description"]',
        );
        description.value = "CORRIGIDO";
        description.dispatchEvent(
          new dom.window.Event("input", { bubbles: true }),
        );
        document.querySelector('[data-action="save-row"]').click();
        await vi.waitFor(() => expect(writes).toHaveLength(1));
        expect(writes[0].row_id).toBe(newConversion ? "txn-first" : "row_1");
        await vi.waitFor(() =>
          expect(
            document.querySelector("#review-rows tr").textContent,
          ).toContain("CORRIGIDO"),
        );
        document.querySelector("#add-row-btn").click();
        expect(
          document.querySelector('[data-action="cancel-row"]'),
        ).not.toBeNull();
        document.querySelector('[data-action="cancel-row"]').click();
        expect(writes).toHaveLength(1);
        expect(document.querySelectorAll("#review-rows tr")).toHaveLength(2);
      } finally {
        dom.window.close();
      }
    },
  );
});
