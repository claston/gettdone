"""Explicit inactivity and investment summaries must retain their document meaning."""
import pytest

from app.application import pdf_parser
from app.application.document_type_classifier import classify_document_type
from app.application.errors import InvalidFileContentError, UnsupportedDocumentContentError
from app.application.parsers.pdf.native_table_source import read_native_table_source
from tests.fixtures.recovery_acceptance import grid_pdf


def xp_inactive(*, columnar=False, mutation=None):
    cells = [("XP empresas", .05, .05), ("Agência 0001 Conta 12345 Emitido em 01/09/2026", .05, .10)]
    labels = ["Saldo inicial", "Entradas", "Saídas", "Saldo final"]
    amounts = ["100,00", "0,00", "0,00", "101,00" if mutation == "balance" else "100,00"]
    if columnar:
        cells += [(label, .05+i*.24, .17) for i, label in enumerate(labels)]
        cells += [(f"R$ {amount}", .05+i*.24, .20) for i, amount in enumerate(amounts)]
    else:
        cells += [(" ".join(f"{label}R$ {amount}" for label, amount in zip(labels, amounts)), .05, .17)]
    cells += [("Extrato da Conta Digital PJ", .05, .28), ("Período de visualização", .05, .32),
              ("De 01/08/2026 à 31/08/2026", .05, .35),
              ("Extrato inexistente para o período selecionado", .05, .40), ("Fale conosco", .05, .50),
              ("4003-3710 SAC 0800-772-0202 Ouvidoria 0800-722-3710", .05, .55)]
    if mutation == "row":
        cells += [("02/08/2026 Pix recebido R$ 1,00", .05, .45)]
    return grid_pdf([cells])


def bradesco_inactive(*, mutation=None):
    cells = [("bradesco net empresa", .1, .05), ("Extrato Mensal / Por Período", .3, .05),
        ("Folha 1/2" if mutation == "partial" else "Folha 1/1", .8, .10),
        ("Agência Conta", .1, .15), ("Total Disponível (R$)", .27, .15),
        ("Investimento sem Baixa automática (R$)", .46, .15), ("Total (R$) (A+B)", .8, .15),
        ("0001 12345-6", .1, .18), ("100,00", .27, .18), ("200,00", .46, .18), ("300,00", .8, .18),
        ("Extrato de: Ag: 0001 Conta: 12345-6 Entre 01/08/2026 e 31/08/2026", .1, .22),
        ("Não há lançamentos para este tipo de extrato. (SMI.WSE.0004)", .15, .27),
        ("Últimos Lançamentos", .1, .34),
        ("Não há lançamentos para este tipo de extrato. (SMI.WSE.0004)", .15, .40)]
    if mutation == "row":
        cells += [("02/08/2026 Pix 10,00", .15, .30)]
    return grid_pdf([cells])


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("columnar", [False, True])
def test_xp_explicit_inactivity_retains_the_nonzero_balance(columnar):
    raw = xp_inactive(columnar=columnar)
    result = pdf_parser.parse_pdf_transactions(raw)
    assert result.transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 100
    source = read_native_table_source(raw)
    ocr = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert ocr.transactions == []
    assert ocr.parse_metrics["verified_closing_balance"] == 100


def test_bradesco_explicit_inactivity_does_not_invent_a_period_balance_from_available_funds():
    source = read_native_table_source(bradesco_inactive())
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert result.transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics.get("verified_opening_balance") is None
    assert result.parse_metrics.get("verified_closing_balance") is None


def test_native_inactivity_cannot_hide_a_raster_ledger_below_the_declaration():
    from tests.test_oct27_mercado_empty import _with_image

    raw = _with_image(bradesco_inactive(), body=True)
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(raw)


@pytest.mark.parametrize("factory,mutation", [(xp_inactive, "balance"), (xp_inactive, "row"),
    (bradesco_inactive, "row"), (bradesco_inactive, "partial")])
def test_missing_or_contradictory_evidence_never_returns_a_confirmed_empty_statement(factory, mutation):
    raw = factory(mutation=mutation)
    try:
        parsed = pdf_parser.parse_pdf_transactions(raw)
    except InvalidFileContentError:
        return
    assert not parsed.parse_metrics.get("statement_no_activity")


def test_bradesco_position_summary_is_a_typed_refusal():
    text = "\n".join(["Dados da Consulta", "Conta: 0001 12345-6", "Período de: 01/08/2026 até 31/08/2026",
        "Investimentos Bradesco", "Modalidade", "Total Aplicado (R$)", "Valor Principal (R$)",
        "Resgate Bruto (R$)", "Resgate Líquido (R$)", "100,00", "0,00", "0,00", "0,00", "Renda Bruta (R$): 0,00"])
    classification = classify_document_type("resumo.pdf", b"", extracted_text=text)
    assert classification.document_type == "investment_summary"
    with pytest.raises(UnsupportedDocumentContentError) as error:
        pdf_parser._parse_pdf_transactions_from_page_texts([text])
    assert error.value.document_type == "investment_summary"


def test_bradesco_investment_appendix_does_not_refuse_a_dated_cash_ledger():
    text = "\n".join(["Bradesco Extrato de Conta Corrente", "Conta 0001 12345-6", "Saldo anterior 100,00",
        "02/08/2026 Pix recebido 10,00 C 110,00", "Investimentos Bradesco", "Modalidade", "Total Aplicado (R$)",
        "Valor Principal (R$)", "Resgate Bruto (R$)", "Resgate Líquido (R$)"])
    assert classify_document_type("extrato.pdf", b"", extracted_text=text).document_type != "investment_summary"
