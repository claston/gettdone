"""Offline browser check of Free review guidance and document warnings."""

import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

from smoke_playwright_navigation import pick_free_port, wait_for_http_ready

ROOT = Path(__file__).resolve().parents[1]
GUIDANCE = "Revise os lançamentos e confira os dados com o documento original antes de baixar o arquivo."


def main():
    base = f"http://127.0.0.1:{pick_free_port()}"
    port = urlparse(base).port
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
                              cwd=ROOT / "frontend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_http_ready(base)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width in (390, 1440):
                for filename in ("ofx-convert.html", "converter-pdf-para-ofx.html", "converter-pdf-para-excel.html"):
                    context = browser.new_context(viewport={"width": width, "height": 900})
                    page = context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    analysis = {
                        "analysis_id": "an_review_smoke", "file_type": "pdf", "transactions_total": 1,
                        "total_inflows": 10, "total_outflows": 0, "net_total": 10,
                        "opening_balance": 0, "closing_balance": 10,
                        "preview_transactions": [{"date": "2026-10-01", "description": "Crédito sintético",
                                                  "amount": 10, "type": "inflow", "warning_types": []}],
                        "pdf_processing_metrics": {"export_recommendation": "safe_to_export"},
                    }

                    def route(request):
                        path = urlparse(request.request.url).path
                        if request.request.resource_type not in {"fetch", "xhr"} and request.request.url.startswith(base):
                            request.continue_()
                            return
                        headers = {"access-control-allow-origin": base, "access-control-allow-credentials": "true",
                                   "access-control-allow-headers": "content-type,x-csrf-token,x-ofx-pro-client",
                                   "access-control-allow-methods": "GET,POST,OPTIONS"}
                        status = 401 if path.endswith(("/auth/me", "/auth/session/refresh")) else 200
                        body = {"quota_limit": 100, "quota_remaining": 99, "direct_batch_enabled": False, "batch_max_files": 1}
                        if path.endswith("/banks"):
                            body = {"items": []}
                        if path.endswith("/api/conversions/upload"):
                            body = {**body, "analysis": analysis, "processing_id": analysis["analysis_id"]}
                        request.fulfill(status=status, headers=headers, content_type="application/json", body=json.dumps(body))

                    page.route("**/*", route)
                    page.goto(base + "/" + filename, wait_until="domcontentloaded")
                    page.locator("#file-input").set_input_files({
                        "name": "synthetic.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                    page.locator("#convert-btn").click()
                    expect(page.locator("#review-section")).to_be_visible()
                    expect(page.locator("#review-section").get_by_text(GUIDANCE, exact=True)).to_be_visible()
                    expect(page.locator(".review-warning-title")).to_have_count(0)
                    analysis["pdf_processing_metrics"] = {
                        "export_recommendation": "review_recommended",
                        "canonical_warning_types_list": ["ocr_control_confidence_unconfirmed", "ocr_structure_review_required"],
                    }
                    page.locator("#file-input").set_input_files({
                        "name": "synthetic-review.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                    page.locator("#convert-btn").click()
                    expect(page.locator(".review-warning-list li")).to_have_count(3)
                    # Exercise restored results through the real initialization path too.
                    page.reload(wait_until="domcontentloaded")
                    expect(page.locator("#review-section").get_by_text(GUIDANCE, exact=True)).to_be_visible()
                    expect(page.locator(".review-warning-title")).to_have_text("Atenção na revisão")
                    expect(page.locator(".review-warning-list li")).to_have_count(3)
                    expect(page.locator(".review-warning-list")).to_contain_text("Confira os saldos no PDF")
                    expect(page.locator(".review-warning-list")).to_contain_text("A organização de algumas linhas")
                    assert not errors, errors
                    context.close()
            browser.close()
        print("Free review browser smoke passed: three pages, mobile/desktop, conversion and restored warnings.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)


if __name__ == "__main__":
    main()
