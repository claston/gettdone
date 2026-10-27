"""Fictional account exports reproduce the next recovery batch's boundaries."""
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError
from tests.fixtures.recovery_acceptance import grid_pdf


def xp_web(*, bad_amount=False, different_dates=True, future=True, scale=1, mismatch=False):
    cells = [("XP INVESTIMENTOS CORRETORA DE CÂMBIO", .05, .03), ("Extrato da conta", .70, .06),
        ("Data da consulta: 01/11/2026 08:00", .60, .09), ("Conta: 123456", .75, .12),
        ("De: 01/10/2026 Até: 31/10/2026", .65, .15), ("Saldo disponível: R$ 999,00", .05, .18),
        ("Projeções Futuras", .05, .21), ("R$ 2.000,00", .84, .21),
        ("Saldo total projetado:", .05, .24), ("R$ 2.000,00", .84, .24),
        ("Liq", .05, .28), ("Mov", .20, .28), ("Histórico", .36, .28),
        ("Valor", .70, .28), ("Saldo", .84, .28),
        ("05/10/2026", .05, .34), ("04/10/2026" if different_dates else "05/10/2026", .20, .34),
        ("TED APLICAÇÃO", .36, .34), ("FUNDO FICTÍCIO", .36, .365),
        ("-R$ 3?,00" if bad_amount else "-R$ 30,00", .70, .34), ("R$ 71,00" if mismatch else "R$ 70,00", .84, .34),
        ("02/10/2026", .05, .41), ("01/10/2026", .20, .41),
        ("RECEBIMENTO", .36, .41), ("CLIENTE TESTE", .36, .435), ("R$ 100,00", .70, .41), ("R$ 100,00", .84, .41),
        ("Lançamentos futuros", .05, .50), ("Liq", .05, .53), ("Mov", .20, .53),
        ("Histórico", .36, .53), ("Valor", .70, .53), ("Saldo", .84, .53),
        ("Extrato simples para conferência, sujeito a atualizações.", .05, .68), ("https://www.xpi.com.br/", .05, .74)]
    if future:
        cells += [("20/11/2026", .05, .59), ("19/11/2026", .20, .59), ("PREVISÃO TESTE", .36, .59),
            ("-R$ 999,00", .70, .59), ("R$ 0,00", .84, .59)]
    else:
        cells += [("Não há lançamentos futuros", .05, .59)]
    return grid_pdf([cells], scale)


def itau_complete(*, mismatch=False):
    cells = [("Itaú Empresas", .05, .03), ("Agência 1234 Conta 12345-6", .60, .06),
        ("Lançamentos do período: 01/10/2026 até 31/10/2026", .05, .10),
        ("Data", .05, .14), ("Lançamentos", .18, .14), ("Razão Social", .38, .14),
        ("CNPJ/CPF", .60, .14), ("Valor (R$)", .79, .14), ("Saldo (R$)", .92, .14)]
    records = [
        ("03/10/2026", "SALDO TOTAL DISPONÍVEL DIA", None, "20,00"),
        ("03/10/2026", "SALDO MOVIMENTAÇÃO CONTA", None, "21,00" if mismatch else "20,00"),
        ("03/10/2026", "PIX ENVIADO", "-5,00", None),
        ("03/10/2026", "RES APLIC AUT MAIS", "15,00", None),
        ("02/10/2026", "SALDO TOTAL DISPONÍVEL DIA", None, "25,00"),
        ("02/10/2026", "SALDO MOVIMENTAÇÃO CONTA", None, "10,00"),
        ("02/10/2026", "SALDO APLIC. AUT.", None, "15,00"),
        ("02/10/2026", "APL APLIC AUT MAIS", "-15,00", None),
        ("02/10/2026", "PIX ENVIADO", "-5,00", None),
        ("02/10/2026", "PIX RECEBIDO", "20,00", None),
        ("30/09/2026", "SALDO ANTERIOR", None, "10,00")]
    for i, (date, history, amount, balance) in enumerate(records):
        y = .19 + i * .045
        cells += [(date, .05, y), (history, .18, y)]
        if amount is not None:
            cells.append((amount, .79, y))
        if balance is not None:
            cells.append((balance, .92, y))
        if i == 2:
            cells += [("CLIENTE FICTÍCIO", .38, y), ("SOBRENOME", .38, y+.02), ("000.000.000-00", .60, y)]
    cells += [("Saldo da conta corrente", .05, .73), ("Descrição", .05, .77),
        ("SALDO DISPONÍVEL SEM INVESTIMENTOS AUTOMÁTICOS", .05, .81), ("20,00", .92, .81),
        ("Lançamentos futuros do período: 01/11/2026 até 30/11/2026", .05, .87),
        ("Você não possui lançamentos futuros", .05, .91)]
    return grid_pdf([cells])


def descending_cards(*, bad_tag=False, orphan=False, mismatch=False, scale=1):
    cells = [("Extrato por Período", .05, .03), ("Ordenar", .80, .06),
        ("03 de Setembro de 2026, Quinta-feira", .04, .10),
        ("Pagamento", .20, .15), ("-R$ 5,00", .72, .15), ("CLIENTE TESTE", .20, .175),
        ("02SET" if bad_tag else "03SET", .83, .18),
        ("Saldo do dia", .08, .23), ("R$ 106,00" if mismatch else "R$ 105,00", .72, .23),
        ("02 de Setembro de 2026, Quarta-feira", .04, .29),
        ("Pix Recebido", .20, .34), ("R$ 20,00", .72, .34), ("Banco do Brasil", .20, .365), ("02SET", .83, .37),
        ("Cobrança de", .20, .41), ("-R$ 10,00", .72, .41), ("Juros", .20, .435), ("02SET", .83, .44),
        ("Ajuste", .20, .49), ("R$ 0,00", .72, .49), ("02SET", .83, .52),
        ("Saldo do dia", .08, .58), ("R$ 110,00", .72, .58),
        ("Saldo Anterior", .08, .65), ("R$ 100,00", .72, .65),
        ("Compartilhar", .30, .76), ("Voltar", .30, .80)]
    if orphan:
        cells.append(("R$ 77,00", .72, .69))
    return grid_pdf([cells], scale)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("future", [False, True])
@pytest.mark.parametrize("scale", [.75, 1.5])
def test_xp_web_keeps_movement_and_settlement_dates_histories_and_investment_export_policy(future, scale):
    result = pdf_parser.parse_pdf_transactions(xp_web(future=future, scale=scale))
    doc = result.financial_document
    assert doc and doc["origin"] == "XP Investimentos"
    assert doc["export"]["available_formats"] == ["csv", "xlsx"]
    assert [(e["dates"]["transaction"], e["dates"]["settlement"], e["amount"]) for e in doc["events"]] == [
        ("2026-10-01", "2026-10-02", "100.00"), ("2026-10-04", "2026-10-05", "-30.00")]
    assert [e["description"] for e in doc["events"]] == ["RECEBIMENTO CLIENTE TESTE", "TED APLICAÇÃO FUNDO FICTÍCIO"]
    assert doc["metadata"].get("opening_balance") is None
    assert all(v["status"] == "passed" for v in doc["validations"])
    assert any(o["amounts"].get("projected") == "2000.00" for o in doc["observations"])
    assert any(o["amounts"].get("projected") == "-999.00" for o in doc["observations"]) == future


def test_xp_web_unreadable_amount_cannot_fall_back_to_generic_bank_export():
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(xp_web(bad_amount=True))


@pytest.mark.parametrize("mismatch", [False, True])
def test_itau_complete_separates_cash_controls_and_investments_and_keeps_wrapped_name(mismatch):
    result = pdf_parser.parse_pdf_transactions(itau_complete(mismatch=mismatch))
    assert [row.amount for row in result.transactions] == [-5, 15, -15, -5, 20]
    assert "CLIENTE FICTÍCIO SOBRENOME" in result.transactions[0].description
    assert not any("SALDO " in row.description for row in result.transactions)
    assert result.parse_metrics.get("statement_validation_failed", 0) == int(mismatch)


@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.parametrize("scale", [.75, 1.5])
def test_descending_cards_keep_full_own_history_and_check_each_printed_day(mismatch, scale):
    result = pdf_parser.parse_pdf_transactions(descending_cards(mismatch=mismatch, scale=scale))
    assert [(r.date, r.amount, r.description) for r in result.transactions] == [
        ("2026-09-03", -5, "Pagamento CLIENTE TESTE"), ("2026-09-02", 20, "Pix Recebido Banco do Brasil"),
        ("2026-09-02", -10, "Cobrança de Juros"), ("2026-09-02", 0, "Ajuste")]
    assert result.parse_metrics.get("printed_balance_checks", 0) >= 2
    assert result.parse_metrics.get("statement_validation_failed", 0) == int(mismatch)
    assert all(row.source_regions for row in result.canonical_transactions)
    assert all(row.running_balance is None for row in result.canonical_transactions)


@pytest.mark.parametrize("options", [{"bad_tag": True}, {"orphan": True}])
def test_descending_cards_require_each_amount_and_its_printed_date_tag(options):
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(descending_cards(**options))


def test_unbranded_cards_do_not_infer_the_issuer_from_a_counterparty():
    raw = descending_cards()
    analysis = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=raw,
        analysis_id="an_fictional_cards").analysis_data
    assert analysis.bank_code is None and analysis.bank_name is None
    assert (analysis.opening_balance, analysis.closing_balance) == (100, 105)


def test_xp_web_real_printed_difference_keeps_values_and_requires_review():
    result = pdf_parser.parse_pdf_transactions(xp_web(mismatch=True))
    assert [row.amount for row in result.transactions] == [100, -30]
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


@pytest.mark.parametrize("paint", [False, True])
def test_itau_only_ignores_a_provably_unpainted_trailing_page(paint):
    writer = PdfWriter(clone_from=BytesIO(itau_complete()))
    page = writer.add_blank_page(width=1000, height=800)
    stream = DecodedStreamObject()
    stream.set_data(b"0.5 w q 0 0 800 600 re W n Q" + (b" 10 10 50 50 re f" if paint else b""))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    if paint:
        with pytest.raises(InvalidFileContentError):
            pdf_parser.parse_pdf_transactions(output.getvalue())
    else:
        result = pdf_parser.parse_pdf_transactions(output.getvalue())
        assert len(result.transactions) == 5
        assert len(result.source_page_texts) == 2
