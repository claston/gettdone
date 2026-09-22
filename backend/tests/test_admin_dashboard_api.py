import csv
import io
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.application.access_control import AccessControlService
from app.application.admin_dashboard_service import AdminDashboardService
from app.dependencies import get_access_control_service
from app.main import app


def _record_user_conversion(
    service: AccessControlService,
    *,
    user_id: str,
    processing_id: str,
    created_at: datetime,
    status: str = "Sucesso",
    transactions_count: int = 10,
    duration_ms: int = 1000,
    pages_count: int = 2,
    ocr_used: bool = False,
    warning_count: int = 0,
    balance_failed: int = 0,
    error_code: str | None = None,
    error_stage: str | None = None,
    layout_name: str = "nubank_statement_ptbr",
    layout_confidence: float = 0.98,
    canonical_capture_status: str = "not_eligible",
    canonical_capture_reason: str | None = "clean_conversion",
) -> None:
    service.record_user_conversion(
        user_id=user_id,
        processing_id=processing_id,
        filename=f"{processing_id}.pdf",
        model="Nubank",
        conversion_type="pdf-ofx",
        status=status,
        transactions_count=transactions_count,
        pages_count=pages_count,
        scanned_likely=ocr_used,
        ocr_used=ocr_used,
        ocr_pages_processed=pages_count if ocr_used else 0,
        duration_ms=duration_ms,
        canonical_warning_transactions_count=warning_count,
        balance_consistency_failed=balance_failed,
        error_code=error_code,
        error_stage=error_stage,
        layout_inference_name=layout_name,
        layout_inference_confidence=layout_confidence,
        selected_parser="inline",
        canonical_capture_status=canonical_capture_status,
        canonical_capture_reason=canonical_capture_reason,
        created_at=created_at.isoformat(),
    )


def _record_anonymous_conversion(
    service: AccessControlService,
    clock: dict[str, datetime],
    *,
    fingerprint: str,
    event_id: str,
    created_at: datetime,
    status: str = "Sucesso",
    transactions_count: int = 10,
    duration_ms: int = 1000,
    pages_count: int = 2,
    ocr_used: bool = False,
    warning_count: int = 0,
    balance_failed: int = 0,
    error_code: str | None = None,
    error_stage: str | None = None,
    layout_name: str = "itau_statement_ptbr",
    layout_confidence: float = 0.97,
    canonical_capture_status: str = "not_eligible",
    canonical_capture_reason: str | None = "clean_conversion",
) -> None:
    clock["now"] = created_at
    service.record_anonymous_conversion_event(
        event_id=event_id,
        anonymous_fingerprint=fingerprint,
        filename=f"{event_id}.pdf",
        model="Itaú",
        conversion_type="pdf-ofx",
        status=status,
        transactions_count=transactions_count,
        pages_count=pages_count,
        scanned_likely=ocr_used,
        ocr_used=ocr_used,
        ocr_pages_processed=pages_count if ocr_used else 0,
        duration_ms=duration_ms,
        canonical_warning_transactions_count=warning_count,
        balance_consistency_failed=balance_failed,
        error_code=error_code,
        error_stage=error_stage,
        layout_inference_name=layout_name,
        layout_inference_confidence=layout_confidence,
        selected_parser="inline",
        canonical_capture_status=canonical_capture_status,
        canonical_capture_reason=canonical_capture_reason,
    )


def _build_dashboard_client(tmp_path: Path) -> tuple[TestClient, AccessControlService, dict[str, datetime], str]:
    clock = {"now": datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)}
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        admin_emails={"admin@example.com"},
        now_provider=lambda: clock["now"],
    )
    service.register_user(name="Admin", email="admin@example.com", password="admin-pass")
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    return TestClient(app), service, clock, user.user_id


def test_admin_dashboard_aggregates_quality_failures_and_returning_people(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)

    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_before_period",
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc),
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_user_clean",
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        duration_ms=1000,
        pages_count=3,
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_user_review",
        created_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
        duration_ms=3000,
        pages_count=4,
        ocr_used=True,
        warning_count=2,
        canonical_capture_status="stored",
        canonical_capture_reason=None,
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-returning",
        event_id="ace_failed",
        created_at=datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc),
        status="Falha",
        transactions_count=0,
        duration_ms=5000,
        ocr_used=True,
        error_code="parse_failed",
        error_stage="extraction",
        canonical_capture_status="upload_failed",
        canonical_capture_reason="ClientError",
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-returning",
        event_id="ace_clean_return",
        created_at=datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
        duration_ms=7000,
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-new",
        event_id="ace_clean_new",
        created_at=datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc),
        duration_ms=9000,
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = client.get("/admin/dashboard", params={"days": 30, "identity_type": "all"})

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        assert payload["days"] == 30
        assert payload["timezone"] == "America/Sao_Paulo"
        assert payload["summary"] == {
            "conversions_total": 5,
            "non_conversion_count": 0,
            "pages_total": 13,
            "pdf_conversions_count": 3,
            "pdf_pages_count": 7,
            "ocr_conversions_count": 2,
            "ocr_pages_count": 6,
            "technical_success_count": 4,
            "technical_success_rate": 80.0,
            "clean_conversion_count": 3,
            "clean_conversion_rate": 60.0,
            "clean_high_confidence_count": 3,
            "clean_high_confidence_rate": 60.0,
            "review_count": 1,
            "failure_count": 1,
            "active_people_count": 3,
            "returning_people_count": 2,
            "median_duration_ms": 5000,
        }
        assert payload["checkout_funnel"] == {
            "checkout_intents_count": 0,
            "checkout_people_count": 0,
            "requested_intents_count": 0,
            "awaiting_payment_intents_count": 0,
            "released_intents_count": 0,
            "released_people_count": 0,
            "checkout_to_release_rate": 0.0,
        }
        assert payload["identities"] == {
            "registered_conversions": 2,
            "registered_people": 1,
            "anonymous_conversions": 3,
            "anonymous_people": 2,
        }
        assert payload["canonical_capture"] == {
            "candidate_count": 2,
            "stored_count": 1,
            "failure_count": 1,
            "skipped_count": 0,
            "not_eligible_count": 3,
            "disabled_count": 0,
            "not_recorded_count": 0,
            "by_status": [
                {"status": "stored", "count": 1},
                {"status": "upload_failed", "count": 1},
                {"status": "not_eligible", "count": 3},
            ],
            "by_reason": [
                {"status": "not_eligible", "reason": "clean_conversion", "count": 3},
                {"status": "upload_failed", "reason": "ClientError", "count": 1},
            ],
        }
        assert len(payload["daily"]) == 30
        september_fourth = next(item for item in payload["daily"] if item["date"] == "2026-09-04")
        assert september_fourth == {
            "date": "2026-09-04",
            "conversions": 1,
            "clean": 0,
            "review": 1,
            "failures": 0,
        }
        assert payload["top_errors"] == [
            {"error_code": "parse_failed", "error_stage": "extraction", "count": 1}
        ]
        assert payload["top_quality_issues"] == [
            {"issue_code": "parse_failed", "severity": "error", "count": 1},
            {"issue_code": "row_warnings", "severity": "warning", "count": 1},
            {"issue_code": "technical_failure", "severity": "warning", "count": 1},
        ]
        assert payload["layouts"][0]["layout_name"] == "itau_statement_ptbr"
        assert payload["layouts"][0]["clean_high_confidence"] == 2
        assert [item["processing_id"] for item in payload["recent_attention"]] == [
            "an_user_review",
            "ace_failed",
        ]
        assert [item["canonical_capture_status"] for item in payload["recent_attention"]] == [
            "stored",
            "upload_failed",
        ]
        assert payload["recent_attention"][1]["canonical_capture_reason"] == "ClientError"
        assert "capture_id" not in str(payload)
        assert "s3_key" not in str(payload)
        assert "anonymous_fingerprint" not in str(payload)
        assert "filename" not in str(payload)
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_filters_registered_conversions(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_registered",
        created_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-filtered",
        event_id="ace_anonymous",
        created_at=datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc),
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200
        response = client.get(
            "/admin/dashboard",
            params={"days": 7, "identity_type": "registered"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["conversions_total"] == 1
        assert payload["identities"]["registered_conversions"] == 1
        assert payload["identities"]["anonymous_conversions"] == 0
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_separates_limit_rejections_from_converter_failures(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_success",
        created_at=datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc),
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_file_too_large",
        created_at=datetime(2026, 9, 8, 11, 0, tzinfo=timezone.utc),
        status="Falha",
        transactions_count=0,
        pages_count=0,
        error_code="file_too_large",
        error_stage="upload_validation",
        canonical_capture_status="not_attempted",
        canonical_capture_reason="pre_parser_failure",
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-limits",
        event_id="ace_quota_exceeded",
        created_at=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        status="Falha",
        transactions_count=0,
        pages_count=0,
        error_code="quota_exceeded",
        error_stage="quota_check",
        canonical_capture_status="not_attempted",
        canonical_capture_reason="pre_parser_failure",
    )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="anon-parser",
        event_id="ace_parse_failed",
        created_at=datetime(2026, 9, 8, 13, 0, tzinfo=timezone.utc),
        status="Falha",
        transactions_count=0,
        pages_count=1,
        error_code="parse_failed",
        error_stage="parse",
        canonical_capture_status="not_attempted",
        canonical_capture_reason="pre_parser_failure",
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = client.get("/admin/dashboard", params={"days": 7, "identity_type": "all"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["summary"]["conversions_total"] == 2
        assert payload["summary"]["non_conversion_count"] == 2
        assert payload["summary"]["technical_success_count"] == 1
        assert payload["summary"]["technical_success_rate"] == 50.0
        assert payload["summary"]["failure_count"] == 1
        assert payload["top_errors"] == [
            {"error_code": "parse_failed", "error_stage": "parse", "count": 1}
        ]
        assert payload["top_quality_issues"] == [
            {"issue_code": "parse_failed", "severity": "error", "count": 1},
            {"issue_code": "technical_failure", "severity": "warning", "count": 1},
        ]
        assert [item["processing_id"] for item in payload["recent_attention"]] == [
            "ace_parse_failed"
        ]
        assert payload["identities"] == {
            "registered_conversions": 1,
            "registered_people": 1,
            "anonymous_conversions": 1,
            "anonymous_people": 1,
        }
        assert payload["canonical_capture"]["not_eligible_count"] == 2

        daily = next(item for item in payload["daily"] if item["date"] == "2026-09-08")
        assert daily == {
            "date": "2026-09-08",
            "conversions": 2,
            "clean": 1,
            "review": 0,
            "failures": 1,
        }

        export_response = client.get(
            "/admin/dashboard/attention.csv",
            params={"identity_type": "all"},
        )
        exported = export_response.content.decode("utf-8-sig")
        assert "ace_parse_failed" in exported
        assert "an_file_too_large" not in exported
        assert "ace_quota_exceeded" not in exported
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_exports_all_attention_items_from_last_seven_days(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    for index in range(11):
        _record_user_conversion(
            service,
            user_id=user_id,
            processing_id=f"an_review_{index:02d}",
            created_at=datetime(2026, 9, 8, 12 + index, 0, tzinfo=timezone.utc),
            warning_count=1,
            pages_count=index + 1,
        )
    _record_anonymous_conversion(
        service,
        clock,
        fingerprint="secret-fingerprint",
        event_id="ace_failed_export",
        created_at=datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc),
        status="Falha",
        transactions_count=0,
        pages_count=4,
        ocr_used=True,
        error_code="parse_failed",
        error_stage="extraction",
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_clean_not_exported",
        created_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
    )
    _record_user_conversion(
        service,
        user_id=user_id,
        processing_id="an_old_review",
        created_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        warning_count=1,
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = client.get(
            "/admin/dashboard/attention.csv",
            params={"identity_type": "all"},
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == (
            'attachment; filename="conversoes-atencao-ultimos-7-dias.csv"'
        )
        decoded = response.content.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(decoded), delimiter=";"))
        assert len(rows) == 12
        assert rows[0]["identificador"] == "an_review_10"
        assert rows[-1]["identificador"] == "ace_failed_export"
        assert rows[-1]["paginas"] == "4"
        assert rows[-1]["usa_ocr"] == "sim"
        assert rows[-1]["codigo_erro"] == "parse_failed"
        assert rows[-1]["motivo_atencao"]
        assert "an_clean_not_exported" not in decoded
        assert "an_old_review" not in decoded
        assert "secret-fingerprint" not in decoded
        assert ".pdf" not in decoded

        registered_only = client.get(
            "/admin/dashboard/attention.csv",
            params={"identity_type": "registered"},
        )
        registered_rows = list(
            csv.DictReader(
                io.StringIO(registered_only.content.decode("utf-8-sig")),
                delimiter=";",
            )
        )
        assert len(registered_rows) == 11
        assert {row["tipo_pessoa"] for row in registered_rows} == {"cadastrada"}
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_ranks_heavy_users_for_selected_period(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    highest_user = service.register_user(
        name="Usuária Heavy",
        email="heavy@example.com",
        password="strong-pass",
    )
    for index in range(2):
        _record_user_conversion(
            service,
            user_id=user_id,
            processing_id=f"an_regular_{index}",
            created_at=datetime(2026, 9, 8, 10 + index, 0, tzinfo=timezone.utc),
            pages_count=2,
        )
    _record_user_conversion(
        service,
        user_id=highest_user.user_id,
        processing_id="an_highest_pages",
        created_at=datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc),
        pages_count=20,
        ocr_used=True,
        warning_count=1,
    )
    for index in range(2):
        _record_anonymous_conversion(
            service,
            clock,
            fingerprint="private-heavy-fingerprint",
            event_id=f"ace_heavy_{index}",
            created_at=datetime(2026, 9, 8, 12 + index, 0, tzinfo=timezone.utc),
            pages_count=6,
            ocr_used=True,
        )
    _record_user_conversion(
        service,
        user_id=highest_user.user_id,
        processing_id="an_old_heavy",
        created_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
        pages_count=50,
    )
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = client.get(
            "/admin/dashboard",
            params={"days": 7, "identity_type": "all"},
        )

        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        rows = payload["heavy_users"]
        assert len(rows) == 3
        assert [row["pages"] for row in rows] == [20, 12, 4]
        assert rows[0]["rank"] == 1
        assert rows[0]["display_name"] == "Usuária Heavy"
        assert rows[0]["email"] == "heavy@example.com"
        assert rows[0]["ocr_conversions"] == 1
        assert rows[0]["ocr_share_rate"] == 100.0
        assert rows[0]["active_days"] == 1
        assert rows[0]["is_returning"] is True
        assert rows[0]["review"] == 1
        assert rows[1]["identity_type"] == "anonymous"
        assert rows[1]["identity_reference"].startswith("anon_")
        assert rows[1]["conversions"] == 2
        assert rows[1]["ocr_pages"] == 12
        assert rows[1]["active_days"] == 1
        assert rows[1]["is_returning"] is False
        assert rows[2]["pdf_conversions"] == 2
        assert rows[2]["active_days"] == 1
        assert rows[2]["is_returning"] is False

        returning_rows = payload["returning_heavy_users"]
        assert len(returning_rows) == 1
        assert returning_rows[0]["display_name"] == rows[0]["display_name"]
        assert returning_rows[0]["rank"] == 1

        ocr_rows = payload["ocr_heavy_users"]
        assert [row["ocr_pages"] for row in ocr_rows] == [20, 12]
        assert [row["rank"] for row in ocr_rows] == [1, 2]
        assert "private-heavy-fingerprint" not in str(payload)
        assert "an_old_heavy" not in str(payload)
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_counts_submitted_checkout_funnel_without_new_tracking_table(tmp_path: Path) -> None:
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    second_user = service.register_user(
        name="Cliente Dois",
        email="cliente2@example.com",
        password="strong-pass",
    )
    third_user = service.register_user(
        name="Cliente Três",
        email="cliente3@example.com",
        password="strong-pass",
    )

    clock["now"] = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    requested = service.create_checkout_intent(
        user_id=user_id,
        plan_code="profissional",
        customer_name="Erica",
        customer_email="erica@example.com",
        customer_whatsapp="+55 11 99999-1111",
    )
    clock["now"] = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    awaiting = service.create_checkout_intent(
        user_id=second_user.user_id,
        plan_code="profissional",
        customer_name="Cliente Dois",
        customer_email="cliente2@example.com",
        customer_whatsapp="+55 11 99999-2222",
    )
    service.mark_checkout_intent_awaiting_payment(
        intent_id=str(awaiting["id"]),
        payment_link="https://example.com/pay",
    )
    clock["now"] = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    released = service.create_checkout_intent(
        user_id=third_user.user_id,
        plan_code="profissional",
        customer_name="Cliente Três",
        customer_email="cliente3@example.com",
        customer_whatsapp="+55 11 99999-3333",
    )
    service.mark_checkout_intent_released_by_id(intent_id=str(released["id"]))
    clock["now"] = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    try:
        login = client.post(
            "/admin/auth/login",
            json={"email": "admin@example.com", "password": "admin-pass"},
        )
        assert login.status_code == 200

        response = client.get("/admin/dashboard", params={"days": 7, "identity_type": "all"})

        assert response.status_code == 200
        assert response.json()["checkout_funnel"] == {
            "checkout_intents_count": 3,
            "checkout_people_count": 3,
            "requested_intents_count": 1,
            "awaiting_payment_intents_count": 1,
            "released_intents_count": 1,
            "released_people_count": 1,
            "checkout_to_release_rate": 33.3,
        }

        anonymous_response = client.get(
            "/admin/dashboard",
            params={"days": 7, "identity_type": "anonymous"},
        )
        assert anonymous_response.status_code == 200
        assert anonymous_response.json()["checkout_funnel"]["checkout_intents_count"] == 0
        assert requested["status"] == "REQUESTED"
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_requires_admin_access(tmp_path: Path) -> None:
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
    )
    service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    app.dependency_overrides[get_access_control_service] = lambda: service
    client = TestClient(app)

    try:
        response = client.get("/admin/dashboard")
        assert response.status_code == 401
        export_response = client.get("/admin/dashboard/attention.csv")
        assert export_response.status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_admin_dashboard_groups_days_in_sao_paulo_timezone(tmp_path: Path) -> None:
    now = datetime(2026, 9, 9, 2, 45, tzinfo=timezone.utc)
    service = AccessControlService(
        state_file=tmp_path / "access-control-state.json",
        token_secret="test-secret",
        now_provider=lambda: now,
    )
    user = service.register_user(name="Erica", email="erica@example.com", password="strong-pass")
    _record_user_conversion(
        service,
        user_id=user.user_id,
        processing_id="an_timezone",
        created_at=datetime(2026, 9, 9, 2, 30, tzinfo=timezone.utc),
    )

    payload = AdminDashboardService(service).get_dashboard(days=1)

    assert payload["start_at"] == "2026-09-08T03:00:00+00:00"
    assert payload["daily"] == [
        {
            "date": "2026-09-08",
            "conversions": 1,
            "clean": 1,
            "review": 0,
            "failures": 0,
        }
    ]


def test_admin_dashboard_is_physically_independent_from_worker_runtime() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source_paths = (
        repository_root / "backend" / "app" / "routers" / "admin_auth.py",
        repository_root
        / "backend"
        / "app"
        / "application"
        / "admin_dashboard_service.py",
    )

    for source_path in source_paths:
        source = source_path.read_text(encoding="utf-8")
        assert "app.workers" not in source
        assert "conversion_jobs" not in source
        assert "conversion_batches" not in source
        assert "sqs" not in source.lower()
        assert "conversion_lambda" not in source.lower()
        assert "boto3" not in source.lower()
