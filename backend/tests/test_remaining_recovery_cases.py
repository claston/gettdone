"""Independent fictional examples of remaining recovery failures."""
import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError, UnsupportedDocumentContentError
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_recovery_sweep_ocr import extraction
from tests.test_sicoob_channel_pending import parse_pages
from tests.test_sicoob_statement_context import sisbr_pending_cells


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("separator", [" ", " – ", " a "])
def test_sisbr_explicit_dates_do_not_require_a_hyphen(representation, separator):
    pages = sisbr_pending_cells()
    pages = [[(text.replace(" - ", separator) if "PERÍODO" in text.upper() or "PERIODO" in text.upper() else text, x, y)
              for text, x, y in page] for page in pages]
    result = parse_pages(pages, representation)
    assert [row.amount for row in result.transactions] == [50, 200, -20]
    assert result.parse_metrics["financial_controls_validated"] == 1


def test_marketplace_payment_uses_printed_currency_sign_not_payment_keyword():
    cells = [("MAREE INSTITUICAO DE PAGAMENTO LTDA", .05, .02),
             ("PERÍODO EXTRATO 01/10/2026 a 31/10/2026", .05, .05),
             ("Data", .05, .1), ("Hora", .2, .1), ("Tipo de Transação", .35, .1), ("Valor da Transação", .8, .1)]
    for i, (label, amount) in enumerate([("Pagamento Marketplace", "R$58,49"),
                                       ("Desconto Marketplace", "-R$18,46"), ("Pagamento Marketplace", "R$58,49")]):
        y = .2 + i*.1
        cells += [("02-10-2026", .05, y), ("14:50:34", .2, y), (label, .35, y), (amount, .8, y)]
    result = pdf_parser.parse_pdf_transactions(grid_pdf([cells]))
    assert [row.amount for row in result.transactions] == [58.49, -18.46, 58.49]
    assert all(row.source_regions for row in result.canonical_transactions)


def cresol_cells():
    cells = [("CRESOL", .1, .02), ("EXTRATO CONSOLIDADO DE CONTA CORRENTE", .2, .04),
             ("Período: 01/08/2026 a 30/09/2026", .1, .07),
             ("Data Movimento", .08, .12), ("Lançamento", .35, .12),
             ("Identificação", .65, .12), ("Valor", .87, .12)]
    for i, (date, label, value) in enumerate([
        ("01/08/2026", "SALDO ANTERIOR", "100,00 C"),
        ("01/08/2026", "PIX CREDITO", "50,00 C"),
        ("02/08/2026", "SALDO ANTERIOR", "150,00 C"),
        ("02/08/2026", "PIX DEBITO", "20,00 D"),
        ("03/08/2026", "SALDO ANTERIOR", "130,00 C"),
        ("03/08/2026", "PIX CREDITO", "10,00 C"),
    ]):
        y = .2 + i*.05
        cells += [(date, .09, y), (label, .2, y), (value, .87, y)]
    return cells + [("LANÇAMENTOS FUTUROS/PENDENTES", .1, .6),
                    ("08/10/2026", .09, .65), ("APLICAÇÃO PARA CONTA INVESTIMENTO", .2, .65), ("500,00 D", .87, .65),
                    ("Página: 10 / 23", .8, .95)]


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_daily_openings_are_controls_and_future_entries_are_not_cash(representation):
    result = parse_pages([cresol_cells()], representation)
    assert [row.amount for row in result.transactions] == [50, -20, 10]
    assert result.parse_metrics["partial_statement"] == 1
    assert result.parse_metrics["scheduled_entries_excluded"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert all(row.source_regions for row in result.canonical_transactions)


def test_cresol_split_footer_pagination_is_not_complete():
    assert validate_statement_completeness(["CRESOL\nEXTRATO CONSOLIDADO DE CONTA CORRENTE\n10 / 23"])
    assert validate_statement_completeness(["EXTRATO CONSOLIDADO DE CONTA CORRENTE\npágina 10 23"])


def test_corrupt_embedded_cash_value_cannot_be_replaced_by_its_balance():
    from app.application.native_text_quality import assess_native_text_quality
    from tests.test_embedded_ocr_quality import layered_pdf

    text = ("Sicredi\nDados referentes ao período 01/05/2026 a 31/05/2026\nData Descrição Valor Saldo\n"
            "04/05/2026 PAGAMENTO PIX -220,0400.566,05")
    assert assess_native_text_quality(layered_pdf(text), [text]).requires_visual_extraction
    healthy = text.replace("-220,0400.566,05", "-220,00 400.566,05")
    assert not assess_native_text_quality(layered_pdf(healthy), [healthy]).requires_visual_extraction


def test_cancelled_sale_preserves_status_and_does_not_become_settled_cash():
    from tests.test_final_recovery_sales import sales_report

    result = pdf_parser._parse_pdf_transactions_from_page_texts([sales_report().replace("Aprovada", "Cancelada")])
    cancelled = next(event for event in result.financial_document["events"] if event["reference"] == "SALE-001")
    assert cancelled["status"] == "cancelled"
    assert cancelled["cash_effect"] is None
    assert cancelled["amounts"] == {"gross": "100.00", "fee": "5.00", "net": "95.00"}
    assert all(item["status"] == "passed" for item in result.financial_document["validations"])


@pytest.mark.parametrize("status,failed", [("Cancelada", False), ("Aprovada", True)])
def test_zero_net_cancellation_is_not_a_false_gross_minus_fee_mismatch(status, failed):
    from tests.test_final_recovery_sales import sales_report

    report = sales_report().replace("Aprovada", status).replace("R$ 5,00  R$ 95,00", "R$ 0,00  R$ 0,00")
    report = report.replace("R$ 145,00", "R$ 50,00")
    result = pdf_parser._parse_pdf_transactions_from_page_texts([report])
    assert any(v["status"] == "failed" for v in result.financial_document["validations"]) is failed
    assert all(event["cash_effect"] is None for event in result.financial_document["events"])


def grouped_daily_cells(account="12345-6"):
    return [("Agência: 0001 Conta: "+account, .6, .04), ("Período: 01/10/2026 a 31/10/2026", .05, .08),
            ("Saldo final do período", .05, .13), ("Saldo inicial do período", .3, .13),
            ("Total de entradas", .55, .13), ("Total de saídas", .8, .13),
            ("R$ 180,00", .05, .16), ("R$ 100,00", .3, .16), ("R$ 100,00", .55, .16), ("R$ 20,00", .8, .16),
            ("Data", .05, .2), ("Lançamentos", .2, .2), ("Valores em R$", .7, .2), ("Saldo em R$", .85, .2),
            ("01/10/2026", .05, .25), ("Pix recebido Conta: 99999-1", .2, .3), ("50,00", .7, .3),
            ("Pix recebido Conta: 88888-2", .2, .4), ("50,00", .7, .4),
            ("Pix enviado", .2, .5), ("- 20,00", .7, .5), ("Saldo do dia", .05, .6), ("180,00", .85, .6)]


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_grouped_daily_uses_physical_header_and_preserves_duplicate_credits(representation):
    cells = grouped_daily_cells()
    # Content stream emits the owner after the movements.
    result = parse_pages([cells[1:]+cells[:1]], representation)
    assert [t.amount for t in result.transactions] == [50, 50, -20]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 180
    assert all(t.source_regions for t in result.canonical_transactions)


def test_grouped_daily_still_rejects_different_owner_accounts():
    with pytest.raises(UnsupportedDocumentContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf([grouped_daily_cells(), grouped_daily_cells("54321-0")]))


def test_grouped_daily_does_not_reuse_previous_date_when_next_date_is_unreadable():
    cells = grouped_daily_cells()+[("02/10/202?", .05, .65), ("Pix recebido", .2, .7), ("50,00", .7, .7)]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(grid_pdf([cells]))


def test_sicredi_merged_date_history_and_later_account_summary_keep_cash_roles():
    from app.application.parsers.pdf.native_table_source import read_native_table_source
    from app.application.parsers.pdf.sicredi_visual_cash import parse_sicredi_visual_cash

    cells = [("Sicredi Cooperativa: 1234", .2, .02), ("Dados referentes ao período 01/10/2026 a 31/10/2026", .2, .05),
        ("Data", .2, .1), ("Descrição", .4, .1), ("Documento", .6, .1), ("Valor (R$)", .7, .1), ("Saldo (R$)", .8, .1),
        ("SALDO", .27, .18), ("100,00", .8, .18),
        ("01/10/2026", .2, .22), ("RECEBIMENTO PIX", .27, .22), ("123", .6, .22), ("50,00", .7, .22), ("150,00", .8, .22),
        ("01/10/2026", .21, .26), ("PAGAMENTO PIX", .28, .26), ("456", .6, .26), ("-20,00", .7, .26), ("130,00", .8, .26),
        ("Saldo da Conta", .2, .4), ("Valores atualizados em 01/11/2026", .2, .45),
        ("Saldo atual (disponível em conta)", .2, .5), ("R$ 999,00", .8, .5)]
    statement = parse_sicredi_visual_cash(read_native_table_source(grid_pdf([cells])))
    assert [row.transaction.amount for row in statement.rows] == [50, -20]
    assert statement.opening == 100 and statement.closing == 130
    assert all(row.source_regions for row in statement.rows)


def bb_optional_reference_cells(token="50,00 C", skew=False):
    cells = [("Cliente Conta atual Dt. balancete Dt. movimento Ag. origem", .05, .02),
             ("Histórico", .35, .1), ("Valor R$", .75, .1), ("Saldo", .86, .1)]
    for y, date, label, amount, balance in [(.2, "30/09/2026", "000 Saldo Anterior", None, "100,00 C"),
        (.3, "01/10/2026", "821 Pix Recebido", token, "150,00 C"),
        (.4, "31/10/2026", "999 Saldo", None, "150,00 C")]:
        cells += [(date, .08, y), (label, .35, y-.005 if skew else y)]
        if amount:
            cells.append((amount, .75, y-.013 if skew else y))
        cells.append((balance, .86, y-.015 if skew else y))
    return cells


@pytest.mark.parametrize("skew", [False, True])
def test_bb_document_reference_is_optional_and_skew_keeps_amount_role(skew):
    result = parse_pages([bb_optional_reference_cells(skew=skew)], "ocr")
    assert [t.amount for t in result.transactions] == [50]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.canonical_transactions[0].external_reference_id is None


@pytest.mark.parametrize("token", ["50,001 C", "5?0,00 C", "50 1 C"])
def test_bb_unreadable_digits_are_never_replaced_by_balance_arithmetic(token):
    with pytest.raises(PdfFinancialValidationError):
        parse_pages([bb_optional_reference_cells(token)], "ocr")


def test_cresol_preserves_first_opening_and_does_not_validate_partial_global_balance():
    from app.application.parsers.pdf.consolidated_cash_columns import parse_consolidated_cash

    statement = parse_consolidated_cash(extraction(cresol_cells()), "cresol")
    assert statement.opening == 100
    assert statement.partial and statement.balance_bounds_unavailable


@pytest.mark.parametrize("token", ["50,001 C", "5?0,00 C", "50,00", "50,00 €"])
def test_cresol_unknown_history_and_unreadable_values_require_evidence(token):
    cells = [("HISTÓRICO NÃO IDENTIFICADO" if t == "PIX CREDITO" else token if t == "50,00 C" else t,x,y)
             for t,x,y in cresol_cells()]
    with pytest.raises(PdfFinancialValidationError):
        parse_pages([cells], "ocr")


@pytest.mark.parametrize("date", ["02/08/202?", ""])
def test_cresol_does_not_silently_omit_a_row_without_readable_date(date):
    cells = [(date if text == "02/08/2026" else text, x, y) for text, x, y in cresol_cells()]
    cells = [(text, x, y) for text, x, y in cells if text]
    with pytest.raises(PdfFinancialValidationError):
        parse_pages([cells], "ocr")
