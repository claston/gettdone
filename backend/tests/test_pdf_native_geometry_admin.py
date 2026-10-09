from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.application import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService


def _record_conversion(service: AccessControlService, *, user_id: str, processing_id: str) -> None:
    service.record_user_conversion(
        user_id=user_id,
        processing_id=processing_id,
        filename="private.pdf",
        model="PDF",
        conversion_type="pdf-ofx",
        status="Sucesso",
        transactions_count=2,
        pages_count=1,
        created_at="2026-10-08T15:00:00+00:00",
    )


def test_admin_dashboard_aggregates_native_geometry_shadow_without_sensitive_content(tmp_path: Path) -> None:
    now = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        now_provider=lambda: now,
    )
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    _record_conversion(service, user_id=user.user_id, processing_id="an_geometry_gain")
    service.record_pdf_native_geometry_shadow_event(
        processing_id="an_geometry_gain",
        identity_type="registered",
        classification="potential_gain",
        baseline_status="ok",
        baseline_layout="itau_statement_ptbr",
        baseline_parser="inline",
        baseline_transactions=2,
        baseline_balance_failed=0,
        geometry_status="ok",
        geometry_layout="itau_statement_ptbr",
        geometry_parser="tabular",
        geometry_transactions=3,
        geometry_balance_failed=0,
        geometry_duration_ms=42,
        matched_transactions=2,
        date_conflicts=0,
        amount_conflicts=0,
        sign_conflicts=0,
        geometry_error_type=None,
        word_count=40,
        line_count=8,
        duplicate_characters_removed=2,
        fragment_merges=1,
        created_at="2026-10-08T15:00:00+00:00",
    )

    payload = AdminDashboardService(service).get_dashboard(days=7)
    shadow = payload["native_geometry_shadow"]

    assert shadow["evaluated_count"] == 1
    assert shadow["success_count"] == 1
    assert shadow["potential_gain_count"] == 1
    assert shadow["potential_rescue_count"] == 0
    assert shadow["conflict_count"] == 0
    assert shadow["median_duration_ms"] == 42
    assert shadow["p95_duration_ms"] == 42
    assert shadow["by_layout"] == [
        {
            "layout_name": "itau_statement_ptbr",
            "evaluated": 1,
            "potential_rescues": 0,
            "potential_gains": 1,
            "conflicts": 0,
            "errors": 0,
            "median_duration_ms": 42,
        }
    ]
    serialized = str(payload)
    assert "private.pdf" not in serialized
    assert "erica@example.com" not in str(shadow)


def test_sqlite_bootstrap_creates_native_geometry_shadow_table(tmp_path: Path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )

    with service._connect() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pdf_native_geometry_shadow_events'"
        ).fetchone()

    assert row is not None
