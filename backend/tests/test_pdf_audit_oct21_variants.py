"""Synthetic coverage for the October audit's document structures (no customer data)."""
import pytest

from app.application import pdf_parser
from app.application.bank_identity import resolve_bank_name
from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.document_type_classifier import classify_document_type
from app.application.errors import PdfFinancialValidationError, UnsupportedDocumentContentError


def ocr_line(text, x, y, number):
    return ExtractedLine(str(number), 1, number, text, bbox={"left": x, "top": y, "width": .08, "height": .008})


def btg_extraction():
    specs = [("BTG Pactual Conta corrente PJ", .03, .05),
             ("Total de entradas", .5, .1), ("R$ 100,00", .85, .1),
             ("Total de saídas", .5, .13), ("R$ 30,00", .85, .13),
             ("Data lançamento", .035, .2), ("Descrição do lançamento", .18, .2),
             ("Entradas / Saídas (R$)", .6, .2), ("Saldo (R$)", .79, .2),
             ("03/08/2026", .035, .25), ("Saldo de fechamento", .18, .25), ("171,00", .79, .25),
             ("03/08/2026", .035, .30), ("Pagamento sintético", .18, .294), ("-30,00", .6, .30), ("171,00", .79, .30),
             ("02/08/2026", .035, .35), ("Crédito sintético", .18, .35), ("100,00", .6, .35), ("200,00", .79, .35),
             ("01/08/2026", .035, .4), ("Saldo de abertura", .18, .4), ("100,00", .79, .4)]
    lines = [ocr_line(*spec, i) for i, spec in enumerate(specs, 1)]
    return RawDocumentExtraction("aws_textract", "synthetic", [ExtractedPage(1, lines)], {})


def test_btg_geometry_preserves_all_movements_and_source_inconsistency():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(btg_extraction())
    assert [row.amount for row in result.transactions] == [100, -30]
    assert result.parse_metrics["printed_opening_balance"] == 100
    assert result.parse_metrics["printed_closing_balance"] == 171
    assert result.parse_metrics["balance_validation_status"] == "inconsistent_printed_balances"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_consistency_failed"] > 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_btg_missing_amount_cannot_silently_drop_transaction():
    from dataclasses import replace
    extraction = btg_extraction()
    page = extraction.pages[0]
    extraction = replace(extraction, pages=[replace(page, lines=[line for line in page.lines if line.text != "-30,00"])])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)


def test_btg_gateway_uses_geometry_even_in_tables_mode(monkeypatch):
    from types import SimpleNamespace

    extraction = btg_extraction()
    gateway = SimpleNamespace(analyze_pdf=lambda **kwargs: {"metrics": {"textract_mode": "tables"}})
    monkeypatch.setattr(pdf_parser, "TextractGateway", lambda: gateway)
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: extraction)
    result = pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic")
    assert [row.amount for row in result.transactions] == [100, -30]
    assert result.parse_metrics["selected_parser"] == "layout_specific_btg_pj_ocr"
    assert result.parse_metrics["textract_used"] == 1


def test_btg_consistent_balances_and_inconsistent_summary(monkeypatch):
    from dataclasses import replace
    extraction = btg_extraction()
    page = extraction.pages[0]
    consistent = replace(extraction, pages=[replace(page, lines=[replace(line, text="170,00") if line.text == "171,00"
                                                              else line for line in page.lines])])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(consistent)
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_validation_status"] == "validated"
    inconsistent = replace(consistent, pages=[replace(consistent.pages[0], lines=[
        replace(line, text="R$ 31,00") if line.text == "R$ 30,00" else line for line in consistent.pages[0].lines])])
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(inconsistent)


def test_btg_summary_difference_is_warned_even_if_every_running_balance_closes():
    from dataclasses import replace
    extraction = btg_extraction()
    page = extraction.pages[0]
    extraction = replace(extraction, pages=[replace(page, lines=[
        replace(line, text="170,00") if line.text == "171,00" and line.bbox["top"] == .30 else line for line in page.lines])])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(extraction)
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert "printed_summary_balance_mismatch" in result.canonical_transactions[-1].warnings
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def parse(pages):
    return pdf_parser._parse_pdf_transactions_from_page_texts(pages)


def test_payment_receipt_has_specific_product_rejection():
    text = "BANCO DO BRASIL\nCOMPROVANTE DE PAGAMENTO DE TITULOS\nVALOR COBRADO 101,20\nDATA DO PAGAMENTO 02/09/2026"
    assert classify_document_type("input.pdf", b"", extracted_text=text).document_type == "bank_receipt"
    with pytest.raises(UnsupportedDocumentContentError, match="comprovante de pagamento"):
        parse([text])


def test_bb_footer_beats_counterparty_bank():
    text = ("Extrato de Conta Corrente\nData Histórico Valor\n03/08/2026 Seguro BRADESCO 10,00 D\n"
            "bb.com.br - Central de Atendimento BB")
    assert resolve_bank_name(extracted_text=text) == "Banco do Brasil"


def test_split_abbreviated_dates_inherit_day_without_inventing_balances():
    result = parse(["Extrato-CNPJ: [EMPRESA] - Gerado em 21/10/2026\n20 -R$ 120,00\nout/26 PAGAMENTO TITULO-MOB\n"
                    "-R$ 80,00\nPAGAMENTO TITULO-MOB\n16 R$ 60,00\nout/26 TED C RECEBIDA-TICKET\nPágina 1 de 1"])
    assert [(r.date, r.amount) for r in result.transactions] == [("2026-10-20", -120), ("2026-10-20", -80), ("2026-10-16", 60)]
    assert all(r.running_balance is None for r in result.canonical_transactions)
    assert result.parse_metrics["balance_validation_status"] == "not_available"


def test_split_abbreviated_dates_reject_unreadable_amount():
    text = ("Extrato-CNPJ: [EMPRESA] - Gerado em 21/10/2026\n20 -R$ 120,00\nout/26 PAGAMENTO TITULO-MOB\n"
            "-R$ ilegível\nPAGAMENTO TITULO-MOB")
    with pytest.raises(PdfFinancialValidationError):
        parse([text])


TOTALBANK = """Extrato de Conta
Filtro: Extrato completo do banco "BRADESCO"
Agência/Conta Data Histórico Doc Valor Saldo
0001-000000123456-7 01/06/2026 Saldo anterior R$ 100,00
0001-000000123456-7 02/06/2026 Saldo do dia R$ 70,00
0001-000000123456-7 02/06/2026 PAGAMENTO 123 R$ -30,00
0001-000000123456-7 02/06/2026 Saldo do dia R$ 70,00
0001-000000123456-7 03/06/2026 Saldo do dia R$ 70,00
0001-000000123456-7 03/06/2026 TED RECEBIDA 456 R$ 50,00
0001-000000123456-7 03/06/2026 Saldo do dia R$ 120,00
0001-000000123456-7 04/06/2026 Saldo do dia R$ 120,00
Demonstrativo de investimento
0001-000000123456-7 04/06/2026 INVESTIMENTO R$ 999,00
http://portal.totalbank.com.br/bradesco/views/extrato.xhtml
"""


def test_totalbank_uses_last_daily_control_and_excludes_investments():
    result = parse([TOTALBANK])
    assert [r.amount for r in result.transactions] == [-30, 50]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert result.parse_metrics["printed_balance_checks"] == 3
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_totalbank_rejects_unbalanced_or_unparsed_rows():
    for text in [TOTALBANK.replace("R$ -30,00", "R$ -31,00"), TOTALBANK.replace("R$ -30,00", "???")]:
        with pytest.raises(PdfFinancialValidationError):
            parse([text])


BB_SPLIT = """Extrato de Conta Corrente
Lançamentos
Dia Lote Documento Histórico Valor
29/05/2025 Saldo Anterior 0,00 (+)
03/06/2025 Pix - Recebido
14397 12345 03/06 04:30 CLIENTE SINTETICO 100,00 (+)
03/06/2025 9903 BB Rende Fácil 100,00 (-)
Rende Facil
00/00/0000 14134 Saldo do dia 0,00 (+)
04/06/2025 Pix - Enviado
13105 60401 DESTINATARIO SINTETICO 30,00 (-)
04/06/2025 9903 BB Rende Fácil 30,00 (+)
Rende Facil
04/06/2025 S A L D O 0,00 (+)
Total Aplicações Financeiras 0,00
"""


def test_bb_split_rows_zero_date_controls_and_explicit_investment_signs():
    result = parse([BB_SPLIT])
    assert [r.amount for r in result.transactions] == [100, -100, -30, 30]
    assert [r.date for r in result.transactions] == ["2025-06-03"] * 2 + ["2025-06-04"] * 2
    assert result.parse_metrics["verified_opening_balance"] == 0
    assert result.parse_metrics["verified_closing_balance"] == 0
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert (1, 6) in result.canonical_transactions[0].source_line_refs  # Numeric continuation, beyond the dated title.


def test_bb_split_bad_sign_cannot_fall_back_to_generic():
    with pytest.raises(PdfFinancialValidationError):
        parse([BB_SPLIT.replace("100,00 (-)", "100,00 (+)")])


def test_bb_combined_controls_are_reported_without_cash_balance_claims():
    result = parse(["""Extrato de Conta Corrente
Data Origem Histórico Documento Lote Valor R$
03/08/2026 1234-5 Pix - Recebido 12345 14397 100,00 C
03/08/2026 1234-5 Saldo do dia 14397 500,00 C
Conta corrente + Rende Fácil
04/08/2026 1234-5 Pagamento de Boleto 12346 13105 30,00 D
bb.com.br - Central de Atendimento BB
"""])
    assert [r.amount for r in result.transactions] == [100, -30]
    assert all(r.running_balance is None for r in result.canonical_transactions)
    assert result.parse_metrics["balance_validation_status"] == "combined_cash_investment_not_comparable"
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
