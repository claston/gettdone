from app.application.document_classification_history import prepare_document_classification_record


def test_preserves_payment_report_classification_for_observability() -> None:
    record = prepare_document_classification_record(
        document_type="payment_report",
        document_type_confidence=0.99,
        document_classification_version="2026-10-14.1",
        document_processing_decision="rejected",
        document_classification_evidence=["payment_report_structure"],
    )

    assert record == (
        "payment_report",
        0.99,
        "2026-10-14.1",
        "rejected",
        '["payment_report_structure"]',
    )
