"""Synthetic browser regression: wrapped first rows and navigation after idle.

Run against a production PRO build with NEXT_PUBLIC_API_URL=/.
The backend stays pending after simulated inactivity; cached UI must remain usable.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright


def run(base: str):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.application.transaction_source_regions import bind_transaction_source_regions
    from tests.test_transaction_source_regions import parsed
    from tests.test_wrapped_native_source_regions import wrapped_columns_pdf

    raw, lines = wrapped_columns_pdf()
    bound = bind_transaction_source_regions(parsed(["\n".join(lines)], [(1, 2), (1, 4), (1, 6)]), raw)
    rows = [{"transaction_id": f"tx-{index}", "source_sequence": index, "source_page": 1,
             "date": "2026-04-01", "description": f"PIX RECEBIDO CLIENTE {index}", "amount": index * 100,
             "source_regions": [asdict(region) for region in transaction.source_regions]}
            for index, transaction in enumerate(bound.canonical_transactions, 1)]
    assert all(row["source_regions"] for row in rows)
    jobs = [{"job_id": identifier, "filename": f"{identifier}.pdf", "status": "completed",
             "analysis_id": f"analysis-{identifier}", "result": {"analysis": {"file_type": "pdf",
                 "updated_at": "2026-10-21T00:00:00Z", "pdf_processing_metrics": {"page_count": 1},
                 "preview_transactions": rows if identifier == "first" else [rows[2]]}}}
            for identifier in ("first", "second")]
    batch = {"batch_id": "idle-navigation", "status": "completed", "jobs": jobs}
    history = {"items": [{"batch_id": batch["batch_id"], "status": "completed",
               "created_at": "2026-10-21T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z",
               "is_expired": False, "files_count": 2, "completed_count": 2,
               "failed_count": 0, "pending_count": 0, "first_job_id": "first"}], "next_cursor": None}
    evidence = Path("tmp/pro-idle-navigation")
    evidence.mkdir(parents=True, exist_ok=True)
    downloads, held, errors, metrics = [], [], [], {}
    idle = False
    history_reads = 0
    saves = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def intercept(route):
            nonlocal history_reads, saves
            path = urlsplit(route.request.url).path
            if idle and (path.startswith("/api/") or path.startswith("/auth/")):
                held.append(route)
                return
            if path == "/auth/me":
                route.fulfill(json={"user_id": "synthetic", "name": "Synthetic", "email": "synthetic@example.invalid"})
            elif path == "/telemetry/events":
                route.fulfill(status=202)
            elif path.endswith("/document"):
                downloads.append(path)
                route.fulfill(content_type="application/pdf", body=raw)
            elif path == "/api/pro/conversion-batches/idle-navigation":
                route.fulfill(json=batch)
            elif path == "/api/pro/conversion-batches":
                history_reads += 1
                route.fulfill(json=history)
            elif path == "/api/pro/convert-edits/analysis-first":
                payload = route.request.post_data_json
                for edit in payload["edits"]:
                    row = next(row for row in rows if row["transaction_id"] == edit["row_id"])
                    row.update(description=edit["description"], date=edit["date"],
                               amount=edit.get("credit") or -(edit.get("debit") or 0))
                saves += 1
                route.fulfill(json={"operation_id": payload["operation_id"],
                                    "updated_at": "2026-10-21T00:00:01Z", "preview_transactions": rows})
            elif path.startswith("/auth/") or path.startswith("/api/"):
                route.fulfill(status=404, json={"detail": "Unexpected synthetic request"})
            else:
                route.continue_()

        page.route("**/*", intercept)
        page.goto(f"{base}/review/idle-navigation/first")
        expect(page.locator("canvas").first).to_be_visible(timeout=60000)
        for index in (1, 2):
            page.locator(f"tr[data-transaction-id='tx-{index}']").hover()
            expect(page.locator(f".pdf-source-highlight[data-source-transaction='tx-{index}']").first).to_be_visible()
            expect(page.locator(f"[data-pdf-reading-marker][data-source-transaction='tx-{index}']").first).to_be_visible()
        cell = page.locator("tr[data-transaction-id='tx-1'] input").nth(1)
        cell.fill("Synthetic confirmed edit")
        cell.press("Tab")
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
        assert saves == 1
        options = page.get_by_role("listbox", name="Conversões do lote")

        def switch(identifier):
            options.get_by_role("option").filter(has_text=f"{identifier}.pdf").click()
            expect(page.locator(".pdf-toolbar")).to_contain_text(f"{identifier}.pdf")
            expect(page.locator("canvas").first).to_be_visible()

        switch("second")
        switch("first")
        assert len(downloads) == 2
        assert history_reads == 1
        # Advance the wall clock beyond access-cookie lifetime, without waiting
        # or changing real browser timers. Hold backend replies indefinitely.
        page.evaluate("Date.now = (() => { const now = Date.now; return () => now() + 20 * 60 * 1000; })()")
        idle = True
        for identifier in ("second", "first"):
            started = time.perf_counter()
            switch(identifier)
            metrics[f"cached_{identifier}_ms"] = round((time.perf_counter() - started) * 1000)
            assert metrics[f"cached_{identifier}_ms"] < 2000, metrics
        started = time.perf_counter()
        page.get_by_role("link", name="Voltar ao dashboard").click()
        expect(page.get_by_role("link", name="Abrir lote")).to_be_visible(timeout=2000)
        metrics["dashboard_ms"] = round((time.perf_counter() - started) * 1000)
        assert metrics["dashboard_ms"] < 2000, metrics
        expect(page.get_by_text("Verificando sua sessão segura…", exact=True)).to_have_count(0)
        page.screenshot(path=str(evidence / "dashboard-pending.png"))
        started = time.perf_counter()
        page.get_by_role("link", name="Abrir lote").click()
        expect(page.locator("canvas").first).to_be_visible(timeout=2000)
        expect(page.locator("tr[data-transaction-id='tx-1'] input").nth(1)).to_have_value("Synthetic confirmed edit")
        metrics["reopen_batch_ms"] = round((time.perf_counter() - started) * 1000)
        assert metrics["reopen_batch_ms"] < 2000, metrics
        assert len(downloads) == 2, downloads
        assert held, "Expected pending background refreshes"
        assert errors == [], errors
        page.screenshot(path=str(evidence / "review-pending.png"))
        result = {"result": "PASS", "metrics": metrics, "pdf_downloads": len(downloads),
                  "pending_refreshes": len(held), "checks": ["first two wrapped rows highlight on hover",
                      "20-minute idle", "cached PDF switch", "dashboard without auth gate remount",
                      "cached batch reopens during backend delay", "confirmed edit preserved", "no JavaScript errors"]}
        (evidence / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
        idle = False
        for route in held:
            try:
                route.abort()
            except PlaywrightError:
                pass  # Navigation may already have cancelled the request.
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    run(parser.parse_args().url.rstrip("/"))
