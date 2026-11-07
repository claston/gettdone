"""Check Free review cards and editing in a real browser using fictional data."""
from __future__ import annotations

import copy
import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    output = ROOT / "tmp/free-editor-preview"
    output.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "frontend")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    scenarios = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for width in (1440, 1024, 768, 390, 320):
                    context = browser.new_context(viewport={"width": width, "height": 844})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    rows = [
                        {"transaction_id": "txn_1", "source_sequence": 1, "date": "2026-10-01",
                         "description": "PIX recebido — Cliente Exemplo", "document_number": "0000123456", "amount": 4500.50},
                        {"transaction_id": "txn_2", "source_sequence": 2, "date": "2026-10-02",
                         "description": "Pagamento de fornecedor com histórico extenso para conferir a quebra de texto na revisão",
                         "document_number": "000ABC-123456789012345678901234567890/2026", "amount": -1240.70},
                        {"transaction_id": "txn_3", "source_sequence": 3, "date": "2026-10-03",
                         "description": "Tarifa bancária", "document_number": None, "amount": -35.90},
                    ]
                    original = copy.deepcopy(rows)
                    analysis = {"analysis_id": "an_free_editor", "file_type": "pdf", "preview_transactions": rows,
                                "transactions_total": 3, "opening_balance": 2000, "closing_balance": 5223.90,
                                "total_inflows": 4500.50, "total_outflows": 1276.60, "net_total": 3223.90}
                    control = {"fail_next": False, "writes": 0}

                    def intercept(route):
                        request = route.request
                        path = urlsplit(request.url).path
                        if request.resource_type not in {"fetch", "xhr"}:
                            if request.url.startswith(base):
                                route.continue_()
                            else:
                                route.abort()
                            return
                        status = 401 if path.endswith(("/auth/me", "/auth/session/refresh")) else 200
                        body = {"quota_limit": 100, "quota_remaining": 99, "batch_max_files": 1}
                        if path.endswith("/banks"):
                            body = {"items": []}
                        if path.endswith("/api/conversions/upload"):
                            body = {**body, "analysis": analysis, "processing_id": analysis["analysis_id"]}
                        if "/convert-edits/" in path:
                            control["writes"] += 1
                            patch = request.post_data_json["edits"][0]
                            if control["fail_next"]:
                                control["fail_next"] = False
                                route.fulfill(status=409, json={"detail": "Confira esta edição e tente salvar novamente."})
                                return
                            row = next(item for item in rows if item["transaction_id"] == patch["row_id"])
                            row.update(date=patch["date"], description=patch["description"], document_number=patch["document_number"])
                            body = analysis
                        route.fulfill(status=status, json=body)

                    page.route("**/*", intercept)
                    page.goto(base + "/ofx-convert.html", wait_until="domcontentloaded")
                    page.locator("#file-input").set_input_files({"name": "exemplo.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                    page.locator("#convert-btn").click()
                    table = page.get_by_role("table", name="Lançamentos extraídos")
                    expect(table).to_be_visible()
                    consent = page.get_by_role("button", name="Recusar opcionais", exact=True)
                    if consent.is_visible():
                        consent.click()
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
                    assert page.locator(".review-table-wrap").evaluate("node => node.scrollWidth <= node.clientWidth + 1"), width
                    before_width = table.bounding_box()["width"]
                    page.get_by_role("button", name="Editar linha", exact=True).first.click()
                    dialog = page.get_by_role("dialog", name="Editar lançamento")
                    expect(dialog).to_be_visible()
                    assert table.bounding_box()["width"] == before_width
                    assert table.locator("input, textarea").count() == 0
                    assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth"), width
                    footer = dialog.locator("footer").bounding_box()
                    assert footer["y"] + footer["height"] <= 845, footer
                    for _ in range(8):
                        page.keyboard.press("Tab")
                        assert dialog.evaluate("node => node.contains(document.activeElement)")
                    dialog.get_by_label("Documento", exact=True).fill("000ABC-01/2")
                    dialog.get_by_label("Histórico", exact=True).fill("PIX recebido — Referência conferida")
                    if width <= 640:
                        page.set_viewport_size({"width": width, "height": 500})
                        page.wait_for_function("document.querySelector('#transaction-editor footer').getBoundingClientRect().bottom <= innerHeight")
                        page.set_viewport_size({"width": width, "height": 844})
                        page.wait_for_function("document.querySelector('#transaction-editor').getBoundingClientRect().top === 0")
                    assert dialog.locator("footer").bounding_box()["y"] + dialog.locator("footer").bounding_box()["height"] <= 845
                    if width in (1440, 390):
                        page.screenshot(path=str(output / ("desktop-editor.png" if width == 1440 else "mobile-editor.png")))
                    dialog.get_by_label("Débito", exact=True).fill("10,00")
                    dialog.get_by_role("button", name="Salvar edição", exact=True).click()
                    expect(dialog.get_by_role("alert")).to_contain_text("Preencha apenas crédito ou débito")
                    assert control["writes"] == 0
                    dialog.get_by_label("Débito", exact=True).fill("")
                    control["fail_next"] = True
                    dialog.get_by_role("button", name="Salvar edição", exact=True).click()
                    expect(dialog.get_by_role("alert")).to_contain_text("tente salvar novamente")
                    expect(dialog.get_by_label("Documento", exact=True)).to_have_value("000ABC-01/2")
                    dialog.get_by_role("button", name="Salvar edição", exact=True).click()
                    expect(dialog).not_to_be_visible()
                    expect(table.locator("tbody tr").first).to_contain_text("000ABC-01/2")
                    page.reload(wait_until="domcontentloaded")
                    expect(table.locator("tbody tr").first).to_contain_text("000ABC-01/2")
                    assert [row["amount"] for row in rows] == [row["amount"] for row in original]
                    assert [row["transaction_id"] for row in rows] == [row["transaction_id"] for row in original]
                    page.get_by_role("button", name="Editar linha", exact=True).first.click()
                    page.get_by_role("dialog").get_by_label("Documento", exact=True).fill("CANCELADO")
                    page.keyboard.press("Escape")
                    expect(dialog).not_to_be_visible()
                    expect(table.locator("tbody tr").first).to_contain_text("000ABC-01/2")
                    assert page.get_by_role("button", name="Editar linha", exact=True).first.evaluate("node => node === document.activeElement")
                    if width in (1440, 390):
                        target = page.locator(".review-table-wrap") if width == 1440 else table.locator("tbody tr").first
                        target.screenshot(path=str(output / ("desktop-review.png" if width == 1440 else "mobile-review.png")))
                    assert not errors, errors
                    scenarios.append({"width": width, "result": "PASS", "no_horizontal_scroll": True, "writes": control["writes"]})
                    context.close()
            finally:
                browser.close()
        summary = {"result": "PASS", "scenarios": scenarios, "artifacts": str(output)}
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == "__main__":
    main()
