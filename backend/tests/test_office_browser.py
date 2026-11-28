"""Real browser -> HTTP -> PostgreSQL -> current exporters, with synthetic inputs."""
import os
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from zipfile import ZipFile

import pytest
from test_canonical_movements_postgres import enroll
from test_canonical_pro import database as canonical_database
from test_canonical_pro import enable

BROWSER_URL = os.getenv('OFX70_BROWSER_URL')
pytestmark = pytest.mark.skipif(not BROWSER_URL, reason='Requires local PRO build (OFX70_BROWSER_URL)')


@pytest.fixture
def database(tmp_path):
    yield from canonical_database.__wrapped__(tmp_path)


def test_client_month_select_preview_and_download_current_package(database, tmp_path, monkeypatch):
    from playwright.sync_api import expect, sync_playwright

    from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
    from app.application import ReportService, TempAnalysisStorage
    from app.application.canonical_pro import CanonicalProService
    from app.application.conversion.conversion_runtime_config import ConversionRuntimeConfig
    from app.application.conversion.uploaded_document import ingest_uploaded_document
    from app.dependencies import (
        get_access_control_service,
        get_async_conversion_report_service,
        get_conversion_batch_document_store,
        get_conversion_batch_service,
        get_conversion_runtime_config,
        get_report_service,
    )
    from app.main import app
    from app.routers import canonical_pro
    from tests.test_convert_edits_http_real_api import _run_http_server

    monkeypatch.delenv('PRO_OFFICE_ENABLED', raising=False)
    monkeypatch.delenv('PRO_ORGANIZATION_ENABLED', raising=False)
    repo, scope, content, _, batches, organization = database
    content['document_type'] = 'bank_statement'
    enroll(database, content)
    enable(database)
    canonical = CanonicalProService(repo, LocalCanonicalObjects(tmp_path / 'objects'))
    canonical.edit(scope, [], expected_version=1, operation_id='reviewed', mark_reviewed=True)
    monkeypatch.setattr(canonical_pro, 'get_canonical_pro_service', lambda: canonical)
    report = ReportService(TempAnalysisStorage(tmp_path / 'legacy'))
    original = ingest_uploaded_document('test.csv', b'date,amount,description\n2026-04-01,10.00,Original\n')
    def configure(_):
        app.dependency_overrides[get_access_control_service] = lambda: repo.test_access
        app.dependency_overrides[get_conversion_batch_service] = lambda: SimpleNamespace(repository=batches)
        app.dependency_overrides[get_report_service] = lambda: report
        app.dependency_overrides[get_async_conversion_report_service] = lambda: report
        app.dependency_overrides[get_conversion_batch_document_store] = lambda: SimpleNamespace(load=lambda _: original)
        app.dependency_overrides[get_conversion_runtime_config] = lambda: ConversionRuntimeConfig.from_mapping(
            {'CONVERSION_ARCHITECTURE_MODE': 'async_aws'})
    with _run_http_server(configure) as api, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def forward(route):
            parsed = urlsplit(route.request.url)
            if parsed.path.startswith(('/api/', '/auth/', '/convert-', '/telemetry/')):
                response = route.fetch(url=api + parsed.path + ('?' + parsed.query if parsed.query else ''),
                    headers={**route.request.headers, 'Authorization': f'Bearer {repo.test_user.token}',
                             'Origin': 'http://localhost:3000', 'X-OFX-Pro-Client': 'web'})
                route.fulfill(response=response)
            else:
                route.continue_()
        page.route('**/*', forward)
        page.goto(f'{BROWSER_URL}/documents?client={scope.client_id}&month=2026-04')
        page.get_by_role('button', name='Recusar opcionais', exact=True).click()
        expect(page.locator('#privacy-consent-banner')).to_be_hidden()
        page.get_by_role('button', name='Entrega', exact=True).click(timeout=60000)
        expect(page.get_by_role('button', name='Conferir pacote (0)')).to_be_visible(timeout=60000)
        expect(page.get_by_label('Mês de trabalho')).to_have_value('2026-04')
        page.get_by_role('checkbox', name='Selecionar test.csv para o pacote').check()
        page.get_by_role('button', name='Conferir pacote (1)').click()
        download = page.get_by_role('button', name='Baixar pacote ZIP')
        expect(download).to_be_enabled(timeout=30000)
        with page.expect_download() as event:
            download.click()
        with ZipFile(BytesIO(Path(event.value.path()).read_bytes())) as archive:
            assert 'manifesto.json' in archive.namelist()
            assert any(name.endswith('.ofx') for name in archive.namelist())
        artifacts = Path(__file__).parents[2] / 'tmp/ofx79-browser'
        artifacts.mkdir(exist_ok=True, parents=True)
        page.screenshot(path=str(artifacts / 'desktop.png'), full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        expect(download).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        page.screenshot(path=str(artifacts / 'mobile.png'), full_page=True)
        assert not errors
        browser.close()
