"""Fictional card statements; no customer document or extracted private data."""

import pytest

from app.application import pdf_parser as parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_layout_inference import infer_pdf_layout
from tests.test_pdf_parser_digital_credit_card import native_pdf

LAYOUT = "bradesco_extrato_cartao_web_usd_brl_v1"
PARSER = "layout_specific_bradesco_card_web"


def statement_pages() -> list[str]:
    return [
        """15/02/2026Data de vencimento:
XXXX.XXXX.XXXX.1111Número do cartão:
CLIENTE FICTÍCIO - VISANome:
Janeiro/2026Mês:
EMPRESA FICTÍCIAEmpresa:
001 - 001Centro de custo:
Extrato
Nova Consulta de Extrato
Detalhe do Extrato
Data Histórico US$ R$
05/06 LOJA FICTÍCIA A 8/12 0,00 125,40
08/11 LOJA FICTÍCIA B 3/6 0,00 60,00
12/12 LOJA FICTÍCIA C 2/3 0,00 18,90
18/12 LOJA FICTÍCIA D 2/4 0,00 40,00
03/01 LOJA FICTÍCIA E 1/5 0,00 75,00
04/01 LOJA FICTÍCIA F 1/2 0,00 30,00
05/01 LOJA FICTÍCIA G 1/3 0,00 50,00
TOTAL: 0,00 399,30
Taxas mensais
Pagamento de contas 1,99% 2,99% 3,99%
""",
        "Fone Bradesco\nSAC - Serviço de Atendimento ao Cliente\n"
        "Instruções de financiamento\n10/01 EXEMPLO DE ENCARGO 0,00 999,00\n",
    ]


def prohibit_ocr(monkeypatch) -> None:
    monkeypatch.setattr(parser, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(parser, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(parser, "is_textract_forced", lambda: False)

    def unexpected(*args, **kwargs):
        pytest.fail("Native card statements must not invoke OCR or generic recovery")

    monkeypatch.setattr(parser, "_extract_pdf_page_texts_with_ocr", unexpected)
    monkeypatch.setattr(parser, "_parse_scanned_pdf_with_textract_gateway", unexpected)
    monkeypatch.setattr(parser, "_try_parse_native_layout_text", unexpected)


def test_native_card_statement_uses_brl_and_printed_reference_without_bank_balances(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    result = parser.parse_pdf_transactions(native_pdf(statement_pages()))
    assert result.layout.layout_name == LAYOUT
    assert result.parse_metrics["selected_parser"] == PARSER
    assert [row.amount for row in result.transactions] == [-125.40, -60, -18.90, -40, -75, -30, -50]
    assert [row.date for row in result.transactions] == [
        "2025-06-05", "2025-11-08", "2025-12-12", "2025-12-18", "2026-01-03", "2026-01-04", "2026-01-05",
    ]
    assert "8/12" in result.transactions[0].description
    assert all(row.running_balance is None for row in result.canonical_transactions)
    assert all(row.source_page == 1 for row in result.canonical_transactions)
    assert result.parse_metrics["balance_consistency_checked"] == 0
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["textract_attempted"] == 0
    assert result.parse_metrics["confidence_band"] == "high"
    for row in result.canonical_transactions:
        assert row.description in result.source_page_texts[0].splitlines()[row.source_line - 1]


def test_pipeline_identifies_card_without_inventing_balances(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    data = build_default_conversion_pipeline().run(
        filename="ficticio.pdf", raw_bytes=native_pdf(statement_pages()), analysis_id="synthetic-bradesco-card",
    ).analysis_data
    assert data.transactions_total == 7
    assert data.total_inflows == 0
    assert data.total_outflows == -399.30
    assert data.document_type == "credit_card_statement"
    assert data.ofx_account_type == "credit_card"
    assert data.bank_name == "Bradesco"
    assert data.bank_code == "237"
    assert "8/12" in data.report_transactions[0].description
    assert data.opening_balance is None
    assert data.closing_balance is None


@pytest.mark.parametrize("before,after", [
    ("399,30", "400,30"),
    ("TOTAL: 0,00 399,30", "TOTAL: 1,00 399,30"),
    ("TOTAL: 0,00 399,30", ""),
    ("0,00 125,40", "125,40"),
    ("0,00 125,40", "0,00 125,4X"),
    ("05/06 LOJA", "31/02 LOJA"),
    ("Janeiro/2026Mês:", "Mês:"),
    ("Janeiro/2026Mês:", "Janeiro/2026Mês:\nFevereiro/2026Mês:"),
])
def test_inconsistent_statement_does_not_fall_back_to_generic_or_ocr(monkeypatch, before, after) -> None:
    prohibit_ocr(monkeypatch)
    pages = statement_pages()
    pages[0] = pages[0].replace(before, after)
    with pytest.raises(PdfFinancialValidationError):
        parser.parse_pdf_transactions(native_pdf(pages))


def test_foreign_amount_is_not_added_to_brl_and_credit_sign_is_preserved() -> None:
    pages = statement_pages()
    pages[0] = pages[0].replace("0,00 60,00", "12,50 60,00").replace("TOTAL: 0,00", "TOTAL: 12,50")
    pages[0] = pages[0].replace("TOTAL: 12,50 399,30", "06/01 ESTORNO FICTÍCIO 0,00 -25,00\nTOTAL: 12,50 374,30")
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert result.transactions[1].amount == -60
    assert result.transactions[-1].amount == 25
    assert len(result.transactions) == 8
    assert all(row.running_balance is None for row in result.canonical_transactions)


@pytest.mark.parametrize("before,after", [
    ("Bradesco", "OUTRO BANCO"),
    ("Número do cartão:", "Identificação:"),
    ("Detalhe do Extrato", "Extrato de conta corrente"),
    ("Data Histórico US$ R$", "Data Histórico Valor Saldo"),
])
def test_card_web_layout_requires_its_identity_and_currency_header(before, after) -> None:
    text = "\n".join(statement_pages()).replace(before, after)
    assert infer_pdf_layout(text).layout_name != LAYOUT


def test_equal_purchases_are_preserved_and_can_span_pages() -> None:
    pages = statement_pages()
    duplicate = "04/01 LOJA FICTÍCIA F 1/2 0,00 30,00"
    pages[0] = pages[0].replace(duplicate, duplicate + "\n" + duplicate).replace("399,30", "429,30")
    first, second = pages[0].split("03/01 LOJA", 1)
    pages = [first, "Data Histórico US$ R$\n03/01 LOJA" + second, pages[1]]
    result = parser._parse_pdf_transactions_from_page_texts(pages)
    assert len(result.transactions) == 8
    assert [r.source_page for r in result.canonical_transactions] == [1, 1, 1, 1, 2, 2, 2, 2]
    assert sum(r.amount == -30 for r in result.transactions) == 2
