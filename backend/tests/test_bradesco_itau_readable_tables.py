"""Fictional regressions for account identity and continued Itaú cash tables."""
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError, UnsupportedDocumentContentError
from app.application.pdf_document_segmentation import _statement_identity, segment_pdf_pages
from tests.fixtures.recovery_acceptance import grid_pdf


def bradesco_header(month="08", account="0123456-7"):
    # Native content-stream order puts the period immediately after CC, before
    # its agency/account values. Printed account columns remain unambiguous.
    return (f"Extrato Mensal / Por Período Bradesco net empresa\n"
            f"Extrato de: Ag: | CC: 01/{month}/2026 28/{month}/2026564 {account} | Entre e\n"
            f"Agência | Conta Total Disponível (R$) Total (R$)\n00564 | {account} 100,00 100,00\n"
            "Data Lançamento Dcto. Crédito (R$) Débito (R$) Saldo (R$)\n"
            f"01/{month}/2026 PIX RECEBIDO TESTE 20,00 120,00")


def test_bradesco_period_dates_are_not_accounts_in_reordered_header():
    pages = [bradesco_header(), bradesco_header("09")]
    assert _statement_identity(pages[0]) == "00564|0123456-7|"
    assert _statement_identity(pages[1]) == _statement_identity(pages[0])
    assert segment_pdf_pages(pages).page_texts == tuple(pages)


def test_bradesco_real_different_accounts_still_require_separate_scope():
    with pytest.raises(UnsupportedDocumentContentError):
        segment_pdf_pages([bradesco_header(), bradesco_header("09", "0987654-3")])


@pytest.mark.parametrize("value", ["01/08/2026", "01/08/2026564"])
def test_date_after_account_label_is_never_used_as_partial_account(value):
    assert _statement_identity(f"Extrato de Conta Corrente\nConta: {value}\nData Descrição Valor Saldo") is None


def itau_pages(*, ascending=True, month="10", opening=100, repeat_header=False, bad_later=False):
    previous = "30/09/2026" if month == "10" else "31/10/2026"
    records = [
        (previous, "SALDO ANTERIOR", None, f"{opening},00"),
        (f"01/{month}/2026", "PIX RECEBIDO", "20,00", None),
        (f"01/{month}/2026", "PIX RECEBIDO", "20,00", None),
        (f"01/{month}/2026", "PIX ENVIADO", "-5,00", None),
        (f"01/{month}/2026", "SALDO MOVIMENTAÇÃO CONTA", None, f"{opening+35},00"),
        (f"02/{month}/2026", "PAGAMENTO TESTE", "-15,00", None),
        (f"02/{month}/2026", "SALDO MOVIMENTAÇÃO CONTA", None, f"{opening+20},00"),
    ]
    if not ascending:
        records.reverse()
    header = [("Data", .05, .14), ("Lançamentos", .18, .14), ("Razão Social", .38, .14),
              ("CNPJ/CPF", .60, .14), ("Valor (R$)", .79, .14), ("Saldo (R$)", .92, .14)]
    pages = [[("Itaú Empresas Extrato de Conta Corrente", .05, .03), ("Agência 1234 Conta 12345-6", .60, .06),
              (f"Lançamentos do período: 01/{month}/2026 até {'31' if month == '10' else '30'}/{month}/2026", .05, .10), *header]]
    for i, (date, history, amount, balance) in enumerate(records):
        if i == 4:
            pages.append(header.copy() if repeat_header else [])
        y = .20 + (i if i < 4 else i-4)*.06
        cells = [(date, .05, y), (history, .18, y)]
        if amount is not None:
            cells += [("XX,00" if bad_later and i == 5 else amount, .79, y),
                      ("EMPRESA FICTÍCIA", .38, y), ("000.000.000-00", .60, y)]
        if balance is not None:
            cells.append((balance, .92, y))
        pages[-1].extend(cells)
        if i == 3:
            pages[-1].append(("PRIMEIRA PARTE", .38, y+.02))
    # A genuine summary page has no movement header, and carries an unrelated
    # current balance. It cannot supply the period's account cash controls.
    pages.append([("Itaú Empresas", .05, .03), ("Agência 1234 Conta 12345-6", .60, .06),
                  ("Saldo total Limite da conta", .05, .09), ("R$ 999,00", .05, .12),
                  ("Saldo da conta corrente", .05, .20), ("Descrição Valor (R$) Saldo (R$)", .05, .25),
                  ("SALDO TOTAL DISPONÍVEL", .05, .30), ("999,00", .92, .30)])
    for index, page in enumerate(pages, 1):
        page.append((f"Página {index}/3", .88, .95))
    return pages


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("ascending", [True, False])
@pytest.mark.parametrize("repeat_header", [True, False])
def test_itau_both_printed_orders_keep_all_pages_duplicates_and_period_cash(ascending, repeat_header):
    result = pdf_parser.parse_pdf_transactions(grid_pdf(itau_pages(ascending=ascending, repeat_header=repeat_header)))
    assert [r.amount for r in result.transactions] == ([20, 20, -5, -15] if ascending else [-15, -5, 20, 20])
    assert {r.source_page for r in result.canonical_transactions} == {1, 2}
    assert all(r.source_regions and r.source_line_refs for r in result.canonical_transactions)
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 120
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_itau_unreadable_later_page_cannot_be_reported_as_complete():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(itau_pages(bad_later=True)))


def test_itau_later_unreadable_date_and_financial_row_cannot_disappear():
    pages = itau_pages()
    pages[1] = [(text.replace("02/10/2026", "02/10/20X6"), x, y) for text, x, y in pages[1]]
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(pages))


def test_itau_changed_account_on_headerless_continuation_is_rejected():
    pages = itau_pages()
    pages[1].insert(0, ("Extrato de Conta Corrente Agência 1234 Conta 98765-4", .05, .06))
    with pytest.raises(UnsupportedDocumentContentError):
        pdf_parser.parse_pdf_transactions(grid_pdf(pages))


def test_itau_repeated_months_and_page_counters_keep_independent_controls():
    first = grid_pdf(itau_pages())
    second = grid_pdf(itau_pages(month="11", opening=120))
    writer = PdfWriter()
    for raw in (first, second):
        for page in PdfReader(BytesIO(raw)).pages:
            writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    result = pdf_parser.parse_pdf_transactions(output.getvalue())
    assert [r.amount for r in result.transactions] == [20, 20, -5, -15]*2
    assert {r.source_page for r in result.canonical_transactions} == {1, 2, 4, 5}
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 140


def test_bradesco_reordered_period_headers_keep_both_months_and_require_gap_review():
    pages = []
    for month, opening, previous in (("10", 100, "30/09/2026"), ("12", 999, "30/11/2026")):
        pages.append([
            ("Extrato Mensal / Por Período Bradesco net empresa", .05, .03),
            ("Extrato de: Ag: | CC:", .05, .08), (f"01/{month}/2026", .44, .08),
            (f"31/{month}/2026", .61, .08), ("1234", .23, .08), ("123456-7", .29, .08),
            ("Entre", .39, .08), ("e", .58, .08),
            ("Agência | Conta Total Disponível (R$) Total (R$)", .05, .12),
            (f"01234 | 123456-7 {opening},00 {opening},00", .05, .15),
            ("Data", .05, .20), ("Lançamento", .18, .20), ("Dcto.", .49, .20),
            ("Crédito (R$)", .62, .20), ("Débito (R$)", .73, .20), ("Saldo (R$)", .88, .20),
            (previous, .05, .25), ("SALDO ANTERIOR", .18, .25), (f"{opening},00", .88, .25),
            (f"02/{month}/2026", .05, .30), ("PIX RECEBIDO", .18, .30), ("1001", .49, .30),
            ("20,00", .64, .30), (f"{opening+20},00", .88, .30),
            (f"02/{month}/2026", .05, .35), ("PIX RECEBIDO", .18, .35), ("1001", .49, .35),
            ("20,00", .64, .35), (f"{opening+40},00", .88, .35),
            ("Total", .18, .40), ("40,00", .64, .40), ("0,00", .75, .40), (f"{opening+40},00", .88, .40),
            ("Folha 1/1", .88, .92)])
    result = pdf_parser.parse_pdf_transactions(grid_pdf(pages))
    assert [row.amount for row in result.transactions] == [20]*4
    assert [row.date for row in result.transactions] == ["2026-10-02"]*2 + ["2026-12-02"]*2
    assert [row.source_page for row in result.canonical_transactions] == [1, 1, 2, 2]
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation_reason"] == "statement_blocks_not_contiguous"
    assert "verified_opening_balance" not in result.parse_metrics


def test_itau_ruled_cells_keep_centered_histories_and_a_row_split_across_pages():
    pages = itau_pages()
    pages[0] = pages[0][:9] + [
        ("30/09/2026", .05, .20), ("SALDO ANTERIOR", .18, .20), ("100,00", .92, .20),
        ("01/10/2026", .05, .30), ("PIX RECEBIDO", .18, .30), ("20,00", .79, .30),
        ("PARTE", .38, .28), ("CONTINUA", .38, .32),
        ("01/10/2026", .05, .40), ("PIX RECEBIDO", .18, .40), ("20,00", .79, .40),
        ("01/10/2026", .05, .50), ("SALDO MOVIMENTAÇÃO CONTA", .18, .50), ("140,00", .92, .50),
        ("EMPRESA", .38, .65)]
    pages[1] = [
        ("02/10/2026", .05, .22), ("PAGAMENTO TESTE", .18, .22), ("-15,00", .79, .22), ("FICTÍCIA", .38, .22),
        ("02/10/2026", .05, .40), ("SALDO MOVIMENTAÇÃO CONTA", .18, .40), ("125,00", .92, .40)]
    writer = PdfWriter(clone_from=BytesIO(grid_pdf(pages)))
    for page, cuts in zip(writer.pages, [[.16, .24, .26, .34, .36, .44, .46, .60, .80], [.30, .45], []], strict=True):
        stream = DecodedStreamObject()
        stream.set_data("\n".join(f"0.5 w 50 {(1-y)*800} m 960 {(1-y)*800} l S" for y in cuts).encode("ascii"))
        page[NameObject("/Contents")] = ArrayObject([page.raw_get("/Contents"), writer._add_object(stream)])
    output = BytesIO()
    writer.write(output)
    result = pdf_parser.parse_pdf_transactions(output.getvalue())
    assert [row.amount for row in result.transactions] == [20, 20, -15]
    assert result.transactions[0].description == "PIX RECEBIDO PARTE CONTINUA"
    assert result.transactions[-1].description == "PAGAMENTO TESTE EMPRESA FICTÍCIA"
    assert {region.page for region in result.canonical_transactions[-1].source_regions} == {1, 2}
    assert {page for page, _ in result.canonical_transactions[-1].source_line_refs} == {1, 2}
    assert result.parse_metrics["verified_closing_balance"] == 125
    assert result.parse_metrics["financial_controls_validated"] == 1
