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
