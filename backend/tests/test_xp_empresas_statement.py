import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_layout_inference import infer_pdf_layout
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts, parse_pdf_transactions
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

LAYOUT = "xp_empresas_extrato_conta_digital_pj_v1"
AMOUNTS = [-100.0, -500.0, 500.0, 250.0, -150.0, -100.0, 50.0]


def xp_pages():
    # Synthetic values and identities; same grouped dates and page break as the observed format.
    return ["\n".join([
        "ClienteEMPRESA SINTÉTICA LTDA CNPJ/CPF00.000.000/0001-00Agência0001 Conta12345678Emitido em09/10/2026",
        "Saldo", "Saldo inicialR$ 1.000,00 EntradasR$ 800,00 Saídas-R$ 850,00 Saldo finalR$ 950,00",
        "Extrato da Conta Digital PJ", "Período de visualizaçãoDe 11/07/2026 à 09/10/2026",
        "06/10/2026 Valor", "Pix recebido de CLIENTE SINTÉTICO R$ 50,00", "Saldo do dia R$ 950,00",
        "30/09/2026 Valor", "LIQUIDACAO CREDITO XP | 12345 -R$ 100,00", "Saldo do dia R$ 900,00",
        "09/09/2026 Valor", "Pix enviado para FORNECEDOR SINTÉTICO -R$ 150,00", "Saldo do dia R$ 1.000,00",
        "03/09/2026 Valor", "Pix recebido de CLIENTE SINTÉTICO R$ 250,00", "Saldo do dia R$ 1.150,00",
        "13/08/2026 Valor", "Transferência enviada para conta investimento -R$ 500,00",
        "Pix recebido de CLIENTE SINTÉTICO R$ 500,00", "Saldo do dia R$ 900,00", "30/07/2026 Valor",
    ]), "\n".join([
        "LIQUIDACAO CREDITO XP | 12345 -R$ 100,00", "Saldo do dia R$ 900,00", "Fale conosco",
        "Central de relacionamento para Capitais e regiões metropolitanas4003-3710 SAC0800-772-0202 Ouvidoria0800-722-3710",
    ])]


def xp_pdf(pages=None):
    return generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in (pages or xp_pages())))


def test_xp_identification_requires_institutional_markers_not_a_transaction_mention():
    pages = xp_pages()
    assert infer_pdf_layout("\n".join(pages)).layout_name == LAYOUT
    unrelated = "\n".join(pages).replace("4003-3710", "4000-0000").replace("0800-772-0202", "0800-000-0000")
    assert infer_pdf_layout(unrelated).layout_name != LAYOUT
    assert infer_pdf_layout(unrelated.replace("CREDITO XP", "TRANSFERENCIA XP EMPRESAS")).layout_name != LAYOUT


def test_xp_dates_currency_signs_daily_balances_and_cross_page_provenance():
    result = _parse_pdf_transactions_from_page_texts(xp_pages())
    assert result.layout.layout_name == LAYOUT
    assert result.layout.confidence >= 0.95
    assert [row.amount for row in result.transactions] == AMOUNTS
    assert [row.date for row in result.transactions] == [
        "2026-07-30", "2026-08-13", "2026-08-13", "2026-09-03", "2026-09-09", "2026-09-30", "2026-10-06",
    ]
    rows = result.canonical_transactions
    assert [row.running_balance for row in rows] == [900, None, 900, 1150, 1000, 900, 950]
    assert rows[0].source_page == 2
    assert {page for page, _ in rows[0].source_line_refs} == {1, 2}
    assert all(row.source_line_refs and not row.warnings for row in rows)
    assert result.parse_metrics["confidence_band"] == "high"
    assert result.parse_metrics["export_recommendation"] == "safe_to_export"


def test_native_xp_pipeline_keeps_validated_ledger_and_bank_identity(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")

    def reject_generic_retry(*_args, **_kwargs):
        raise AssertionError("A fully validated XP ledger must retain its printed values")

    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", reject_generic_retry)
    analysis = build_default_conversion_pipeline().run(
        filename="xp.pdf", raw_bytes=xp_pdf(), analysis_id="an_xp_synthetic",
    ).analysis_data
    assert analysis.transactions_total == 7
    assert [row.amount for row in analysis.report_transactions] == AMOUNTS
    assert analysis.total_inflows == 800
    assert analysis.total_outflows == -850
    assert analysis.opening_balance == 1000
    assert analysis.closing_balance == 950
    assert analysis.bank_name == "Banco XP"
    assert analysis.bank_code == "348"
    assert analysis.bank_branch == "0001"
    assert analysis.account_number == "12345678"
    assert all(row.source_regions for row in analysis.report_transactions)


def test_xp_one_page_and_split_column_heading_preserve_the_same_transactions():
    text = "\n".join(xp_pages()).replace(" Valor", "\nValor")
    result = _parse_pdf_transactions_from_page_texts([text])
    assert [row.amount for row in result.transactions] == AMOUNTS


@pytest.mark.parametrize("old,new", [
    ("R$ 950,00", "R$ 951,00"),
    ("EntradasR$ 800,00", "EntradasR$ 801,00"),
    ("Saídas-R$ 850,00", "Saídas-R$ 851,00"),
    ("Saldo do dia R$ 1.150,00", "Saldo do dia R$ 1.151,00"),
    ("06/10/2026 Valor", "32/10/2026 Valor"),
    ("06/10/2026 Valor", "10/10/2026 Valor"),
    ("06/10/2026 Valor", "06/10/2026 Valor\nLANÇAMENTO SEM VALOR"),
    ("Pix recebido de CLIENTE SINTÉTICO R$ 500,00\n", ""),
    ("-R$ 150,00", "R$ 150,00"),
    ("Saldo do dia R$ 900,00", "Saldo do dia"),
])
def test_xp_ambiguous_or_incomplete_ledgers_are_rejected(old, new):
    pages = [page.replace(old, new) for page in xp_pages()]
    with pytest.raises(PdfFinancialValidationError, match="XP"):
        _parse_pdf_transactions_from_page_texts(pages)


@pytest.mark.parametrize("page_index", [0, 1])
def test_xp_missing_page_does_not_export_partial_transactions(page_index, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    # A visible institutional header keeps the XP validator active even without the footer.
    with pytest.raises(PdfFinancialValidationError, match="XP"):
        parse_pdf_transactions(xp_pdf(["XP Empresas\n" + xp_pages()[page_index]]))
