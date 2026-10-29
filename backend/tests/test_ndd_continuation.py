"""Readable NDD continuations keep all movements and physical page evidence."""

import pytest

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.followup_native_tables import _kind
from tests.fixtures.recovery_acceptance import grid_pdf


def ndd_continuation(*, repeated_headers=False, shifted=False, ambiguous=False, auxiliary_columns=False):
    headers = [
        ("Id", .03, .20), ("Data/Hora", .13, .20), ("Lançamento", .30, .20),
        ("Operação", .43, .20), ("Descrição da Movimentação", .56, .20), ("Valor", .93, .20),
    ]
    if auxiliary_columns:
        headers = [(text, .58 if text == "Descrição da Movimentação" else x, y) for text, x, y in headers]
        headers.append(("N° da Operação", .76, .20))
    pages = []
    for index, (date, time, direction, description, amount) in enumerate([
        ("02/10/2026", "12:00", "Débito", "PAGAMENTO FICTÍCIO", "10,00"),
        ("01/10/2026", "11:00", "Débito", "PIX ENVIADO FICTÍCIO", "30,00"),
        ("01/10/2026", "10:00", "Crédito", "PIX RECEBIDO FICTÍCIO", "100,00"),
    ]):
        page = [
            ("NDD IP Conta Digital | Extrato", .03, .03),
            ("De: 01/10/2026 Até: 02/10/2026", .03, .06),
            ("Saldo inicial 01/10:", .68, .09), ("0,00", .93, .09),
        ]
        has_header = index == 0 or repeated_headers
        if has_header:
            page += headers
        y = .27 if has_header else .14
        page += [
            (str(10003 - index), .03, y), (f"{date} {time}", .22 if shifted and index else .13, y),
            (direction, .30, y), ("PIX", .43, y), (description, .56, y), (amount, .93, y),
        ]
        if auxiliary_columns:
            page.append(("999999", .76, y))
        if ambiguous and index == 1:
            page.append(("40,00", .90, y))
        if index in (0, 2):
            page += [(f"Saldo final do dia {date}:", .68, .40),
                     ("R$60,00" if index == 0 else "R$70,00", .93, .40)]
        if index == 2:
            page += [
                ("Total geral:", .68, .56), ("60,00", .93, .56),
                ("Total Crédito:", .68, .60), ("100,00", .93, .60),
                ("Total Débito:", .68, .64), ("40,00", .93, .64),
                ("Quantidade de Operações:", .68, .68), ("3", .93, .68),
            ]
        pages.append(page)
    return grid_pdf(pages)


@pytest.mark.parametrize("repeated_headers", [False, True])
def test_readable_ndd_continuation_preserves_every_movement_and_page(monkeypatch, repeated_headers):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = pdf_parser.parse_pdf_transactions(ndd_continuation(repeated_headers=repeated_headers))
    assert result.layout.layout_name == "ndd_conta_digital_operacoes_v1"
    assert [row.amount for row in result.transactions] == [100, -30, -10]
    assert [row.date for row in result.transactions] == ["2026-10-01", "2026-10-01", "2026-10-02"]
    assert [row.source_page for row in result.canonical_transactions] == [3, 2, 1]
    assert [row.description for row in result.transactions] == [
        "PIX RECEBIDO FICTÍCIO", "PIX ENVIADO FICTÍCIO", "PAGAMENTO FICTÍCIO",
    ]
    assert all(row.source_regions for row in result.canonical_transactions)
    assert result.parse_metrics["verified_closing_balance"] == 60


@pytest.mark.parametrize("change", ["shifted", "ambiguous"])
def test_unknown_geometry_or_ambiguous_amount_cannot_silently_drop_a_later_page(change):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(ndd_continuation(**{change: True}))


def test_ndd_title_alone_does_not_claim_an_unknown_table():
    assert _kind("NDD Cargo Conta Digital | Extrato") is None


def test_centered_description_heading_keeps_history_without_adjacent_reference_fields():
    result = pdf_parser.parse_pdf_transactions(ndd_continuation(auxiliary_columns=True))
    assert [row.description for row in result.transactions] == [
        "PIX RECEBIDO FICTÍCIO", "PIX ENVIADO FICTÍCIO", "PAGAMENTO FICTÍCIO",
    ]
