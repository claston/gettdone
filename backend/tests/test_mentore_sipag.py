"""Fictional cash groups and sales installments keep their printed financial roles."""
import pytest

from app.application.errors import InvalidFileContentError
from app.application.financial_document_export import assert_financial_export, write_financial_artifacts
from tests.test_sicoob_channel_pending import parse_pages


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def mentore_cells(month="10", *, account="12345-6", mismatch=False):
    return [("FINANCEIRO / EXTRATO - Extrato Consolidado", .04, .03),
        (f"Conta: {account} Agência: 0001", .04, .07),
        (f"Saldo Inicial no dia 01/{month}/2026:", .04, .11), ("R$ 0,00", .33, .11),
        (f"Saldo Final no dia 30/{month}/2026:", .04, .14), ("R$ 0,00", .33, .14),
        ("HISTÓRICO", .04, .2), ("TIPO", .4, .2), ("VALOR", .6, .2), ("SALDO", .8, .2),
        (f"15/{month}/2026", .04, .25), ("Saldo final do dia", .4, .25), ("R$ 0,00", .8, .25),
        ("000002 | Outros", .04, .3), ("TEF", .4, .3), ("- R$ 50,00", .6, .3), ("R$ 0,00", .8, .3),
        ("Transferência recebida", .04, .35), ("TED", .4, .35), ("R$ 50,00", .6, .35), ("R$ 50,00", .8, .35),
        (f"06/{month}/2026", .04, .45), ("Saldo final do dia", .4, .45), ("R$ 0,00", .8, .45),
        ("Pagamento de salário", .04, .5), ("TEF", .4, .5), ("- R$ 100,00", .6, .5), ("R$ 0,00", .8, .5),
        ("Recebimento de PIX", .04, .55), ("PIX", .4, .55), ("R$ 100,00", .6, .55),
        ("R$ 99,00" if mismatch else "R$ 100,00", .8, .55),
        (f"06/{month}/2026", .04, .62), ("Saldo inicial do dia", .4, .62), ("R$ 0,00", .8, .62),
        ("Mêntore Instituição de Pagamento S.A.", .4, .9), ("Página 1 de 1", .04, .95)]


def sales_cells(*, total="60,00", last_page=True):
    xs = [.04, .14, .24, .30, .36, .46, .56, .66, .76, .89]
    labels = ["Data da transação", "Nº da transação", "Bandeira", "Parcela", "Tipo cartão",
              "Número do terminal", "Indicador", "Nº ordem de", "Status", "Valor parcela bruto"]
    cells = [("RELATÓRIO DE VENDAS", .04, .03), ("Estabelecimento(s): LOJA-FICTICIA", .04, .07)]
    cells += [(label, x, .12) for label, x in zip(labels, xs, strict=True)]
    cells += [("Crédito/Débito", .56, .14), ("pagamento", .66, .14)]
    for i in range(3):
        y = .25 + i*.12
        values = ["07/10/2026 12:30:45", "000123", "Visa", str(i+1), "Cartão teste", "000987", "C", "-", "Transação", "R$ 20,00"]
        cells += [(value, x, y) for value, x in zip(values, xs, strict=True)]
        cells.append(("Processada", .76, y+.025))
    if last_page:
        cells += [("Total", .04, .8), ("R$ "+total, .89, .8)]
    cells += [("Emitido em: 04/11/2026 às 12:39", .04, .94), ("Página 1 de 1", .85, .94)]
    return cells


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_cash_groups_keep_four_signed_movements_and_zero_balance(representation):
    result = parse_pages([mentore_cells()], representation)
    assert [t.amount for t in result.transactions] == [100, -100, 50, -50]
    assert [t.date for t in result.transactions] == ["2026-10-06"]*2+["2026-10-15"]*2
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert result.parse_metrics["verified_opening_balance"] == result.parse_metrics["verified_closing_balance"] == 0
    assert all(t.source_regions for t in result.canonical_transactions)


def test_cash_real_balance_mismatch_remains_reviewable():
    result = parse_pages([mentore_cells(mismatch=True)], "native")
    assert len(result.transactions) == 4
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("old,new", [("R$ 50,00", "R$ 5?,00"), ("15/10/2026", "15/10/202?"),
                                    ("- R$ 50,00", "")])
def test_cash_unreadable_amount_or_date_cannot_be_omitted(old, new):
    cells = [(new if t == old else t,x,y) for t,x,y in mentore_cells()]
    with pytest.raises(InvalidFileContentError):
        parse_pages([[(t,x,y) for t,x,y in cells if t]], "ocr")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sales_installments_are_distinct_non_cash_events_and_exportable(representation, tmp_path):
    result = parse_pages([sales_cells()], representation)
    document = result.financial_document
    assert document and document["document_type"] == "acquirer_sales_report"
    assert len(document["events"]) == 3
    assert [e["attributes"]["installment"] for e in document["events"]] == ["1", "2", "3"]
    assert all(e["cash_effect"] is None and e["amounts"] == {"gross": "20.00"} for e in document["events"])
    assert all(e["reference"] == "000123" for e in document["events"])
    assert all(t.type == "non_cash" for t in result.transactions)
    assert all(v["status"] == "passed" for v in document["validations"])
    assert all(t.source_regions for t in result.canonical_transactions)
    write_financial_artifacts(tmp_path, document)
    assert "000123" in (tmp_path/"converted.csv").read_text(encoding="utf-8-sig")
    assert (tmp_path/"converted.xlsx").exists()
    with pytest.raises(InvalidFileContentError):
        assert_financial_export(document, "ofx")


def test_sales_inconsistent_total_preserves_values_and_requires_review():
    result = parse_pages([sales_cells(total="61,00")], "native")
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])
    assert sum(t.amount for t in result.transactions) == 60


@pytest.mark.parametrize("old,new", [("R$ 20,00", "R$ 2?,00"), ("07/10/2026 12:30:45", "07/10/202? 12:30:45")])
def test_sales_unreadable_row_never_falls_back_to_banking(old,new):
    cells=[(new if t==old else t,x,y) for t,x,y in sales_cells()]
    with pytest.raises(InvalidFileContentError):
        parse_pages([cells], "ocr")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_cash_continuation_inherits_date_and_keeps_its_original_page(representation):
    original = mentore_cells()
    first = [(t.replace("1 de 1", "1 de 2"), x, y) for t,x,y in original if y <= .5 or y >= .9]
    second = [(t, x, y-.4) for t,x,y in original if .55 <= y <= .62]
    second += [("Página 2 de 2", .04, .95)]
    result = parse_pages([first, second], representation)
    assert [t.amount for t in result.transactions] == [100, -100, 50, -50]
    row = result.canonical_transactions[0]
    assert {region.page for region in row.source_regions} == {1, 2}
    assert result.parse_metrics["financial_controls_validated"] == 1


@pytest.mark.parametrize("representation", ["native", "ocr"])
@pytest.mark.parametrize("month,contiguous", [("11", True), ("12", False)])
def test_cash_period_blocks_keep_movements_and_review_only_unconfirmed_continuity(representation, month, contiguous):
    first = [(t.replace("30/10/2026", "31/10/2026"), x, y) for t,x,y in mentore_cells()]
    result = parse_pages([first, mentore_cells(month)], representation)
    assert len(result.transactions) == 8
    assert {t.source_page for t in result.canonical_transactions} == {1, 2}
    assert bool(result.parse_metrics["financial_controls_validated"]) == contiguous
    assert not result.parse_metrics.get("partial_statement")
    if not contiguous:
        assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_cash_missing_type_cannot_silently_omit_a_movement():
    cells = [(t,x,y) for t,x,y in mentore_cells() if not (t == "TEF" and y == .3)]
    with pytest.raises(InvalidFileContentError):
        parse_pages([cells], "ocr")


def test_cash_changed_account_is_not_combined_into_one_balance():
    with pytest.raises(InvalidFileContentError):
        parse_pages([mentore_cells(), mentore_cells(account="98765-4")], "ocr")


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_sales_missing_continuation_header_preserves_same_id_installments(representation):
    first = [(t.replace("1 de 1", "1 de 2"),x,y) for t,x,y in sales_cells(last_page=False)]
    second = [(t.replace("1 de 1", "2 de 2"),x,y) for t,x,y in sales_cells(total="120,00") if y > .14]
    result = parse_pages([first, second], representation)
    assert len(result.financial_document["events"]) == 6
    assert sum(t.amount for t in result.transactions) == 120
    assert all(v["status"] == "passed" for v in result.financial_document["validations"])
    assert {t.source_page for t in result.canonical_transactions} == {1, 2}


def test_sales_unconfirmed_total_and_missing_pages_require_review():
    cells = [(t.replace("1 de 1", "1 de 2"),x,y) for t,x,y in sales_cells(last_page=False)]
    result = parse_pages([cells], "native")
    assert len(result.transactions) == 3
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert {v["code"] for v in result.financial_document["validations"]} == {
        "sales_total_unconfirmed", "sales_page_coverage"}


def test_sales_printed_status_and_card_modality_never_invent_settlement():
    cells = [("Cancelada" if t == "Processada" else "D" if t == "C" else t,x,y) for t,x,y in sales_cells()]
    result = parse_pages([cells], "ocr")
    assert all(t.type == "non_cash" for t in result.transactions)
    assert all(e["attributes"]["printed_status"] == "Transação Cancelada" and
        e["attributes"]["card_indicator"] == "D" and "settlement" not in e["dates"]
        for e in result.financial_document["events"])


@pytest.mark.parametrize("representation", ["native", "ocr"])
def test_payment_institution_in_history_cannot_truncate_the_ledger(representation):
    cells = [("Instituição de Pagamento" if t == "Transferência recebida" else t, x, y)
             for t,x,y in mentore_cells()]
    result = parse_pages([cells], representation)
    assert [t.amount for t in result.transactions] == [100, -100, 50, -50]
    assert result.parse_metrics["financial_controls_validated"] == 1


def test_sales_merged_terminal_indicator_header_uses_original_word_positions():
    cells = [(t, .536 if x == .56 else x, y) for t,x,y in sales_cells()]
    result = parse_pages([cells], "native")
    assert len(result.financial_document["events"]) == 3
    assert all(e["attributes"]["terminal"] == "000987" and e["attributes"]["card_indicator"] == "C"
               for e in result.financial_document["events"])
