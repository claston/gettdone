from decimal import Decimal
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.document_classification_history import prepare_document_classification_record
from app.application.document_type_classifier import classify_document_type
from app.application.errors import PdfFinancialValidationError, UnsupportedDocumentContentError
from app.application.parsers.pdf.empty_statement import has_explicit_no_activity
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext
from app.application.parsers.pdf.verified_statements import parse_verified_statement
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.afternoon_statements import (
    alelo_page,
    btg_investment_page,
    btg_invoice_pages,
    c6_inactive_page,
    colmeia_pages,
    cresol_descending_pages,
    inter_page,
    unicred_page,
)
from tests.test_oct20_new_native_statements import bb_page, xp_page


def parse(pages):
    return pdf_parser._parse_pdf_transactions_from_page_texts(
        pages, preserve_layout_spacing=True, context=LayoutSpecificParseContext(is_native_text=True),
    )


@pytest.mark.parametrize("pages,amounts,opening,closing", [
    ([inter_page()], [-97, 990, -270.9], 100, 722.1),
    (cresol_descending_pages(), [30, -2, 2, -10], -50, -30),
    (colmeia_pages(), [100, -30, -20, -50], 1, 1),
    ([btg_investment_page()], [-100.05, .05], 100, 0),
    (btg_invoice_pages(), [30, -25, -100], -30, -125),
])
def test_new_formats_preserve_every_amount_and_validate_controls(pages, amounts, opening, closing):
    result = parse(pages)
    assert [t.amount for t in result.transactions] == amounts
    assert result.parse_metrics["verified_opening_balance"] == opening
    assert result.parse_metrics["verified_closing_balance"] == closing
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(t.source_page and t.source_line for t in result.canonical_transactions)


@pytest.mark.parametrize("pages,before,after", [
    ([inter_page()], "722,10", "722,11"),
    ([inter_page()], "-270,9", "-270,90x"),
    (cresol_descending_pages(), "- R$ 20,00", "- R$ 21,00"),
    (cresol_descending_pages(), "IOF - R$ 2,00", "IOF - R$ ilegível"),
    (colmeia_pages(), "71,00 C", "72,00 C"),
    (colmeia_pages(), "(PARA: EXEMPLO) 20,00 D", "(PARA: EXEMPLO) ilegível"),
    ([btg_investment_page()], "100,05\n", "101,05\n"),
    (btg_invoice_pages(), "Conversão para Real - R$ 100,00", "Conversão para Real - R$ 99,00"),
    (btg_invoice_pages(), "R$ 25,00", "R$ ilegível"),
])
def test_incomplete_or_inconsistent_financial_fields_cannot_fall_back(pages, before, after):
    assert any(before in page for page in pages)
    with pytest.raises(PdfFinancialValidationError):
        parse([p.replace(before, after) for p in pages])


def test_alelo_keeps_repeated_comma_amounts_and_excludes_later_current_balance():
    result = parse([alelo_page()])
    assert [t.amount for t in result.transactions] == [3000, -1264.86, -1000]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_validation_status"] == "not_available"
    assert all(t.date.startswith("2026-09") for t in result.transactions)
    assert not any(t.amount == 100 for t in result.transactions)


@pytest.mark.parametrize("value", ["1,00,00", "1,000,0", "1,000,00,00", "1,000,00x"])
def test_alelo_rejects_invalid_currency_grouping(value):
    with pytest.raises(PdfFinancialValidationError):
        parse([alelo_page().replace("1,000,00", value)])


def test_no_activity_with_month_and_zero_flow_summaries_is_positive_evidence():
    assert has_explicit_no_activity([c6_inactive_page()])
    result = parse([c6_inactive_page()])
    assert not result.transactions
    assert result.parse_metrics["statement_no_activity"] == 1


@pytest.mark.parametrize("mutation", ["nonzero_credit", "nonzero_debit", "dated_movement", "other_page"])
def test_monthly_inactivity_does_not_hide_movements(mutation):
    text = c6_inactive_page()
    pages = [text]
    if mutation == "nonzero_credit":
        pages = [text.replace("Entradas: R$ 0,00", "Entradas: R$ 1,00")]
    elif mutation == "nonzero_debit":
        pages = [text.replace("Saídas: R$ 0,00", "Saídas: R$ 1,00")]
    elif mutation == "dated_movement":
        pages = [text + "\n02/01/2026 PIX 10,00"]
    else:
        pages.append("C6 BANK Extrato Conta 123456\n02/01/2026 PIX 10,00")
    assert not has_explicit_no_activity(pages)


def test_unicred_credits_with_debito_in_the_card_arrangement_keep_their_sign():
    result = parse([unicred_page()])
    assert [t.amount for t in result.transactions] == [25, -5]
    assert result.parse_metrics["balance_consistency_failed"] == 0


@pytest.mark.parametrize("history", ["CRED DOM ARRANJO DEBITO", "DEBITO NO NOME DO PAGADOR", "PAGAMENTO DEBITO"])
def test_unicred_printed_opening_and_balance_delta_take_priority_over_history(history):
    result = parse([unicred_page().replace("CRED DOM ARRANJO DEBITO", history)])
    assert [t.amount for t in result.transactions] == [25, -5]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_btg_two_column_invoice_keeps_domestic_amount_beside_foreign_exchange_metadata():
    pages = btg_invoice_pages()
    pages = [p.replace("125,00", "130,00").replace(
        "                                              Cotação", "07 Dez OUTRA LOJA R$ 5,00                    Cotação",
    ) for p in pages]
    result = parse(pages)
    assert [t.amount for t in result.transactions] == [30, -25, -5, -100]
    assert result.parse_metrics["verified_closing_balance"] == -130


@pytest.mark.parametrize("pages,expected", [
    (cresol_descending_pages(), "bank_statement"),
    ([btg_investment_page()], "bank_statement"),
    (btg_invoice_pages(), "credit_card_statement"),
    ([alelo_page()], "prepaid_card_statement"),
])
def test_explicit_statement_identity_does_not_come_from_transaction_keywords(pages, expected):
    result = classify_document_type("input.pdf", b"", extracted_text="\n".join(pages))
    assert result.document_type == expected


@pytest.mark.parametrize("document_type", ["prepaid_card_statement", "notary_receipt"])
def test_new_document_classifications_survive_history_serialization(document_type):
    record = prepare_document_classification_record(
        document_type=document_type, document_type_confidence=.99,
        document_classification_version="2026-10-21.1", document_processing_decision="accepted",
        document_classification_evidence=["explicit_structure"],
    )
    assert record[0] == document_type


def test_inter_windows_text_map_can_represent_portuguese_labels_as_utf8_bytes(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    broken = inter_page().encode("utf-8").decode("latin-1")
    raw = generate_native_text_pdf((tuple(broken.splitlines()),))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [-97, 990, -270.9]
    assert result.parse_metrics["verified_closing_balance"] == 722.1


def test_xp_short_minute_field_does_not_drop_a_financial_row():
    result = parse([xp_page().replace("09:12:34", "09:1:34")])
    assert [t.amount for t in result.transactions] == [500, -300]
    assert result.parse_metrics["verified_closing_balance"] == 1200


def test_bb_stops_at_final_balance_before_future_zero_date_and_investment_positions():
    result = parse([bb_page() + "\nInformações Adicionais\nSaldo 999,00 (+)\n"
                    "Lançamentos Futuros\n00/00/0000 0,00 (+)\nAplicações Financeiras\nBB CDB 800,00"])
    assert len(result.transactions) == 2
    assert result.parse_metrics["verified_closing_balance"] == 1905.43


def test_bb_does_not_ignore_a_historical_transaction_after_an_early_final_balance():
    with pytest.raises(PdfFinancialValidationError):
        parse([bb_page() + "\n05/07/2026 13105 123 PIX 100,00 (+)"])


def test_unsupported_notary_receipt_is_rejected_before_ocr(monkeypatch):
    text = "CARTÓRIO DE NOTAS E PROTESTO\nRECIBO DE CANCELAMENTO DE PROTESTO N 123\nTotal R$ 20,00"
    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda _: [text])
    monkeypatch.setattr(pdf_parser, "extract_pdf_page_texts_with_ocr", lambda *a, **kw: pytest.fail("No OCR"))
    with pytest.raises(UnsupportedDocumentContentError, match="cartório") as exc:
        pdf_parser.parse_pdf_transactions(b"%PDF synthetic")
    assert exc.value.document_type == "notary_receipt"


@pytest.mark.parametrize("pages,bank,opening,closing", [
    ([inter_page()], "077", 100, 722.1),
    (cresol_descending_pages(), "133", -50, -30),
    (colmeia_pages(), "133", 1, 1),
    ([btg_investment_page()], "208", 100, 0),
    (btg_invoice_pages(), "208", -30, -125),
    ([c6_inactive_page()], "336", None, None),
])
def test_native_pipeline_preserves_identity_and_printed_period_balances(pages, bank, opening, closing, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in pages))
    result = build_default_conversion_pipeline().run(filename="input.pdf", raw_bytes=raw, analysis_id="an_afternoon")
    data = result.analysis_data
    assert data.bank_code == bank
    assert data.opening_balance == opening
    assert data.closing_balance == closing
    if data.report_transactions:
        assert Decimal(str(opening)) + sum((Decimal(str(t.amount)) for t in data.report_transactions), Decimal(0)) == Decimal(str(closing))


@pytest.mark.parametrize("scale", [.75, 1, 2])
def test_colmeia_native_watermark_does_not_hide_rows_or_create_transactions(scale, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(p.splitlines()) for p in colmeia_pages()))
    reader, writer = PdfReader(BytesIO(raw)), PdfWriter()
    for page in reader.pages:
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 18 Tf .707 .707 -.707 .707 100 350 Tm "
                        b"(OPERADOR TESTE 21/10/2026 12:00 VALOR 9.999,00 C) Tj ET")
        previous = page[NameObject("/Contents")]
        page[NameObject("/Contents")] = ArrayObject([previous, writer._add_object(stream)])
        page.scale_by(scale)
        writer.add_page(page)
    payload = BytesIO()
    writer.write(payload)
    result = pdf_parser.parse_pdf_transactions(payload.getvalue())
    assert [t.amount for t in result.transactions] == [100, -30, -20, -50]
    assert "OPERADOR TESTE" not in result.extracted_text
    assert result.parse_metrics["verified_closing_balance"] == 1


def test_unrelated_statement_does_not_match_new_strict_readers():
    assert parse_verified_statement(["Extrato genérico\n01/08/2026 PIX 100,00"]) is None
