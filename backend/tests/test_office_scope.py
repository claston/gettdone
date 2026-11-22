import pytest

from app.application.document_routing_policy import DocumentRoutingAction, decide_document_routing
from app.application.document_type_classifier import DocumentTypeClassification, classify_document_type
from tests.test_document_evidence import INVOICE, RECEIPT


def test_free_retains_invoice_rejection_and_classifier_version():
    classification = classify_document_type('note.pdf', b'', extracted_text=INVOICE)
    assert classification.classifier_version == '2026-10-27.1'
    assert decide_document_routing(classification).action == DocumentRoutingAction.REJECT
    receipt = DocumentTypeClassification(document_type='bank_receipt', confidence=.99, evidence=['receipt_payment_title'])
    assert decide_document_routing(receipt).action == DocumentRoutingAction.REJECT


def test_pro_evidence_scope_is_reset_after_failure():
    from app.application.office_scope import document_scope
    from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts as parse_pdf_transactions_from_page_texts

    with pytest.raises(RuntimeError):
        with document_scope(True):
            classification = classify_document_type('note.pdf', b'', extracted_text=INVOICE)
            assert decide_document_routing(classification).action == DocumentRoutingAction.CONTINUE
            parsed = parse_pdf_transactions_from_page_texts([RECEIPT])
            assert parsed.transactions == []
            assert parsed.financial_document['document_type'] == 'bank_receipt'
            raise RuntimeError('worker failed')
    assert classify_document_type('note.pdf', b'', extracted_text=INVOICE).classifier_version == '2026-10-27.1'


def test_legacy_job_defaults_to_free():
    from app.application.conversion.contracts.documents import ConversionDocumentReference
    from app.application.conversion.conversion_job import ConversionJob
    from app.application.conversion.identity import IdentityContext

    document = ConversionDocumentReference('document', 'statement.pdf', 'pdf', 20, '0' * 64)
    identity = IdentityContext('user', 'paid-user', 100, batch_conversion_enabled=True)
    job = ConversionJob.create(document=document, identity=identity)
    assert job.product == 'free'
    assert ConversionJob.create(document=document, identity=identity, product='pro').product == 'pro'
