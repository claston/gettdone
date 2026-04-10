from datetime import datetime, timedelta, timezone

import pytest

from app.application import AnalysisNotFoundError, TempAnalysisStorage
from app.application.models import AnalysisData, TransactionRow


def _build_analysis_data(analysis_id: str = "an_testttl") -> AnalysisData:
    return AnalysisData(
        analysis_id=analysis_id,
        file_type="csv",
        transactions_total=1,
        total_inflows=100.0,
        total_outflows=-20.0,
        net_total=80.0,
        preview_transactions=[
            TransactionRow(
                date="2026-04-01",
                description="TEST",
                amount=-20.0,
                category="Outros",
                reconciliation_status="unmatched",
            )
        ],
    )


def test_save_analysis_persists_created_and_expires_at(tmp_path) -> None:
    base_now = datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=3600, now_provider=lambda: base_now)

    storage.save_analysis(_build_analysis_data())

    analysis_json = tmp_path / "an_testttl" / "analysis.json"
    content = analysis_json.read_text(encoding="utf-8")

    assert '"created_at": "2026-04-10T12:00:00+00:00"' in content
    assert '"expires_at": "2026-04-10T13:00:00+00:00"' in content


def test_get_report_path_returns_report_when_analysis_is_not_expired(tmp_path) -> None:
    current = {"now": datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)}
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=7200, now_provider=lambda: current["now"])

    storage.save_analysis(_build_analysis_data())
    current["now"] = current["now"] + timedelta(minutes=30)

    report_path = storage.get_report_path("an_testttl")
    assert report_path.exists()


def test_get_report_path_raises_not_found_and_cleans_directory_when_expired(tmp_path) -> None:
    current = {"now": datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc)}
    storage = TempAnalysisStorage(root_dir=tmp_path, ttl_seconds=60, now_provider=lambda: current["now"])

    storage.save_analysis(_build_analysis_data())
    current["now"] = current["now"] + timedelta(minutes=2)

    with pytest.raises(AnalysisNotFoundError):
        storage.get_report_path("an_testttl")

    assert not (tmp_path / "an_testttl").exists()
