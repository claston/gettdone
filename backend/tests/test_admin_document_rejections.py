"""Expected document refusals must be distinct from technical failures."""
import csv
import io
from datetime import datetime, timezone

import httpx
import pytest

from app.dependencies import get_access_control_service
from app.main import app
from tests.test_admin_dashboard_api import _build_dashboard_client
from tests.test_admin_dashboard_http_real_api import _run_http_server


def record_refusal(service, *, registered_user=None, legacy=False, document_type="fiscal_invoice", technical=False):
    payload = dict(filename="fictional-document.pdf", model="Não identificado", conversion_type="pdf-ofx",
        status="Falha", transactions_count=0, pages_count=1, scanned_likely=False, ocr_used=False,
        ocr_pages_processed=0, duration_ms=10, error_stage="parse",
        error_code="invalid_pdf_content" if legacy else "parse_failed" if technical else "unsupported_document_type",
        error_subcode="unsupported_document_type" if legacy else None,
        document_type=None if legacy else document_type,
        document_processing_decision=None if legacy else "failed" if technical else "rejected",
        failure_diagnostics={"unsupported_document_type": document_type} if legacy and document_type else None)
    if registered_user:
        service.record_user_conversion(user_id=registered_user, processing_id="an_fictional_refusal",
            created_at=datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc).isoformat(), **payload)
        return "an_fictional_refusal", "user_conversions", "analysis_id"
    service.record_anonymous_conversion_event(event_id="anon_evt_fictional_refusal",
        anonymous_fingerprint="fictional-refusal-owner", **payload)
    return "anon_evt_fictional_refusal", "anonymous_conversion_events", "id"


@pytest.mark.parametrize("registered", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_admin_explains_invoice_refusal_in_dashboard_and_csv_without_rewriting_history(tmp_path, registered, legacy):
    client, service, clock, user_id = _build_dashboard_client(tmp_path)
    clock["now"] = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    identifier, table, key = record_refusal(service, registered_user=user_id if registered else None, legacy=legacy)
    try:
        assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"}).status_code == 200
        response = client.get("/admin/dashboard", params={"days": 7})
        assert response.status_code == 200
        payload = response.json()
        item = next(row for row in payload["recent_attention"] if row["processing_id"] == identifier)
        assert item["status"] == "Recusado"
        assert item["document_type"] == "fiscal_invoice"
        assert item["document_processing_decision"] == "rejected"
        assert "Tipo de documento não suportado" in item["issue_reason"]
        assert "Nota fiscal" in item["issue_reason"]
        assert "Falha técnica" not in item["issue_reason"]
        assert "Nenhuma transação encontrada" not in item["issue_reason"]
        fiscal = next(row for row in payload["document_types"]["by_type"] if row["document_type"] == "fiscal_invoice")
        assert fiscal["rejected"] == 1 and fiscal["failed"] == 0
        assert payload["top_errors"][0]["error_code"] == "unsupported_document_type"
        export = client.get("/admin/dashboard/attention.csv")
        assert export.status_code == 200
        rows = list(csv.DictReader(io.StringIO(export.content.decode("utf-8-sig")), delimiter=";"))
        exported = next(row for row in rows if row["identificador"] == identifier)
        assert exported["status"] == "Recusado"
        assert exported["tipo_documento"] == "fiscal_invoice"
        assert exported["motivo_atencao"] == item["issue_reason"]
        with service._connect() as conn:
            row = service._fetchone(conn, f"SELECT status, document_type FROM {table} WHERE {key} = ?", (identifier,))
        assert row["status"] == "Falha"
        assert row["document_type"] == (None if legacy else "fiscal_invoice")
    finally:
        app.dependency_overrides.clear()


def test_actual_parse_failure_is_not_relabelled_as_a_document_refusal(tmp_path):
    client, service, clock, _ = _build_dashboard_client(tmp_path)
    clock["now"] = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    identifier, _, _ = record_refusal(service, technical=True)
    try:
        client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"})
        payload = client.get("/admin/dashboard").json()
        item = next(row for row in payload["recent_attention"] if row["processing_id"] == identifier)
        assert item["status"] == "Falha"
        assert "Falha técnica" in item["issue_reason"]
        assert "Tipo de documento não suportado" not in item["issue_reason"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("legacy,document_type", [(False, None), (True, ["fiscal_invoice"])])
def test_refusal_without_a_recorded_document_type_does_not_invent_a_classification(tmp_path, legacy, document_type):
    client, service, clock, _ = _build_dashboard_client(tmp_path)
    clock["now"] = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    identifier, _, _ = record_refusal(service, document_type=document_type, legacy=legacy)
    try:
        client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"})
        payload = client.get("/admin/dashboard").json()
        item = next(row for row in payload["recent_attention"] if row["processing_id"] == identifier)
        assert item["status"] == "Recusado"
        assert item["document_type"] in (None, "unclassified")
        assert "Tipo de documento não suportado" in item["issue_reason"]
        assert "Nota fiscal" not in item["issue_reason"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.real_http
def test_document_refusal_is_clear_through_real_admin_http(tmp_path):
    with _run_http_server(tmp_path) as base_url:
        service = app.dependency_overrides[get_access_control_service]()
        identifier, _, _ = record_refusal(service)
        # Password hashing under Windows/xdist can exceed httpx's implicit five-second limit.
        with httpx.Client(base_url=base_url, timeout=30) as client:
            assert client.get("/admin/dashboard").status_code == 401
            assert client.post("/admin/auth/login", json={"email": "admin@example.com", "password": "admin-pass"}).status_code == 200
            response = client.get("/admin/dashboard", params={"days": 7})
            assert response.status_code == 200
            row = next(row for row in response.json()["recent_attention"] if row["processing_id"] == identifier)
            assert row["status"] == "Recusado"
            assert "Nota fiscal" in row["issue_reason"]
            assert client.get("/admin/dashboard/attention.csv").status_code == 200
