"""Real browser -> HTTP -> PostgreSQL: Central/editor/Central without mock rows."""
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from test_canonical_pro import database as canonical_database
from test_financial_center import adopt

BROWSER_URL = os.getenv("OFX74_BROWSER_URL")
pytestmark = pytest.mark.skipif(not BROWSER_URL, reason="Requires running local PRO build (OFX74_BROWSER_URL)")


@pytest.fixture
def database(tmp_path):
    yield from canonical_database.__wrapped__(tmp_path)


def test_calendar_editor_return_and_refresh(database, tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
    from app.application.financial_center import FinancialCenterService
    from app.application.received_statements import ReceivedStatements
    from app.dependencies import get_access_control_service, get_conversion_batch_service, get_conversion_runtime_config, get_report_service
    from app.main import app
    from app.routers import canonical_pro
    from app.routers.financial_center import financial_service
    from app.routers.received_statements import receipt_service
    from tests.test_convert_edits_http_real_api import _run_http_server

    repo, scope, content, _, batches, _ = database
    content["file_type"] = "csv"
    batches.update_analysis(scope.job_id, analysis=content)
    receipts = ReceivedStatements(repo.test_access.db, batches)
    receipts.receive(batches.get(scope.job_id))
    adopt(database)
    service = CanonicalProService(repo, LocalCanonicalObjects(tmp_path / "objects"))
    storage = TempAnalysisStorage(tmp_path / "legacy")
    path = storage.root_dir / scope.analysis_id / "analysis.json"
    path.parent.mkdir()
    path.write_text(json.dumps(content), encoding="utf-8")
    storage.fence_canonical_analysis(scope.analysis_id)
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: service)
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repo.test_access
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {"CONVERSION_ARCHITECTURE_MODE": "async_aws"})
        app.dependency_overrides[financial_service] = lambda: FinancialCenterService(repo)
        app.dependency_overrides[receipt_service] = lambda: receipts
    with _run_http_server(configure) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
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
        page.goto(f"{BROWSER_URL}/dashboard")
        expect(page.get_by_role("link", name="Central Financeira", exact=True)).to_be_visible(timeout=60000)
        page.get_by_role("link", name="Central Financeira", exact=True).click()
        page.goto(f"{BROWSER_URL}/financial/{scope.client_id}?month=2026-04")
        expect(page.get_by_role("button", name="01/04/2026: 1 movimentações, com pendências")).to_be_visible(timeout=60000)
        page.get_by_role("button", name="01/04/2026: 1 movimentações, com pendências").click()
        expect(page.get_by_text("Original", exact=True)).to_be_visible()
        page.get_by_role("link", name="Abrir origem", exact=False).click()
        expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible(timeout=60000)
        editor = page.locator("tr[data-transaction-id='stable'] input").nth(1)
        editor.fill("Revisão vista na Central")
        editor.press("Tab")
        expect(page.get_by_text("Salvo", exact=True)).to_be_visible(timeout=15000)
        back = page.locator(f'a[href^="/financial/{scope.client_id}"]')
        expect(back).to_be_visible()
        back.click()
        expect(page.get_by_text("Revisão vista na Central", exact=True)).to_be_visible(timeout=15000)
        assert "month=2026-04" in page.url and "day=2026-04-01" in page.url
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        browser.close()
