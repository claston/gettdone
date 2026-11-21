"""Fictional BB cash columns with numeric signs repeated by C/D indicators."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_bb_optional_balances_and_joined_money import joined_cash
from tests.test_recovery_case_ocr import bb_source


def signed_cash(*, debit="- 20,00 D", opening="- 100,00 D", closing="- 120,00 D"):
    source = bb_source()
    page = source.pages[0]
    replacements = {"20,00 D": debit, "100,00 C": opening, "80,00 C": closing}
    return replace(source, pages=[replace(page, lines=[
        replace(line, text=replacements.get(line.text, line.text)) for line in page.lines
    ])])


@pytest.mark.parametrize("space", ["", " "])
@pytest.mark.parametrize("prefix", ["-", "−"])
def test_repeated_debit_signs_keep_negative_amounts_and_balances(prefix, space):
    source = signed_cash(debit=f"{prefix}{space}20,00 D", opening=f"{prefix}{space}100,00 D",
                         closing=f"{prefix}{space}120,00 D")
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == -120
    assert result.parse_metrics["financial_controls_validated"] == 1
    row = result.canonical_transactions[0]
    assert row.source_page == 1 and row.source_regions and row.source_line_refs
    assert row.external_reference_id == "123.001"


def test_explicit_plus_and_credit_are_not_double_counted():
    source = signed_cash(debit="+ 20,00 C", opening="+100,00 C", closing="+ 120,00 C")
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [20]
    assert result.parse_metrics["verified_closing_balance"] == 120


@pytest.mark.parametrize("token", ["-1,200,00D", "-1,200,00 D", "- 1,200,00 D"])
def test_ocr_grouping_repair_preserves_repeated_debit_signs(token):
    source = signed_cash(debit=token, opening="100,00 C", closing="-1.100,00 D")
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-1200]
    assert result.parse_metrics["verified_closing_balance"] == -1100
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert any(line.text == token for line in source.pages[0].lines)


def boleto_without_cd(*, label="109 Pagamento de Boleto", closing="80,00 C"):
    source = signed_cash(debit="20,00", opening="100,00 C", closing=closing)
    page = source.pages[0]
    return replace(source, pages=[replace(page, lines=[
        replace(line, text=label) if line.text == "144 Pix Enviado" else line for line in page.lines
    ])])


def test_explicit_boleto_payment_with_printed_balance_requires_review_when_cd_is_missing():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(boleto_without_cd())
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["verified_closing_balance"] == 80
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "amount_sign_from_explicit_payment_label" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("label,closing", [("109 Lançamento", "80,00 C"), ("999 Pagamento de Boleto", "80,00 C"),
                                        ("109 Pagamento de Boleto", "120,00 C"),
                                        ("109 Pagamento de Boleto", "81,00 C")])
def test_missing_cd_requires_both_explicit_payment_identity_and_matching_balance(label, closing):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(boleto_without_cd(label=label, closing=closing))


def test_native_pdf_uses_the_same_repeated_sign_policy(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    source = signed_cash()
    cells = [(line.text, line.bbox["left"], line.bbox["top"]) for line in source.pages[0].lines]
    result = pdf_parser.parse_pdf_transactions(grid_pdf([cells]))
    assert [(row.date, row.amount) for row in result.transactions] == [("2026-07-01", -20)]
    assert result.parse_metrics["verified_opening_balance"] == -100
    assert result.parse_metrics["verified_closing_balance"] == -120


@pytest.mark.parametrize("token", ["+20,00 D", "-20,00 C", "--20,00 D", "-2O,00 D", "-20,000 D", "+1,200,00D", "-1,200,00C"])
def test_conflicting_or_unreadable_movement_signs_remain_blocked(token):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(signed_cash(debit=token))


def test_repeated_signs_do_not_hide_a_real_running_balance_difference():
    source = signed_cash(closing="-121,00 D")
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["statement_validation_failed"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert result.parse_metrics["verified_closing_balance"] == -121


@pytest.mark.parametrize("token", ["-20,00D-120,00D", "- 20,00 D - 120,00 D"])
def test_joined_columns_keep_repeated_signs_and_shared_evidence(token):
    source, box = joined_cash(token)
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[
        replace(line, text="-100,00 D" if line.text == "100,00 C" else
                "-120,00 D" if line.text == "80,00 C" else line.text) for line in page.lines
    ])])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["verified_closing_balance"] == -120
    assert any(region.left == box["left"] and region.width == box["width"]
               for region in result.canonical_transactions[0].source_regions)


@pytest.mark.real_http
@pytest.mark.parametrize("mismatch", [False, True])
def test_free_and_pro_exports_preserve_signed_rows_and_required_review(mismatch, tmp_path, monkeypatch):
    raw = configure_ocr(monkeypatch, signed_cash(closing="-121,00 D" if mismatch else "-120,00 D"))
    with public_client(tmp_path / "free") as client:
        owner = {"anonymous_fingerprint": "fp-bb-signed-cd"}
        response = client.post("/convert", data=owner, files={"file": ("synthetic.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == [-20]
        assert analysis["opening_balance"] == -100
        assert analysis["closing_balance"] == (-121 if mismatch else -120)
        if mismatch:
            from app.application.pdf_review_guidance import build_pdf_review_guidance

            assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
            assert build_pdf_review_guidance(analysis)["category"] == "financial_discrepancy"
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        assert client.get("/report/an_missing", params=owner).status_code == 404
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "ofx":
                assert exported.text.count("<STMTTRN>") == 1
                assert "<TRNAMT>-20.00" in exported.text
    with worker_case(tmp_path / "pro", raw) as (client, job):
        assert job["result"]["analysis"]["transactions_total"] == 1
        if mismatch:
            assert job["result"]["analysis"]["pdf_processing_metrics"]["financial_controls_validated"] == 0
        exported = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert exported.status_code == 200 and "<TRNAMT>-20.00" in exported.text
