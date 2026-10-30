"""Offline browser smoke: scoped account summaries/rows and direct OFX download."""

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

from smoke_playwright_navigation import pick_free_port, wait_for_http_ready  # noqa: E402

from app.application.default_conversion_pipeline import build_default_conversion_pipeline  # noqa: E402
from app.application.storage_service import TempAnalysisStorage  # noqa: E402
from tests.fixtures.positioned_cash_statements import positioned_pdf  # noqa: E402
from tests.test_statement_account_blocks import consolidated_pages  # noqa: E402


def main():
    from playwright.sync_api import sync_playwright

    os.environ.update(PDF_MULTI_ACCOUNT_ENABLED="true", TEXTRACT_ENABLED="false", PDF_OCR_ENABLED="false")
    data = build_default_conversion_pipeline().run(filename="consolidado.pdf", raw_bytes=positioned_pdf(consolidated_pages()),
        analysis_id="an_browser_accounts").analysis_data
    port = pick_free_port()
    base = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port)], cwd=ROOT / "frontend",
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for_http_ready(base)
        with tempfile.TemporaryDirectory(prefix="account-browser-") as temporary, sync_playwright() as playwright:
            storage = TempAnalysisStorage(Path(temporary))
            storage.save_analysis(data)
            ofx = storage.get_convert_report_path(data.analysis_id, "ofx").read_bytes()
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            edits = []
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def route(request):
                path = urlparse(request.request.url).path
                if request.request.resource_type not in {"fetch", "xhr"} and request.request.url.startswith(base):
                    request.continue_()
                    return
                headers = {"access-control-allow-origin": base, "access-control-allow-credentials": "true"}
                headers.update({"access-control-allow-headers": "content-type,x-csrf-token,x-ofx-pro-client",
                                "access-control-allow-methods": "GET,POST,OPTIONS"})
                requests.append(path)
                if "/convert-edits/" in path:
                    edits.append(path)
                if "/convert-report/" in path:
                    request.fulfill(status=200, headers={**headers, "content-disposition": 'attachment; filename="contas.ofx"'},
                                    content_type="application/x-ofx", body=ofx)
                    return
                status = 401 if path.endswith(("/auth/me", "/auth/session/refresh")) else 200
                body = {"quota_limit": 100, "quota_remaining": 100, "direct_batch_enabled": False, "batch_max_files": 1}
                if path.endswith("/banks"):
                    body = {"items": []}
                request.fulfill(status=status, headers=headers, content_type="application/json", body=json.dumps(body))

            page.route("**/*", route)
            page.goto(base + "/ofx-convert.html", wait_until="domcontentloaded")
            page.wait_for_timeout(600)
            state = {"analysis": asdict(data), "processing_id": data.analysis_id, "analysis_id": data.analysis_id,
                     "file_name": "consolidado.pdf", "quota_limit": 100, "quota_remaining": 99}
            page.evaluate("value => sessionStorage.setItem('ofxsimples_ofx_convert_view_state_ofx_v1', JSON.stringify(value))", state)
            page.reload(wait_until="domcontentloaded")
            try:
                page.wait_for_selector("#download-ofx-btn:not([disabled])", timeout=10000)
            except Exception:
                print(json.dumps({"url": page.url, "errors": errors, "requests": requests,
                    "status": page.locator("#status-msg").text_content() if page.locator("#status-msg").count() else None,
                    "kpis": page.locator("#kpis").text_content() if page.locator("#kpis").count() else None}))
                raise
            assert page.locator("#kpis").get_by_text("Agência 0017 · Conta 12345-6").count() == 1
            assert page.locator("#kpis").get_by_text("Agência 0039 · Conta 98765-4").count() == 1
            assert page.locator("#kpis .kpi-value").first.text_content() == "4"
            assert page.locator("#opening-balance-input, #closing-balance-input, #account-number-input").count() == 0
            assert page.get_by_text("Agência 0039 · Conta 98765-4").count() >= 2
            with page.expect_download() as downloaded:
                page.locator("#download-ofx-btn").click()
            assert Path(downloaded.value.path()).read_bytes() == ofx
            assert not edits, "A multi-account download must not submit one global account/balance."
            browser.close()
        print("Multi-account browser smoke passed: separate accounts, no global overrides, complete OFX download.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
