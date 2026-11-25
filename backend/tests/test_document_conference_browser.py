"""Synthetic statement/receipt/invoice through real browser, HTTP and PostgreSQL."""
import os
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from psycopg.types.json import Jsonb
from test_canonical_pro import database as canonical_database
from test_document_conference import setup
from test_financial_center import add_document

BROWSER_URL = os.getenv("DOCUMENT_CONFERENCE_BROWSER_URL")
pytestmark = pytest.mark.skipif(not BROWSER_URL, reason="Requires PRO build with financial center enabled")


@pytest.fixture
def database(tmp_path):
    yield from canonical_database.__wrapped__(tmp_path)


def test_compare_confirm_reopen_undo_and_mobile(database, tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
    from app.dependencies import (
        get_access_control_service,
        get_async_conversion_report_service,
        get_conversion_batch_document_store,
        get_conversion_batch_service,
        get_conversion_runtime_config,
        get_report_service,
    )
    from app.main import app
    from app.routers import canonical_pro, financial_center
    from tests.test_convert_edits_http_real_api import _run_http_server

    service, scope, movement, receipt = setup(database)
    repo, _, _, _, batches, _ = database
    note, content = add_document(database, suffix="invoice", rows=[])
    content = deepcopy(content)
    content.update(document_type="fiscal_invoice", financial_document={
        "document_type": "fiscal_invoice", "observations": [{"observation_type": "document_field",
        "dates": {"issue_date": "2026-04-01"}, "amounts": {},
        "attributes": {"label": "Prestador", "value": "Fornecedor Exemplo"}, "source_refs": [{"page": 1}]}]})
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}conversion_jobs SET result_payload=%s WHERE job_id=%s",
                     (Jsonb({"analysis": content}), note.job.job_id))
        for job, name in ((scope.job_id, "extrato-exemplo.pdf"), (receipt, "comprovante-exemplo.pdf"),
                          (note.job.job_id, "nota-exemplo.pdf")):
            conn.execute(f"UPDATE {repo.p}conversion_jobs SET document=document || %s WHERE job_id=%s",
                         (Jsonb({"filename": name, "file_type": "pdf"}), job))
    pdf = PdfWriter()
    page = pdf.add_blank_page(width=595, height=842)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 14 Tf 50 780 Td (DOCUMENTO SINTETICO PARA TESTE) Tj "
                    b"0 -25 Td (Fornecedor Exemplo) Tj 0 -25 Td (01/04/2026 - R$ 10,00) Tj ET")
    page[NameObject("/Contents")] = pdf._add_object(stream)
    buffer = BytesIO()
    pdf.write(buffer)
    original = buffer.getvalue()
    canonical = CanonicalProService(repo, None)
    report = ReportService(TempAnalysisStorage(tmp_path / "legacy"))
    monkeypatch.setenv("PRO_FINANCIAL_CENTER_ENABLED", "true")
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: canonical)
    monkeypatch.setattr(financial_center, "get_canonical_pro_service", lambda: canonical)
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repo.test_access
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_report_service] = lambda: report
        app.dependency_overrides[get_async_conversion_report_service] = lambda: report
        app.dependency_overrides[get_conversion_batch_document_store] = lambda: SimpleNamespace(
            load=lambda reference: SimpleNamespace(raw_bytes=original, filename=reference.filename))
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {"CONVERSION_ARCHITECTURE_MODE": "async_aws"})
    with _run_http_server(configure) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        def forward(route):
            parsed = urlsplit(route.request.url)
            if parsed.path.startswith(("/api/", "/auth/", "/convert-", "/telemetry/")):
                response = route.fetch(url=api + parsed.path + ("?" + parsed.query if parsed.query else ""),
                    headers={**route.request.headers, "Authorization": f"Bearer {repo.test_user.token}",
                             "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"})
                route.fulfill(response=response)
            else:
                route.continue_()
        page.route("**/*", forward)
        url = f"{BROWSER_URL}/documents?client={scope.client_id}&month=2026-04"
        page.goto(url)
        expect(page.get_by_role("region", name="Conferência por movimentação")).to_be_visible(timeout=60000)
        expect(page.get_by_role("button", name="Conferir pacote (0)")).to_have_count(0)
        page.get_by_label("Documento para comparar").select_option(receipt)
        confirm = page.get_by_role("button", name="Confirmar relação documental")
        expect(confirm).to_be_disabled()
        page.get_by_role("button", name="Ver documento original").click()
        try:
            expect(page.locator("canvas").first).to_be_visible(timeout=30000)
        except AssertionError:
            raise AssertionError(page.locator("body").inner_text() + "\n" + str(errors)) from None
        page.get_by_role("button", name="Fechar original").click()
        page.get_by_label("Conferi os documentos", exact=False).check()
        confirm.click()
        expect(page.get_by_role("button", name="Desfazer relação")).to_have_count(1)
        page.get_by_label("Documento para comparar").select_option(note.job.job_id)
        page.get_by_label("Conferi os documentos", exact=False).check()
        confirm.click()
        expect(page.get_by_role("button", name="Desfazer relação")).to_have_count(2)
        page.reload()
        expect(page.get_by_role("button", name="Desfazer relação")).to_have_count(2, timeout=30000)
        artifacts = Path(__file__).parents[2] / "local/ux-conferencia-pro/implemented"
        artifacts.mkdir(exist_ok=True, parents=True)
        page.screenshot(path=str(artifacts / "desktop.png"), full_page=True)
        assert page.get_by_role("button", name="Menu", exact=True).count() == 0 or not page.get_by_role(
            "button", name="Menu", exact=True).is_visible()
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Original", exact=False).first.click()
        expect(page.get_by_role("region", name="Comparação documental")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(artifacts / "mobile.png"), full_page=True)
        page.get_by_role("button", name="Desfazer relação").first.click()
        expect(page.get_by_role("button", name="Desfazer relação")).to_have_count(1)
        page.get_by_role("button", name="Voltar às movimentações").click()
        expect(page.get_by_role("region", name="Comparação documental")).not_to_be_visible()
        page.get_by_role("button", name="Arquivos", exact=True).click()
        page.get_by_role("button", name="comprovante-exemplo.pdf", exact=False).click()
        page.get_by_role("button", name="Comparar com movimentação").click()
        page.get_by_role("button", name="Original", exact=False).first.click()
        expect(page.get_by_label("Documento para comparar")).to_have_value(receipt)
        assert not errors, errors
        browser.close()
