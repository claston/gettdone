"""Real browser smoke using synthetic PDFs and intercepted API responses only.

Start PRO with NEXT_PUBLIC_API_URL=/, then run with the project Python venv:
  python scripts/smoke_pro_pdf_sync.py --url http://127.0.0.1:3100
Requires the existing Playwright Python package and an installed Chromium.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def page_evidence(number: int):
    width, height = (800, 600) if number % 3 == 0 else (600, 800)
    lines = [(f"Synthetic statement - page {number}", 40, height - 65, 22)]
    if number == 6:
        return width, height, [], {}
    if number == 3:
        lines.append(("Continuation of Page 2 transaction 10", 40, height - 85, 8))
    references = {}
    for index in range(20):
        baseline = height - 110 - index * 20
        lines.append((f"Page {number} transaction {index}", 40, baseline, 9))
        references[index] = [(number, len(lines))]
        if number == 2 and index == 10:
            lines.append(("Multiline source continuation", 40, baseline - 8, 6))
            references[index].extend([(number, len(lines)), (3, 2)])
    return width, height, lines, references


def synthetic_pdf(count: int) -> bytes:
    """Mixed page dimensions, including an image-only page, without private files."""
    objects: list[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for number in range(1, count + 1):
        page_id = len(objects) + 1
        content_id = page_id + 1
        kids.append(f"{page_id} 0 R")
        width, height, lines, _ = page_evidence(number)
        image = number == 6
        resources = "/XObject << /Scan 3 0 R >>" if image else "/Font << /F1 3 0 R >>"
        # Image-only pages use a self-contained inline bitmap, independent of OCR.
        content = (b"q 500 0 0 500 25 25 cm BI /W 2 /H 2 /CS /G /BPC 8 /F /AHx ID 00808000> EI Q" if image else
                   "\n".join(f"BT /F1 {font} Tf {x} {y} Td ({text}) Tj ET" for text, x, y, font in lines).encode())
        rotation = "/Rotate 90 /CropBox [20 30 780 590]" if number == 3 else ""
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] {rotation} /Resources << {resources if not image else ''} >> /Contents {content_id} 0 R >>".encode())
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


def bind_fixture_regions(raw: bytes, transactions: list[dict]) -> None:
    """Use production native geometry, rather than invented browser rectangles."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.application.models import CanonicalTransaction, NormalizedTransaction
    from app.application.parsers.pdf.models import PdfParseResult
    from app.application.pdf_layout_inference import PdfLayoutInference
    from app.application.transaction_source_regions import bind_transaction_source_regions

    pages = [page_evidence(number) for number in range(1, 31)]
    canonical = []
    for row in transactions:
        number = row.get("source_page")
        index = int(row["transaction_id"].rsplit("-", 1)[-1]) if number else 0
        refs = pages[number - 1][3].get(index, []) if number else []
        canonical.append(CanonicalTransaction("2026-10-18", row["description"], 10, "inflow",
            source_page=number, source_line=refs[0][1] if refs else None, source_line_refs=tuple(refs)))
    parsed = PdfParseResult(
        transactions=[NormalizedTransaction("2026-10-18", row["description"], 10, "inflow") for row in transactions],
        canonical_transactions=canonical, layout=PdfLayoutInference("generic_statement_ptbr", 0.25, True),
        extracted_text="", parse_metrics={}, source_page_texts=tuple("\n".join(line[0] for line in page[2]) for page in pages),
    )
    bound = bind_transaction_source_regions(parsed, raw)
    for row, transaction in zip(transactions, bound.canonical_transactions, strict=True):
        row["source_regions"] = [asdict(region) for region in transaction.source_regions]
        if row["transaction_id"] == "tx-2-0":
            row["source_regions"] = []  # Legacy page-only compatibility.
        if row["transaction_id"] == "tx-6-0":
            # Synthetic OCR box on the actual image-only page.
            row["source_regions"] = [{"page": 6, "left": 25 / 800, "top": 75 / 600,
                                      "width": 500 / 800, "height": 500 / 600}]
    assert len(next(row for row in transactions if row["transaction_id"] == "tx-2-10")["source_regions"]) == 3


def run(base_url: str) -> None:
    transactions = [
        {"transaction_id": f"tx-{page}-{index}", "source_page": page,
         "source_sequence": page * 20 + index, "date": "2026-10-18",
         "description": f"Page {page} transaction {index}", "amount": 10,
         "warning_types": ["synthetic"] if page == 2 else []}
        for page in range(2, 30) if page != 8 for index in range(20)
    ] + [{"transaction_id": "manual", "date": "2026-10-18", "description": "Manual without origin", "amount": 1}]
    long_pdf, short_pdf = synthetic_pdf(30), synthetic_pdf(2)
    bind_fixture_regions(long_pdf, transactions)

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
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1, has_touch=True)
        context.add_init_script("delete Promise.withResolvers;")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def intercept(route):
            path = route.request.url.split(base_url)[-1]
            if path == "/auth/me":
                route.fulfill(json={"user_id": "synthetic", "name": "Synthetic User", "email": "synthetic@example.invalid"})
            elif path.startswith("/api/pro/conversion-batches?"):
                route.fulfill(json={"items": [], "next_cursor": None})
            elif path.endswith("/document"):
                assert route.request.headers.get("x-ofx-pro-client") == "web"
                downloads.append(path)
                if "/short/" in path and downloads.count(path) == 1:
                    route.fulfill(status=403, json={"detail": "Synthetic expired access"})
                    return
                route.fulfill(content_type="application/pdf", body=short_pdf if "/short/" in path else long_pdf)
            elif path == "/api/pro/conversion-batches/sync-test":
                route.fulfill(json=batch)
            elif "/convert-edits/" in path:
                writes.append(path)
                payload = route.request.post_data_json
                analysis = batch["jobs"][0]["result"]["analysis"]
                rows = analysis["preview_transactions"]
                for edit in payload.get("edits", []):
                    row = next(item for item in rows if item["transaction_id"] == edit["row_id"])
                    if "review_regions" in edit:
                        row["review_regions"] = edit["review_regions"]
                    if edit.get("action") != "annotate":
                        row["date"], row["description"] = edit["date"], edit["description"]
                        row["amount"] = edit.get("credit") or -(edit.get("debit") or 0)
                analysis["updated_at"] = f"2026-10-18T12:00:{len(writes):02d}Z"
                route.fulfill(json={"operation_id": payload.get("operation_id"), "updated_at": analysis["updated_at"], "preview_transactions": rows})
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
            stage.hover()
            page.wait_for_function("""el => {
                const now = performance.now(), previous = el.__smokeReadingPosition;
                if (!previous || Math.abs(previous.top - el.scrollTop) > 0.5) { el.__smokeReadingPosition = {top: el.scrollTop, at: now}; return false; }
                return now - previous.at > 100;
            }""", arg=stage.element_handle())
            delta = stage.evaluate("(el, n) => el.querySelector(`[data-pdf-page='${n}']`).getBoundingClientRect().top - el.getBoundingClientRect().top", number)
            page.mouse.wheel(0, delta)
            expect(page.locator(".pdf-controls strong")).to_have_text(str(number))
            if number < 30:
                page.wait_for_function("n => { const el = document.querySelector('.pdf-stage'), slot = el.querySelector(`[data-pdf-page='${n}']`); return Math.abs(slot.getBoundingClientRect().top - el.getBoundingClientRect().top) < 2; }", arg=number)

        def aligned(number: int):
            expect(page.locator(".pdf-controls strong")).to_have_text(str(number))
            page.wait_for_function("n => { const row = document.querySelector(`tr.source-active-row[data-source-page='${n}']`) || document.querySelector(`tr[data-source-page='${n}']`); const grid = document.querySelector('.table-scroll'); return Math.abs(row.getBoundingClientRect().top - grid.getBoundingClientRect().top - 32) < 3; }", arg=number)

        wheel_to(7)
        aligned(7)
        assert 0 < page.locator("canvas").count() < 10
        assert writes == []
        assert len(downloads) == 1
        expect(page.get_by_role("checkbox", name="Sincronizar PDF e lançamentos")).to_have_count(0)
        expect(page.get_by_text(re.compile("Acompanhando página"))).to_have_count(0)
        wheel_to(30)
        page.get_by_role("button", name="Diminuir zoom").click()
        expect(page.locator(".pdf-controls strong")).to_have_text("30")
        page.get_by_role("button", name="Ajustar à largura").click()
        wheel_to(7)
        aligned(7)
        wheel_to(9)
        aligned(9)
        wheel_to(8)
        expect(page.get_by_text("Página 8: sem lançamentos associados")).to_be_visible()
        expect(page.locator("tr.source-active-row")).to_have_count(0)

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
        expect(input_field).to_be_focused()
        assert grid.evaluate("el => el.scrollTop") == before
        frozen_pdf = stage.evaluate("el => el.scrollTop")
        page.locator("tr[data-transaction-id='tx-6-1']").hover()
        page.wait_for_timeout(200)
        expect(input_field).to_be_focused()
        assert stage.evaluate("el => el.scrollTop") == frozen_pdf
        input_field.press("Tab")
        expect(page.locator('input[value="Preserved edit"]')).to_be_visible()
        # Leaving the editor explicitly resumes at the current PDF page.
        page.get_by_role("button", name="Ajustar à largura").focus()
        aligned(9)
        page.get_by_role("button", name="Localizar origem de Page 2 transaction 0 no PDF (página 2)").click()
        expect(page.locator(".pdf-controls strong")).to_have_text("2")
        expect(page.locator(".pdf-source-highlight")).to_have_count(0)
        expect(page.get_by_text("Página de origem aberta. A região exata não está disponível para este lançamento.")).to_have_count(1)
        expect(page.locator(".pdf-reading-marker")).to_have_count(0)
        assert len(downloads) == 1
        page.get_by_role("button", name="Aumentar zoom").click()
        expect(page.locator(".pdf-controls strong")).to_have_text("2")
        page.get_by_role("button", name="Ajustar à largura").click()

        def locate(identifier: str):
            page.locator(f"tr[data-transaction-id='{identifier}'] button[title='Destacar origem no PDF']").click()
            expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", identifier)

        def verify_overlay(identifier: str, number: int):
            overlays = page.locator(f"[data-pdf-page='{number}'] .pdf-source-highlight[data-source-transaction='{identifier}']")
            expect(overlays.first).to_be_visible()
            # The automatic ruler spans the physical page and covers the same
            # source lines, including rotated/cropped pages and any zoom level.
            page.wait_for_function("""({id, number}) => {
                const slot = document.querySelector(`[data-pdf-page='${number}']`);
                const markers = [...slot.querySelectorAll(`[data-pdf-reading-marker][data-source-transaction='${id}']`)];
                const sources = [...slot.querySelectorAll(`.pdf-source-highlight[data-source-transaction='${id}']`)];
                const box = slot.getBoundingClientRect();
                return sources.length && sources.every(source => {
                    const rect = source.getBoundingClientRect(), center = rect.top + rect.height / 2;
                    return markers.some(marker => { const band = marker.getBoundingClientRect();
                        return Math.abs(band.left - box.left) < 1 && Math.abs(band.width - box.width) < 1 &&
                            center >= band.top - 1 && center <= band.bottom + 1 &&
                            getComputedStyle(marker).pointerEvents === 'none' &&
                            getComputedStyle(marker, '::before').borderLeftWidth === '9px'; });
                });
            }""", arg={"id": identifier, "number": number})
            for overlay in overlays.all():
                # Read real canvas pixels under each overlay: source glyphs must
                # be present, including on the intrinsically rotated page.
                try:
                    page.wait_for_function("""el => {
                    const slot = el.parentElement, canvas = slot.querySelector('canvas');
                    if (!canvas) return false;
                    const left = parseFloat(el.style.left) / 100, top = parseFloat(el.style.top) / 100;
                    const width = parseFloat(el.style.width) / 100, height = parseFloat(el.style.height) / 100;
                    const data = canvas.getContext('2d').getImageData(Math.floor(left * canvas.width), Math.floor(top * canvas.height), Math.max(1, Math.floor(width * canvas.width)), Math.max(1, Math.floor(height * canvas.height))).data;
                    // Small glyphs can be mostly antialiased gray at fit width.
                    let ink = 0; for (let i = 0; i < data.length; i += 4) if (data[i] < 240 && data[i+1] < 240 && data[i+2] < 240 && data[i+3]) ink++;
                    return ink > 8;
                    }""", arg=overlay.element_handle(), timeout=10000)
                except Exception:
                    print("REGION_DEBUG", overlay.evaluate("""el => {
                        const canvas = el.parentElement.querySelector('canvas');
                        const left = parseFloat(el.style.left) / 100, top = parseFloat(el.style.top) / 100;
                        const width = parseFloat(el.style.width) / 100, height = parseFloat(el.style.height) / 100;
                        const data = canvas.getContext('2d').getImageData(Math.floor(left * canvas.width), Math.floor(top * canvas.height), Math.max(1, Math.floor(width * canvas.width)), Math.max(1, Math.floor(height * canvas.height))).data;
                        let dark = 0, gray = 0, min = 255; for(let i=0;i<data.length;i+=4) { min = Math.min(min,data[i]); if(data[i]<100)dark++; if(data[i]<240)gray++; }
                        return {style: el.getAttribute('style'), canvas:[canvas.width,canvas.height], dark, gray, min, slot:el.parentElement.getBoundingClientRect().toJSON()};
                    }"""), flush=True)
                    page.screenshot(path=str(evidence / "region-debug.png"))
                    raise

        # Mouse movement selects and highlights a row without the origin button.
        writes_before_sync = list(writes)
        page.locator("tr[data-transaction-id='tx-2-5']").hover()
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-5")
        verify_overlay("tx-2-5", 2)
        page.wait_for_function("""() => {
            const el = document.querySelector('.pdf-stage'), region = document.querySelector(".pdf-source-highlight[data-source-transaction='tx-2-5']");
            return region && Math.abs(region.getBoundingClientRect().top + region.getBoundingClientRect().height / 2 - el.getBoundingClientRect().top - Math.min(120, el.clientHeight * 0.2)) < 2;
        }""")
        grid_before = grid.evaluate("el => el.scrollTop")
        page.wait_for_timeout(350)
        assert grid.evaluate("el => el.scrollTop") == grid_before
        assert len(downloads) == 1

        # PDF margin hover follows an existing line without a click or drawing.
        point = page.locator("[data-pdf-page='2']").evaluate("""(el, r) => {
            const b = el.getBoundingClientRect();
            return {x: b.right - 12, y: b.top + (r.top + r.height / 2) * b.height};
        }""", next(row for row in transactions if row["transaction_id"] == "tx-2-7")["source_regions"][0])
        stationary_pdf = stage.evaluate("el => el.scrollTop")
        page.mouse.move(point["x"], point["y"], steps=5)
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-7")
        verify_overlay("tx-2-7", 2)
        assert abs(stage.evaluate("el => el.scrollTop") - stationary_pdf) < 2
        assert writes == writes_before_sync
        page.screenshot(path=str(evidence / "reading-marker-desktop.png"))

        # Real wheel scrolling of the table drives PDF navigation, without a
        # programmatic PDF scroll pulling the table back to its previous row.
        grid.hover()
        page.mouse.wheel(0, 220)
        page.wait_for_function("() => { const row = document.querySelector('tr.source-active-row'); return row && row.dataset.transactionId !== 'tx-2-7'; }")
        table_selected = page.locator("tr.source-active-row").get_attribute("data-transaction-id")
        assert table_selected
        expect(page.locator(f".pdf-source-highlight[data-source-transaction='{table_selected}']").first).to_be_visible()
        page.wait_for_timeout(500)
        assert page.locator("tr.source-active-row").get_attribute("data-transaction-id") == table_selected
        steady_grid = grid.evaluate("el => el.scrollTop")
        page.wait_for_timeout(250)
        assert grid.evaluate("el => el.scrollTop") == steady_grid

        # PDF scrolling selects another transaction inside the same page, and
        # highlights both views. The automatic table scroll must not move PDF.
        source = next(row for row in transactions if row["transaction_id"] == "tx-2-15")["source_regions"][0]
        stage.hover()
        delta = stage.evaluate("""(el, r) => {
            const slot = el.querySelector(`[data-pdf-page='${r.page}']`);
            return slot.getBoundingClientRect().top - el.getBoundingClientRect().top + (r.top + r.height / 2) * slot.getBoundingClientRect().height - Math.min(120, el.clientHeight * 0.2);
        }""", source)
        page.mouse.wheel(0, delta)
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-15")
        verify_overlay("tx-2-15", 2)
        aligned(2)
        steady_pdf = stage.evaluate("el => el.scrollTop")
        page.wait_for_timeout(350)
        assert abs(stage.evaluate("el => el.scrollTop") - steady_pdf) < 2
        assert writes == writes_before_sync

        # Reselecting the current file clears selection without remounting PDF.
        # New hover requests still navigate even when their token restarts.
        for identifier in ("tx-2-6", "tx-2-7"):
            page.get_by_role("option", name=re.compile("long.pdf")).click()
            expect(page.locator(".pdf-source-highlight")).to_have_count(0)
            page.locator(f"tr[data-transaction-id='{identifier}']").hover()
            verify_overlay(identifier, 2)
            page.wait_for_function("""id => {
                const el = document.querySelector('.pdf-stage'), region = document.querySelector(`.pdf-source-highlight[data-source-transaction='${id}']`);
                return region && Math.abs(region.getBoundingClientRect().top + region.getBoundingClientRect().height / 2 - el.getBoundingClientRect().top - Math.min(120, el.clientHeight * 0.2)) < 2;
            }""", arg=identifier)
        assert len(downloads) == 1

        locate("tx-6-0")
        verify_overlay("tx-6-0", 6)
        locate("tx-2-10")
        verify_overlay("tx-2-10", 2)
        expect(page.locator("[data-pdf-page='2'] .pdf-source-highlight")).to_have_count(2)
        region_before = page.locator("[data-pdf-page='2'] .pdf-source-highlight").first.get_attribute("style")
        selected_input = page.locator("tr[data-transaction-id='tx-2-10'] input").nth(1)
        selected_input.fill("Edited source anchor")
        selected_input.press("Tab")
        assert page.locator("[data-pdf-page='2'] .pdf-source-highlight").first.get_attribute("style") == region_before
        locate("tx-2-10")
        assert len(downloads) == 1
        # Editing pauses the inverse follow, retaining the selected transaction's
        # multiple original regions while reading its continuation page.
        selected_input.focus()
        wheel_to(3)
        expect(page.locator(".pdf-controls strong")).to_have_text("3")
        verify_overlay("tx-2-10", 3)
        page.get_by_role("button", name="Ajustar à largura").focus()
        locate("tx-3-10")
        expect(page.locator(".pdf-source-highlight[data-source-transaction='tx-2-10']")).to_have_count(0)
        verify_overlay("tx-3-10", 3)
        page.get_by_role("button", name="Aumentar zoom").click()
        verify_overlay("tx-3-10", 3)
        page.get_by_role("button", name="Ajustar à largura").click()
        verify_overlay("tx-3-10", 3)
        page.screenshot(path=str(evidence / "desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        expect(stage).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        verify_overlay("tx-3-10", 3)
        page.screenshot(path=str(evidence / "mobile.png"))
        stage.focus()
        stage.press("PageDown")
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.get_by_role("option", name=re.compile("short.pdf")).click()
        expect(page.locator(".pdf-source-highlight")).to_have_count(0)
        expect(page.get_by_role("button", name="Recarregar PDF")).to_be_visible()
        expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible()
        page.get_by_role("button", name="Recarregar PDF").click()
        expect(page.locator(".pdf-controls")).to_contain_text("de 2")
        expect(page.locator(".pdf-controls strong")).to_have_text("1")
        page.get_by_role("option", name=re.compile("long.pdf")).click()
        expect(page.locator(".pdf-controls")).to_contain_text("de 30")
        expect(page.locator('input[value="Preserved edit"]')).to_have_count(1)
        expect(page.locator('input[value="Edited source anchor"]')).to_have_count(1)
        expect(page.locator(".pdf-source-highlight")).to_have_count(0)
        assert not any("review" in path for path in writes)

        def pdf_target(identifier: str, number: int, kind="extracted"):
            return page.locator(f"[data-pdf-page='{number}'] button[data-pdf-target='{identifier}'][data-region-kind='{kind}']").first

        def evidence_rect(identifier: str, number: int):
            source = next(row for row in transactions if row["transaction_id"] == identifier)["source_regions"][0]
            return page.locator(f"[data-pdf-page='{number}']").evaluate("""(el, r) => {
                const b = el.getBoundingClientRect();
                return {x: b.left + r.left * b.width - 3, y: b.top + r.top * b.height - 3,
                    width: r.width * b.width + 6, height: r.height * b.height + 6};
            }""", source)

        # Direct PDF interaction selects the exact reviewer row without moving
        # the PDF to a reading-line estimate or saving any financial edits.
        locate("tx-2-9")
        target = pdf_target("tx-2-7", 2)
        target.scroll_into_view_if_needed()
        unchanged_pdf = stage.evaluate("el => el.scrollTop")
        unchanged_writes = len(writes)
        target.hover()
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-7")
        verify_overlay("tx-2-7", 2)
        assert abs(stage.evaluate("el => el.scrollTop") - unchanged_pdf) < 2
        pdf_target("tx-2-9", 2).click()
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-9")
        assert len(writes) == unchanged_writes
        original_row = next(row for row in transactions if row["transaction_id"] == "tx-2-9")
        original_finances = {key: original_row[key] for key in ("date", "description", "amount", "source_regions")}

        # Draw over another real line: the manually associated transaction must
        # win pointer selection over automatic evidence inside its rectangle.
        rectangle = evidence_rect("tx-2-12", 2)
        page.get_by_role("button", name="Marcar no PDF", exact=True).click()
        page.mouse.move(rectangle["x"], rectangle["y"])
        page.mouse.down()
        page.mouse.move(rectangle["x"] + rectangle["width"], rectangle["y"] + rectangle["height"], steps=6)
        page.mouse.up()
        expect(page.get_by_role("button", name="Marcar no PDF", exact=True)).to_be_visible()
        expect(page.locator(".pdf-source-highlight.is-manual")).to_have_count(1)
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
        assert len(original_row["review_regions"]) == 1
        pdf_target("tx-2-9", 2, "manual").click()
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-9")
        verify_overlay("tx-2-9", 2)
        assert {key: original_row[key] for key in original_finances} == original_finances

        # A full reload fetches the persisted reviewer metadata, not UI cache.
        page.reload()
        expect(page.locator(".pdf-controls")).to_contain_text("de 30", timeout=60000)
        locate("tx-2-9")
        expect(page.locator(".pdf-source-highlight.is-manual")).to_have_count(1)
        verify_overlay("tx-2-9", 2)
        page.get_by_role("button", name="Marcar no PDF", exact=True).click()
        wheel_to(3)
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-9")
        rectangle = evidence_rect("tx-3-8", 3)
        page.mouse.move(rectangle["x"], rectangle["y"])
        page.mouse.down()
        page.mouse.move(rectangle["x"] + rectangle["width"], rectangle["y"] + rectangle["height"], steps=6)
        page.mouse.up()
        if page.get_by_role("button", name="Cancelar marcação", exact=True).count():
            print("MANUAL_SECOND_DEBUG", {"rectangle": rectangle, "stage": stage.bounding_box(),
                "selected": page.locator("tr.source-active-row").get_attribute("data-transaction-id"),
                "layers": page.locator(".pdf-interaction-layer[data-drawing-for]").count()}, flush=True)
            page.screenshot(path=str(evidence / "manual-debug.png"))
        expect(page.get_by_role("button", name="Marcar no PDF", exact=True)).to_be_visible()
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
        assert len(original_row["review_regions"]) == 2
        pdf_target("tx-2-9", 3, "manual").click()
        expect(page.locator("tr.source-active-row")).to_have_attribute("data-transaction-id", "tx-2-9")
        verify_overlay("tx-2-9", 3)

        # Cancel a draft before pointer-up: no metadata write may occur.
        unchanged_writes = len(writes)
        page.get_by_role("button", name="Marcar no PDF", exact=True).click()
        page.mouse.move(rectangle["x"], rectangle["y"])
        page.mouse.down()
        page.mouse.move(rectangle["x"] + rectangle["width"], rectangle["y"] + rectangle["height"])
        page.keyboard.press("Escape")
        page.mouse.up()
        page.wait_for_timeout(800)
        assert len(writes) == unchanged_writes
        assert len(original_row["review_regions"]) == 2
        page.get_by_role("button", name="Limpar marcações", exact=True).click()
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
        assert original_row["review_regions"] == []
        page.get_by_role("button", name="Desfazer", exact=True).click()
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
        assert len(original_row["review_regions"]) == 2

        # Real touch events in a narrow viewport draw in the same normalized
        # coordinate system, without turning the gesture into native panning.
        page.set_viewport_size({"width": 390, "height": 844})
        rectangle = evidence_rect("tx-3-14", 3)
        page.get_by_role("button", name="Marcar no PDF", exact=True).click()
        touch = context.new_cdp_session(page)
        touch.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": rectangle["x"], "y": rectangle["y"]}]})
        touch.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": rectangle["x"] + rectangle["width"], "y": rectangle["y"] + rectangle["height"]}]})
        touch.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        expect(page.get_by_role("button", name="Marcar no PDF", exact=True)).to_be_visible()
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible()
        assert len(original_row["review_regions"]) == 3
        assert {key: original_row[key] for key in original_finances} == original_finances
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(evidence / "manual-mobile.png"))
        page.set_viewport_size({"width": 1600, "height": 1000})
        page.screenshot(path=str(evidence / "manual-desktop.png"))
        assert not any("review" in path for path in writes)
        assert errors == [], errors
        print(json.dumps({"result": "PASS", "pages": 30, "transactions": len(transactions), "checks": [
            "real wheel scroll and grid alignment", "bounded canvases", "automatic synchronization without controls or banner",
            "unmatched pages", "filters", "grouped layout", "image-only page", "editing focus and resume",
            "explicit origin navigation", "no document reload", "zoom", "mobile 390px", "keyboard",
            "document switch preserving edits", "expired access and retry", "Promise.withResolvers fallback",
            "no review writes", "no browser errors", "source overlays over real canvas ink", "multiline and multipage regions",
            "intrinsic page rotation and crop", "stable origin after editing", "legacy page-only fallback", "cleared document selection",
            "hover highlight without clicking", "table wheel drives PDF", "PDF wheel follows transactions within a page",
            "stationary pointer does not change the followed row", "no bidirectional scroll feedback", "hover paused during editing",
            "navigation after clearing and reselecting the current document",
            "direct PDF hover and click select the reviewer row", "manual rectangle association and autosave",
            "manual association overrides overlapping automatic hit target", "manual marks survive a full reload",
            "manual marks across intrinsically rotated/cropped pages", "cancelled draft does not save",
            "clear and undo manual marks", "real touch drawing at mobile width", "manual marks preserve financial values and extraction evidence",
            "automatic full-width reading marker follows the blue row",
            "PDF margin hover follows a real line without clicks or manual drawing",
        ]}))
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    run(parser.parse_args().url.rstrip("/"))
