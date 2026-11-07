import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { JSDOM } from "jsdom";
import { describe, expect, it, vi } from "vitest";

const html = readFileSync(resolve(process.cwd(), "../frontend/ofx-convert.html"), "utf8");
const script = readFileSync(resolve(process.cwd(), "../frontend/ofx-convert.js"), "utf8");

async function openReviewer({ draft, save } = {}) {
  const dom = new JSDOM(html, { url: "https://ofx.test/ofx-convert.html", runScripts: "outside-only", pretendToBeVisual: true });
  let rows = [{ transaction_id: "txn_1", date: "2026-09-30", description: "PIX original", amount: 100, document_number: "000123", source_sequence: 1 }];
  const edits = [];
  dom.window.fetch = vi.fn(async () => new Response("[]"));
  dom.window.OfxSession = {
    getCurrentUser: vi.fn(async () => ({ email: "owner@example.test", quota_remaining: 10, quota_limit: 50 })),
    request: vi.fn(async (url, init) => {
      if (!url.includes("/convert-edits/") || init?.method !== "POST") return new Response("{}");
      const patch = JSON.parse(init.body).edits[0];
      edits.push(patch);
      if (save) return save(patch);
      const row = patch.action === "delete"
        ? { ...rows[0], is_deleted: true }
        : { ...rows[0], ...patch, amount: patch.credit ?? -patch.debit, is_deleted: false };
      if (patch.action === "insert") rows.unshift({ ...row, transaction_id: "txn_manual" });
      else rows[0] = row;
      return new Response(JSON.stringify({ preview_transactions: rows, transactions_total: rows.length, updated_at: "saved" }));
    }),
  };
  Object.defineProperty(dom.window.performance, "getEntriesByType", { value: () => [{ type: "reload" }] });
  dom.window.sessionStorage.setItem("ofxsimples_ofx_convert_view_state_ofx_v1", JSON.stringify({
    processing_id: "an_editor", analysis: { analysis_id: "an_editor", preview_transactions: rows, transactions_total: 1 },
    preview_rows: rows, original_rows: rows,
    ...(draft ? { editing_row_id: "txn_1", edit_draft: draft } : {}),
  }));
  dom.window.eval(script);
  await vi.waitFor(() => expect(dom.window.document.querySelector('[data-action="edit-row"]')).not.toBeNull());
  return { dom, document: dom.window.document, edits };
}

function fill(dom, field, value) {
  const input = dom.window.document.querySelector(`#transaction-editor [data-edit-field="${field}"]`);
  input.value = value;
  input.dispatchEvent(new dom.window.Event("input", { bubbles: true }));
}

describe("edição do Free fora da tabela", () => {
  it("abre um formulário identificado e cancela sem modificar o lançamento", async () => {
    const { dom, document, edits } = await openReviewer();
    try {
      document.querySelector('[data-action="edit-row"]').click();
      const dialog = document.querySelector("#transaction-editor");
      expect(dialog.open).toBe(true);
      expect(dialog.closest(".table-wrap")).toBeNull();
      expect(document.querySelector("#review-rows input")).toBeNull();
      expect(dialog.querySelector("textarea").value).toBe("PIX original");
      expect(dialog.querySelector('[data-edit-field="credit"]').value).toBe("100,00");
      fill(dom, "description", "Alterado sem salvar");
      dialog.querySelector('[data-action="cancel-row"]').click();
      expect(dialog.open).toBe(false);
      expect(document.querySelector("#review-rows").textContent).toContain("PIX original");
      expect(edits).toHaveLength(0);
      await vi.waitFor(() => expect(document.activeElement.dataset.action).toBe("edit-row"));
    } finally { dom.window.close(); }
  });

  it("salva documento e histórico sem alterar o crédito, fecha e destaca a linha", async () => {
    const { dom, document, edits } = await openReviewer();
    try {
      document.querySelector('[data-action="edit-row"]').click();
      fill(dom, "document_number", "000ABC-01/2");
      fill(dom, "description", "Histórico revisado");
      document.querySelector('#transaction-editor [data-action="save-row"]').click();
      await vi.waitFor(() => expect(document.querySelector("#transaction-editor").open).toBe(false));
      expect(edits[0]).toMatchObject({ row_id: "txn_1", document_number: "000ABC-01/2", description: "Histórico revisado", credit: 100, debit: null });
      expect(document.querySelector("#review-rows .row-changed").textContent).toContain("000ABC-01/2");
    } finally { dom.window.close(); }
  });

  it("mantém o formulário e o rascunho quando o salvamento falha", async () => {
    const { dom, document } = await openReviewer({ save: async () => new Response(JSON.stringify({ detail: "Conflito de edição" }), { status: 409 }) });
    try {
      document.querySelector('[data-action="edit-row"]').click();
      fill(dom, "document_number", "000PENDENTE");
      document.querySelector('#transaction-editor [data-action="save-row"]').click();
      await vi.waitFor(() => expect(document.querySelector("#transaction-editor [role=alert]").textContent).toContain("Conflito de edição"));
      expect(document.querySelector("#transaction-editor").open).toBe(true);
      expect(document.querySelector('#transaction-editor [data-edit-field="document_number"]').value).toBe("000PENDENTE");
      expect(document.querySelector('#transaction-editor [data-action="save-row"]').disabled).toBe(false);
    } finally { dom.window.close(); }
  });

  it("inclui pelo mesmo formulário e descarta uma inclusão cancelada", async () => {
    const { dom, document, edits } = await openReviewer();
    try {
      document.querySelector("#add-row-btn").click();
      expect(document.querySelector("#transaction-editor-title").textContent).toBe("Novo lançamento");
      document.querySelector('#transaction-editor [data-action="cancel-row"]').click();
      expect(document.querySelectorAll("#review-rows tr")).toHaveLength(1);
      document.querySelector("#add-row-btn").click();
      fill(dom, "date", "2026-09-29");
      fill(dom, "description", "Lançamento manual");
      fill(dom, "document_number", "000MANUAL");
      fill(dom, "debit", "25,50");
      document.querySelector('#transaction-editor [data-action="save-row"]').click();
      await vi.waitFor(() => expect(edits).toHaveLength(1));
      expect(edits[0]).toMatchObject({ action: "insert", document_number: "000MANUAL", debit: 25.5, credit: null });
      await vi.waitFor(() => expect(document.querySelector("#transaction-editor").open).toBe(false));
    } finally { dom.window.close(); }
  });

  it("reabre rascunhos antigos no formulário e permite cancelar por Escape", async () => {
    const { dom, document } = await openReviewer({ draft: { date: "2026-09-30", description: "Rascunho recuperado", credit: "100,00", debit: "" } });
    try {
      const dialog = document.querySelector("#transaction-editor");
      expect(dialog.open).toBe(true);
      expect(dialog.querySelector('[data-edit-field="document_number"]').value).toBe("000123");
      expect(dialog.querySelector("textarea").value).toBe("Rascunho recuperado");
      dialog.dispatchEvent(new dom.window.Event("cancel", { cancelable: true }));
      expect(dialog.open).toBe(false);
      expect(document.querySelector("#review-rows").textContent).toContain("PIX original");
    } finally { dom.window.close(); }
  });

  it("bloqueia duplo envio e fechamento enquanto aguarda a resposta", async () => {
    let finish;
    const { dom, document, edits } = await openReviewer({ save: () => new Promise((resolve) => { finish = resolve; }) });
    try {
      document.querySelector('[data-action="edit-row"]').click();
      const dialog = document.querySelector("#transaction-editor");
      const button = dialog.querySelector('[data-action="save-row"]');
      button.click();
      button.click();
      await vi.waitFor(() => expect(edits).toHaveLength(1));
      expect(button.disabled).toBe(true);
      dialog.dispatchEvent(new dom.window.Event("cancel", { cancelable: true }));
      expect(dialog.open).toBe(true);
      finish(new Response(JSON.stringify({ detail: "Falha temporária" }), { status: 500 }));
      await vi.waitFor(() => expect(button.disabled).toBe(false));
      expect(dialog.open).toBe(true);
    } finally { dom.window.close(); }
  });

  it("mantém apagar e restaurar acessíveis nas ações secundárias", async () => {
    const { dom, document, edits } = await openReviewer();
    try {
      const menu = document.querySelector(".row-actions-menu");
      menu.querySelector("summary").click();
      expect(menu.open).toBe(true);
      document.body.click();
      expect(menu.open).toBe(false);
      menu.querySelector("summary").click();
      menu.querySelector('[data-action="delete-row"]').click();
      await vi.waitFor(() => expect(document.querySelector("#review-rows .row-deleted")).not.toBeNull());
      expect(edits[0].action).toBe("delete");
      document.querySelector('[data-action="revert-row"]').click();
      await vi.waitFor(() => expect(document.querySelector("#review-rows .row-deleted")).toBeNull());
      expect(edits[1]).toMatchObject({ action: "restore", document_number: "000123", credit: 100 });
    } finally { dom.window.close(); }
  });
});
