"""Evidence-based inactive statements and explanations, with fictional data."""
from copy import deepcopy

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.pdf_review_guidance import build_pdf_review_guidance
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_pdf_review_guidance import analysis


def bb_inactive(*, value='0,00', sign='C'):
    return '\n'.join([
        'G1234567890123', '10/03/2026 13:43:23',
        'Consultas - Extrato de conta corrente', 'Cliente - Conta atual', 'Agência 1234-5',
        'Conta corrente 12345-6 CONTA FICTÍCIA', 'Período do extrato 02 / 2026', 'Lançamentos',
        'Dt. balancete Dt. movimento Ag. origem Lote Histórico Documento Valor R$ Saldo',
        f'20/01/2026 0000 00000 000 Saldo Anterior {value} {sign}',
        f'28/02/2026 0000 00000 999 S A L D O {value} {sign}',
        '*** A CONTA NAO FOI MOVIMENTADA ***', '------------------------------------------------',
        'Transação efetuada com sucesso por: OPERADOR FICTÍCIO.', 'Ouvidoria BB 0800 729 5678',
    ])


def read_bb(pages, representation):
    if representation == 'native':
        return pdf_parser.parse_pdf_transactions(generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in pages)))
    from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction

    source = RawDocumentExtraction('aws_textract', 'synthetic', [ExtractedPage(p, lines=[
        ExtractedLine(f'p{p}l{i}', p, i, text) for i, text in enumerate(page.splitlines(), 1)])
        for p, page in enumerate(pages, 1)], {})
    return pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('TEXTRACT_ENABLED', 'false')
    monkeypatch.setenv('PDF_OCR_ENABLED', 'false')


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('value,sign,expected', [('0,00', 'C', 0), ('150,00', 'C', 150), ('150,00', 'D', -150)])
def test_bb_explicit_no_activity_keeps_printed_balances(representation, value, sign, expected):
    result = read_bb([bb_inactive(value=value, sign=sign)], representation)
    assert not result.transactions
    assert result.parse_metrics['statement_no_activity'] == 1
    assert result.parse_metrics['verified_opening_balance'] == expected
    assert result.parse_metrics['verified_closing_balance'] == expected
    assert result.source_page_texts


@pytest.mark.parametrize('representation', ['native', 'ocr'])
@pytest.mark.parametrize('mutation', ['movement', 'later_page', 'blank_page', 'bad_sign', 'different_balance', 'bad_period'])
def test_bb_inactivity_never_hides_movements_or_unreadable_context(representation, mutation):
    pages = [bb_inactive()]
    if mutation == 'movement':
        pages[0] += '\n20/02/2026 0000 13105 144 Pix Enviado 123456 50,00 D'
    elif mutation == 'later_page':
        pages.append('20/02/2026 PIX ENVIADO 50,00 D')
    elif mutation == 'blank_page':
        pages.append('')
    elif mutation == 'bad_sign':
        pages[0] = pages[0].replace('0,00 C', '0,00 ?')
    elif mutation == 'different_balance':
        pages[0] = pages[0].replace('S A L D O 0,00', 'S A L D O 50,00')
    else:
        pages[0] = pages[0].replace('02 / 2026', '?? / ????')
    try:
        result = read_bb(pages, representation)
    except InvalidFileContentError:
        return
    assert not result.parse_metrics.get('statement_no_activity')


def clean_medium(**metrics):
    return {**analysis(confidence_band='medium', selected_parser='tabular',
            export_recommendation_reason='medium_confidence_band', **metrics),
            'layout_inference_name': 'caixa_extrato_por_periodo_web_v1', 'layout_inference_confidence': .99}


def test_clean_medium_confidence_is_information_without_rewriting_metrics():
    data = clean_medium()
    original = deepcopy(data)
    guide = build_pdf_review_guidance(data)
    assert guide['category'] == 'information'
    assert 'saldo global' not in guide['message']
    assert data == original


@pytest.mark.parametrize('metrics', [
    {'opening_balance_ambiguous': 1}, {'partial_statement': 1}, {'balance_bounds_unavailable': 1},
    {'ocr_review_fields_count': 1}, {'ocr_unresolved_rows': 1}, {'document_period_unavailable': 1},
    {'canonical_warning_count': 1}, {'canonical_warning_types_list': ['future_warning']},
    {'balance_validation_status': 'opening_inferred_from_closing'},
    {'statement_rows_expected': 4, 'statement_rows_parsed': 3, 'statement_row_coverage_rate': .75},
    {'canonical_warning_transactions_count': 1}, {'statement_validation_failed': 1},
    {'canonical_warning_types_list': 'ocr_amount_confidence_unconfirmed|manual_review_recommended'},
])
def test_clean_quality_cannot_suppress_concrete_uncertainty(metrics):
    assert build_pdf_review_guidance(clean_medium(**metrics))['category'] != 'information'


def test_located_balance_discrepancy_explains_original_evidence():
    data = clean_medium(balance_consistency_failed=1)
    data['preview_transactions'] = [{'source_page': 3, 'source_line': 12, 'warning_types': ['balance_consistency_failed'],
        'balance_check': {'expected_balance': 80.0, 'reported_balance': 79.0, 'difference': -1.0}}]
    guide = build_pdf_review_guidance(data)
    assert guide['category'] == 'financial_discrepancy'
    assert 'Página 3, linha 12' in guide['message']
    assert '80,00' in guide['message'] and '79,00' in guide['message'] and '-1,00' in guide['message']


def test_partial_blocks_explain_location_without_inventing_a_balance():
    data = clean_medium(partial_statement=1)
    data['document_parts'] = [{'id': 'statement-2', 'pages': [4, 5], 'included_in_statement': True, 'requires_review': True}]
    guide = build_pdf_review_guidance(data)
    assert guide['category'] == 'validation_incomplete'
    assert 'páginas 4, 5' in guide['message']
    assert 'cobertura' in guide['message'].lower()


def test_unreadable_field_explains_row_and_field():
    data = clean_medium()
    data['preview_transactions'] = [{'source_page': 2, 'source_line': 8,
                                     'warning_types': ['ocr_amount_confidence_unconfirmed']}]
    guide = build_pdf_review_guidance(data)
    assert 'Página 2, linha 8' in guide['message'] and 'valor' in guide['message'].lower()


def test_legacy_insights_follow_current_guidance_without_changing_stored_data():
    from app.application.balance_checks import enrich_legacy_conversion_result

    payload = {'analysis': {**clean_medium(), 'insights': [
        {'type': 'pdf_export_review_recommended', 'title': 'Antigo', 'description': 'Revise'}]}}
    original = deepcopy(payload)
    enriched = enrich_legacy_conversion_result(payload)
    assert enriched['analysis']['insights'][0]['type'] == 'pdf_extraction_information'
    assert payload == original
    enriched['analysis']['pdf_processing_metrics']['balance_consistency_failed'] = 1
    assert enrich_legacy_conversion_result(enriched)['analysis']['insights'][0]['type'] == 'pdf_export_review_recommended'


def test_clean_medium_response_uses_informational_insight():
    from app.application.conversion.result_payload import _build_export_review_insight

    data = clean_medium()
    insight = _build_export_review_insight(extension='pdf', pdf_processing_metrics=data['pdf_processing_metrics'],
                                          guidance=build_pdf_review_guidance(data))
    assert insight.type == 'pdf_extraction_information'


@pytest.mark.real_http
def test_bb_empty_http_keeps_bank_period_and_balance_in_exports(tmp_path):
    from tests.ocr_conversion_http_helpers import public_client

    with public_client(tmp_path) as client:
        owner = {'anonymous_fingerprint': 'fp-review-lot-bb'}
        raw = generate_native_text_pdf((tuple(bb_inactive(value='150,00', sign='D').splitlines()),))
        response = client.post('/convert', data=owner, files={'file': ('inactive.pdf', raw, 'application/pdf')})
        assert response.status_code == 200, response.text
        data = response.json()['analysis']
        assert data['transactions_total'] == 0 and data['bank_code'] == '001'
        assert data['opening_balance'] == data['closing_balance'] == -150
        assert data['document_parts'][0]['period'] == {
            'start_date': '2026-02-01', 'end_date': '2026-02-28', 'basis': 'document_metadata'}
        ident = data['analysis_id']
        assert client.get('/report/' + ident, params=owner).status_code == 200
        for fmt in ('csv', 'xlsx', 'ofx'):
            exported = client.get('/convert-report/' + ident, params={**owner, 'format': fmt})
            assert exported.status_code == 200, exported.text
            if fmt == 'ofx':
                assert '<STMTTRN>' not in exported.text and '<BALAMT>-150.00' in exported.text
                assert '<DTSTART>20260201000000' in exported.text and '<DTEND>20260228000000' in exported.text
                assert '<DTASOF>20260228000000' in exported.text
        valid = client.post('/convert', data=owner, files={
            'file': ('cash.csv', b'date,description,amount\n2026-10-01,Entrada,10\n', 'text/csv')})
        assert valid.status_code == 200
        assert client.get('/report/an_missing_review_lot', params=owner).status_code == 404
        assert client.post('/analyze').status_code == 405
