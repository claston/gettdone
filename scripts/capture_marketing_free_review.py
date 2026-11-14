"""Capture the actual Free editor with fictional data, without external requests."""
from __future__ import annotations

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "marketing/templates/ofx-content-series/assets/free-review-editor.png"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "frontend")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    analysis = {
        "analysis_id": "an_marketing_free_example", "file_type": "pdf", "transactions_total": 1,
        "opening_balance": 1000, "closing_balance": 680,
        "total_inflows": 0, "total_outflows": 320, "net_total": -320,
        "preview_transactions": [{
            "transaction_id": "txn_fictional", "source_sequence": 1, "date": "2026-11-08",
            "description": "Pagamento de fornecedor — NF 1234", "document_number": "0001234", "amount": -320,
        }],
    }

    def intercept(route):
        request = route.request
        path = urlsplit(request.url).path
        if request.resource_type not in {"fetch", "xhr"}:
            if request.url.startswith(base + "/"):
                route.continue_()
            else:
                route.abort()
            return
        status = 401 if path.endswith(("/auth/me", "/auth/session/refresh")) else 200
        body = {"quota_limit": 3, "quota_remaining": 2, "batch_max_files": 1, "direct_batch_enabled": False}
        if path.endswith("/banks"):
            body = {"items": []}
        if path.endswith("/api/conversions/upload"):
            body = {**body, "analysis": analysis, "processing_id": analysis["analysis_id"]}
        if "/convert-edits/" in path:
            raise AssertionError("The screenshot must not submit an edit")
        route.fulfill(status=status, json=body)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 390, "height": 600}, device_scale_factor=2, locale="pt-BR")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.route("**/*", intercept)
                page.goto(base + "/converter-pdf-para-ofx.html", wait_until="domcontentloaded")
                page.get_by_role("button", name="Recusar opcionais", exact=True).click()
                page.locator("#file-input").set_input_files({
                    "name": "extrato-ficticio.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF",
                })
                page.locator("#convert-btn").click()
                expect(page.locator("#review-section")).to_be_visible()
                page.get_by_role("button", name="Editar linha", exact=True).click()
                dialog = page.get_by_role("dialog", name="Editar lançamento")
                expect(dialog).to_be_visible()
                expect(dialog.get_by_label("Crédito", exact=True)).to_have_value("")
                expect(dialog.get_by_label("Débito", exact=True)).to_have_value("320,00")
                expect(dialog.get_by_label("Documento", exact=True)).to_have_value("0001234")
                expect(dialog.get_by_role("button", name="Salvar edição", exact=True)).to_have_text("Salvar alterações")
                page.get_by_role("button", name="Fechar edição").focus()
                assert dialog.evaluate("node => node.scrollWidth <= node.clientWidth")
                assert dialog.locator("footer").bounding_box()["y"] + dialog.locator("footer").bounding_box()["height"] <= 601
                dialog.screenshot(path=str(OUTPUT), animations="disabled")
                assert not errors, errors
                context.close()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    print(f"Free editor screenshot saved: {OUTPUT}")


if __name__ == "__main__":
    main()
