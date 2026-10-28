"""Fictional regressions for the remaining October 28 extraction failures."""

import re
from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.parsers.pdf.statement_blocks import statement_blocks
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct20_new_native_statements import bb_page
from tests.test_oct27_mercado_grid import mercado_grid


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def test_mercado_merged_history_and_reference_keep_original_words_and_amounts(monkeypatch):
    from app.application.parsers.pdf import followup_native_tables

    original = followup_native_tables.read_native_table_source

    def merged(raw, **kwargs):
        source = original(raw, **kwargs)
        pages = []
        for page in source.pages:
            history = next(line for line in page.lines if line.text == "para CLIENTE")
            reference = next(line for line in page.lines if line.text == "100000001" and
                             abs(line.bbox["top"] - history.bbox["top"]) < .001)
            right = reference.bbox["left"] + reference.bbox["width"]
            combined = replace(history, text=history.text+" "+reference.text,
                bbox={**history.bbox, "width": right-history.bbox["left"]},
                word_ids=(*history.word_ids, *reference.word_ids))
            pages.append(replace(page, lines=[combined if line.id == history.id else line
                                             for line in page.lines if line.id != reference.id]))
        return replace(source, pages=pages)

    monkeypatch.setattr(followup_native_tables, "read_native_table_source", merged)
    result = pdf_parser.parse_pdf_transactions(mercado_grid())
    assert [row.amount for row in result.transactions] == [10, -5]
    assert result.transactions[0].description == "Reembolso Envio cancelado para CLIENTE FICTÍCIO"
    assert [row.external_reference_id for row in result.canonical_transactions] == ["100000001"] * 2
    assert all(row.source_regions for row in result.canonical_transactions)


def bb_zero_with_blocked(*, blocked="500,00 *"):
    return "\n".join([
        "Extrato de Conta Corrente", "Cliente: EMPRESA FICTÍCIA", "Dia Lote Documento Histórico Valor",
        "30/06/2026 Saldo Anterior 100,00 (+)",
        f"01/07/2026 99020 1000 Depósito bloquead. {blocked}",
        "01/07/2026 14397 1001 Pix Recebido 50,00 (+)",
        "00/00/0000 Saldo do dia 150,00 (+)", "31/07/2026 S A L D O 150,00 (+)",
    ])


def test_bb_blocked_deposit_is_reported_but_never_added_to_available_cash():
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((bb_zero_with_blocked().splitlines(),)))
    assert [row.amount for row in result.transactions] == [50]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 150
    assert result.parse_metrics["non_cash_entries_excluded"] == 1
    assert "pending_deposits_excluded" in result.canonical_transactions[0].warnings


def test_bb_unreadable_movement_is_not_dismissed_as_a_blocked_deposit():
    text = bb_zero_with_blocked().replace("Depósito bloquead. 500,00 *", "Pix enviado 500,00 *")
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(generate_native_text_pdf((text.splitlines(),)))


def test_bb_prior_day_cash_control_is_a_printed_opening_even_with_another_label():
    text = bb_page().replace("30/06/2026 Saldo Anterior", "30/06/2026 Saldo do dia")
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((text.splitlines(),)))
    assert [row.amount for row in result.transactions] == [-194.57, 1100]
    assert result.parse_metrics["verified_opening_balance"] == 1000
    assert result.parse_metrics["verified_closing_balance"] == 1905.43
    assert result.parse_metrics["balance_consistency_failed"] == 0


@pytest.mark.parametrize("label", ["PAGAMENTO DE BOLETO", "13105 70201 Pagamento de Boleto", "Pix - Recebido - Agrupado"])
def test_bb_label_above_date_and_value_preserves_its_explicit_history(label):
    text = bb_page().replace("02/07/2026 13105 70201 Pagamento de Boleto 194,57 (-)",
                             label+"\n02/07/2026 194,57 (-)")
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((text.splitlines(),)))
    assert [row.amount for row in result.transactions] == [-194.57, 1100]
    expected = label.removeprefix("13105 70201 ")
    assert result.transactions[0].description.upper().startswith(expected.upper())
    assert result.canonical_transactions[0].external_reference_id == ("70201" if label.startswith("13105") else None)
    assert len(result.canonical_transactions[0].source_line_refs) >= 2


def test_bb_date_and_value_without_history_are_still_ambiguous():
    text = bb_page().replace("02/07/2026 13105 70201 Pagamento de Boleto 194,57 (-)", "02/07/2026 194,57 (-)")
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(generate_native_text_pdf((text.splitlines(),)))


@pytest.mark.parametrize("changed_amount", [False, True])
def test_bb_alternative_native_view_must_preserve_all_printed_financial_fields(monkeypatch, changed_amount):
    from app.application.parsers.pdf import native_horizontal_text

    text = bb_page()
    horizontal = text.replace("02/07/2026 13105 70201 Pagamento de Boleto 194,57 (-)", "02/07/2026 194,57 (-)")
    if changed_amount:
        horizontal = horizontal.replace("194,57", "194,58")
    monkeypatch.setattr(native_horizontal_text, "read_horizontal_native_pages", lambda *a, **kw: [horizontal])
    raw = generate_native_text_pdf((text.splitlines(),))
    if changed_amount:
        with pytest.raises(InvalidFileContentError):
            pdf_parser.parse_pdf_transactions(raw)
    else:
        result = pdf_parser.parse_pdf_transactions(raw)
        assert [row.amount for row in result.transactions] == [-194.57, 1100]
        assert result.parse_metrics["balance_consistency_failed"] == 0


def sicoob_descending():
    return grid_pdf([[
        ("SICOOB SISTEMA DE COOPERATIVAS DE CRÉDITO DO BRASIL", .19, .03),
        ("PLATAFORMA DE SERVIÇOS FINANCEIROS DO SICOOB - SISBR", .19, .06),
        ("EXTRATO CONTA CORRENTE", .35, .09), ("COOP.: 1234-0 / SICOOB TESTE", .19, .12),
        ("CONTA: 12345-6 / EMPRESA FICTÍCIA", .19, .15),
        ("PERÍODO: 01/07/2026 - 31/07/2026", .19, .18), ("HISTÓRICO DE MOVIMENTAÇÃO", .25, .21),
        ("DATA", .19, .24), ("HISTÓRICO", .26, .24), ("VALOR", .75, .24),
        ("02/07", .19, .28), ("SALDO DO DIA", .26, .28), ("50,00D", .75, .28),
        ("02/07", .19, .32), ("PAGAMENTO TESTE", .26, .32), ("200,00D", .75, .32),
        ("FORNECEDOR FICTÍCIO", .26, .345), ("DOC.: 1002", .26, .37),
        ("01/07", .19, .41), ("SALDO DO DIA", .26, .41), ("150,00C", .75, .41),
        ("01/07", .19, .45), ("CRÉDITO TESTE", .26, .45), ("50,00C", .75, .45), ("DOC.: 1001", .26, .48),
        ("30/06", .19, .53), ("SALDO ANTERIOR", .26, .53), ("100,00C", .75, .53),
        ("RESUMO", .45, .60), ("(+) SALDO EM CONTA:", .19, .64), ("50,00D", .75, .64),
    ]])


@pytest.mark.parametrize("merged", [False, True])
def test_sicoob_descending_ledger_validates_all_printed_cash_controls(monkeypatch, merged):
    if merged:
        from app.application.parsers.pdf import followup_native_tables

        original = followup_native_tables.read_native_table_source
        def group_fragments(raw, **kwargs):
            source = original(raw, **kwargs)
            pages = []
            for page in source.pages:
                consumed, lines = set(), []
                for line in page.lines:
                    if line.id in consumed:
                        continue
                    candidate = line.text == "DATA" or re.fullmatch(r"\d{2}/\d{2}", line.text)
                    next_cell = next((other for other in page.lines if other.bbox["left"] > line.bbox["left"]
                        and other.bbox["left"] < .5 and abs(other.bbox["top"]-line.bbox["top"]) < .001), None) if candidate else None
                    if next_cell:
                        consumed.add(next_cell.id)
                        right = next_cell.bbox["left"]+next_cell.bbox["width"]
                        line = replace(line, text=line.text+" "+next_cell.text,
                            bbox={**line.bbox, "width": right-line.bbox["left"]}, word_ids=(*line.word_ids, *next_cell.word_ids))
                    lines.append(line)
                pages.append(replace(page, lines=lines))
            return replace(source, pages=pages)
        monkeypatch.setattr(followup_native_tables, "read_native_table_source", group_fragments)
    result = pdf_parser.parse_pdf_transactions(sicoob_descending())
    assert [(row.date, row.amount) for row in result.transactions] == [("2026-07-01", 50), ("2026-07-02", -200)]
    assert [row.external_reference_id for row in result.canonical_transactions] == ["1001", "1002"]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == -50
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions for row in result.canonical_transactions)


@pytest.mark.parametrize("value", ["200,00", "200,?0D"])
def test_sicoob_missing_sign_or_unreadable_digits_are_never_repaired_from_a_balance(monkeypatch, value):
    from app.application.parsers.pdf import followup_native_tables

    original = followup_native_tables.read_native_table_source
    def unreadable(raw, **kwargs):
        source = original(raw, **kwargs)
        return replace(source, pages=[replace(page, lines=[replace(line, text=value) if line.text == "200,00D" else line
            for line in page.lines]) for page in source.pages])
    monkeypatch.setattr(followup_native_tables, "read_native_table_source", unreadable)
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(sicoob_descending())


def test_investment_opening_after_cash_movements_is_not_a_new_statement_block():
    pages = ["Bradesco Extrato Mensal\n31/08/2026 SALDO ANTERIOR 100,00\n01/09/2026 PIX 50,00",
             "Bradesco Extrato Mensal\n02/09/2026 PIX ENVIADO 20,00\nTOTAL 130,00\n"
             "Saldos Invest Fácil\n01/09/2026 SALDO ANTERIOR 999,00"]
    assert statement_blocks(pages) == []
