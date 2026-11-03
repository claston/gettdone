"""Actual upload, worker and export regressions using synthetic native PDFs."""
from io import BytesIO

import pytest
from openpyxl import load_workbook

from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.ocr_conversion_http_helpers import public_client, worker_case
from tests.test_november_statement_integrity import caixa_text, inherited_pages, position_text, split_pages

pytestmark = pytest.mark.real_http


def document(kind):
    pages = {"inherited": inherited_pages, "split": split_pages, "positions": lambda: [position_text()],
             "caixa": lambda: [caixa_text()], "itau": lambda: ["""ItaúEmpresas
Extrato de 01/10/2026 até 31/10/2026
Data Lançamento Ag./Origem Valor (R$) Saldo (R$)
30/09 SALDO ANTERIOR 1,00
01/10 PIX ENVIADO TESTE 9001 -50,00
01/10 REDE VISA TESTE 9002 100,00
01/10 SDO CTA/APL AUTOMATICAS 9.000,00
31/10 S A L D O 1,00"""]}[kind]()
    return generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in pages))


def check_analysis(analysis, kind):
    if kind == "positions":
        assert analysis["transactions_total"] == 0
        report = analysis["financial_document"]
        assert len(report["observations"]) == 8
        assert all(item["dates"]["as_of"] == "2026-10-01" for item in report["observations"])
        assert analysis["opening_balance"] is None and analysis["closing_balance"] is None
    elif kind == "caixa":
        assert analysis["transactions_total"] == 2
        assert analysis["opening_balance"] == 100
        assert analysis["closing_balance"] == 105
        assert analysis["pdf_processing_metrics"]["balance_bounds_unavailable"] == 0
        assert analysis["pdf_processing_metrics"]["balance_consistency_checked"] == 2
        assert analysis["pdf_processing_metrics"]["balance_consistency_failed"] == 0
    elif kind == "itau":
        assert analysis["transactions_total"] == 2
        assert analysis["opening_balance"] is None and analysis["closing_balance"] is None
        assert analysis["pdf_processing_metrics"].get("balance_bounds_unavailable") == 1
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "review_recommended"
    else:
        assert analysis["transactions_total"] == (5 if kind == "inherited" else 4)
        assert analysis["opening_balance"] == (10 if kind == "inherited" else -10)
        assert analysis["closing_balance"] == (20 if kind == "inherited" else 5.5)
        assert analysis["pdf_processing_metrics"]["export_recommendation"] == "safe_to_export"
        assert not any(row["warning_types"] for row in analysis["preview_transactions"])


def check_exports(client, analysis_id, kind, owner=None):
    owner = owner or {}
    for fmt in ("csv", "xlsx", "ofx"):
        response = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
        if fmt == "ofx" and kind == "positions":
            assert response.status_code == 400
            continue
        assert response.status_code == 200, response.text
        if fmt == "ofx":
            assert response.text.count("<STMTTRN>") == {"inherited": 5, "split": 4, "caixa": 2, "itau": 2}[kind]
            if kind == "itau":
                assert "<LEDGERBAL>" not in response.text and "<AVAILBAL>" not in response.text
            else:
                assert "<BALAMT>" in response.text
        if fmt == "xlsx" and kind == "positions":
            workbook = load_workbook(BytesIO(response.content))
            assert "Posições" in workbook.sheetnames
            assert workbook["Posições"].max_row == 9
        if fmt == "csv" and kind == "positions":
            assert "2026-10-01" in response.text


@pytest.mark.parametrize("kind", ["inherited", "split", "positions", "caixa", "itau"])
def test_native_upload_reports_preserve_movements_and_position_semantics(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "november-synthetic-owner"}
        response = client.post("/convert", data=owner, files={"file": ("statement.pdf", document(kind), "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        check_analysis(analysis, kind)
        check_exports(client, analysis["analysis_id"], kind, owner)
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        assert client.get("/report/an_november_missing", params=owner).status_code == 404


@pytest.mark.parametrize("kind", ["inherited", "split", "positions", "caixa", "itau"])
def test_worker_uses_same_movement_and_position_policy(kind, tmp_path, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with worker_case(tmp_path, document(kind)) as (client, job):
        check_analysis(job["result"]["analysis"], kind)
        check_exports(client, job["analysis_id"], kind)
