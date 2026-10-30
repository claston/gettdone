"""Real HTTP acceptance for survey ownership and exports, with fictional PDFs."""

import httpx
import pytest

from app.application.admin_dashboard_service import AdminDashboardService
from app.dependencies import get_access_control_service
from app.main import app
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_followup_conversion_recovery import btg_native

pytestmark = pytest.mark.real_http


def test_survey_tracks_exposures_answers_and_retries_without_blocking_download(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        assert client.post("/auth/anonymous-session", json={}).status_code == 200
        response = client.post("/api/conversions/upload", files={"file": ("ficticio.pdf", btg_native(), "application/pdf")})
        assert response.status_code == 200, response.text
        processing_id = response.json()["processing_id"]
        path = f"/conversion-feedback/{processing_id}"
        service = app.dependency_overrides[get_access_control_service]()
        assert AdminDashboardService(service).get_dashboard()["conversion_feedback"]["offered_count"] == 0
        exposed = client.put(path, json={"answer": None})
        assert exposed.status_code == 200, exposed.text
        assert exposed.json()["answer"] is None
        assert AdminDashboardService(service).get_dashboard()["conversion_feedback"]["unanswered_count"] == 1
        assert client.get(f"/report/{processing_id}").status_code == 200
        for _ in range(2):
            assert client.put(path, json={"answer": "yes"}).status_code == 200
        assert client.put(path, json={"answer": None}).json()["answer"] == "yes"
        assert client.put(path, json={"answer": "no"}).json()["answer"] == "no"
        assert client.get(f"/convert-report/{processing_id}?format=ofx").status_code == 200
        assert client.put(path, json={"answer": "maybe"}).status_code == 400
        assert client.put("/conversion-feedback/unknown", json={"answer": "no"}).status_code == 404
        assert client.get("/report/nonexistent").status_code == 404
        # /analyze is deliberately retired in production; exercise its current contract.
        assert client.post("/analyze").status_code == 405
        with httpx.Client(base_url=str(client.base_url)) as stranger:
            assert stranger.put(path, json={"answer": "no"}).status_code == 401
            assert stranger.post("/auth/anonymous-session", json={}).status_code == 200
            assert stranger.put(path, json={"answer": "no"}).status_code == 403
        summary = AdminDashboardService(service).get_dashboard()["conversion_feedback"]
        assert (summary["offered_count"], summary["no_count"], summary["yes_count"]) == (1, 1, 0)


def test_async_conversion_feedback_uses_async_report_ownership(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, btg_native()) as (client, job):
        response = client.put(f"/conversion-feedback/{job['analysis_id']}", json={"answer": "yes"})
        assert response.status_code == 200, response.text
        assert response.json()["answer"] == "yes"
