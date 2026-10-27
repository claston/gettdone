"""Mercado Pago row borders preserve wrapped histories above the date baseline."""
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.recovery_acceptance import grid_pdf


def mercado_grid(*, missing_rule=False, compact_dates=False, split_description=False):
    cells = [("EXTRATO DE CONTA", .70, .03), ("Periodo: De 01-10-2026 al 31-10-2026", .60, .08),
        ("Saldo inicial: R$ 100,00", .08, .13), ("Entradas: R$ 10,00", .40, .13),
        ("Saidas: R$ -5,00", .40, .16), ("Saldo final: R$ 105,00", .72, .13),
        ("Data", .09, .26), ("Descrição", .20, .26), ("ID da operação", .44, .26),
        ("Valor", .70, .26), ("Saldo", .86, .26),
        ("Reembolso Envio cancelado", .20, .33), ("02-10-2026", .09, .36), ("para CLIENTE", .20, .36),
        ("FICTÍCIO", .20, .39), ("100000001", .44, .36), ("R$ 10,00", .70, .36), ("R$ 110,00", .86, .36),
        ("Débito por dívida/dinheiro", .20, .50), ("03-10-2026", .09, .55), ("retido", .20, .55),
        ("100000001", .44, .55), ("R$ -5,00", .70, .55), ("R$ 105,00", .86, .55),
        ("Data de geração: 01-11-2026", .07, .88),
        ("Mercado Pago Instituição de Pagamento Ltda.", .07, .95), ("1/1", .89, .98)]
    if compact_dates:
        cells = [(text, .155 if len(text) == 10 and text[2:3] == "-" else x, y) for text, x, y in cells]
    pages = [cells]
    if split_description:
        first = [cell for cell in cells if cell[2] < .44] + [("Débito por dívida/dinheiro", .20, .50), ("1/2", .89, .98)]
        second = [("2/2" if text == "1/1" else text, x, y) for text, x, y in cells if y == .26 or y > .85]
        second += [(text, x, y - .19) for text, x, y in cells if y == .55]
        pages = [first, second]
    writer = PdfWriter(clone_from=BytesIO(grid_pdf(pages)))
    for index, page in enumerate(writer.pages):
        stream = DecodedStreamObject()
        rules = [.30, .44] if index else [.30, .62] if missing_rule else [.30, .44, .62]
        commands = "\n".join(f"0.8 g 70 {(1-y)*800} 860 0.5 re f" for y in rules).encode()
        stream.set_data(page.get_contents().get_data() + b"\n" + commands)
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("compact_dates", [False, True])
@pytest.mark.parametrize("split_description", [False, True])
def test_mercado_row_rules_keep_wrapped_descriptions_and_shared_reference_events(compact_dates, split_description):
    r = pdf_parser.parse_pdf_transactions(mercado_grid(compact_dates=compact_dates, split_description=split_description))
    assert [row.amount for row in r.transactions] == [10, -5]
    assert [row.description for row in r.transactions] == [
        "Reembolso Envio cancelado para CLIENTE FICTÍCIO", "Débito por dívida/dinheiro retido"]
    assert [row.external_reference_id for row in r.canonical_transactions] == ["100000001", "100000001"]
    assert all(row.source_regions for row in r.canonical_transactions)
    assert "shared_operation_reference" in r.canonical_transactions[0].warnings
    assert r.parse_metrics["balance_consistency_failed"] == 0
    assert not r.parse_metrics.get("partial_statement")
    if split_description:
        assert {page for page, _ in r.canonical_transactions[1].source_line_refs} == {1, 2}


def test_two_movements_cannot_be_assigned_to_the_same_printed_row():
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(mercado_grid(missing_rule=True))
