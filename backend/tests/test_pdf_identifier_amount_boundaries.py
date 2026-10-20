import pytest

from app.application.layout_profiles.registry import get_layout_profile
from app.application.parsers.pdf.models import _PdfLine
from app.application.pdf_parser import _classify_tabular_statement_line, _parse_pdf_transactions_from_page_texts

UUID = "ABCDEF12-1234-4ABC-9876-ABCDEF0000B4"


def identifier_statement_lines():
    return (
        "Sicredi", "Cooperativa 0001 Conta 12345-6", "Extrato", "Período de 01/09/2026 a 30/09/2026",
        "Data Descrição Documento Valor (R$) Saldo (R$)", "01/09/2026 SALDO ANTERIOR 1.000,00",
        "02/09/2026 CREDITO SEGURADORA 080----85 134,46 1.134,46",
        f"03/09/2026 RECEBIMENTO PIX {UUID} 300,00 1.434,46",
        "04/09/2026 PAGAMENTO PIX ABCDEF12-1234-4ABC-9876-ABCDEF0000A9 -299,00 1.135,46",
    )


@pytest.mark.parametrize("profile_name", [None, "sicredi_extrato_tabela_pix_cred_saldo_v1"])
@pytest.mark.parametrize("reference,amount,expected", [
    ("080----85", "134,46", 134.46), (UUID, "300,00", 300),
    ("ABCDEF12-1234-4ABC-9876-ABCDEF0000A9", "299,00", 299),
    ("080----85", "-134,46", -134.46),
])
def test_tabular_parser_keeps_full_identifier_separate_from_amount_and_sign(profile_name, reference, amount, expected):
    row, candidate = _classify_tabular_statement_line(
        line=_PdfLine(f"02/09/2026 RECEBIMENTO PIX {reference} {amount} 1.500,00", 1, 8),
        inferred_year=2026, tabular_profile=get_layout_profile(profile_name),
    )
    assert candidate and row is not None
    assert row.transaction.amount == expected
    assert row.running_balance == 1500
    assert row.transaction.description.endswith(reference)
    if profile_name:
        assert row.external_reference_id == reference


def test_complete_statement_keeps_identifiers_and_reconciles_all_balances():
    result = _parse_pdf_transactions_from_page_texts(["\n".join(identifier_statement_lines())])
    rows = [row for row in result.canonical_transactions if "SALDO ANTERIOR" not in row.description]
    assert [row.amount for row in rows] == [134.46, 300, -299]
    assert rows[0].description.endswith("080----85")
    assert rows[1].description.endswith(UUID)
    assert all(not row.warnings for row in rows)
    assert result.parse_metrics["balance_consistency_failed"] == 0
