"""Synthetic BB combined-balance statements retain complete history and evidence."""
import pytest

from app.application import pdf_parser
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.ocr_conversion_http_helpers import public_client
from tests.test_oct20_followup_ocr import ocr


def pages():
    header = [("Banco do Brasil Extrato de Conta Corrente", .08, .02),
              ("Movimentação em Novembro de 2025", .08, .05),
              ("Data", .08, .10), ("Origem", .17, .10), ("Histórico", .25, .10),
              ("Documento", .67, .10), ("Lote", .78, .10), ("Valor R$", .87, .10)]
    return [header + [
        ("03/11/2025", .08, .20), ("1234-5", .17, .20), ("Pix - Enviado", .25, .20),
        ("001103", .67, .20), ("13105", .78, .20), ("20,00 D", .87, .20),
        ("01/11 19:10 PESSOA FICTÍCIA", .25, .23),
        ("03/11/2025", .08, .30), ("1234-5", .17, .30), ("Saldo do dia", .25, .30),
        ("13105", .78, .30), ("500,00 C", .87, .30), ("Conta corrente + Rende Fácil", .25, .33),
        ("04/11/2025", .08, .80), ("1234-5", .17, .80), ("Pix - Enviado", .25, .80),
        ("001104", .67, .80), ("13105", .78, .80), ("20,00 D", .87, .80),
        ("bb.com.br - Central de Atendimento BB", .08, .92), ("Pág. 1", .90, .95),
    ], header + [
        ("04/11 08:15 OUTRA PESSOA FICTÍCIA", .25, .15),
        ("04/11/2025", .08, .25), ("1234-5", .17, .25), ("TED Pagamento a Lojistas", .25, .25),
        ("001105", .67, .25), ("14175", .78, .25), ("50,00 C", .87, .25),
        ("290 0000 00000000000000 EMPRESA FICTÍCIA", .25, .28),
        ("04/11/2025", .08, .40), ("1234-5", .17, .40), ("Saldo do dia", .25, .40),
        ("14175", .78, .40), ("530,01 C", .87, .40), ("Conta corrente + Rende Fácil", .25, .43),
        ("bb.com.br - Central de Atendimento BB", .08, .92), ("Pág. 2", .90, .95),
    ]]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


@pytest.mark.parametrize("mode", ["native", "ocr"])
def test_full_history_across_pages_keeps_values_references_and_provenance(mode):
    result = (pdf_parser.parse_pdf_transactions(grid_pdf(pages())) if mode == "native"
              else pdf_parser._parse_textract_text_pages(ocr(pages())))
    rows = {row.external_reference_id: row for row in result.canonical_transactions}
    assert len(result.transactions) == len(rows) == 3
    assert rows["001103"].amount == rows["001104"].amount == -20
    assert rows["001105"].amount == 50
    assert "01/11 19:10 PESSOA FICTÍCIA" in rows["001103"].description
    assert "04/11 08:15 OUTRA PESSOA FICTÍCIA" in rows["001104"].description
    assert "290 0000 00000000000000 EMPRESA FICTÍCIA" in rows["001105"].description
    assert rows["001104"].source_page == 1
    assert {page for page, _ in rows["001104"].source_line_refs} == {1, 2}
    for row in rows.values():
        assert "Saldo do dia" not in row.description
        assert "Rende Fácil" not in row.description
        assert "bb.com.br" not in row.description
        assert "Documento" not in row.description
    assert result.parse_metrics["balance_validation_status"] == "combined_cash_investment_not_comparable"
    assert result.parse_metrics["financial_controls_validated"] == 0


@pytest.mark.real_http
def test_complete_history_is_returned_and_exported_over_http(tmp_path):
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-bb-description"}
        response = client.post("/convert", data=owner, files={"file": ("synthetic.pdf", grid_pdf(pages()), "application/pdf")})
        assert response.status_code == 200, response.text
        assert "OUTRA PESSOA" in response.text
        analysis_id = response.json()["processing_id"]
        report = client.get(f"/report/{analysis_id}", params=owner)
        assert report.status_code == 200
        exported = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"})
        assert exported.status_code == 200
        assert "OUTRA PESSOA" in exported.text
        csv = client.post("/convert", data=owner, files={
            "file": ("synthetic.csv", b"date,description,amount\n2025-11-03,TESTE,10\n", "text/csv"),
        })
        assert csv.status_code == 200, csv.text
        assert client.get("/report/"+csv.json()["processing_id"], params=owner).status_code == 200
        assert client.post("/analyze").status_code == 405
        assert client.get("/report/nonexistent-description", params=owner).status_code == 404


@pytest.mark.parametrize("offset", [.001, .004, -.003])
def test_ocr_header_vertical_jitter_is_not_a_history_continuation(offset):
    original = pdf_parser._parse_textract_text_pages(ocr(pages()))
    shifted = [[(text, left, top + offset if text == "Histórico" else top)
                for text, left, top in page] for page in pages()]
    result = pdf_parser._parse_textract_text_pages(ocr(shifted))
    expected = {r.external_reference_id: (r.date, r.amount, r.description) for r in original.canonical_transactions}
    assert {r.external_reference_id: (r.date, r.amount, r.description) for r in result.canonical_transactions} == expected


@pytest.mark.parametrize("mode", ["native", "ocr"])
def test_next_page_metadata_stays_outside_history_and_ofx_identity(mode):
    from app.application.ofx_writer import build_ofx_statement

    def parse(source):
        return (pdf_parser.parse_pdf_transactions(grid_pdf(source)) if mode == "native"
                else pdf_parser._parse_textract_text_pages(ocr(source)))

    original = parse(pages())
    source = pages()
    source[1].append(("Agência 1234-5 Conta 000123-4 Cliente EXEMPLO FICTÍCIO", .25, .075))
    result = parse(source)
    assert [r.description for r in result.transactions] == [r.description for r in original.transactions]
    assert build_ofx_statement(result.transactions) == build_ofx_statement(original.transactions)
    row = next(r for r in result.canonical_transactions if r.external_reference_id == "001104")
    assert "OUTRA PESSOA" in row.description
    assert {page for page, _ in row.source_line_refs} == {1, 2}


def test_readable_dated_rows_do_not_require_a_repeated_table_header():
    from app.application.parsers.pdf.signed_statement_variants import BB_COMBINED, parse_variant

    result = parse_variant([
        "03/11/2025 1234-5 Pix - Enviado 001103 13105 20,00 D\nComplemento inicial",
        "04/11/2025 1234-5 Pix - Enviado 001104 13105 30,00 D\nComplemento seguinte",
    ], BB_COMBINED)
    assert [r.transaction.amount for r in result.rows] == [-20, -30]
    assert [r.transaction.description for r in result.rows] == [
        "Pix - Enviado Complemento inicial", "Pix - Enviado Complemento seguinte"]
