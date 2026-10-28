"""Fictional controls for the third October 28 production audit."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.document_extraction_models import ExtractedWord
from app.application.errors import InvalidFileContentError
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct28_new_statement_failures import bb_duplicate_opening
from tests.test_recovery_sweep_ocr import extraction


def santander_available(*, missing_amount=False, debit=False):
    cells = [
        ("Santander Empresas EXTRATO CONSOLIDADO INTELIGENTE agosto/2026", .1, .02),
        ("Saldo Disponível de Conta Corrente em 31/07", .1, .08), ("0,76", .65, .08),
        ("(+) Total de Créditos", .1, .11), ("5.600,00", .65, .11),
        ("Saldo Disponível de Conta Corrente em 31/08", .1, .14), ("5.600,76", .65, .14),
        ("Limite de Cheque Empresa", .1, .17), ("1.000,00", .65, .17),
        ("Saldo Disponível Total em 31/08", .1, .20), ("6.600,76", .65, .20),
        ("Data", .075, .30), ("Descrição", .125, .30), ("Movimentos (R$)", .64, .30),
        ("Saldo (R$)", .834, .30), ("Créditos", .637, .313), ("Débitos", .717, .313),
        ("SALDO EM 31/07", .125, .35), ("0,76", .86, .35),
    ]
    for index, (day, amount, balance) in enumerate([
        ("21/08", "5.000,00", "5.000,76"), ("24/08", "200,00", "5.200,76"),
        ("26/08", "200,00", "5.400,76"), ("31/08", "200,00", "5.600,76"),
    ]):
        y = .4 + .06 * index
        cells += [(day, .075, y), ("PIX ENVIADO" if debit and index == 0 else "PIX RECEBIDO", .125, y),
                  (balance, .86, y)]
        if not (missing_amount and index == 0):
            cells.append((amount, .70 if debit and index == 0 else .62, y))
    cells += [("SALDO EM 31/08", .125, .68), ("5.600,76", .86, .68)]
    return extraction(cells)


def sicoob_page_break(*, blocked=False, unknown_star=False):
    first = [
        ("SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", .19, .03),
        ("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .19, .06),
        ("EXTRATO CONTA CORRENTE", .35, .09), ("CONTA: 12345-6 / EMPRESA FICTÍCIA", .19, .15),
        ("COOP.: 1234-0 / SICOOB TESTE", .19, .12),
        ("PERÍODO: 01/07/2026 - 31/07/2026", .19, .18),
        ("HISTÓRICO DE MOVIMENTAÇÃO", .25, .21),
        ("DATA", .19, .24), ("HISTÓRICO", .26, .24), ("VALOR", .75, .24),
        ("30/06", .19, .28), ("SALDO ANTERIOR", .26, .28), ("0,00C", .75, .28),
        ("01/07", .19, .80), ("PIX RECEBIDO", .26, .80), ("50,00C", .75, .80),
        ("DOC.: 1001", .26, .83),
        ("01/07", .19, .90), ("SALDO DO DIA", .26, .90), ("50,00C", .75, .90),
        ("20,00", .75, .94),
    ]
    second = [
        ("02/07", .19, .05), ("PIX EMIT.OUTRA IF", .26, .05), ("D", .775, .05), ("DOC.: 1002", .26, .08),
        ("02/07", .19, .30), ("SALDO DO DIA", .26, .30), ("30,00C", .75, .30),
        ("RESUMO", .45, .60), ("(+) SALDO EM CONTA:", .19, .64), ("30,00C", .75, .64),
    ]
    if blocked:
        second += [("02/07", .19, .15), ("PIX ENVIADO" if unknown_star else "DEP.CHEQUE BLOQ.1D", .26, .15),
                   ("200,00*", .75, .15), ("DOC.: 1003", .26, .18)]
    return grid_pdf([first, second])


def bb_page_break(*, extra_sign=False):
    first = [
        ("Cliente Conta atual Dt. balancete Dt. movimento Ag. origem", .1, .02),
        ("Histórico", .35, .1), ("Documento", .65, .1), ("Valor R$", .75, .1), ("Saldo", .86, .1),
        ("30/06/2026", .1, .2), ("Saldo Anterior", .35, .2), ("100,00 C", .86, .2),
        ("01/07/2026", .1, .85), ("821 Pix Recebido", .35, .85), ("1001", .65, .85),
        ("10,00 C", .75, .85), ("110,00 C", .86, .85), ("20,00", .75, .94),
    ]
    second = [
        ("02/07/2026", .1, .05), ("821 Pix Recebido", .35, .05), ("1002", .65, .05),
        ("C", .775, .05), ("130,00 C", .86, .05),
        ("31/07/2026", .1, .20), ("999 S A L D O", .35, .20), ("130,00 C", .86, .20),
    ]
    if extra_sign:
        second.append(("D", .79, .05))
    return grid_pdf([first, second])


def bb_overlapping_sign(*, overlap=True, confidence=45, closing="95,55 C", token="4,451 D"):
    source = bb_duplicate_opening(duplicate=False)
    page = source.pages[0]
    bad = next(line for line in page.lines if line.text == "50,00 C")
    words = [
        ExtractedWord("number", 1, 1, token.split()[0], confidence,
                      {"left": .75, "top": .3, "width": .055, "height": .01}, "PRINTED"),
        ExtractedWord("sign", 1, 2, "D", 99,
                      {"left": .80 if overlap else .81, "top": .3, "width": .01, "height": .01}, "PRINTED"),
    ]
    lines = [replace(line, text=token, word_ids=("number", "sign")) if line is bad
             else replace(line, text=closing) if line.text == "150,00 C" else line for line in page.lines]
    return replace(source, pages=[replace(page, lines=lines, words=words)])


def bradesco_reordered_period(*, mismatch=False):
    return grid_pdf([[
        ("Extrato Mensal / Por Período Bradesco net empresa", .06, .03),
        ("Extrato de: Ag: | CC:", .06, .07), ("01/12/2026", .40, .07), ("31/12/2026", .53, .07),
        ("1234 123456-7", .22, .07), ("| Entre", .36, .07), ("e", .51, .07),
        ("Data", .06, .12), ("Lançamento", .18, .12), ("Dcto.", .49, .12),
        ("Crédito (R$)", .62, .12), ("Débito (R$)", .73, .12), ("Saldo (R$)", .88, .12),
        ("SALDO ANTERIOR", .18, .20), ("100,00", .88, .20),
        ("16/12/2026", .06, .28), ("PIX RECEBIDO", .18, .28), ("1001", .49, .28),
        ("50,00", .64, .28), ("150,00", .88, .28),
        ("17/12/2026", .06, .34), ("PIX ENVIADO", .18, .34), ("1002", .49, .34),
        ("-20,00", .75, .34), ("130,00", .88, .34),
        ("Total", .18, .40), ("100,00" if mismatch else "50,00", .64, .40),
        ("50,00" if mismatch else "20,00", .75, .40), ("130,00", .88, .40),
        ("Folha 1/1", .88, .92),
    ]])


def bb_pending_deposit(*, unknown=False):
    source = bb_duplicate_opening(duplicate=False)
    pending = extraction([
        ("03/08/2026", .1, .35), ("144 PIX ENVIADO" if unknown else "911 Depósito bloquead.1d útil", .35, .35),
        ("1002", .65, .35), ("200,00 *", .75, .35),
    ]).pages[0].lines
    pending = [replace(line, id="pending-" + line.id, line_index=line.line_index+50) for line in pending]
    return replace(source, pages=[replace(source.pages[0], lines=[*source.pages[0].lines, *pending])])


def bb_trailing_mark(*, confidence=45, balance="150,00 C", sign=True, skew=False):
    source = bb_duplicate_opening(duplicate=False)
    lines = []
    for line in source.pages[0].lines:
        if line.text == "50,00 C":
            line = replace(line, text="50,00 C &" if sign else "50,00 &", confidence=confidence)
        if line.text == "150,00 C":
            line = replace(line, text=balance)
        if skew and line.bbox["top"] == .3:
            if line.bbox["left"] < .65:
                line = replace(line, bbox={**line.bbox, "top": .308})
            elif line.bbox["left"] == .86:
                line = replace(line, text=balance.removesuffix(" C"))
        lines.append(line)
    if skew:
        template = next(line for line in lines if line.bbox["left"] == .86 and line.bbox["top"] == .3)
        lines.append(replace(template, id="detached-C", text="C", bbox={**template.bbox, "left": .935, "width": .01}))
    return replace(source, pages=[replace(source.pages[0], lines=lines)])


def test_santander_available_summary_stays_in_cash_without_a_printed_zero_debit_total():
    source = santander_available()
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [5000, 200, 200, 200]
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["selected_parser"] == "layout_specific_santander_ocr_columns"
    assert result.source_extraction == source


@pytest.mark.parametrize("kwargs", [{"missing_amount": True}, {"debit": True}])
def test_santander_absent_summary_control_cannot_restore_or_hide_a_debit(kwargs):
    with pytest.raises(InvalidFileContentError) as error:
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(santander_available(**kwargs))
    assert error.value._parse_observability["layout_inference_name"].startswith("santander_")


@pytest.mark.parametrize("blocked", [False, True])
def test_sicoob_amount_before_page_break_keeps_both_physical_pages(blocked):
    result = pdf_parser.parse_pdf_transactions(sicoob_page_break(blocked=blocked))
    assert [t.amount for t in result.transactions] == [50, -20]
    assert result.parse_metrics["verified_closing_balance"] == 30
    assert {page for page, _ in result.canonical_transactions[1].source_line_refs} == {1, 2}
    assert result.parse_metrics.get("non_cash_entries_excluded", 0) == int(blocked)


def test_unknown_starred_sicoob_movement_is_not_a_blocked_cheque():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(sicoob_page_break(blocked=True, unknown_star=True))


def test_bb_amount_before_page_break_keeps_both_physical_pages():
    result = pdf_parser.parse_pdf_transactions(bb_page_break())
    assert [t.amount for t in result.transactions] == [10, 20]
    assert [t.date for t in result.transactions] == ["2026-07-01", "2026-07-02"]
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert {page for page, _ in result.canonical_transactions[1].source_line_refs} == {1, 2}


def test_conflicting_signs_cannot_reassign_bb_page_fragment():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(bb_page_break(extra_sign=True))


def test_bb_duplicated_sign_stroke_needs_word_overlap_and_printed_balance():
    source = bb_overlapping_sign()
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [-4.45]
    assert "ocr_sign_stroke_overlap_confirmed" in result.canonical_transactions[0].warnings
    assert "manual_review_recommended" in result.canonical_transactions[0].warnings
    assert result.source_extraction == source


@pytest.mark.parametrize("kwargs", [
    {"overlap": False}, {"confidence": 99}, {"closing": "95,54 C"}, {"token": "4,452 D"}, {"token": "4,451 D &"},
])
def test_bb_extra_decimal_digit_without_independent_stroke_proof_remains_ambiguous(kwargs):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_overlapping_sign(**kwargs))


@pytest.mark.parametrize("skew", [False, True])
def test_bb_complete_signed_amount_with_low_confidence_trailing_mark_needs_its_balance(skew):
    source = bb_trailing_mark(skew=skew)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [50]
    assert "ocr_trailing_mark_confirmed" in result.canonical_transactions[0].warnings
    assert result.canonical_transactions[0].running_balance == 150
    assert result.source_extraction == source


@pytest.mark.parametrize("kwargs", [{"confidence": 99}, {"balance": "149,99 C"}, {"sign": False}])
def test_bb_unconfirmed_trailing_mark_is_not_silently_removed(kwargs):
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_trailing_mark(**kwargs))


def test_bb_ocr_cash_columns_distinguish_pending_deposits_from_available_cash():
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_pending_deposit())
    assert [t.amount for t in result.transactions] == [50]
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert result.parse_metrics["verified_closing_balance"] == 150
    assert "pending_deposits_excluded" in result.canonical_transactions[0].warnings


def test_bb_starred_cash_movement_is_not_a_pending_deposit():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_pending_deposit(unknown=True))


@pytest.mark.parametrize("mismatch", [False, True])
def test_bradesco_reads_printed_header_order_and_preserves_real_total_divergences(mismatch):
    result = pdf_parser.parse_pdf_transactions(bradesco_reordered_period(mismatch=mismatch))
    assert [t.amount for t in result.transactions] == [50, -20]
    assert result.parse_metrics.get("document_period_unavailable", 0) == 0
    assert result.parse_metrics.get("statement_validation_failed", 0) == int(mismatch)
    if mismatch:
        assert "printed_totals_differ_from_extracted_movements" in result.canonical_transactions[0].warnings
        assert result.parse_metrics["financial_controls_validated"] == 0
