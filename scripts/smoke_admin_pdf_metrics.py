"""Render synthetic admin PDF metrics at desktop/mobile sizes, with no production calls."""

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.application.admin_pdf_metrics import build_pdf_document_metrics  # noqa: E402


def main():
    events = [{"conversion_type": "pdf-ofx", "status": status, "quality_status": quality,
               "created_at": f"2026-10-28T04:58:{second}+00:00", "file_sha256": "a" * 64,
               "model": "Banco fictício", "error_subcode": "financial_validation_failed",
               "statement_period": {"start_date": "2026-09-01", "end_date": "2026-09-30", "basis": "document_metadata"}}
              for status, quality, second in [("Falha", "failed", "32"), ("Falha", "failed", "52"), ("Sucesso", "review", "59")]]
    payload = {"pdf_documents": build_pdf_document_metrics(events), "end_at": "2026-10-28T05:00:00+00:00"}
    output = ROOT / "tmp/admin-pdf-metrics"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for name, width, height in [("desktop", 1440, 1100), ("mobile", 390, 844)]:
                page = browser.new_page(viewport={"width": width, "height": height}, timezone_id="America/Los_Angeles")
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))

                def route(request):
                    path = urlparse(request.request.url).path
                    if path == "/admin/me":
                        request.fulfill(json={"is_admin": True})
                    elif path == "/admin/dashboard":
                        request.fulfill(json=payload)
                    elif path in ("/admin.html", "/admin.js", "/admin.css"):
                        content_type = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}[Path(path).suffix]
                        request.fulfill(body=(ROOT / "frontend" / path.lstrip("/")).read_bytes(), content_type=content_type)
                    else:
                        request.fulfill(status=404, body="")

                page.route("**/*", route)
                page.goto("http://127.0.0.1:8099/admin.html")
                section = page.locator("#dashboard-pdf-documents")
                section.get_by_text("Mês do documento", exact=True).wait_for()
                assert section.get_by_role("cell", name="09/2026", exact=True).count() == 1
                assert section.get_by_role("cell", name="pdf_aaaaaaaaaaaa", exact=True).count() == 1
                assert section.get_by_role("cell", name="01/09/2026 a 30/09/2026 (período do documento)", exact=True).count() == 1
                assert "01:58:32" in section.inner_text(), "Event times must remain in Sao Paulo even in another browser timezone"
                assert section.locator(".dashboard-summary .metric-card").count() == 4
                assert not errors, errors
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflow"
                section.screenshot(path=str(output / f"{name}.png"))
                page.locator("#admin-logout-btn").click()
                page.wait_for_function("document.getElementById('dashboard-pdf-documents').childElementCount === 0")
                print(json.dumps({"viewport": name, "pdfs": 1, "failures": 2, "reviews": 1, "page_errors": errors}))
                page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
