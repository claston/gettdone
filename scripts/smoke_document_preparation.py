"""Synthetic acceptance for import review, automatic evidence links and delivery."""
import argparse
from pathlib import Path
from urllib.parse import quote, urlsplit

from playwright.sync_api import expect, sync_playwright
from smoke_pro_pdf_sync import synthetic_pdf


def run(base):
    output = Path('tmp/document-preparation-browser')
    output.mkdir(parents=True, exist_ok=True)
    catalog = {'clients': [{'id': 'client_a', 'name': 'Empresa Exemplo', 'tax_id': None, 'trade_name': ''}],
               'accounts': [], 'periods': []}
    context = {'client_id': 'client_a', 'account_id': None, 'period_ids': [], 'version': 1}
    updated = '2026-11-27T12:00:00Z'
    receipt = {'document_type': 'bank_receipt', 'events': [], 'metadata': {}, 'validations': [],
               'observations': [{'observation_type': 'document_field', 'dates': {'document_date': '2026-11-27'},
                                 'amounts': {'value': '100.00'}, 'attributes': {'label': 'Valor da operação', 'value': 'R$ 100,00'},
                                 'source_refs': [{'page': 1, 'line': 1}]}],
               'notice': 'O comprovante não acrescenta uma movimentação ao extrato.',
               'export': {'available_formats': ['csv', 'xlsx'], 'requires_destination_account': False}}
    jobs = [{'job_id': 'job_bank', 'filename': 'extrato.pdf', 'analysis_id': 'an_bank', 'status': 'completed',
             'result': {'analysis': {'file_type': 'pdf', 'document_type': 'bank_statement', 'updated_at': updated,
                                    'pdf_processing_metrics': {'page_count': 2}, 'preview_transactions': [
                                     {'transaction_id': 'tx1', 'date': '2026-11-27', 'description': 'Pagamento exemplo', 'amount': -100}]} }},
            {'job_id': 'job_receipt', 'filename': 'comprovante.pdf', 'analysis_id': 'an_receipt', 'status': 'completed',
             'result': {'analysis': {'file_type': 'pdf', 'updated_at': updated, 'document_type': 'bank_receipt',
                                    'pdf_processing_metrics': {'page_count': 2}, 'preview_transactions': [], 'financial_document': receipt}}}]
    writes, errors = [], []
    read_only = False
    destination = '/documents?client=client_a&batch=import-demo&view=delivery'

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(error.message))

        def intercept(route):
            path = urlsplit(route.request.url).path
            if path == '/auth/me':
                route.fulfill(json={'user_id': 'synthetic', 'name': 'Escritório Exemplo', 'email': 'example@example.invalid'})
            elif path == '/telemetry/events':
                route.fulfill(status=202)
            elif path == '/api/pro/documents/retention':
                route.fulfill(json={'read_only': read_only, 'retained_while_paid': not read_only, 'grace_days': 30,
                                   'delete_after': '2026-12-27T00:00:00Z' if read_only else None})
            elif path.endswith('/preparation'):
                job = next(job for job in jobs if f"/{job['job_id']}/" in path)
                assert route.request.method == ('GET' if read_only else 'POST')
                route.fulfill(json={'job_id': job['job_id'], 'batch_id': 'import-demo', 'filename': job['filename'],
                    'context': context, 'catalog': catalog, 'is_bank': job['job_id'] == 'job_bank',
                    'automatically_associated': False, 'suggestions': {'clients': [], 'accounts': [], 'period': None},
                    'matching': None if job['job_id'] == 'job_bank' else {'source': 'a'*64, 'partial': False, 'matches': [
                        {'id': 'movement', 'version': 1, 'date': '2026-11-27', 'amount': '-100.00', 'currency': 'BRL',
                         'description': 'Pagamento exemplo', 'automatic': True, 'linked': True,
                         'link_id': 'link', 'link_version': 1, 'evidence': ['Mesmo identificador da operação', 'Mesmo valor']}]} })
            elif path.endswith('/document'):
                route.fulfill(content_type='application/pdf', body=synthetic_pdf(2))
            elif path == '/api/pro/conversion-batches/import-demo':
                route.fulfill(json={'batch_id': 'import-demo', 'status': 'completed', 'jobs': jobs})
            elif path.endswith('/review'):
                assert not read_only
                payload = route.request.post_data_json
                assert payload['preparation_context_version'] == 1
                assert payload['expected_updated_at'] == updated
                job = next(job for job in jobs if f"/{job['job_id']}/" in path)
                job['reviewed_at'] = '2026-11-27T12:10:00Z'
                writes.append(job['job_id'])
                route.fulfill(json={'job_id': job['job_id'], 'reviewed_at': job['reviewed_at']})
            elif path == '/api/pro/organization/catalog':
                route.fulfill(json=catalog)
            elif path == '/api/pro/documents':
                route.fulfill(json={'items': [{'job_id': job['job_id'], 'batch_id': 'import-demo', 'filename': job['filename'],
                    'context': context, 'status': 'completed', 'category': 'statements' if index == 0 else 'receipts',
                    'is_expired': False, 'expires_at': '9999-01-01T00:00:00Z', 'created_at': updated,
                    'review_url': '/review/import-demo/'+job['job_id'], 'is_bank': index == 0,
                    'bank_reviewed': bool(job.get('reviewed_at')), 'verification': {'status': 'verified', 'version': 0},
                    'movement_start_date': '2026-11-27', 'movement_end_date': '2026-11-27'} for index, job in enumerate(jobs)],
                    'next_cursor': None})
            else:
                route.continue_()

        page.route('**/*', intercept)
        try:
            page.goto(base+'/review/import-demo/job_bank?return_to='+quote(destination, safe=''))
            expect(page.get_by_role('region', name='Revisão da importação')).to_be_visible(timeout=30000)
            page.get_by_role('button', name='Recusar opcionais', exact=True).click()
            expect(page.locator('canvas').first).to_be_visible(timeout=60000)
            expect(page.get_by_label('Cliente', exact=True)).to_have_value('client_a')
            assert page.locator('.workspace-body').bounding_box()['height'] > 400
            page.screenshot(path=str(output/'review-desktop.png'))
            page.get_by_role('button', name='Confirmar e próximo', exact=True).click()
            expect(page.get_by_text('Vínculo confirmado', exact=False)).to_be_visible()
            assert 'return_to=' in page.url and '/job_receipt?' in page.url
            page.set_viewport_size({'width': 390, 'height': 844})
            expect(page.locator('.conversion-panel')).to_have_css('width', '48px')
            expect(page.locator('canvas').first).to_be_visible(timeout=60000)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert page.locator('.review-stage').bounding_box()['width'] >= 280
            assert page.locator('.workspace-body').bounding_box()['height'] > 250
            page.screenshot(path=str(output/'receipt-mobile.png'))
            page.get_by_role('button', name='Confirmar e próximo', exact=True).click()
            expect(page.get_by_role('link', name='Preparar entrega', exact=True)).to_be_visible()
            assert writes == ['job_bank', 'job_receipt']
            page.get_by_role('link', name='Preparar entrega', exact=True).click()
            expect(page.get_by_role('checkbox', name='Selecionar extrato.pdf para o pacote')).to_be_checked()
            expect(page.get_by_role('checkbox', name='Selecionar comprovante.pdf para o pacote')).to_be_checked()
            page.screenshot(path=str(output/'delivery-mobile.png'))
            read_only = True
            page.goto(base+'/review/import-demo/job_bank?return_to='+quote(destination, safe=''))
            expect(page.get_by_text('Consulta e download até', exact=False)).to_be_visible()
            expect(page.get_by_role('button', name='Confirmar e próximo', exact=True)).to_be_disabled()
            expect(page.locator('canvas').first).to_be_visible(timeout=60000)
            assert errors == [], errors
            print('PASS: single review, next document, persistent context, linked receipt, desktop/mobile, delivery, grace read-only')
        except Exception:
            page.screenshot(path=str(output/'failure.png'))
            raise
        finally:
            browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:3100')
    run(parser.parse_args().url.rstrip('/'))
