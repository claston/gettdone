"""Financial and coverage regressions through the public PDF pipeline."""

import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import parse_pdf_transactions
from tests.fixtures.split_sheet_statements import BUILDERS, change, document


@pytest.fixture(autouse=True)
def no_remote_extraction(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setenv("AI_RECOVERY_MODE", "off")


def run(pages, **kwargs):
    return (
        build_default_conversion_pipeline()
        .run(filename="synthetic.pdf", raw_bytes=document(pages, **kwargs), analysis_id="an_synthetic_split_sheet")
        .analysis_data
    )


@pytest.mark.parametrize("kind", ["bb", "billing", "amazon"])
@pytest.mark.parametrize("scale,shuffled", [(1, False), (0.75, True), (2, True)])
def test_complete_native_statements_preserve_signs_controls_and_identity(kind, scale, shuffled):
    analysis = run(BUILDERS[kind](), scale=scale, shuffled=shuffled)
    assert len(analysis.report_transactions) == 3
    assert analysis.opening_balance == {"bb": 10, "billing": 0, "amazon": 0}[kind]
    assert analysis.closing_balance == {"bb": 0, "billing": 79.01, "amazon": 85}[kind]
    assert analysis.total_inflows == {"bb": 25, "billing": 100, "amazon": 105}[kind]
    assert analysis.total_outflows == {"bb": -35, "billing": -20.99, "amazon": -20}[kind]
    assert analysis.bank_code == {"bb": "001", "billing": None, "amazon": "003"}[kind]
    assert analysis.document_type == ("financial_spreadsheet" if kind == "billing" else "bank_statement")
    metrics = analysis.pdf_processing_metrics
    assert metrics["financial_controls_validated"] == 1
    assert metrics["statement_row_coverage_rate"] == 1
    assert metrics["canonical_warning_count"] == 0
    assert metrics["textract_attempted"] == 0
    assert metrics["printed_balance_checks"] >= 3
    if kind == "bb":
        assert any(len({region.page for region in r.source_regions}) >= 2 for r in analysis.report_transactions)
    if kind == "billing":
        # Rounded spreadsheet IDs cannot be reconstructed as original transaction IDs.
        parsed = parse_pdf_transactions(document(BUILDERS[kind](), scale=scale, shuffled=shuffled))
        assert any("1.86E+09" in r.description for r in parsed.transactions)


@pytest.mark.parametrize(
    "kind,before,after",
    [
        ("bb", "35,00", "35,01"),
        ("bb", "-5,00", "5,00"),
        ("bb", "25,00", "24,00"),
        ("bb", "28/02/2026 S A L D O", "28/02/2026 Encerramento"),
        ("billing", "99.01", "99.02"),
        ("billing", "-0.99", "0.99"),
        ("billing", "Débito", "Crédito"),
        ("billing", "Saldo Final", "Fim"),
        ("amazon", "R$ 85,00", "R$ 85,01"),
        ("amazon", "\uf056", "\uf055"),
        ("amazon", "Saldo Anterior:", "Sem saldo inicial"),
        ("amazon", "03/02/2026", "01/02/2026"),
    ],
)
def test_inconsistent_or_incomplete_financial_controls_fail_closed(kind, before, after):
    with pytest.raises(PdfFinancialValidationError):
        run(change(BUILDERS[kind](), before, after))


@pytest.mark.parametrize("kind", ["bb", "billing"])
def test_missing_column_page_is_rejected(kind):
    with pytest.raises(PdfFinancialValidationError):
        run(BUILDERS[kind]()[:-1])


@pytest.mark.parametrize("kind", ["bb", "billing"])
def test_misaligned_column_pages_are_rejected(kind):
    pages = BUILDERS[kind]()
    pages[2] = [(x, y - 8, t) for x, y, t in pages[2]]
    with pytest.raises(PdfFinancialValidationError):
        run(pages)


def test_extra_unmatched_amount_on_column_page_is_rejected():
    pages = BUILDERS["bb"]()
    pages[2].append((190, 650, "7,00"))
    with pytest.raises(PdfFinancialValidationError):
        run(pages)


def test_missing_amazon_icon_does_not_guess_sign_from_history():
    pages = change(BUILDERS["amazon"](), "\uf056", "")
    with pytest.raises(PdfFinancialValidationError):
        run(pages)


def test_billing_recipient_bank_does_not_identify_the_issuing_institution():
    analysis = run(change(BUILDERS["billing"](), "PAGADOR FICTÍCIO", "Banco do Brasil"))
    assert analysis.bank_code is None
    assert analysis.bank_name is None


def test_billing_undated_money_rows_cannot_disappear_even_when_their_net_is_zero():
    pages = BUILDERS["billing"]()
    pages[0].extend(
        [
            (345, 660, "Ajuste sem data"),
            (405, 660, "1"),
            (455, 660, "100.01"),
            (345, 640, "Outro ajuste"),
            (405, 640, "-1"),
            (455, 640, "99.01"),
        ]
    )
    with pytest.raises(PdfFinancialValidationError):
        run(pages)


def test_distinct_amazon_rows_are_not_collapsed_by_overlapping_text_deduplication():
    pages = BUILDERS["amazon"]()
    for y, reference, sign in [(674, "200", "\uf055"), (666, "201", "\uf056")]:
        pages[0].extend(
            [
                (70, y, "02/02/2026"),
                (150, y, reference),
                (220, y, "ADQUIRENTE CART"),
                (465, y, "R$ 100,00"),
                (465.2, y, "R$ 100,00"),
                (510, y, sign),
            ]
        )
    analysis = run(pages)
    assert len(analysis.report_transactions) == 5
    assert analysis.total_inflows == 205
    assert analysis.total_outflows == -120
    assert analysis.closing_balance == 85
