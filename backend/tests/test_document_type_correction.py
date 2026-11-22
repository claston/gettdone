from copy import deepcopy

import pytest

from app.application.errors import AnalysisEditConflictError
from app.application.review_commands import ReviewCommandService


def statement():
    row = {
        "date": "2026-07-20",
        "description": "PIX",
        "amount": 4189.59,
        "category": "Outros",
        "reconciliation_status": "unmatched",
        "source_page": 1,
        "source_line": 3,
        "transaction_id": "txn_test",
        "is_deleted": False,
    }
    return {
        "analysis_id": "an_test",
        "file_type": "pdf",
        "document_type": "bank_statement",
        "document_type_confidence": 0.82,
        "updated_at": "2026-11-22T10:00:00+00:00",
        "preview_transactions": [row],
        "report_transactions": [deepcopy(row)],
        "transactions_total": 1,
        "total_inflows": 4189.59,
        "total_outflows": 0,
        "net_total": 4189.59,
        "opening_balance": None,
        "closing_balance": None,
    }


def correct(service, content, kind, operation):
    return service.apply(
        content, [], expected_updated_at=content["updated_at"], operation_id=operation, document_type=kind, actor_id="owner"
    )


def test_type_correction_excludes_movements_preserves_history_and_can_restore():
    content = statement()
    service = ReviewCommandService()
    correct(service, content, "bank_receipt", "correction1")
    assert content["document_type"] == "bank_receipt"
    assert content["total_inflows"] == content["net_total"] == 0
    assert content["report_transactions"][0]["is_deleted"]
    assert content["report_transactions"][0]["amount"] == 4189.59
    assert content["document_type_history"][0]["actor_id"] == "owner"
    assert content["document_type_history"][0]["before"] == "bank_statement"
    assert content["document_type_automatic"] == "bank_statement"
    assert content["financial_document"]["events"] == []
    correct(service, content, "bank_statement", "correction2")
    assert content["net_total"] == 4189.59
    assert not content["report_transactions"][0]["is_deleted"]
    assert len(content["document_type_history"]) == 2


def test_type_correction_replays_and_rejects_stale_version():
    content = statement()
    service = ReviewCommandService()
    original = content["updated_at"]
    kwargs = dict(document_type="bank_receipt", actor_id="owner", operation_id="same", expected_updated_at=original)
    service.apply(content, [], **kwargs)
    assert service.apply(content, [], **kwargs).replayed
    assert len(content["document_type_history"]) == 1
    with pytest.raises(AnalysisEditConflictError):
        service.apply(content, [], document_type="fiscal_invoice", actor_id="owner", operation_id="stale", expected_updated_at=original)


def test_type_correction_does_not_invent_statement_from_receipt():
    content = statement()
    content.update(
        document_type="bank_receipt", financial_document={"document_type": "bank_receipt"}, preview_transactions=[], report_transactions=[]
    )
    with pytest.raises(ValueError, match="leitura de extrato"):
        correct(ReviewCommandService(), content, "bank_statement", "wrong")
