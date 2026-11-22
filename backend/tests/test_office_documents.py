from datetime import datetime, timedelta, timezone

import pytest

from app.application.access_control import AccessControlService
from app.application.office_documents import OfficeReviews, document_period, matches_month
from app.application.pro_organization import OrganizationConflict


def test_operational_month_uses_evidence_never_upload_date():
    assert document_period({}) == (None, None)
    analysis = {'preview_transactions': [{'date': '2026-01-31'}, {'date': '2026-02-01'}]}
    assert document_period(analysis) == ('2026-01-31', '2026-02-01')
    assert matches_month(analysis, '2026-02')
    assert not matches_month(analysis, '2026-03')
    assert matches_month({}, 'unknown') and not matches_month({}, '2026-02')
    evidence = {'financial_document': {'observations': [
        {'dates': {'document_date': '2026-02-18'}}, {'dates': {'document_date': '2026-02-19'}}]}}
    # Two conflicting document dates require confirmation; do not invent a period.
    assert document_period(evidence) == (None, None)
    with pytest.raises(ValueError):
        matches_month({}, '2026-13')


def test_compound_pdf_derivatives_keep_page_map_and_reject_uncertain_groups():
    from io import BytesIO

    from fastapi import HTTPException
    from pypdf import PdfReader

    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.office_scope import document_scope
    from app.routers.office_documents import part_bytes, safe_name
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
    from tests.test_document_evidence import RECEIPT

    statement = ('EXTRATO CONTA CORRENTE', 'Saldo anterior: 100,00', 'DATA HISTORICO VALOR SALDO',
                 '20/07/2026 PIX RECEBIDO 50,00 150,00', 'Saldo final: 150,00')
    raw = generate_native_text_pdf((statement, tuple(RECEIPT.splitlines())))
    with document_scope(True):
        result = build_default_conversion_pipeline().run(filename='composto.pdf', raw_bytes=raw, analysis_id='an_compound')
    parts = result.analysis_data.document_parts
    assert [part['pages'] for part in parts] == [[1], [2]]
    assert result.analysis_data.transactions_total == 1
    extracted = PdfReader(BytesIO(part_bytes(raw, parts[1])))
    assert len(extracted.pages) == 1 and 'COMPROVANTE' in extracted.pages[0].extract_text()
    with pytest.raises(HTTPException) as uncertain:
        part_bytes(raw, {**parts[1], 'requires_review': True})
    assert uncertain.value.status_code == 409
    assert safe_name('../../escape.pdf') == 'escape'
    assert safe_name('..\\..\\escape.pdf') == 'escape'


def test_verification_cas_retry_audit_and_expiry(tmp_path):
    access = AccessControlService(state_file=tmp_path / 'access.json', token_secret='office-test')
    owner = access.register_user(name='Office', email='office@example.test', password='long-password').user_id
    reviews = OfficeReviews(access.db)
    until = datetime.now(timezone.utc) + timedelta(hours=1)
    args = dict(owner=owner, job_id='job', source='v1', expires_at=until, status='verified',
                document_type=None, expected_version=0, operation_id='op1')
    first = reviews.save(**args)
    assert reviews.save(**args) == first
    assert reviews.read(owner, 'job', 'v1')['status'] == 'verified'
    assert reviews.read(owner, 'job', 'v2')['status'] == 'pending'
    with pytest.raises(OrganizationConflict):
        reviews.save(**{**args, 'operation_id': 'op2'})
    second = reviews.save(**{**args, 'status': 'attention', 'expected_version': 1, 'operation_id': 'op2'})
    assert second['version'] == 2 and second['actor'] == owner
    with access.db.connect() as conn:
        assert len(access.db.fetchall(conn, 'SELECT * FROM pro_document_review_events')) == 2
    reviews.purge(now=until + timedelta(seconds=1))
    assert reviews.read(owner, 'job', 'v1')['version'] == 0
    with access.db.connect() as conn:
        assert access.db.fetchall(conn, 'SELECT * FROM pro_document_review_events') == []
