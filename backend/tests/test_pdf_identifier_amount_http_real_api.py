import pytest

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_pdf_identifier_amount_boundaries import UUID, identifier_statement_lines

pytestmark = pytest.mark.real_http
EXPECTED = [134.46, 300, -299]


def identifier_pdf():
    return generate_native_text_pdf((identifier_statement_lines(),))


def assert_identifier_analysis(analysis):
    rows = [row for row in analysis["preview_transactions"] if "SALDO ANTERIOR" not in row["description"]]
    assert [row["amount"] for row in rows] == EXPECTED
    # The API's existing description normalization replaces punctuation with spaces.
    assert rows[0]["description"].endswith("080 85")
    assert rows[1]["description"].endswith(UUID.replace("-", " "))
    assert analysis["closing_balance"] == 1135.46
    assert analysis["pdf_processing_metrics"]["canonical_warning_count"] == 0


def test_http_identifiers_do_not_change_values_in_preview_and_exported_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-identifier-boundaries"}
        response = client.post("/convert", data=owner, files={"file": ("statement.pdf", identifier_pdf(), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert_identifier_analysis(analysis)
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx", "ofx"):
            report = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
            assert report.status_code == 200, report.text
            if fmt == "ofx":
                for amount in EXPECTED:
                    assert f"<TRNAMT>{amount:.2f}" in report.text
                assert "<TRNAMT>-85134.46" not in report.text
                assert "<TRNAMT>4300.00" not in report.text
        csv = client.post("/convert", data=owner, files={
            "file": ("statement.csv", b"date,description,amount\n2026-09-02,PIX,134.46\n", "text/csv"),
        })
        assert csv.status_code == 200 and csv.json()["analysis"]["preview_transactions"][0]["amount"] == 134.46
        assert client.get("/report/an_identifier_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405


def test_worker_preserves_identifier_boundaries_in_result_and_ofx(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, identifier_pdf(), filename="identifiers.pdf") as (client, job):
        assert_identifier_analysis(job["result"]["analysis"])
        report = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert report.status_code == 200, report.text
        for amount in EXPECTED:
            assert f"<TRNAMT>{amount:.2f}" in report.text
