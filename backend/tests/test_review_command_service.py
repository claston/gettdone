"""The review boundary transforms legacy state without publishing artifacts."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from app.application.errors import AnalysisEditConflictError
from app.application.models import TransactionRow
from app.application.review_commands import ReviewCommandService

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def document():
    row = asdict(TransactionRow("2026-04-01", "Original", 10, "Outros", "unmatched",
                               transaction_id="stable", source_sequence=1, running_balance=110))
    return {"analysis_id": "an_review", "file_type": "pdf", "updated_at": "v1",
            "preview_transactions": [row], "report_transactions": [deepcopy(row)],
            "transactions_total": 1, "total_inflows": 10, "total_outflows": 0, "net_total": 10,
            "opening_balance": 100, "closing_balance": 110, "pdf_processing_metrics": {"page_count": 1}}


def test_service_applies_lifecycle_and_confirms_retry_without_io():
    service = ReviewCommandService(now_provider=lambda: NOW)
    content = document()
    patch = {"row_id": "stable", "action": "update", "date": "2026-04-02", "description": "Correção", "debit": 2.75}
    result = service.apply(content, [patch], expected_updated_at="v1", operation_id="save-1")
    assert result.publish_artifacts
    assert content["net_total"] == -2.75
    assert content["closing_balance"] == 97.25
    assert content["preview_transactions"][0]["transaction_id"] == "stable"
    replay = service.apply(content, [patch], expected_updated_at="v1", operation_id="save-1")
    assert replay.replayed
    assert not replay.publish_artifacts
    assert service.confirm(content, "save-1")["updated_at"] == NOW.isoformat()
    with pytest.raises(AnalysisEditConflictError):
        service.apply(content, [{**patch, "debit": 3}], operation_id="save-1")
    for action, total in [("delete", 0), ("restore", -2.75)]:
        service.apply(content, [{**patch, "action": action}])
        assert content["net_total"] == total


def test_annotation_preserves_all_financial_fields_and_does_not_publish(monkeypatch):
    service = ReviewCommandService(now_provider=lambda: NOW)
    content = document()
    before = deepcopy(content)
    monkeypatch.setattr(service, "_recalculate_running_balances", lambda **kw: pytest.fail("recalculation"))
    result = service.apply(content, [{"action": "annotate", "row_id": "stable", "review_regions": []}])
    assert not result.publish_artifacts
    assert not result.replayed
    assert content == {**before, "updated_at": NOW.isoformat()}


def test_download_overrides_use_same_boundary_without_advancing_edit_version():
    service = ReviewCommandService(now_provider=lambda: NOW)
    content = document()
    result = service.prepare_export(content, bank_code=" 341 ", closing_balance=123)
    assert result.metadata_changed
    assert result.effective_overrides["bank_code"] == "341"
    assert content["convert_closing_balance"] == 123
    assert content["updated_at"] == "v1"
    assert content["report_transactions"] == document()["report_transactions"]


def test_review_mark_uses_existing_repository_contract():
    calls = []

    class Repository:
        def mark_reviewed(self, job_id, *, reviewed_at):
            calls.append((job_id, reviewed_at))

    assert ReviewCommandService(now_provider=lambda: NOW).mark_reviewed(Repository(), "job-1") == NOW.isoformat()
    assert calls == [("job-1", NOW.isoformat())]
