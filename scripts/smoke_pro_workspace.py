"""Browser acceptance/performance budget: 12 documents, 541 rows, synthetic data only.

Run against a production PRO build with NEXT_PUBLIC_API_URL=/.
API requests are intercepted; authentication/ownership/storage are covered by real-HTTP tests.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from smoke_pro_pdf_sync import bind_fixture_regions, synthetic_pdf


def run(base: str):
    deadline = time.monotonic() + 30
    while True:
        try:
            with urlopen(base+"/dashboard", timeout=2) as response:
                if response.status == 200:
                    break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(.2)
    output = Path("tmp/pro-workspace-acceptance")
    output.mkdir(parents=True, exist_ok=True)
    rows = [{"transaction_id": f"tx-{number}-{index}", "source_sequence": number*20+index,
             "source_page": number, "date": "2026-10-18", "description": f"Page {number} transaction {index}",
             "amount": 10, "warning_types": ["synthetic"] if number == 2 else []}
            for number in range(2,30) if number != 8 for index in range(20)]
    rows.append({"transaction_id": "manual", "date": "2026-10-18", "description": "Synthetic manual", "amount": 1})
    large_pdf, small_pdf = synthetic_pdf(30), synthetic_pdf(2)
    bind_fixture_regions(large_pdf, rows)

    def result(index):
        items = copy.deepcopy(rows if index == 0 else [rows[1]])
        if index:
            items[0].update(transaction_id=f"short-{index}", warning_types=[])
        return {"analysis": {"file_type": "pdf", "bank_name": "Banco sintético", "updated_at": "2026-10-18T00:00:00Z",
            "pdf_processing_metrics": {"page_count": 30 if index == 0 else 2}, "preview_transactions": items}}

    jobs = [{"job_id": f"doc-{index:02d}", "filename": f"synthetic-{index:02d}.pdf", "analysis_id": f"analysis-{index:02d}",
             "status": "running" if index == 1 else "failed" if index == 2 else "completed",
             "result": None if index in (1,2) else result(index)} for index in range(12)]
    batch = {"batch_id": "workspace-mvp", "status": "processing", "jobs": jobs}
    events, errors, documents, exports, writes = [], [], [], [], []
    receipts = {}
    reads = 0
    poll_outage = True
    save_attempts = 0
    export_attempts = 0
    metrics = {}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append({"message": error.message, "stack": error.stack}))

        def intercept(route):
            nonlocal reads, save_attempts, export_attempts
            path = urlsplit(route.request.url).path
            if path == "/auth/me":
                route.fulfill(json={"user_id": "synthetic", "name": "Synthetic", "email": "synthetic@example.invalid"})
            elif path == "/telemetry/events":
                events.append(route.request.post_data_json)
                route.fulfill(status=202)
            elif path == "/api/pro/conversion-batches":
                route.fulfill(json={"items": [], "next_cursor": None})
            elif path.endswith("/document"):
                documents.append(path)
                route.fulfill(content_type="application/pdf", body=large_pdf if "/doc-00/" in path else small_pdf)
            elif path == "/api/pro/conversion-batches/workspace-mvp":
                reads += 1
                # Keep the simulated outage active until the test explicitly
                # restores the service. A single failed poll can auto-recover
                # before a slower browser reaches the error/retry assertion.
                if reads >= 2 and poll_outage:
                    route.fulfill(status=503, json={"detail": "Synthetic polling outage"})
                    return
                if reads >= 3:
                    for index in (1,2):
                        if jobs[index]["status"] == "running":
                            jobs[index].update(status="completed", result=result(index))
                route.fulfill(json=batch)
            elif "/convert-edits/" in path:
                if "/operations/" in path:
                    receipt = receipts.get(path.rsplit("/",1)[-1])
                    route.fulfill(status=200 if receipt else 404, json=receipt or {"detail": "Edit operation not found."})
                    return
                save_attempts += 1
                if save_attempts == 1:
                    route.fulfill(status=503, json={"detail": "Synthetic save outage"})
                    return
                payload = route.request.post_data_json
                job = next(job for job in jobs if path.endswith(job["analysis_id"]))
                analysis = job["result"]["analysis"]
                assert payload.get("expected_updated_at") == analysis["updated_at"]
                for edit in payload["edits"]:
                    row = next(row for row in analysis["preview_transactions"] if row["transaction_id"] == edit["row_id"])
                    row.update(date=edit["date"], description=edit["description"], amount=edit.get("credit") or -(edit.get("debit") or 0))
                analysis["updated_at"] = f"2026-10-18T00:00:{save_attempts:02d}Z"
                receipt = {"operation_id": payload["operation_id"], **copy.deepcopy(analysis)}
                receipts[payload["operation_id"]] = receipt
                writes.append(payload)
                route.fulfill(json=receipt)
            elif path.endswith("/review"):
                job = next(job for job in jobs if f'/{job["job_id"]}/' in path)
                job["reviewed_at"] = "2026-10-18T01:00:00Z"
                route.fulfill(json={"reviewed": True})
            elif path.endswith("/retry"):
                job = next(job for job in jobs if f'/{job["job_id"]}/' in path)
                job["status"] = "running"
                route.fulfill(json={"status": "queued"})
            elif path.endswith("/export"):
                export_attempts += 1
                if export_attempts == 1:
                    route.fulfill(status=503, json={"detail": "Synthetic export outage"})
                    return
                archive = io.BytesIO()
                with zipfile.ZipFile(archive, "w") as zipped:
                    for job in jobs:
                        assert job.get("reviewed_at"), job["job_id"]
                        zipped.writestr(job["job_id"]+".json", json.dumps(job["result"]["analysis"]["preview_transactions"]))
                exports.append(archive.getvalue())
                route.fulfill(body=archive.getvalue(), content_type="application/zip",
                    headers={"content-disposition": 'attachment; filename="reviewed.zip"', "x-conversion-count": "12"})
            elif path.startswith(("/api/", "/auth/", "/convert-")):
                raise AssertionError(f"Unexpected API request: {route.request.method} {path}")
            else:
                route.continue_()

        try:
            page.route("**/*", intercept)
            started = time.perf_counter()
            page.goto(f"{base}/review/workspace-mvp/doc-00")
            expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible(timeout=60000)
            expect(page.locator("canvas").first).to_be_visible(timeout=60000)
            metrics["open_ms"] = round((time.perf_counter()-started)*1000)
            assert metrics["open_ms"] < 12000, metrics
            assert page.get_by_role("listbox", name="Conversões do lote").get_by_role("option").count() == 12
            page.locator("canvas").first.evaluate("node => node.dataset.acceptance = 'original'")

            cell = page.locator("tr[data-transaction-id='tx-2-1'] input").nth(1)
            started = time.perf_counter()
            cell.fill("Synthetic corrected description")
            expect(cell).to_have_value("Synthetic corrected description")
            metrics["edit_ms"] = round((time.perf_counter()-started)*1000)
            assert metrics["edit_ms"] < 1000, metrics
            cell.press("Tab")
            expect(page.get_by_role("button", name="Falha ao salvar · tentar novamente")).to_be_visible()
            assert page.locator("canvas[data-acceptance='original']").count() == 1
            assert len(documents) == 1

            page.get_by_role("option").filter(has_text="synthetic-03.pdf").click()
            expect(page.get_by_role("alert").filter(has_text="Synthetic polling outage")).to_be_visible(timeout=10000)
            poll_outage = False
            page.get_by_role("button", name="Tentar atualizar novamente").click()
            expect(page.get_by_role("option").filter(has_text="synthetic-01.pdf")).to_contain_text("Pronto", timeout=10000)
            page.get_by_role("option").filter(has_text="synthetic-00.pdf").click()
            expect(cell).to_have_value("Synthetic corrected description")
            page.get_by_role("button", name="Falha ao salvar · tentar novamente").click()
            expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
            assert jobs[0]["result"]["analysis"]["preview_transactions"][1]["description"] == "Synthetic corrected description"

            divider = page.get_by_role("separator", name="Ajustar largura do PDF e dos lançamentos")
            divider.focus()
            divider.press("ArrowRight")
            expect(divider).to_have_attribute("aria-valuenow", "50")
            page.get_by_role("button", name="Recolher PDF", exact=True).click()
            expect(page.locator(".pdf-panel")).to_be_hidden()
            page.get_by_role("button", name="Mostrar PDF", exact=True).click()
            expect(page.locator(".pdf-panel")).to_be_visible()

            search = page.get_by_role("searchbox", name="Buscar no PDF")
            search.fill("Page 3 transaction 19")
            expect(page.locator(".pdf-search [role='status']")).to_have_text("1 de 1")
            expect(page.locator(".pdf-controls strong")).to_have_text("3")
            expect(page.locator(".pdf-search-highlight.is-active").first).to_be_visible()
            search.fill("")
            expect(page.locator(".pdf-search-highlight")).to_have_count(0)
            cell.focus()
            cell.press("Alt+ArrowDown")
            assert page.evaluate("document.activeElement.closest('tr').dataset.transactionId") == "tx-2-2"
            page.get_by_role("button", name="Suspeitos", exact=False).click()
            page.get_by_role("button", name="Todos", exact=False).last.click()

            page.get_by_role("option").filter(has_text="synthetic-02.pdf").click()
            page.get_by_role("button", name="Tentar novamente", exact=True).click()
            expect(page.get_by_role("heading", name="Aguardando lançamentos")).to_be_visible()
            page.get_by_role("button", name="Atualizar agora", exact=True).click()
            expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible()

            for count in range(12):
                page.get_by_role("button", name="Marcar revisado", exact=True).click()
                expect(page.locator(".batch-progress strong")).to_have_text(f"{count+1} de 12")
            assert all(job.get("reviewed_at") for job in jobs)
            page.get_by_role("button", name="Exportar lote", exact=True).click()
            expect(page.get_by_role("alert").filter(has_text="Synthetic export outage")).to_be_visible()
            with page.expect_download() as download:
                page.get_by_role("button", name="Tentar exportar novamente").click()
            download.value.save_as(output / "reviewed.zip")
            with zipfile.ZipFile(io.BytesIO(exports[0])) as zipped:
                assert len(zipped.namelist()) == 12
                assert json.loads(zipped.read("doc-00.json"))[1]["description"] == "Synthetic corrected description"
            expect(page.get_by_text("Exportação concluída", exact=True)).to_be_visible()
            page.get_by_role("button", name="Fechar aviso", exact=True).click()
            expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible()
            expected = {"workspace_opened", "conversion_selected", "suspicious_filter_used", "transaction_edited", "conversion_reviewed", "batch_export_requested"}
            assert expected <= {event["event_type"] for event in events}, events
            assert all(set(event) == {"event_type", "page_path"} and event["page_path"] == "/review" for event in events)
            assert errors == [], errors
            page.screenshot(path=str(output / "desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_function("document.querySelector('.conversion-panel').getBoundingClientRect().width <= 64")
            assert page.locator(".review-stage").bounding_box()["width"] >= 260
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(output / "mobile.png"))
            summary = {"result": "PASS", "documents": 12, "large_document_rows": len(rows), "metrics": metrics,
                       "budgets_ms": {"open_ms": 12000, "edit_ms": 1000}, "save_attempts": save_attempts,
                       "poll_reads": reads, "reviewed": 12, "exported": 12, "event_types": sorted(expected)}
            (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(json.dumps(summary))
        except Exception:
            page.screenshot(path=str(output / "failure.png"))
            (output / "failure.html").write_text(page.content(), encoding="utf-8")
            print(json.dumps({"browser_errors": errors, "metrics": metrics}), flush=True)
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    run(parser.parse_args().url.rstrip("/"))
