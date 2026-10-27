"""Fictional regressions for the four-case recovery batch."""
from dataclasses import replace
from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from tests.fixtures.positioned_cash_statements import grid, positioned_pdf, santander_pages
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_followup_conversion_recovery import column_ocr


def three_column_cd(*, mutation=None):
    cells = [("Extrato de Conta Corrente", .05, .03), ("Cliente: EMPRESA FICTÍCIA", .05, .06),
        ("Lançamentos", .05, .10), ("Dia", .05, .15), ("Histórico", .25, .15), ("Valor", .80, .15),
        ("28/03/2024", .05, .20), ("Saldo Anterior", .25, .20), ("100,00 C", .80, .20),
        ("01/04/2024", .05, .30), ("ENVIO PIX", .25, .30), ("20,00 D", .80, .30),
        ("01/04/2024", .05, .40), ("Saldo", .25, .40), ("80,00 C", .80, .40),
        ("02/04/2024", .05, .50), ("RECEBIMENTO FICTÍCIO", .25, .50), ("50,00 C", .80, .50),
        ("30/04/2024", .05, .60), ("Saldo Dia", .25, .60), ("130,00 C", .80, .60)]
    substitutions = {"wrong_balance": {"130,00 C": "131,00 C"}, "bad_date": {"02/04/2024": "32/04/2024"},
        "missing_sign": {"20,00 D": "20,00"}, "bad_order": {"02/04/2024": "01/03/2024"},
        "closing_before_movement": {"30/04/2024": "01/04/2024"}}
    cells = [(substitutions.get(mutation, {}).get(text, text), x, y) for text, x, y in cells]
    if mutation == "orphan_value":
        cells.append(("9,00 D", .80, .70))
    if mutation == "missing_date":
        cells = [cell for cell in cells if cell[0] != "02/04/2024"]
    if mutation == "missing_closing_value":
        cells = [cell for cell in cells if cell[0] != "130,00 C"]
    return grid_pdf([cells])


def separated_negative_balance(*, adjacent=True, mismatch=False, blocked=False, wrong_blocked=False):
    summary = santander_pages()[0][:11]
    if blocked:
        summary = [(x, y, "40,00" if (x, y) == (400, 665) else text) for x, y, text in summary]
        summary += [(25, 655, "Saldo Bloqueio Dia"), (400, 655, "39,00" if wrong_blocked else "40,00"),
                    (25, 645, "Saldo Total de Conta Corrente em 28/02"), (400, 645, "0,00")]
    pages = [summary + [(25, 640, "Conta Corrente Movimentação"), *grid(615, santander=True),
        (80, 580, "SALDO EM 31/01"), (540, 580, "0,00"),
        (25, 550, "04/02"), (80, 550, "PIX ENVIADO"), (270, 550, "000001"),
        (445, 550, "300,00-"), (540, 550, "301,00" if mismatch else "300,00"),
        (25, 520, "Página 1/2")],
        [*grid(750, santander=True), (25, 710, "05/02"), (80, 710, "PIX RECEBIDO"),
        (270, 710, "000002"), (365, 710, "200,00"),
        (80, 680, "PIX RECEBIDO"), (270, 680, "000003"), (365, 680, "100,00"), (540, 680, "0,00"),
        (80, 655, "SALDO EM 28/02"), (540, 655, "0,00"),
        (25, 625, "Saldos por Período")]]
    # Printed beside the balance, but stored after the other text objects.
    pages[0].append((564.97, 550 if adjacent else 540, "-"))
    return positioned_pdf(pages)


def test_centered_nordeste_history_heading_does_not_drop_indented_descriptions():
    source = column_ocr("nordeste")
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line,
        bbox={**line.bbox, "left": .29}) if line.text == "Histórico" else line for line in page.lines])])
    r = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in r.transactions] == [-20, 50]
    assert [row.description for row in r.transactions] == ["ENVIO TESTE", "RECEBIMENTO TESTE"]
    assert [row.external_reference_id for row in r.canonical_transactions] == ["100001", "100002"]


def test_three_column_signed_table_exports_only_movements_and_all_controls():
    r = pdf_parser.parse_pdf_transactions(three_column_cd())
    assert [row.amount for row in r.transactions] == [-20, 50]
    assert r.layout.layout_name == "dated_cd_three_column_statement_v1"
    assert r.parse_metrics["verified_opening_balance"] == 100
    assert r.parse_metrics["verified_closing_balance"] == 130
    assert r.parse_metrics["printed_balance_checks"] == 2
    assert r.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_regions for row in r.canonical_transactions)
    a = build_default_conversion_pipeline().run(filename="fictional.pdf", raw_bytes=three_column_cd(),
        analysis_id="an_three_column_fictional").analysis_data
    assert a.bank_name is None
    assert a.bank_code in (None, "000")


@pytest.mark.parametrize("mutation", ["bad_date", "missing_sign", "bad_order", "closing_before_movement",
                                      "orphan_value", "missing_date", "missing_closing_value"])
def test_three_column_reader_rejects_unexplained_or_unreadable_rows(mutation):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(three_column_cd(mutation=mutation))


def test_three_column_real_balance_difference_is_preserved_for_review():
    r = pdf_parser.parse_pdf_transactions(three_column_cd(mutation="wrong_balance"))
    assert [row.amount for row in r.transactions] == [-20, 50]
    assert "financial_validation_failed" in r.canonical_transactions[0].warnings


@pytest.mark.parametrize("adjacent,mismatch", [(True, False), (False, False), (True, True)])
def test_santander_negative_balance_uses_the_printed_sign_geometry(adjacent, mismatch):
    r = pdf_parser.parse_pdf_transactions(separated_negative_balance(adjacent=adjacent, mismatch=mismatch))
    assert [row.amount for row in r.transactions] == [-300, 200, 100]
    if adjacent and not mismatch:
        assert r.canonical_transactions[0].running_balance == -300
        assert r.parse_metrics["balance_consistency_failed"] == 0
        assert not any(row.warnings for row in r.canonical_transactions)
    else:
        assert "financial_validation_failed" in r.canonical_transactions[0].warnings


def test_bb_correct_arithmetic_does_not_remove_a_real_printed_period_conflict():
    from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf

    text = """Banco do Brasil
Extrato de Conta Corrente
Período: 01 a 02/10/2026
Lançamentos
Dia Lote Documento Histórico Valor
28/09/2026 Saldo Anterior 100,00 (+)
30/09/2026 13105 000001 Pix - Enviado 20,00 (-)
30/09/2026 13105 Saldo do dia 80,00 (+)
02/10/2026 S A L D O 80,00 (+)
Informações Adicionais"""
    result = pdf_parser.parse_pdf_transactions(generate_native_text_pdf((tuple(text.splitlines()),)))
    assert [row.amount for row in result.transactions] == [-20]
    assert "financial_validation_failed" in result.canonical_transactions[0].warnings


@pytest.mark.parametrize("wrong_blocked", [False, True])
def test_santander_cash_closing_uses_the_explicit_total_after_blocked_funds(wrong_blocked):
    r = pdf_parser.parse_pdf_transactions(separated_negative_balance(blocked=True, wrong_blocked=wrong_blocked))
    assert [row.amount for row in r.transactions] == [-300, 200, 100]
    if wrong_blocked:
        assert "financial_validation_failed" in r.canonical_transactions[0].warnings
    else:
        assert not any(row.warnings for row in r.canonical_transactions)
        assert r.parse_metrics["verified_closing_balance"] == 0


@pytest.mark.parametrize("glyph", ["S", "9"])
def test_large_history_overlay_does_not_change_or_hide_financial_glyphs(glyph):
    writer = PdfWriter(clone_from=BytesIO(three_column_cd()))
    page = writer.pages[0]
    stream = DecodedStreamObject()
    stream.set_data(page.get_contents().get_data() + f"\nBT /F1 100 Tf 1 0 0 1 350 640 Tm ({glyph}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    if glyph.isdigit():
        with pytest.raises(PdfFinancialValidationError):
            pdf_parser.parse_pdf_transactions(output.getvalue())
    else:
        r = pdf_parser.parse_pdf_transactions(output.getvalue())
        assert [row.amount for row in r.transactions] == [-20, 50]
        assert r.source_extraction.metrics["decorative_history_glyphs_excluded"] == 1


@pytest.mark.real_http
def test_nordeste_centered_history_converts_with_the_correct_issuer_over_http(tmp_path, monkeypatch):
    from tests.ocr_conversion_http_helpers import configure_ocr, public_client

    source = column_ocr("nordeste")
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, bbox={**line.bbox, "left": .29})
        if line.text == "Histórico" else line for line in page.lines])])
    raw = configure_ocr(monkeypatch, source)
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fictional-nordeste-batch"}
        response = client.post("/convert", data=owner, files={"file": ("fictional.pdf", raw, "application/pdf")})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["analysis"]["transactions_total"] == 2
        assert payload["analysis"]["bank_code"] == "004"
        assert client.get(f"/report/{payload['processing_id']}", params=owner).status_code == 200
