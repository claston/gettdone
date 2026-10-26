import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.application.conversion_quality import QUALITY_RULE_VERSION
from app.schemas import AdminDashboardResponse


@pytest.fixture
def history(tmp_path: Path):
    service = AccessControlService(
        state_file=tmp_path / "history.json",
        token_secret="synthetic-review-test",
        now_provider=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc),
    )
    try:
        yield service
    finally:
        service.close()


def _record(service, identity_type, **overrides):
    kwargs = dict(
        filename="synthetic.pdf", model="Sintético", conversion_type="pdf-ofx",
        status="Sucesso", transactions_count=8,
        pages_count=1, scanned_likely=False, ocr_used=False, ocr_pages_processed=0, duration_ms=100,
        layout_inference_name="synthetic_statement_v1", layout_inference_confidence=0.99,
        selected_parser="tabular", parser_confidence_band="medium", parser_coverage_rate=0.625,
        canonical_capture_status="disabled", canonical_capture_reason="feature_disabled",
    )
    kwargs.update(overrides)
    if identity_type == "registered":
        user = service.register_user(name="Teste", email="test@example.com", password="synthetic-password")
        service.record_user_conversion(user_id=user.user_id, processing_id="an_review", **kwargs)
    else:
        service.record_anonymous_conversion_event(
            event_id="anon_review", anonymous_fingerprint="private-fingerprint", **kwargs,
        )


def _update(service, identity_type, **values):
    table = "user_conversions" if identity_type == "registered" else "anonymous_conversion_events"
    with service._connect() as conn:
        conn.execute(f"UPDATE {table} SET " + ", ".join(f"{key} = ?" for key in values), tuple(values.values()))
        conn.commit()


@pytest.mark.parametrize("identity_type", ["registered", "anonymous"])
def test_attention_explains_persisted_confidence_and_every_warning(history, identity_type):
    _record(history, identity_type, canonical_warning_transactions_count=2, balance_consistency_failed=1,
            warning_types=["partial_statement", "financial_validation_failed", "future_warning"])
    payload = AdminDashboardResponse(**AdminDashboardService(history).get_dashboard(days=7)).model_dump()
    item = payload["recent_attention"][0]
    reason = item["issue_reason"]
    assert "Confiança do parser média" in reason
    assert "Cobertura registrada do parser: 62,5%" in reason
    assert "Documento parcial ou incompleto" in reason
    assert "Validação financeira não conferiu" in reason
    assert "Alerta registrado: future_warning" in reason
    assert "2 transações com alerta" in reason
    assert "1 inconsistência de saldo" in reason
    assert "feature_disabled" not in reason
    assert item["parser_confidence_band"] == "medium"
    assert item["parser_coverage_rate"] == 0.625
    assert item["warning_types"] == ["financial_validation_failed", "future_warning", "partial_statement"]
    assert item["quality_rule_version"] == QUALITY_RULE_VERSION
    assert item["quality_assessment_source"] == "stored"
    exported = AdminDashboardService(history).get_attention_export_rows()[0]
    assert exported["issue_reason"] == reason


@pytest.mark.parametrize("identity_type", ["registered", "anonymous"])
def test_historical_review_does_not_invent_missing_parser_diagnostics(history, identity_type):
    _record(history, identity_type)
    _update(history, identity_type, parser_confidence_band=None, parser_coverage_rate=None,
            quality_rule_version="historical.v1", warning_types_json=None)
    item = AdminDashboardService(history).get_dashboard()["recent_attention"][0]
    assert "Confiança e causa detalhada do parser não registradas no histórico" in item["issue_reason"]
    assert "62,5%" not in item["issue_reason"]
    assert "média" not in item["issue_reason"]
    assert item["quality_rule_version"] == "historical.v1"
    assert item["quality_reason_codes"] == ["parser_requires_review"]


@pytest.mark.parametrize("identity_type", ["registered", "anonymous"])
def test_stored_review_without_reason_is_explicitly_missing_not_reassessed(history, identity_type):
    _record(history, identity_type)
    _update(history, identity_type, quality_reason_codes_json="[]", quality_rule_version="historical.v1")
    before = AdminDashboardService(history).get_dashboard()["recent_attention"][0]
    assert before["quality_reason_codes"] == []
    assert before["issue_reason"] == "Revisão registrada sem motivo detalhado no histórico"
    assert before["quality_assessment_source"] == "stored"
    _update(history, identity_type, quality_status="clean")
    payload = AdminDashboardService(history).get_dashboard()
    assert payload["summary"]["clean_conversion_count"] == 1
    assert payload["recent_attention"] == []


@pytest.mark.parametrize("identity_type", ["registered", "anonymous"])
def test_only_unassessed_legacy_history_uses_current_rule_and_marks_reconstruction(history, identity_type):
    _record(history, identity_type)
    _update(history, identity_type, quality_status=None, quality_score=None,
            quality_reason_codes_json=None, quality_rule_version=None)
    dashboard = AdminDashboardService(history)
    item = dashboard.get_dashboard()["recent_attention"][0]
    assert item["quality_status"] == "review"
    assert item["quality_reason_codes"] == ["parser_requires_review"]
    assert item["quality_assessment_source"] == "recalculated"
    assert "Avaliação reconstruída" in item["issue_reason"]
    assert QUALITY_RULE_VERSION in item["issue_reason"]
    # A dashboard read must never backfill the stored assessment.
    table = "user_conversions" if identity_type == "registered" else "anonymous_conversion_events"
    with history._connect() as conn:
        row = conn.execute(f"SELECT quality_status, quality_reason_codes_json FROM {table}").fetchone()
    assert row["quality_status"] is None
    assert row["quality_reason_codes_json"] is None


def test_attention_retains_unknown_quality_codes_and_all_layout_reasons(history):
    _record(history, "anonymous", layout_inference_name="generic_pdf", layout_inference_confidence=0.72,
            selected_parser=None, parser_confidence_band=None, parser_coverage_rate=None)
    _update(history, "anonymous", quality_reason_codes_json=json.dumps([
        "generic_layout", "layout_confidence_below_95", "parser_missing", "future_quality_rule",
    ]))
    reason = AdminDashboardService(history).get_dashboard()["recent_attention"][0]["issue_reason"]
    assert "Layout genérico ou não identificado" in reason
    assert "72,0%" in reason and "95%" in reason
    assert "Parser não identificado" in reason
    assert "Motivo registrado: future_quality_rule" in reason
