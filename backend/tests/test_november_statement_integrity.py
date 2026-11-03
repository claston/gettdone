"""Synthetic regressions for native statements captured on November 2–3."""
from decimal import Decimal

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError

HEADER = "Sicredi\nExtrato de conta corrente\nMomento de emissão Período das movimentações\nCooperativa Conta\n"


def inherited_pages():
    return [
        HEADER + "Saldo em conta R$ 999,00\nMovimentações de 01/09/2026 a 30/09/2026\n"
        "Data Descrição Valor\nSaldo anterior R$ 10,00\n"
        "30/09/2026 PAGAMENTO PIX FORNECEDOR - R$ 3,00\n"
        "RECEBIMENTO PIX CLIENTE R$ 20,00\nNOME COMPLEMENTAR DO CLIENTE",
        "PAGAMENTO PIX FORNECEDOR - R$ 4,00\n15/09/2026 CHEQUE ESPECIAL-JUROS - R$ 2,00",
        "01/09/2026 CHEQUE ESPECIAL-IOF - R$ 1,00\nSaldo no dia R$ 20,00\nFim desse extrato.",
    ]


def split_pages():
    return [
        HEADER + "Saldo atual\nR$ 999,00\nSaldo bloqueado\nR$ 0,00\n"
        "Movimentações\nde\n01/08/2026\na\n31/08/2026\nData\nDescrição\nValor\n"
        "Saldo Anterior\n-R$ 10,00\n03/08/2026\nCHEQUE ESPECIAL-IOF\n- R$ 0,50\n"
        "03/08/2026\nTRANSFERÊNCIA RECEBIDA\n+ R$ 20,00\nSaldo do dia\n03/08/2026\nR$ 9,50",
        "Movimentações de 01/08/2026 a 31/08/2026\nData Descrição Valor\n"
        "06/08/2026\nPAGAMENTO PIX REPETIDO\n- R$ 2,00\n"
        "06/08/2026\nPAGAMENTO PIX REPETIDO\n- R$ 2,00\nSaldo do dia\n06/08/2026\nR$ 5,50\n"
        "Cheque especial em sua situação acumulada até hoje, 14/09/2026\n"
        "Limite contratado\nR$ 200,00\nTaxa de cheque especial - mensal\n7,51%\nFim desse extrato.",
    ]


def parse(monkeypatch, pages):
    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda _: pages)
    monkeypatch.setattr(pdf_parser, "_read_layout_native_pdf_page_texts", lambda _: pages)
    return pdf_parser.parse_pdf_transactions(b"%PDF synthetic")


def test_sicredi_inherits_group_dates_across_pages_and_keeps_wrapped_descriptions(monkeypatch):
    result = parse(monkeypatch, inherited_pages())
    assert [r.amount for r in result.transactions] == [-3, 20, -4, -2, -1]
    assert [r.date for r in result.transactions] == ["2026-09-30"] * 3 + ["2026-09-15", "2026-09-01"]
    assert "NOME COMPLEMENTAR DO CLIENTE" in result.transactions[1].description
    assert [r.source_page for r in result.canonical_transactions] == [1, 1, 2, 2, 3]
    assert result.parse_metrics["verified_opening_balance"] == 10
    assert result.parse_metrics["verified_closing_balance"] == 20
    assert result.parse_metrics["statement_row_coverage_rate"] == 1


def test_sicredi_split_fields_exclude_positions_and_rates_but_keep_legitimate_charges_and_repeats(monkeypatch):
    result = parse(monkeypatch, split_pages())
    assert [r.amount for r in result.transactions] == [-.5, 20, -2, -2]
    assert sum(Decimal(str(r.amount)) for r in result.transactions) == Decimal("15.50")
    assert result.parse_metrics["verified_opening_balance"] == -10
    assert result.parse_metrics["verified_closing_balance"] == 5.5
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["printed_balance_checks"] >= 2


def test_sicredi_uses_spatial_native_text_when_content_stream_moves_footer_ahead_of_transactions(monkeypatch):
    pages = inherited_pages()
    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda _: [pages[0], pages[1], "Fim desse extrato.\n" + pages[2]])
    monkeypatch.setattr(pdf_parser, "_read_layout_native_pdf_page_texts", lambda _: pages)
    result = pdf_parser.parse_pdf_transactions(b"%PDF synthetic")
    assert len(result.transactions) == 5
    assert result.parse_metrics["textract_attempted"] == 0


@pytest.mark.parametrize("before,after", [("30/09/2026 PAGAMENTO", "32/09/2026 PAGAMENTO"),
                                         ("- R$ 4,00", "- R$ ilegível")])
def test_sicredi_does_not_silently_omit_unreadable_movements(monkeypatch, before, after):
    pages = [p.replace(before, after) for p in inherited_pages()]
    with pytest.raises(PdfFinancialValidationError):
        parse(monkeypatch, pages)


def test_sicredi_wrong_printed_control_keeps_readable_movements_with_explicit_review(monkeypatch):
    pages = [p.replace("Saldo no dia R$ 20,00", "Saldo no dia R$ 21,00") for p in inherited_pages()]
    result = parse(monkeypatch, pages)
    assert len(result.transactions) == 5
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("native", [True, False])
def test_sicredi_optional_table_headers_do_not_drop_readable_native_or_ocr_rows(native):
    from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext

    pages = [p.replace("Data Descrição Valor\n", "") for p in inherited_pages()]
    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages, context=LayoutSpecificParseContext(is_native_text=native))
    assert len(result.transactions) == 5
    assert result.parse_metrics["financial_controls_validated"] == 1


@pytest.mark.parametrize("sign,expected", [("C", 6005.01), ("D", -6005.01)])
def test_opening_balance_uses_balance_column_instead_of_zero_movement_column(sign, expected):
    from app.application.default_conversion_pipeline import _resolve_opening_balance

    text = f"Data Mov. Nr. Doc. Histórico Valor Saldo\n000000 SALDO ANTERIOR 0,00 6.005,01 {sign}"
    assert _resolve_opening_balance([], extracted_text=text, layout_name="caixa_extrato_por_periodo_web_v1") == expected


def test_combined_cash_and_investment_controls_cannot_seed_invented_account_balances():
    result = pdf_parser._parse_pdf_transactions_from_page_texts(["""ItaúEmpresas
Extrato de 01/10/2026 até 31/10/2026
Data Lançamento Ag./Origem Valor (R$) Saldo (R$)
30/09 SALDO ANTERIOR 1,00
01/10 PIX ENVIADO TESTE 9001 -50,00
01/10 REDE VISA TESTE 9002 100,00
01/10 SDO CTA/APL AUTOMATICAS 9.000,00
31/10 S A L D O 1,00"""])
    assert [r.amount for r in result.transactions] == [-50, 100]
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def position_text(position="01/10/2026"):
    return f"""SICOOB
PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB – SISBR SALDO RENDA FIXA
Data de Emissão: 02/11/2026
DATA POSIÇÃO: {position}
Valor aplicado: R$ 1.000,00
Valor bruto: R$ 1.100,00
Valor líquido: R$ 1.080,00
Rendimentos: R$ 100,00
IOF previsto: R$ 0,00
IR previsto: R$ 20,00
Valor bloqueado: R$ 10,00
Valor disponível para resgate: R$ 900,00
*O valor disponível para resgate considera carência e valores bloqueados."""


def test_sicoob_positions_keep_financial_meaning_and_as_of_date_without_inventing_movements(monkeypatch):
    result = parse(monkeypatch, [position_text()])
    assert result.transactions == []
    document = result.financial_document
    assert document["document_type"] == "investment_statement"
    assert len(document["observations"]) == 8
    assert all(o["dates"] == {"as_of": "2026-10-01"} for o in document["observations"])
    assert document["export"]["available_formats"] == ["csv", "xlsx"]
    assert all(v["status"] == "passed" for v in document["validations"])


def test_sicoob_multiple_positions_preserve_independent_dates_and_source_pages():
    from app.application.parsers.pdf.financial_reports import parse_financial_report_pages

    document = parse_financial_report_pages([position_text("30/09/2026"), position_text("29/10/2026")])
    assert document is not None
    assert len(document["observations"]) == 16
    assert {(o["dates"]["as_of"], o["source_refs"][0]["page"]) for o in document["observations"]} == {
        ("2026-09-30", 1), ("2026-10-29", 2)}


@pytest.mark.parametrize("replacement", ["", "DATA POSIÇÃO: ilegível"])
def test_sicoob_cannot_substitute_emission_date_for_missing_position_date(monkeypatch, replacement):
    with pytest.raises(PdfFinancialValidationError):
        parse(monkeypatch, [position_text().replace("DATA POSIÇÃO: 01/10/2026", replacement)])


@pytest.mark.parametrize("continuous", [True, False])
def test_sicredi_independent_monthly_blocks_preserve_all_movements_and_physical_pages(monkeypatch, continuous):
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    september = inherited_pages()
    if continuous:
        september = [page.replace("Saldo anterior R$ 10,00", "Saldo anterior R$ 5,50")
                     .replace("Saldo no dia R$ 20,00", "Saldo no dia R$ 15,50") for page in september]
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in [*split_pages(), *september]))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert len(result.transactions) == 9
    assert {row.source_page for row in result.canonical_transactions} == {1, 2, 3, 4, 5}
    if continuous:
        assert result.parse_metrics["verified_opening_balance"] == -10
        assert result.parse_metrics["verified_closing_balance"] == 15.5
    else:
        assert result.parse_metrics["balance_bounds_unavailable"] == 1
        assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_sicoob_arithmetic_mismatch_preserves_positions_for_review():
    from app.application.financial_event_projection import parse_financial_pdf_pages

    result = parse_financial_pdf_pages([position_text().replace("Valor líquido: R$ 1.080,00", "Valor líquido: R$ 1.081,00")])
    assert result.transactions == []
    assert len(result.financial_document["observations"]) == 8
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])


def caixa_text():
    return """CAIXA
EXTRATO POR PERIODO
Período: 01/08/2026 a 31/08/2026
Data Mov. Nr. Doc. Histórico Valor Saldo
000000 SALDO ANTERIOR 0,00 100,00 C
03/08/2026 000001 CR PIX QRD 10,00 C 110,00 C
04/08/2026 000002 DEB PIX 5,00 D 105,00 C"""


@pytest.mark.parametrize("opening,failures", [("100,00", 0), ("101,00", 1)])
def test_value_balance_columns_validate_opening_against_first_movement(monkeypatch, opening, failures):
    result = parse(monkeypatch, [caixa_text().replace("100,00", opening)])
    assert len(result.transactions) == 2
    assert result.parse_metrics["balance_consistency_checked"] == 2
    assert result.parse_metrics["balance_consistency_failed"] == failures
    if failures:
        assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("amount", ["− R$ 3,00", "R$ -3,00"])
def test_sicredi_signs_survive_native_and_ocr_currency_spacing(monkeypatch, amount):
    result = parse(monkeypatch, [page.replace("- R$ 3,00", amount) for page in inherited_pages()])
    assert [row.amount for row in result.transactions] == [-3, 20, -4, -2, -1]


@pytest.mark.parametrize("old,new", [("Saldo Anterior\n-R$ 10,00", "Saldo Anterior"),
                                    ("Saldo do dia\n03/08/2026\nR$ 9,50", "Saldo do dia\n03/08/2026"),
                                    ("Saldo do dia\n03/08/2026\nR$ 9,50", "Saldo do dia\n03/08/2026\nR$ ilegível")])
def test_unreadable_optional_control_cannot_consume_the_next_legible_movement(monkeypatch, old, new):
    result = parse(monkeypatch, [page.replace(old, new) for page in split_pages()])
    assert [row.amount for row in result.transactions] == [-.5, 20, -2, -2]
    assert [row.date for row in result.transactions] == ["2026-08-03"] * 2 + ["2026-08-06"] * 2
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
