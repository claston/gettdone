"""Two real browser editors -> real HTTP -> disposable PostgreSQL + private objects.

Run against a local PRO production build using OFX70_BROWSER_URL. The route
bridge only supplies the test owner's bearer token and forwards to live HTTP;
it does not fabricate API responses or SQL/storage outcomes.
"""
import json
import os
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from test_canonical_movements_postgres import enroll
from test_canonical_pro import database as canonical_database
from test_canonical_pro import enable

BROWSER_URL = os.getenv("OFX70_BROWSER_URL")
pytestmark = pytest.mark.skipif(not BROWSER_URL, reason="Requires running local PRO build (OFX70_BROWSER_URL)")


@pytest.fixture
def database(tmp_path):
    yield from canonical_database.__wrapped__(tmp_path)


def test_two_editors_compare_reapply_and_refresh(database, tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
    from app.dependencies import get_access_control_service, get_conversion_batch_service, get_conversion_runtime_config, get_report_service
    from app.main import app
    from app.routers import canonical_pro
    from tests.test_convert_edits_http_real_api import _run_http_server

    repository, scope, content, _, batches, _ = database
    # CSV makes this test independent of PDF rendering/OCR, which have their own gates.
    content["file_type"] = "csv"
    batches.update_analysis(scope.job_id, analysis=content)
    enroll(database, content)
    enable(database)
    service = CanonicalProService(repository, LocalCanonicalObjects(tmp_path / "objects"))
    storage = TempAnalysisStorage(tmp_path / "legacy")
    path = storage.root_dir / scope.analysis_id / "analysis.json"
    path.parent.mkdir()
    path.write_text(json.dumps(content), encoding="utf-8")
    storage.fence_canonical_analysis(scope.analysis_id)
    monkeypatch.setattr(canonical_pro, "get_canonical_pro_service", lambda: service)
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repository.test_access
        app.dependency_overrides[get_report_service] = lambda: ReportService(storage)
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {"CONVERSION_ARCHITECTURE_MODE": "async_aws"})
    with _run_http_server(configure) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1500, "height": 1000})
        first, second = context.new_page(), context.new_page()
        delayed = []
        hold = True
        errors = []
        def forward(route):
            parsed = urlsplit(route.request.url)
            if parsed.path.startswith(("/api/", "/auth/", "/convert-", "/telemetry/")):
                response = route.fetch(url=api + parsed.path + ("?" + parsed.query if parsed.query else ""),
                    headers={**route.request.headers, "Authorization": f"Bearer {repository.test_user.token}",
                             "Origin": "http://localhost:3000", "X-OFX-Pro-Client": "web"})
                route.fulfill(response=response)
            else:
                route.continue_()
        def second_route(route):
            if hold and "/api/pro/convert-edits/" in route.request.url and route.request.method == "POST":
                delayed.append(route)
            else:
                forward(route)
        first.route("**/*", forward)
        second.route("**/*", second_route)
        for page in (first, second):
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(f"{BROWSER_URL}/review/{batches.get(scope.job_id).job.batch_id}/{scope.job_id}")
            expect(page.get_by_role("table", name="Lançamentos extraídos")).to_be_visible(timeout=60000)
        local = second.locator("tr[data-transaction-id='stable'] input").nth(1)
        remote = first.locator("tr[data-transaction-id='stable'] input").nth(1)
        local.fill("Rascunho do segundo editor")
        local.press("Tab")
        second.wait_for_timeout(900)
        assert delayed
        remote.fill("Confirmado pelo primeiro editor")
        remote.press("Tab")
        expect(first.get_by_text("Salvo", exact=True)).to_be_visible(timeout=15000)
        assert repository.read(scope)["version"] == 2
        hold = False
        for route in delayed:
            forward(route)
        expect(second.get_by_role("button", name="Comparar revisão e rascunho")).to_be_visible(timeout=15000)
        expect(local).to_have_value("Rascunho do segundo editor")
        second.get_by_role("button", name="Comparar revisão e rascunho").click()
        dialog = second.get_by_role("dialog", name="Comparar revisão")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("Confirmado pelo primeiro editor")
        expect(dialog).to_contain_text("Rascunho do segundo editor")
        dialog.get_by_role("button", name="Reaplicar minhas alterações").click()
        expect(second.get_by_text("Salvo", exact=True)).to_be_visible(timeout=15000)
        assert repository.read(scope)["version"] == 3
        first.reload()
        expect(remote).to_have_value("Rascunho do segundo editor", timeout=15000)
        assert not errors, errors
        browser.close()
