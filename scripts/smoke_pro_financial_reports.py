"""Synthetic browser regression for the specialized financial-report panel."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright
from smoke_pro_pdf_sync import synthetic_pdf


def run(base: str):
    output = Path("tmp/pro-financial-reports")
    output.mkdir(parents=True, exist_ok=True)
    events = [{"event_id": f"event-{index}", "event_type": "receipt", "status": "paid",
        "description": f"Recebimento título {index:06d} — CLIENTE SINTÉTICO COM NOME COMERCIAL EXTENSO LTDA",
        "reference": f"{index:06d}", "dates": {"payment": "2026-09-21"}, "amount": "1234.56",
        "cash_effect": "1234.56", "source_refs": [{"page": 2, "line": index+1}]} for index in range(90)]
    report = {"document_type": "receivables_report", "origin": "Bradesco", "events": events,
        "notice": "Títulos recebidos: data de pagamento e valor cobrado. O documento não informa saldo bancário nem confirma a data de crédito na conta.",
        "validations": [], "export": {"available_formats": ["csv", "xlsx", "ofx"], "requires_destination_account": True}}
    fiscal = copy.deepcopy(report)
    fiscal.update(document_type="fiscal_summary", origin="Emissor sintético", events=[{**events[0],
        "event_type": "fiscal_document", "status": "voided", "cash_effect": None,
        "dates": {"issue": "2026-09-21"}, "description": "Documento fiscal 000001"}],
        notice="Emissão fiscal não comprova recebimento.")
    fiscal["export"] = {"available_formats": ["csv", "xlsx"], "requires_destination_account": False}
    positions = {**fiscal, "document_type": "investment_statement", "events": [], "origin": "Banco sintético",
                 "notice": "As posições de investimento estão disponíveis no Excel."}
    jobs = [{"job_id": f"doc-{index}", "filename": name, "analysis_id": f"analysis-{index}", "status": "completed",
        "result": {"analysis": {"file_type": "pdf", "financial_document": document,
            "pdf_processing_metrics": {"page_count": 2}, "preview_transactions": [{
                "transaction_id": "projected-"+event["event_id"], "date": "2026-09-21", "description": event["description"],
                "amount": float(event["amount"]), "source_page": 2,
            } for event in document["events"]]}}} for index, (name, document) in enumerate([
                ("recebimentos.pdf", report), ("fiscal.pdf", fiscal), ("posicoes.pdf", positions)])]
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(error.message))

        def intercept(route):
            path = urlsplit(route.request.url).path
            if path == "/auth/me":
                route.fulfill(json={"user_id": "synthetic", "name": "Synthetic", "email": "synthetic@example.invalid"})
            elif path == "/telemetry/events":
                route.fulfill(status=202)
            elif path.endswith("/document"):
                route.fulfill(content_type="application/pdf", body=synthetic_pdf(2))
            elif path == "/api/pro/conversion-batches/report-review":
                route.fulfill(json={"batch_id": "report-review", "status": "completed", "jobs": jobs})
            else:
                route.continue_()

        try:
            page.route("**/*", intercept)
            page.goto(f"{base}/review/report-review/doc-0")
            expect(page.get_by_role("heading", name="Relatório de recebimentos")).to_be_visible()
            expect(page.locator("canvas").first).to_be_visible(timeout=60000)
            panel = page.get_by_role("region", name="Relatório especializado")
            expect(panel.get_by_role("row")).to_have_count(91)
            assert panel.evaluate("el => getComputedStyle(el).backgroundColor") == "rgb(255, 255, 255)"
            scroll = panel.locator(".table-scroll")
            assert scroll.evaluate("el => el.clientHeight > 150 && el.scrollHeight > el.clientHeight")
            assert panel.locator("tbody td").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") <= 12
            assert panel.locator("tbody tr").first.bounding_box()["height"] < 120
            expect(page.get_by_role("textbox", name="Banco de destino")).to_have_count(0)
            page.get_by_role("button", name="Exportar OFX", exact=True).click()
            for label, value in (("Banco de destino", "237"), ("Agência de destino", "1234"), ("Conta de destino", "56789")):
                page.get_by_role("textbox", name=label).fill(value)
            inputs = panel.locator("fieldset input")
            boxes = [inputs.nth(index).bounding_box() for index in range(3)]
            assert boxes[0]["x"]+boxes[0]["width"] < boxes[1]["x"]
            assert boxes[1]["x"]+boxes[1]["width"] < boxes[2]["x"]
            expect(page.get_by_role("link", name="Baixar OFX", exact=True)).to_have_attribute("href",
                "/convert-report/analysis-0?format=ofx&bank_code=237&bank_branch=1234&account_number=56789")
            page.get_by_role("button", name="Exportar OFX", exact=True).click()
            page.screenshot(path=str(output/"receivables-desktop.png"))
            page.get_by_role("button", name="Ver origem do evento "+events[0]["description"], exact=True).click()
            expect(page.locator(".pdf-controls strong")).to_have_text("2")

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_function("document.querySelector('.conversion-panel').getBoundingClientRect().width <= 64")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            assert scroll.evaluate("el => el.clientHeight > 40 && el.scrollHeight > el.clientHeight")
            page.screenshot(path=str(output/"receivables-mobile.png"))
            page.get_by_role("button", name="Exportar OFX", exact=True).click()
            boxes = [inputs.nth(index).bounding_box() for index in range(3)]
            assert boxes[0]["y"] < boxes[1]["y"] < boxes[2]["y"]
            page.screenshot(path=str(output/"destination-mobile.png"))

            page.set_viewport_size({"width": 1440, "height": 1000})
            page.get_by_role("listbox", name="Conversões do lote").get_by_role("option").filter(has_text="fiscal.pdf").click()
            expect(page.get_by_role("heading", name="Resumo fiscal")).to_be_visible()
            expect(page.get_by_text("Inutilizado", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Exportar OFX", exact=True)).to_have_count(0)
            page.screenshot(path=str(output/"fiscal-desktop.png"))
            page.get_by_role("listbox", name="Conversões do lote").get_by_role("option").filter(has_text="posicoes.pdf").click()
            expect(page.get_by_role("heading", name="Extrato de investimentos")).to_be_visible()
            expect(page.get_by_text("Sem movimentos neste período", exact=True)).to_be_visible()
            assert errors == [], errors
            print(json.dumps({"result": "PASS", "reports": 3, "events": 90,
                "checks": ["bounded scroll", "compact rows", "aligned amounts", "destination account desktop/mobile",
                           "source navigation", "fiscal semantics", "positions without movements", "mobile 390px", "no browser errors"]}))
        except Exception:
            page.screenshot(path=str(output/"failure.png"))
            raise
        finally:
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:3100")
    run(parser.parse_args().url.rstrip("/"))
