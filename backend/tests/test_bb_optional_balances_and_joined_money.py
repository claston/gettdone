"""Fictional regressions for optional controls and OCR joining adjacent cells."""
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import InvalidFileContentError
from tests.ocr_conversion_http_helpers import configure_ocr, public_client, worker_case
from tests.test_oct20_followup_ocr import ocr
from tests.test_recovery_case_ocr import bb_source


def signed_without_opening(*, closing=True, controls=True, final="130,00 (+)"):
    lines = [
        ("Extrato de Conta Corrente", .1, .02),
        ("Dia", .08, .1), ("Lote", .2, .1), ("Documento", .3, .1),
        ("Histórico", .45, .1), ("Valor", .9, .1),
        ("01/07/2026", .08, .2), ("10001", .3, .2), ("Pix Recebido", .45, .2), ("50,00 (+)", .9, .2),
    ]
    if controls:
        lines += [("01/07/2026", .08, .3), ("Saldo do dia", .45, .3), ("150,00 (+)", .9, .3)]
    lines += [("02/07/2026", .08, .4), ("10002", .3, .4), ("Pagamento", .45, .4), ("20,00 (-)", .9, .4)]
    if closing:
        lines += [("02/07/2026", .08, .5), ("Saldo", .45, .5), (final, .9, .5)]
    return ocr([lines])


def joined_cash(token="20,00D80,00C", *, left=None, width=None, handwriting=False):
    source = bb_source()
    page = source.pages[0]
    amount = next(line for line in page.lines if line.text == "20,00 D")
    balance = next(line for line in page.lines if line.text == "80,00 C" and line.bbox["top"] == .3)
    box = {**amount.bbox, "width": balance.bbox["left"] + balance.bbox["width"] - amount.bbox["left"]}
    if left is not None:
        box["left"] = left
    if width is not None:
        box["width"] = width
    word = ExtractedWord("merged-money", 1, 1, token, confidence=46.5, bbox=box,
                         text_type="HANDWRITING" if handwriting else "PRINTED")
    lines = [replace(line, text=token, bbox=box, word_ids=(word.id,)) if line.id == amount.id else line
             for line in page.lines if line.id != balance.id]
    return replace(source, pages=[replace(page, lines=lines, words=[word])]), box


@pytest.mark.parametrize("closing,controls", [(True, True), (True, False), (False, True), (False, False)])
def test_missing_printed_balances_preserve_readable_rows_without_inventing_bounds(closing, controls):
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(signed_without_opening(closing=closing, controls=controls))
    assert [(row.date, row.amount) for row in result.transactions] == [("2026-07-01", 50), ("2026-07-02", -20)]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert "verified_opening_balance" not in result.parse_metrics
    assert "derived_opening_balance" not in result.parse_metrics
    assert result.parse_metrics["printed_balance_checks"] == int(closing and controls)
    assert all(row.source_regions and row.external_reference_id for row in result.canonical_transactions)
    assert "balance_bounds_unavailable" in result.canonical_transactions[0].warnings


def test_missing_opening_preserves_repeated_movements_and_later_page_provenance():
    source = signed_without_opening(closing=False, controls=False)
    first = source.pages[0]
    # A readable continuation carries a legitimate repetition of the same payment.
    later_lines = [replace(line, id="later-" + line.id, page_number=2)
                   for line in first.lines if line.bbox["top"] <= .1 or line.bbox["top"] == .4]
    later = replace(first, page_number=2, lines=later_lines)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[first, later]))
    assert [row.amount for row in result.transactions] == [50, -20, -20]
    assert {region.page for region in result.canonical_transactions[-1].source_regions} == {2}
    assert result.parse_metrics["financial_controls_validated"] == 0


def test_missing_opening_still_checks_later_printed_balances():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(signed_without_opening(final="140,00 (+)"))
    assert [row.amount for row in result.transactions] == [50, -20]
    assert result.parse_metrics["statement_validation_failed"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_consistency_failed"] == 1


@pytest.mark.parametrize("old,new", [("50,00 (+)", "50,00"), ("50,00 (+)", "valor ilegível"),
                                    ("01/07/2026", "data ilegível")])
def test_optional_balances_do_not_relax_required_transaction_fields(old, new):
    source = signed_without_opening()
    source = replace(source, pages=[replace(page, lines=[replace(line, text=new) if line.text == old else line
                                                        for line in page.lines]) for page in source.pages])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.parametrize("token", ["20,00D80,00C", "20,00 D80,00 C", "20,00D 80,00C", "20,00 D 80,00 C"])
def test_joined_explicit_amount_and_balance_keep_signs_and_original_evidence(token):
    source, box = joined_cash(token)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 80
    assert any(region.left == box["left"] and region.width == box["width"]
               for region in result.canonical_transactions[0].source_regions)


@pytest.mark.parametrize("token,options", [
    ("20,00D80,00", {}), ("2O,00D80,00C", {}), ("20,00D80,00C1,00C", {}),
    ("20,00D80,00C", {"width": .04}), ("20,00D80,00C", {"left": .65, "width": .23}),
    ("20,00D80,00C", {"handwriting": True}),
])
def test_compact_money_requires_two_explicit_tokens_in_both_financial_columns(token, options):
    source, _ = joined_cash(token, **options)
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


@pytest.mark.real_http
@pytest.mark.parametrize("case,amounts", [("missing_opening", [50, -20]), ("joined_money", [-20])])
def test_upload_worker_and_exports_preserve_optional_balance_review(case, amounts, tmp_path, monkeypatch):
    source = signed_without_opening() if case == "missing_opening" else joined_cash()[0]
    raw = configure_ocr(monkeypatch, source)
    with public_client(tmp_path / "public") as client:
        owner = {"anonymous_fingerprint": "fp-bb-optional-balances"}
        response = client.post("/convert", data=owner, files={"file": ("statement.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert [row["amount"] for row in analysis["preview_transactions"]] == amounts
        if case == "missing_opening":
            from app.application.pdf_review_guidance import build_pdf_review_guidance

            assert analysis["opening_balance"] is None
            assert analysis["closing_balance"] is None
            assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
            guidance = build_pdf_review_guidance(analysis)
            assert guidance["category"] == "validation_incomplete"
            assert "não estão disponíveis" in guidance["message"]
        assert client.get(f"/report/{analysis['analysis_id']}", params=owner).status_code == 200
        assert client.get("/report/an_missing", params=owner).status_code == 404
        for fmt in ("csv", "xlsx", "ofx"):
            exported = client.get(f"/convert-report/{analysis['analysis_id']}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
            if fmt == "ofx":
                assert exported.text.count("<STMTTRN>") == len(amounts)
                if case == "missing_opening":
                    assert "<LEDGERBAL>" not in exported.text
    with worker_case(tmp_path / "worker", raw) as (client, job):
        analysis = job["result"]["analysis"]
        assert analysis["transactions_total"] == len(amounts)
        if case == "missing_opening":
            assert analysis["opening_balance"] is None
            assert analysis["pdf_processing_metrics"]["financial_controls_validated"] == 0
        exported = client.get(f"/convert-report/{job['analysis_id']}", params={"format": "ofx"})
        assert exported.status_code == 200 and exported.text.count("<STMTTRN>") == len(amounts)
