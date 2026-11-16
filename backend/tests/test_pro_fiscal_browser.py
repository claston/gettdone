"""PRO browser -> live HTTP -> PostgreSQL fiscal confirmation workflow."""

import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from test_canonical_pro import database as paid_database
from test_pro_fiscal_service import insert_note, setup

BROWSER_URL = os.getenv("PRO_FISCAL_BROWSER_URL")
pytestmark = pytest.mark.skipif(not BROWSER_URL, reason="Requires local PRO build (PRO_FISCAL_BROWSER_URL)")
database = paid_database


def test_invoice_link_and_separate_classification_in_browser(database, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    from app.application.financial_center import FinancialCenterService
    from app.dependencies import get_access_control_service
    from app.main import app
    from app.routers.financial_center import financial_service
    from app.routers.pro_fiscal import fiscal_service
    from tests.test_convert_edits_http_real_api import _run_http_server

    service, scope, movement = setup(database)
    note = insert_note(service, scope)
    for name in ("PRO_FISCAL_ENABLED", "PRO_TAX_ENABLED", "PRO_REVENUE_ENABLED", "PRO_ORGANIZATION_ENABLED"):
        monkeypatch.setenv(name, "true")

    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: service.repo.test_access
        app.dependency_overrides[fiscal_service] = lambda: service
        app.dependency_overrides[financial_service] = lambda: FinancialCenterService(service.repo)

    with _run_http_server(configure) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def forward(route):
            parsed = urlsplit(route.request.url)
            if parsed.path.startswith(("/api/", "/auth/", "/convert-", "/telemetry/")):
                response = route.fetch(
                    url=api + parsed.path + ("?" + parsed.query if parsed.query else ""),
                    headers={
                        **route.request.headers,
                        "Authorization": f"Bearer {service.repo.test_user.token}",
                        "Origin": "http://localhost:3000",
                        "X-OFX-Pro-Client": "web",
                    },
                )
                route.fulfill(response=response)
            else:
                route.continue_()

        page.route("**/*", forward)
        page.goto(f"{BROWSER_URL}/financial/{scope.client_id}?month=2026-04")
        page.get_by_role("button", name="Conciliação", exact=True).click(timeout=60000)
        expect(page.get_by_label("Nota fiscal", exact=True)).to_be_visible(timeout=15000)
        page.get_by_label("Nota fiscal", exact=True).select_option(note)
        page.get_by_label("Movimento para vincular", exact=True).select_option(movement)
        page.get_by_role("button", name="Confirmar vínculo", exact=True).click()
        expect(page.get_by_text("Vínculo confirmado", exact=False)).to_be_visible(timeout=15000)
        assert service.workspace(scope.actor_id, scope.client_id, month="2026-04")["summary"]["operating_receipts"] == "0.00"
        page.get_by_role("button", name="Gestão Tributária", exact=True).click()
        expect(page.get_by_role("heading", name="Gestão Tributária", exact=True)).to_be_visible()
        page.get_by_label("Categoria de Original", exact=True).select_option("operating_receipt")
        with page.expect_response(lambda response: "/classifications/" in response.url and response.request.method == "POST") as saved:
            page.get_by_role("button", name="Confirmar classificação", exact=True).click()
        assert saved.value.status == 200
        page.get_by_role("button", name="Relatórios", exact=True).click()
        expect(page.get_by_text("Recebimentos operacionais identificados", exact=True)).to_be_visible()
        assert service.workspace(scope.actor_id, scope.client_id, month="2026-04")["summary"]["operating_receipts"] == "10.00"
        output = Path(__file__).resolve().parents[2] / "tmp/pro-fiscal-browser"
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(output / "mobile.png"), full_page=True)
        assert not errors, errors
        browser.close()
