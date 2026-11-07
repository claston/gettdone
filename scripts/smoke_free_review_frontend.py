"""Offline browser check of Free review guidance and document warnings."""

import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright
from smoke_playwright_navigation import pick_free_port, wait_for_http_ready

ROOT = Path(__file__).resolve().parents[1]
GUIDANCE = "Revise os lançamentos e confira os dados com o documento original antes de baixar o arquivo."


def converted_review_cases():
    """Use real PDF parsing results for the two review regressions."""
    sys.path.insert(0, str(ROOT / "backend"))
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    os.environ.update(TEXTRACT_ENABLED="false", PDF_OCR_ENABLED="false")
    empty_page = (
        "Banco Teste - Extrato bancário", "Agência: 0001 Conta: 12345-6",
        "Período: 01/09/2026 a 30/09/2026", "Saldo anterior: R$ 150,00",
        "Nenhum lançamento no período.", "Saldo final: R$ 150,00",
    )
    compound_pages = (
        ("EXTRATO DE CONTA CORRENTE", "01/06/2026 PIX RECEBIDO 350,00", "02/06/2026 PIX ENVIADO -20,00",
         "03/06/2026 PIX RECEBIDO 100,00", "04/06/2026 PIX ENVIADO -10,00", "SALDO FINAL 420,00"),
        ("COMPROVANTE DE TRANSFERÊNCIA PIX", "VALOR 350,00", "AUTENTICAÇÃO ABC", "DATA DA TRANSAÇÃO 01/06/2026"),
    )
    pipeline = build_default_conversion_pipeline()
    empty = asdict(pipeline.run(filename="empty.pdf", raw_bytes=generate_native_text_pdf((empty_page, empty_page)),
                               analysis_id="an_review_empty").analysis_data)
    compound = asdict(pipeline.run(filename="compound.pdf", raw_bytes=generate_native_text_pdf(compound_pages),
                                  analysis_id="an_review_compound").analysis_data)
    assert empty["transactions_total"] == 0 and empty["pdf_processing_metrics"]["statement_no_activity"] == 1
    assert empty["pdf_processing_metrics"]["page_count"] == 2
    assert empty["layout_inference_name"] == "generic_statement_ptbr"
    assert compound["transactions_total"] == 4
    assert compound["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
    assert any(part["document_type"] == "bank_receipt" and not part["requires_review"]
               for part in compound["document_parts"])
    return empty, compound


def main():
    empty, compound = converted_review_cases()
    from app.application.pdf_review_guidance import build_pdf_review_guidance

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
                        if "/convert-edits/" in path:
                            patch = request.request.post_data_json["edits"][0]
                            analysis["preview_transactions"][0]["document_number"] = patch["document_number"]
                            body = analysis
                        request.fulfill(status=status, headers=headers, content_type="application/json", body=json.dumps(body))

                    page.route("**/*", route)
                    page.goto(base + "/" + filename, wait_until="domcontentloaded")
                    page.locator("#file-input").set_input_files({
                        "name": "synthetic.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                    page.locator("#convert-btn").click()
                    expect(page.locator("#review-section")).to_be_visible()
                    expect(page.locator("#review-section").get_by_text(GUIDANCE, exact=True)).to_be_visible()
                    expect(page.locator(".review-warning-title")).to_have_count(0)
                    expect(page.locator("#review-section th")).to_have_text([
                        "Data", "Histórico", "Documento", "Crédito", "Débito", "Saldo", "Ações"])
                    page.get_by_role("button", name="Editar linha", exact=True).click()
                    page.get_by_role("textbox", name="Documento", exact=True).fill("000ABC-01/2")
                    expect(page.get_by_role("textbox", name="Documento", exact=True)).to_have_value("000ABC-01/2")
                    with page.expect_response(lambda response: response.request.method == "POST"
                                              and "/convert-edits/" in urlparse(response.url).path) as saved:
                        page.get_by_role("button", name="Salvar edição", exact=True).click()
                    assert saved.value.status == 200
                    expect(page.get_by_role("dialog", name="Editar lançamento")).not_to_be_visible()
                    expect(page.locator("#review-rows tr").first.locator("td").nth(2)).to_have_text("000ABC-01/2")
                    page.reload(wait_until="domcontentloaded")
                    expect(page.locator("#review-rows tr").first.locator("td").nth(2)).to_have_text("000ABC-01/2")
                    page.get_by_role("button", name="Editar linha", exact=True).click()
                    page.get_by_role("textbox", name="Documento", exact=True).fill("")
                    page.get_by_role("button", name="Salvar edição", exact=True).click()
                    expect(page.locator("#review-rows tr").first.locator("td").nth(2)).to_have_text("—")
                    analysis["pdf_processing_metrics"] = {
                        "export_recommendation": "review_recommended",
                        "canonical_warning_types_list": ["ocr_control_confidence_unconfirmed", "ocr_structure_review_required"],
                    }
                    page.locator("#file-input").set_input_files({
                        "name": "synthetic-review.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                    page.locator("#convert-btn").click()
                    expect(page.locator(".review-warning-list li")).to_have_count(2)
                    # Exercise restored results through the real initialization path too.
                    page.reload(wait_until="domcontentloaded")
                    expect(page.locator("#review-section").get_by_text(GUIDANCE, exact=True)).to_be_visible()
                    expect(page.locator(".review-warning-title")).to_have_text("Atenção na revisão")
                    expect(page.locator(".review-warning-list li")).to_have_count(2)
                    expect(page.locator(".review-warning-list")).to_contain_text("Confira os saldos no PDF")
                    expect(page.locator(".review-warning-list")).to_contain_text("A organização de algumas linhas")
                    for case, pdf_name in ((empty, "empty.pdf"), (compound, "compound.pdf")):
                        analysis = case
                        page.locator("#file-input").set_input_files({
                            "name": pdf_name, "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                        page.locator("#convert-btn").click()
                        expected_title = "Extrato sem movimentação no período." if case is empty else "Atenção na revisão"
                        expect(page.locator(".review-warning-title")).to_have_text(expected_title)
                        for restored in (False, True):
                            if restored:
                                page.reload(wait_until="domcontentloaded")
                            expect(page.locator(".review-warning-title")).to_have_text(expected_title)
                            expect(page.locator("#review-section").get_by_text(GUIDANCE, exact=True)).to_be_visible()
                            expect(page.locator("#kpis")).not_to_contain_text("Leitura parcial detectada")
                            if case is compound:
                                expect(page.locator(".review-warning-list")).to_contain_text("Páginas 2: comprovantes")
                                expect(page.locator(".review-warning-list")).to_contain_text("recomendação de revisão manual")
                    for category, metrics in (
                        ("validation_incomplete", {"balance_consistency_checked": 1}),
                        ("information", {"financial_controls_validated": 1, "balance_validation_status": "validated",
                         "printed_balance_checks": 3, "statement_row_coverage_rate": 1,
                         "canonical_warning_types_list": ["shared_operation_reference", "manual_review_recommended"]}),
                        ("financial_discrepancy", {"balance_consistency_failed": 1}),
                    ):
                        analysis = {"analysis_id": "an_guidance_smoke", "file_type": "pdf", "transactions_total": 1,
                                    "total_inflows": 10, "total_outflows": 0, "net_total": 10,
                                    "preview_transactions": [{"date": "2026-10-01", "description": "Crédito fictício",
                                                              "amount": 10, "warning_types": []}],
                                    "pdf_processing_metrics": {"export_recommendation": "review_recommended", **metrics}}
                        analysis["review_guidance"] = build_pdf_review_guidance(analysis)
                        page.locator("#file-input").set_input_files({
                            "name": category + ".pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1.4\n%%EOF"})
                        page.locator("#convert-btn").click()
                        for restored in (False, True):
                            if restored:
                                page.reload(wait_until="domcontentloaded")
                            expect(page.locator(".review-warning-title")).to_have_text(analysis["review_guidance"]["title"])
                            expect(page.locator(".review-warning-card")).to_have_attribute("data-review-category", category)
                            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Horizontal overflow"
                    assert not errors, errors
                    context.close()
            browser.close()
        print("Free review browser smoke passed: three pages, mobile/desktop, conversion/restoration and three review categories.")
    finally:
        server.terminate()
        try:
            server.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=3)


if __name__ == "__main__":
    main()
