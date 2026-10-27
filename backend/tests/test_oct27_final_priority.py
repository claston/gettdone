"""Fictional last-priority ledgers preserve signs, controls and real truncation."""
import pytest

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_pdf_parser_sicoob_examples import SICOOB_EXAMPLE_CASES


def sicoob_split_signs(*, missing_sign=False, mismatch=False, cross_page=True):
    first = [("SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", .19, .03),
        ("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .19, .06),
        ("EXTRATO CONTA CORRENTE", .35, .09), ("COOP.: 1234-0 / SICOOB TESTE", .19, .12),
        ("CONTA: 12345-6 / EMPRESA FICTÍCIA", .19, .15),
        ("PERÍODO: 01/07/2026 - 31/07/2026", .19, .18), ("HISTÓRICO DE MOVIMENTAÇÃO", .25, .21),
        ("DATA", .19, .24), ("HISTÓRICO", .26, .24), ("VALOR", .75, .24),
        ("30/06", .19, .28), ("SALDO ANTERIOR", .26, .28), ("100,00C", .75, .28),
        ("30/06", .19, .32), ("SALDO BLOQ.ANTERIOR", .26, .32), ("0,00*", .75, .32),
        ("01/07", .19, .37), ("CRÉDITO TESTE", .26, .37), ("50,00C", .75, .37),
        ("CLIENTE FICTÍCIO", .26, .395), ("DOC.: 1001", .26, .42),
        ("01/07", .19, .47), ("AJUSTE TESTE", .26, .47), ("200,00", .75, .464),
        ("PAGAMENTO TESTE", .26, .50)]
    tail = [("D", .79, .04), ("INSTITUIÇÃO DE PAG", .26, .07), ("38.000.000 0001-00", .26, .095),
        ("DOC.: 1002", .26, .12), ("01/07", .19, .17), ("SALDO DO DIA", .26, .17),
        ("49,00D" if mismatch else "50,00D", .75, .17),
        ("RESUMO", .45, .24), ("(+) SALDO EM CONTA:", .19, .28), ("50,00D", .75, .28),
        ("(=) SALDO DISPONÍVEL:", .19, .33), ("950,00C", .75, .33)]
    if missing_sign:
        tail = [cell for cell in tail if cell[0] != "D"]
    pages = [first, tail] if cross_page else [first + [(s, x, y+.51) for s, x, y in tail]]
    return grid_pdf(pages)


def pagbank_daily(*, mismatch=False, missing_day=False, unreadable=False, multiple_first_day=False):
    first = [("290 - PagSeguro Internet S/A", .60, .04), ("Agência 0001 Conta 123456-7", .60, .08),
        ("Extrato da conta", .05, .12), ("Emitido em: 04/10/2026 às 09:00", .05, .16),
        ("Periodo: 01/10/2026 a 03/10/2026", .05, .20),
        ("Data", .06, .25), ("Descrição", .20, .25), ("Valor", .88, .25),
        ("01/10/2026", .06, .31), ("Vendas - Disponível", .20, .31), ("R$ 20,00", .82, .31),
        ("01/10/2026", .06, .37), ("Saldo do dia", .20, .37), ("R$ 120,00", .82, .37),
        ("02/10/2026", .06, .43), ("Pix enviado", .20, .43), ("-R$ 1?,00" if unreadable else "-R$ 10,00", .82, .43),
        ("CLIENTE FICTÍCIO", .20, .455), ("02/10/2026", .06, .51), ("Pix enviado", .20, .51),
        ("-R$ 10,00", .82, .51), ("CLIENTE FICTÍCIO", .20, .535)]
    second = [("Data", .06, .05), ("Descrição", .20, .05), ("Valor", .88, .05),
        ("02/10/2026", .06, .11), ("Saldo do dia", .20, .11), ("R$ 101,00" if mismatch else "R$ 100,00", .82, .11),
        ("03/10/2026", .06, .17), ("Vendas - Disponível", .20, .17), ("R$ 5,00", .82, .17),
        ("03/10/2026", .06, .23), ("Saldo do dia", .20, .23), ("R$ 105,00", .82, .23)]
    if missing_day:
        second = [cell for cell in second if cell[2] != .11]
    if multiple_first_day:
        first = [("R$ 130,00" if text == "R$ 120,00" else text, x, y) for text, x, y in first]
        first += [("01/10/2026", .06, .34), ("Vendas - Disponível", .20, .34), ("R$ 10,00", .82, .34)]
        second = [("R$ 110,00" if text == "R$ 100,00" else "R$ 115,00" if text == "R$ 105,00" else text, x, y)
                  for text, x, y in second]
    return grid_pdf([first, second])


def santander_limited(*, mismatch=False):
    cells = [("Aplicativo Santander Empresas", .58, .03), ("Agência: 1234 Conta: 123456-7", .55, .07),
        ("Períodos: 01/10/2026 a 31/10/2026", .05, .11), ("Saldo disponível para uso: R$ 999,00", .60, .14),
        ("Data", .06, .20), ("Histórico", .18, .20), ("Documento", .51, .20),
        ("Valor (R$)", .71, .20), ("Saldo (R$)", .87, .20),
        ("03/10/2026 Pix Recebido 11111111111", .05, .27), ("5,00", .72, .27),
        ("106,00" if mismatch else "105,00", .87, .27),
        ("02/10/2026 Pix Recebido 22222222222", .05, .34), ("12345678", .51, .34),
        ("10,00", .72, .34), ("100,00", .87, .34),
        ("Saldo de ContaMax", .05, .42), ("0,00", .87, .42),
        ("Você alcançou o número máximo de consultas.", .05, .48),
        ("para ver todos os lançamentos.", .05, .51),
        ("Posição em: 01/11/2026", .05, .60), ("Saldo de Conta Corrente", .05, .65), ("999,00", .87, .65)]
    return grid_pdf([cells])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("cross_page", [False, True])
@pytest.mark.parametrize("mismatch", [False, True])
def test_sicoob_separate_signs_and_history_are_kept_across_page_boundaries(cross_page, mismatch):
    raw = sicoob_split_signs(cross_page=cross_page, mismatch=mismatch)
    parsed = pdf_parser.parse_pdf_transactions(raw)
    assert [r.amount for r in parsed.transactions] == [50, -200]
    assert [r.external_reference_id for r in parsed.canonical_transactions] == ["1001", "1002"]
    assert "INSTITUIÇÃO DE PAG 38.000.000 0001-00" in parsed.transactions[1].description
    assert parsed.parse_metrics.get("partial_statement", 0) == 0
    assert parsed.parse_metrics.get("statement_validation_failed", 0) == int(mismatch)
    assert parsed.parse_metrics.get("printed_balance_checks", 0) >= 2
    assert all(r.source_regions for r in parsed.canonical_transactions)
    controls = {(p.page_number, line.line_index) for p in parsed.source_extraction.pages
                for line in p.lines if line.text == "SALDO DO DIA"}
    assert controls.issubset(set(parsed.canonical_transactions[-1].source_line_refs))
    if cross_page:
        assert {p for p, _ in parsed.canonical_transactions[1].source_line_refs} == {1, 2}
    if not mismatch:
        analysis = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=raw,
            analysis_id="an_final_sicoob").analysis_data
        assert (analysis.opening_balance, analysis.closing_balance, analysis.bank_code) == (100, -50, "756")


def test_sicoob_cannot_infer_a_missing_sign_from_description_or_balance():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(sicoob_split_signs(missing_sign=True))


def test_sicoob_native_flowing_text_with_inline_signs_retains_its_existing_reader():
    case = SICOOB_EXAMPLE_CASES["conta_historico_2021_janeiro"]
    text = case["text"].replace("COOP.: CONTA:", "COOP.: 1234 CONTA: 12345-6")
    raw = generate_native_text_pdf((text.splitlines(),))
    parsed = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in parsed.transactions] == case["amounts"]
    assert parsed.layout.layout_name == case["profile"]


@pytest.mark.parametrize("mismatch", [False, True])
def test_pagbank_checks_daily_controls_and_preserves_repeated_debits(mismatch):
    raw = pagbank_daily(mismatch=mismatch)
    parsed = pdf_parser.parse_pdf_transactions(raw)
    assert [r.amount for r in parsed.transactions] == [20, -10, -10, 5]
    assert parsed.transactions[1].description == parsed.transactions[2].description == "Pix enviado CLIENTE FICTÍCIO"
    assert parsed.parse_metrics.get("printed_balance_checks", 0) >= 2
    assert parsed.parse_metrics.get("statement_validation_failed", 0) == int(mismatch)
    assert "derived_opening" in parsed.parse_metrics["parser_selection_reason"]
    assert all(r.source_regions for r in parsed.canonical_transactions)


@pytest.mark.parametrize("options", [{"missing_day": True}, {"unreadable": True}])
def test_pagbank_cannot_skip_amounts_or_a_day_without_its_closing_control(options):
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(pagbank_daily(**options))


def test_pagbank_derived_opening_subtracts_every_movement_before_first_daily_balance():
    raw = pagbank_daily(multiple_first_day=True)
    analysis = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=raw,
        analysis_id="an_derived_pagbank").analysis_data
    assert (analysis.opening_balance, analysis.closing_balance) == (100, 115)
    assert analysis.pdf_processing_metrics["financial_controls_validated"] == 0


@pytest.mark.parametrize("counterparty", ["INSTITUIÇÃO DE PAG\n38.000.000 0001-00", "CLIENTE PAG. 38", "PAG\n38.000.000 0001-00"])
def test_counterparty_is_not_a_page_counter(counterparty):
    assert not validate_statement_completeness([f"SICOOB\n01/07 PIX\n{counterparty}\nDOC.: Pix"])


@pytest.mark.parametrize("page", ["PAG.: 00038", "PÁGINA: 38", "Página 38 de 40"])
def test_actual_missing_page_counter_still_requires_review(page):
    assert validate_statement_completeness([f"SICOOB\n{page}\n01/07 PIX TESTE"])


def test_santander_query_limit_still_requires_review_with_all_physical_pages_present():
    pages = ["Santander\nVocê alcançou o número máximo de consultas.\npara ver todos os lançamentos."]
    assert validate_statement_completeness(pages)


@pytest.mark.parametrize("mismatch", [False, True])
def test_santander_actual_document_column_is_separate_from_counterparty_tax_ids(mismatch):
    raw = santander_limited(mismatch=mismatch)
    parsed = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in parsed.transactions] == [5, 10]
    assert [row.external_reference_id for row in parsed.canonical_transactions] == [None, "12345678"]
    assert [row.running_balance for row in parsed.canonical_transactions] == [106 if mismatch else 105, 100]
    assert parsed.parse_metrics["partial_statement"] == 1
    assert parsed.parse_metrics["statement_validation_failed"] == int(mismatch)
    assert parsed.parse_metrics["printed_balance_checks"] == 1
    analysis = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=raw,
        analysis_id="an_limited_santander").analysis_data
    assert analysis.opening_balance is None
    assert analysis.closing_balance == (106 if mismatch else 105)
