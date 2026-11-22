from datetime import date

import pytest
from test_canonical_movements_postgres import DSN, enroll
from test_canonical_pro import database as paid_database
from test_canonical_pro import enable

from app.adapters.conversion.canonical_artifacts import LocalCanonicalObjects
from app.application.canonical_pro import CanonicalProService
from app.application.financial_center import FinancialCenterService

database = paid_database
pytestmark = pytest.mark.skipif(not DSN, reason="Requires disposable local PostgreSQL")


def test_correction_versions_preserve_baseline_and_remove_cash_from_financial_center(database, tmp_path):
    repo, scope, content, _, batches, _ = database
    enroll(database)
    enable(database)
    with repo._connect() as conn:
        conn.execute(f"UPDATE {repo.p}canonical_analyses SET ui_enabled=TRUE")
    service = CanonicalProService(repo, LocalCanonicalObjects(tmp_path / "exports"))
    center = FinancialCenterService(repo)
    assert len(center.month(scope.actor_id, scope.client_id, month=date(2026, 4, 1))["movements"]) == 1
    changed = service.edit(
        scope,
        [],
        expected_version=1,
        expected_updated_at=content["updated_at"],
        operation_id="type-receipt",
        document_type="bank_receipt",
        actor_id=scope.actor_id,
    )
    assert changed["version"] == 2 and changed["net_total"] == 0
    assert changed["preview_transactions"][0]["is_deleted"]
    assert center.month(scope.actor_id, scope.client_id, month=date(2026, 4, 1))["movements"] == []
    assert batches.get(scope.job_id).result.payload["analysis"]["document_type"] == "bank_receipt"
    with repo._connect() as conn:
        row = conn.execute(f"SELECT baseline,version FROM {repo.p}bank_movements").fetchone()
        assert row["baseline"]["amount"] == 10 and row["version"] == 2
        assert conn.execute(f"SELECT count(*) AS n FROM {repo.p}movement_revisions").fetchone()["n"] == 1
    assert service.download(scope, "converted.xlsx", expected_version=2)[0].startswith(b"PK")
    with pytest.raises(ValueError):
        service.download(scope, "converted.ofx", expected_version=2)
    restored = service.edit(
        scope,
        [],
        expected_version=2,
        expected_updated_at=changed["updated_at"],
        operation_id="type-restore",
        document_type="bank_statement",
        actor_id=scope.actor_id,
    )
    assert restored["version"] == 3 and restored["net_total"] == 10
    assert not restored["preview_transactions"][0]["is_deleted"]
    assert len(center.month(scope.actor_id, scope.client_id, month=date(2026, 4, 1))["movements"]) == 1
    assert service.download(scope, "converted.ofx", expected_version=3)[0].count(b"<STMTTRN>") == 1


def test_receipts_and_invoices_are_not_reported_as_missing_bank_sources(database):
    from test_financial_center import add_document

    repo, scope, _, _, batches, _ = database
    enroll(database)
    enable(database)
    for index, kind in enumerate(('bank_receipt', 'fiscal_invoice')):
        record, _ = add_document(database, suffix=f'evidence{index}')
        batches.update_analysis(record.job.job_id, analysis={'financial_document': {'document_type': kind},
                                                            'updated_at': '2099-01-01T00:00:00+00:00'})
    result = FinancialCenterService(repo).month(scope.actor_id, scope.client_id, month=date(2026, 4, 1))
    assert result['legacy_sources'] == 0
