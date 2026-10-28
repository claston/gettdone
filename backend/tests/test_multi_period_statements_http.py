"""Real upload/report/export and worker coverage for shared statement blocks."""

from dataclasses import replace

import pytest

from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_multi_period_statements import bb_month, pdf
from tests.test_oct27_today_ocr_controls import mercado

pytestmark = pytest.mark.real_http


def assert_exports(client, identifier, owner, count, *, global_balance):
    assert client.get(f"/report/{identifier}", params=owner).status_code == 200
    for fmt in ("csv", "xlsx", "ofx"):
        exported = client.get(f"/convert-report/{identifier}", params={**owner, "format": fmt})
        assert exported.status_code == 200, exported.text
        if fmt == "ofx":
            assert exported.text.count("<STMTTRN>") == count
            assert ("<LEDGERBAL>" in exported.text) is global_balance


def test_full_year_native_document_is_one_upload_with_complete_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = pdf([page for month in range(1, 13) for page in bb_month(month, opening=1000+(month-1)*180, pages=6)])
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-multi-month-native"}
        response = client.post("/convert", data=owner, files={"file": ("year.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert data["transactions_total"] == 144
        assert len(data["document_parts"]) == 12
        assert (data["opening_balance"], data["closing_balance"]) == (1000, 3160)
        assert data["pdf_processing_metrics"]["balance_consistency_failed"] == 0
        assert_exports(client, data["analysis_id"], owner, 144, global_balance=True)
        assert client.get("/report/an_multi_month_nonexistent", params=owner).status_code == 404


@pytest.mark.parametrize("mode", ["text", "tables"])
def test_ocr_blocks_preserve_movements_and_unconfirmed_global_balance(mode, tmp_path, monkeypatch):
    source = mercado(annotated=False)
    second = replace(source.pages[0], page_number=2,
        lines=[replace(line, id="p2-"+line.id, page_number=2, text=line.text.replace("08-2026", "10-2026"))
               for line in source.pages[0].lines])
    raw = configure_ocr(monkeypatch, replace(source, pages=[source.pages[0], second]), mode)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-multi-month-ocr-"+mode}
        response = client.post("/convert", data=owner, files={"file": ("months.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        data = response.json()["analysis"]
        assert [row["amount"] for row in data["preview_transactions"]] == [50, -20, 50, -20]
        assert data["opening_balance"] is data["closing_balance"] is None
        assert "statement_blocks_not_contiguous" in data["pdf_processing_metrics"]["canonical_warning_types_list"]
        assert_exports(client, data["analysis_id"], owner, 4, global_balance=False)


def test_background_worker_uses_the_same_multi_month_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = pdf(bb_month(1)+bb_month(2, opening=1030))
    with worker_case(tmp_path, raw) as (client, job):
        data = job["result"]["analysis"]
        assert data["transactions_total"] == 4
        assert len(data["document_parts"]) == 2
        assert (data["opening_balance"], data["closing_balance"]) == (1000, 1060)
        assert client.get(f"/convert-report/{data['analysis_id']}", params={"format": "ofx"}).status_code == 200


def test_csv_and_negative_http_paths_remain_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-multi-month-csv"}
        raw = b"date,description,amount\n2025-01-01,Entrada teste,100\n2025-02-01,Saida teste,-20\n"
        response = client.post("/convert", data=owner, files={"file": ("months.csv", raw, "text/csv")})
        assert response.status_code == 200, response.text
        assert client.get(f"/report/{response.json()['analysis']['analysis_id']}", params=owner).status_code == 200
        assert client.post("/analyze").status_code == 405
