"""Real browser smoke using synthetic PDFs and intercepted API responses only.

Start PRO with NEXT_PUBLIC_API_URL=/, then run with the project Python venv:
  python scripts/smoke_pro_pdf_sync.py --url http://127.0.0.1:3100
Requires the existing Playwright Python package and an installed Chromium.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def synthetic_pdf(count: int) -> bytes:
    """Mixed page dimensions, including an image-only page, without private files."""
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for number in range(1, count + 1):
        page_id = len(objects) + 1
        content_id = page_id + 1
        kids.append(f"{page_id} 0 R")
        landscape = number % 3 == 0
        width, height = (800, 600) if landscape else (600, 800)
        image = number == 6
        resources = "/XObject << /Scan 3 0 R >>" if image else "/Font << /F1 3 0 R >>"
        # Image-only pages use a self-contained inline bitmap, independent of OCR.
        content = (b"q 500 0 0 500 25 25 cm BI /W 2 /H 2 /CS /G /BPC 8 /F /AHx ID 00808000> EI Q" if image else
                   f"BT /F1 22 Tf 40 {height - 65} Td (Synthetic statement - page {number}) Tj ET".encode())
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Resources << {resources if not image else ''} >> /Contents {content_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
    objects[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[1] = f"<< /Type /Pages /Count {count} /Kids [{' '.join(kids)}] >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(output)


def run(base_url: str) -> None:
    transactions = [
        {"transaction_id": f"tx-{page}-{index}", "source_page": page,
         "source_sequence": page * 20 + index, "date": "2026-10-18",
         "description": f"Page {page} transaction {index}", "amount": 10,
         "warning_types": ["synthetic"] if page == 2 else []}
        for page in range(2, 30) if page != 8 for index in range(20)
    ] + [{"transaction_id": "manual", "date": "2026-10-18", "description": "Manual without origin", "amount": 1}]

    def job(identifier: str, pages: int, rows: list[dict]) -> dict:
        return {"job_id": identifier, "filename": f"{identifier}.pdf", "status": "completed", "analysis_id": f"analysis-{identifier}",
                "result": {"analysis": {"file_type": "pdf", "pdf_processing_metrics": {"page_count": pages}, "preview_transactions": rows}}}

    batch = {"batch_id": "sync-test", "status": "completed", "jobs": [job("long", 30, transactions), job("short", 2, [])]}
    writes: list[str] = []
    downloads: list[str] = []
    evidence = Path("tmp/pro-pdf-sync")
    evidence.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        context.add_init_script("delete Promise.withResolvers;")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def intercept(route):
            path = route.request.url.split(base_url)[-1]
            if path == "/auth/me":
                route.fulfill(json={"user_id": "synthetic", "name": "Synthetic User", "email": "synthetic@example.invalid"})
            elif path.endswith("/document"):
                assert route.request.headers.get("x-ofx-pro-client") == "web"
                downloads.append(path)
                if "/short/" in path and downloads.count(path) == 1:
                    route.fulfill(status=403, json={"detail": "Synthetic expired access"})
                    return
                route.fulfill(content_type="application/pdf", body=synthetic_pdf(2 if "/short/" in path else 30))
            elif path == "/api/pro/conversion-batches/sync-test":
                route.fulfill(json=batch)
            elif "/convert-edits/" in path:
                writes.append(path)
                payload = route.request.post_data_json
                route.fulfill(json={"operation_id": payload.get("operation_id"), "updated_at": "2026-10-18T12:00:00Z"})
            elif path.startswith("/api/") or path.startswith("/auth/"):
                writes.append(path)
                route.fulfill(status=404, json={"detail": "Unexpected request"})
            else:
                route.continue_()

        page.route("**/*", intercept)
        page.goto(f"{base_url}/review/sync-test/long")
        stage = page.get_by_role("region", name="Visualizador do extrato original")
        expect(page.locator(".pdf-controls")).to_contain_text("de 30", timeout=60000)
        expect(page.locator("canvas").first).to_be_visible()
        expect(page.get_by_text("Página 1: sem lançamentos associados")).to_be_visible()
        grid = page.locator(".table-scroll")

        def wheel_to(number: int):
            delta = stage.evaluate("(el, n) => el.querySelector(`[data-pdf-page='${n}']`).getBoundingClientRect().top - el.getBoundingClientRect().top", number)
            stage.hover()
            page.mouse.wheel(0, delta)
            expect(page.locator(".pdf-controls strong")).to_have_text(str(number))

        def aligned(number: int):
            expect(page.get_by_text(f"Acompanhando página {number} · localização por página")).to_be_visible()
            page.wait_for_function("n => { const row = document.querySelector(`tr[data-source-page='${n}']`); const grid = document.querySelector('.table-scroll'); return Math.abs(row.getBoundingClientRect().top - grid.getBoundingClientRect().top - 32) < 3; }", arg=number)

        wheel_to(7)
        aligned(7)
        assert 0 < page.locator("canvas").count() < 10
        assert writes == []
        assert len(downloads) == 1
        toggle = page.get_by_role("checkbox", name="Sincronizar PDF e lançamentos")
        wheel_to(30)
        page.get_by_role("button", name="Diminuir zoom").click()
        expect(page.locator(".pdf-controls strong")).to_have_text("30")
        page.get_by_role("button", name="Ajustar à largura").click()
        wheel_to(7)
        aligned(7)
        toggle.uncheck()
        before = grid.evaluate("el => el.scrollTop")
        wheel_to(9)
        assert grid.evaluate("el => el.scrollTop") == before
        toggle.check()
        aligned(9)
        wheel_to(8)
        expect(page.get_by_text("Página 8: sem lançamentos associados")).to_be_visible()

        page.get_by_role("button", name=re.compile("Suspeitos")).click()
        wheel_to(9)
        expect(page.get_by_text("Página 9: nenhum lançamento visível neste filtro")).to_be_visible()
        page.get_by_role("button", name=re.compile("^Todos")).click()
        aligned(9)
        page.get_by_label("Organizar lançamentos").select_option("PAGES")
        wheel_to(6)
        aligned(6)
        expect(page.locator("[data-pdf-page='6'] canvas")).to_be_visible()

        input_field = page.get_by_role("textbox", name="Descrição de Page 6 transaction 0", exact=True)
        input_field.fill("Preserved edit")
        input_field = page.get_by_role("textbox", name="Descrição de Preserved edit", exact=True)
        before = grid.evaluate("el => el.scrollTop")
        wheel_to(9)
        expect(page.get_by_text("Acompanhamento pausado durante a edição")).to_be_visible()
        expect(input_field).to_be_focused()
        assert grid.evaluate("el => el.scrollTop") == before
        input_field.press("Tab")
        expect(page.locator('input[value="Preserved edit"]')).to_be_visible()
        # Leaving the editor explicitly resumes at the current PDF page.
        toggle.focus()
        expect(page.get_by_text("Acompanhamento pausado durante a edição")).to_have_count(0)
        aligned(9)
        page.get_by_role("button", name="Localizar origem de Page 2 transaction 0 no PDF (página 2)").click()
        expect(page.locator(".pdf-controls strong")).to_have_text("2")
        assert len(downloads) == 1
        page.get_by_role("button", name="Aumentar zoom").click()
        expect(page.locator(".pdf-controls strong")).to_have_text("2")
        page.get_by_role("button", name="Ajustar à largura").click()
        page.screenshot(path=str(evidence / "desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        expect(stage).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(evidence / "mobile.png"))
        stage.focus()
        stage.press("PageDown")
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.get_by_role("option", name=re.compile("short.pdf")).click()
        expect(page.get_by_role("button", name="Recarregar PDF")).to_be_visible()
        expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible()
        page.get_by_role("button", name="Recarregar PDF").click()
        expect(page.locator(".pdf-controls")).to_contain_text("de 2")
        expect(page.locator(".pdf-controls strong")).to_have_text("1")
        page.get_by_role("option", name=re.compile("long.pdf")).click()
        expect(page.locator(".pdf-controls")).to_contain_text("de 30")
        expect(page.locator('input[value="Preserved edit"]')).to_have_count(1)
        assert not any("review" in path for path in writes)
        assert errors == [], errors
        print(json.dumps({"result": "PASS", "pages": 30, "transactions": len(transactions), "checks": ["real wheel scroll and grid alignment", "bounded canvases", "toggle", "unmatched pages", "filters", "grouped layout", "image-only page", "editing focus and resume", "explicit origin navigation", "no document reload", "zoom", "mobile 390px", "keyboard", "document switch preserving edits", "expired access and retry", "Promise.withResolvers fallback", "no review writes", "no browser errors"]}))
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    run(parser.parse_args().url.rstrip("/"))
