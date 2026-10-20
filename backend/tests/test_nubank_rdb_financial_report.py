import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


def rdb_pages():
    header = ["Extrato de Rendimentos", "Caixinhas PJ", "Período: 01 SET 2026 a 30 SET 2026"]
    columns = "Data Movimentação Rendimento Valor bruto IR IOF Saldo Líquido"
    return ["\n".join([
        *header, "RDB Resgate Imediato Rendimento 30 dias", "Instituição 260 - Nu Financeira S.A.",
        "Transações concluídas Saldo no final do período: R$ 2.000,00", columns,
        "01/09/2026 Compra por", "aplicação R$ 1.000,00 R$ 0,00 R$ 0,00 R$ 1.000,00",
        "02/09/2026 Resgate R$ 105,00 R$ 4,00 R$ 1,00 R$ 100,00",
        "Extrato de Caixinhas PJ para 01 SET 2026 a 30 SET 2026", "Página de 1 2",
    ]), "\n".join([
        *header, "Transações concluídas Saldo no final do período: R$ 2.000,00", columns,
        "30/09/2026 Rendimento até R$ 25,00", "essa data", "Transações em processamento",
        "Sem transações em processamento no período.", "Página 2 de 2",
    ])]


def test_rdb_preserves_net_gross_taxes_and_positions_without_bank_running_balances():
    result = _parse_pdf_transactions_from_page_texts(rdb_pages())
    document = result.financial_document
    assert document is not None
    assert document["document_type"] == "investment_statement"
    assert [row.amount for row in result.transactions] == [-1000, 100]
    assert all(row.running_balance is None for row in result.canonical_transactions)
    redemption = document["events"][1]
    assert redemption["amounts"] == {"gross": "105.00", "irrf": "4.00", "iof": "1.00", "net": "100.00"}
    assert {row["observation_type"] for row in document["observations"]} == {"closing_position", "period_income"}
    assert document["export"]["available_formats"] == ["csv", "xlsx"]
    assert all(row["status"] == "passed" for row in document["validations"])


def test_native_rdb_pipeline_keeps_investment_semantics(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in rdb_pages()))
    analysis = build_default_conversion_pipeline().run(filename="caixinhas.pdf", raw_bytes=raw, analysis_id="an_rdb").analysis_data
    assert analysis.financial_document["origin"] == "Nubank"
    assert analysis.transactions_total == 2
    assert analysis.opening_balance is analysis.closing_balance is None


@pytest.mark.parametrize("before,after", [
    ("R$ 105,00", "R$ 106,00"), ("R$ 4,00", "R$ 5,00"),
    ("R$ 100,00", "R$ 99,00"), ("02/09/2026", "32/09/2026"),
    ("02/09/2026", "02/10/2026"), ("Página 2 de 2", "Página 3 de 3"),
    ("aplicação R$", "operação desconhecida R$"),
    ("01/09/2026 Compra por", "Compra por"),
    ("Sem transações em processamento no período.", "01/09/2026 Aplicação R$ 10,00"),
])
def test_rdb_ambiguous_rows_or_missing_pages_cannot_fall_back_to_bank_parser(before, after):
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts([page.replace(before, after) for page in rdb_pages()])


def test_rdb_missing_last_page_is_refused():
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts(rdb_pages()[:1])


def test_rdb_missing_first_page_cannot_be_converted_as_one_bank_income():
    with pytest.raises(PdfFinancialValidationError):
        _parse_pdf_transactions_from_page_texts(rdb_pages()[1:])


def test_income_only_final_page_cannot_discard_the_rest_of_the_investment_report(monkeypatch):
    def reject_bank_segmentation(_pages):
        raise AssertionError("Typed RDB operations must be parsed together before bank appendix filtering")

    monkeypatch.setattr(pdf_parser_module, "segment_pdf_pages", reject_bank_segmentation)
    assert len(_parse_pdf_transactions_from_page_texts(rdb_pages()).financial_document["events"]) == 2
