"""Repeated uploads count as attempts, while PDF bytes identify unique documents."""

from datetime import datetime, timezone

import pytest

from app.main import app
from tests.test_admin_dashboard_api import _build_dashboard_client


def test_dashboard_deduplicates_pdf_outcomes_across_identities_days_and_months(tmp_path):
    client, service, clock, user = _build_dashboard_client(tmp_path)
    try:
        for index, (kind, digest, status, warnings, day, period) in enumerate([
            ("anonymous", "a" * 64, "Falha", 0, 28, ("2026-09-01", "2026-09-30")),
            ("registered", "a" * 64, "Falha", 0, 28, ("2026-09-01", "2026-09-30")),
            ("registered", "a" * 64, "Sucesso", 1, 29, ("2026-09-01", "2026-09-30")),
            ("anonymous", "b" * 64, "Sucesso", 1, 29, ("2026-10-01", "2026-10-28")),
            ("registered", "c" * 64, "Sucesso", 0, 29, ("2026-08-01", "2026-09-30")),
            ("anonymous", "c" * 64, "Sucesso", 0, 29, ("2026-08-01", "2026-09-30")),
            ("registered", None, "Falha", 0, 29, None),
        ]):
            clock["now"] = datetime(2026, 10, day, 15, index, tzinfo=timezone.utc)
            kwargs = dict(filename=f"statement-{index}.pdf", model="Banco fictício", conversion_type="pdf-ofx",
                          status=status, transactions_count=3, pages_count=1, scanned_likely=False,
                          ocr_used=False, ocr_pages_processed=0, duration_ms=100,
                          file_sha256=digest, canonical_warning_transactions_count=warnings,
                          layout_inference_name="itau_statement_ptbr", layout_inference_confidence=0.99,
                          selected_parser="inline", document_type="bank_statement",
                          statement_period={"start_date": period[0], "end_date": period[1],
                                            "basis": "document_metadata"} if period else None)
            if kind == "registered":
                service.record_user_conversion(user_id=user, processing_id=f"an_{index}", **kwargs)
            else:
                service.record_anonymous_conversion_event(event_id=f"anon_evt_{index}",
                                                          anonymous_fingerprint="fictional-owner", **kwargs)
        service.record_user_conversion(user_id=user, processing_id="csv", filename="statement.csv",
                                       model="CSV", conversion_type="csv-conversion", status="Sucesso",
                                       transactions_count=1, file_sha256="d" * 64)
        assert client.get("/admin/dashboard").status_code == 401
        assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"}).status_code == 200
        response = client.get("/admin/dashboard", params={"days": 2})
        assert response.status_code == 200
        data = response.json()["pdf_documents"]
        assert data["attempts"] == 7
        assert data["unique_documents"] == 3
        assert data["failure_attempts"] == 3
        assert data["review_attempts"] == 2
        assert data["unique_failed_documents"] == 1
        assert data["unique_review_documents"] == 2
        assert data["unidentified_attempts"] == 1
        assert data["attention_total"] == 2
        by_day = {row["date"]: row for row in data["by_day"]}
        assert by_day["2026-10-28"]["failure_attempts"] == 2
        assert by_day["2026-10-28"]["unique_failed_documents"] == 1
        assert by_day["2026-10-29"]["unique_review_documents"] == 2
        months = {row["key"]: row for row in data["by_period"]}
        assert months["2026-09:2026-09"]["attempts"] == 3
        assert months["2026-09:2026-09"]["unique_documents"] == 1
        assert months["2026-08:2026-09"]["attempts"] == 2
        assert months["unknown"]["unidentified_attempts"] == 1
        document = next(row for row in data["attention"] if row["file_sha256"] == "a" * 64)
        assert document["failure_attempts"] == 2 and document["review_attempts"] == 1
        assert document["first_failure_at"].startswith("2026-10-28")
        assert document["last_review_at"].startswith("2026-10-29")
        assert document["statement_period"]["start_date"] == "2026-09-01"
        filtered = client.get("/admin/dashboard", params={"days": 2, "identity_type": "anonymous"}).json()["pdf_documents"]
        assert filtered["attempts"] == filtered["unique_documents"] == 3
        assert filtered["failure_attempts"] == filtered["unique_failed_documents"] == 1
        attention = response.json()["recent_attention"]
        assert next(row for row in attention if row["processing_id"] == "an_1")["file_sha256"] == "a" * 64
        exported = client.get("/admin/dashboard/attention.csv").text
        assert "sha256_arquivo" in exported and "inicio_periodo_documento" in exported
        assert "statement-" not in exported
    finally:
        client.close()
        app.dependency_overrides.clear()


@pytest.mark.parametrize("text,expected", [
    ("Período: 01/09/2026 a 30/09/2026", ("2026-09-01", "2026-09-30")),
    ("Período: 01-08-2026 al 31-08-2026", ("2026-08-01", "2026-08-31")),
    ("Período de 01.09.2026 até 30.09.2026", ("2026-09-01", "2026-09-30")),
    ("01/09/2026 PIX 10,00\n30/09/2026 SALDO 10,00", None),
    ("Emitido em 28/10/2026", None),
    ("Período: 31/02/2026 a 31/03/2026", None),
    ("Período: 30/09/2026 a 01/09/2026", None),
    ("Período: 01/09/2026 a 30/09/2026\nPeríodo: 01/08/2026 a 31/08/2026", None),
])
def test_only_explicit_unambiguous_document_periods_are_captured(text, expected):
    from app.application.statement_period_history import statement_period_from_text

    result = statement_period_from_text(text)
    assert ((result["start_date"], result["end_date"]) if result else None) == expected


def test_pdf_counts_use_sao_paulo_days_and_do_not_guess_missing_or_invalid_hashes():
    from app.application.admin_pdf_metrics import build_pdf_document_metrics

    base = {"conversion_type": "pdf-ofx", "status": "Falha", "quality_status": "failed",
            "created_at": "2026-10-28T01:00:00+00:00", "model": "Banco"}
    events = [{**base, "file_sha256": "A" * 64}, {**base, "file_sha256": "a" * 64},
              {**base, "file_sha256": "invalid"}, {**base, "file_sha256": "b" * 64, "status": "Processando"}]
    summary = build_pdf_document_metrics(events)
    assert summary["attempts"] == 3
    assert summary["unique_failed_documents"] == 1
    assert summary["unidentified_attempts"] == 1
    assert summary["by_day"][0]["date"] == "2026-10-27"


def test_failed_pdf_retains_explicit_period_without_claiming_transactions_were_parsed():
    from types import SimpleNamespace

    from app.application.conversion.uploaded_document import UploadedDocument
    from app.application.statement_period_history import statement_period_from_failed_pdf
    from tests.fixtures.recovery_acceptance import grid_pdf

    raw = grid_pdf([[('Período: 01-08-2026 al 31-08-2026', .06, .10)]])
    document = UploadedDocument(filename="statement.pdf", file_type="pdf", raw_bytes=raw)
    period = statement_period_from_failed_pdf(document, ValueError("Parser failed"))
    assert period == {"start_date": "2026-08-01", "end_date": "2026-08-31", "basis": "document_metadata"}
    unreadable = UploadedDocument(filename="broken.pdf", file_type="pdf", raw_bytes=b"not a pdf")
    assert statement_period_from_failed_pdf(unreadable, ValueError()) is None
    ocr = SimpleNamespace(_ocr_source_extraction=SimpleNamespace(pages=[
        SimpleNamespace(lines=[SimpleNamespace(text="Período: 01/09/2026 a 30/09/2026")])]))
    assert statement_period_from_failed_pdf(unreadable, ocr)["start_date"] == "2026-09-01"


def test_document_period_uses_complete_ledger_and_prefers_document_metadata():
    from types import SimpleNamespace

    from app.application.statement_period_history import statement_period_from_analysis

    analysis = SimpleNamespace(preview_transactions=[SimpleNamespace(date="2026-09-15")],
                               report_transactions=[SimpleNamespace(date="2026-08-03"), SimpleNamespace(date="2026-09-20")],
                               financial_document=None)
    assert statement_period_from_analysis(analysis) == {
        "start_date": "2026-08-03", "end_date": "2026-09-20", "basis": "movement_dates"}
    analysis.financial_document = {"metadata": {"period_start": "2026-08-01", "period_end": "2026-09-30"}}
    assert statement_period_from_analysis(analysis) == {
        "start_date": "2026-08-01", "end_date": "2026-09-30", "basis": "document_metadata"}


@pytest.mark.real_http
def test_pdf_metrics_over_real_http_and_csv(tmp_path):
    import httpx

    from app.dependencies import get_access_control_service
    from tests.test_admin_dashboard_http_real_api import _run_http_server

    with _run_http_server(tmp_path) as base_url:
        service = app.dependency_overrides[get_access_control_service]()
        for identifier in ("first-failure", "second-failure"):
            service.record_anonymous_conversion_event(
                event_id=identifier, anonymous_fingerprint="fictional-repeated-upload", filename="private-name.pdf",
                model="Banco fictício", conversion_type="pdf-ofx", status="Falha", transactions_count=0,
                pages_count=2, scanned_likely=False, ocr_used=False, ocr_pages_processed=0, duration_ms=100,
                error_code="invalid_pdf_content", file_sha256="a" * 64, document_type="bank_statement",
                statement_period={"start_date": "2026-08-01", "end_date": "2026-08-31", "basis": "document_metadata"})
        with httpx.Client(base_url=base_url, timeout=10) as client:
            assert client.get("/admin/dashboard").status_code == 401
            assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"}).status_code == 200
            response = client.get("/admin/dashboard", params={"days": 1})
            assert response.status_code == 200
            metrics = response.json()["pdf_documents"]
            assert metrics["failure_attempts"] == 2 and metrics["unique_failed_documents"] == 1
            assert metrics["by_period"][0]["label"] == "08/2026"
            assert metrics["attention"][0]["statement_period"]["basis"] == "document_metadata"
            assert "private-name" not in response.text
            exported = client.get("/admin/dashboard/attention.csv")
            assert exported.status_code == 200 and exported.text.count("2026-08-31") == 2


def test_period_and_hash_survive_history_upsert_without_new_metadata(tmp_path):
    client, service, clock, user = _build_dashboard_client(tmp_path)
    try:
        base = dict(user_id=user, processing_id="an_retry", filename="statement.pdf", model="Banco",
                    conversion_type="pdf-ofx", status="Sucesso", transactions_count=2)
        service.record_user_conversion(**base, file_sha256="e" * 64,
                                       statement_period={"start_date": "2026-07-01", "end_date": "2026-07-31",
                                                         "basis": "document_metadata"})
        service.record_user_conversion(**base)
        with service._connect() as conn:
            row = conn.execute("SELECT file_sha256, statement_period_start, statement_period_end, statement_period_basis "
                               "FROM user_conversions WHERE analysis_id='an_retry'").fetchone()
        assert tuple(row) == ("e" * 64, "2026-07-01", "2026-07-31", "document_metadata")
    finally:
        client.close()
        app.dependency_overrides.clear()
