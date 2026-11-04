"""Fictional reproductions for the second recovery batch."""

from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.errors import OcrFinancialAmbiguityError, PdfFinancialValidationError
from app.application.native_text_quality import assess_native_text_quality
from tests.test_financial_document_events import convert_report, report_pages
from tests.test_oct28_new_statement_failures import caixa_tilted_rows
from tests.test_pdf_parser_digital_credit_card import native_pdf


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def test_cdb_application_with_only_printed_principal_preserves_investment_meaning():
    pages = [report_pages("investment")[0].replace("50,00 0,00 0,00 0,00\nTotal", "50,00\nTotal")]
    result = convert_report("investment", pages)
    doc = result.financial_document
    assert result.transactions_total == 2
    assert doc["events"][0]["amounts"] == {"principal": "50.00"}
    assert doc["events"][0]["cash_effect"] == "-50.00"
    assert len(doc["observations"]) == 2
    assert all(v["status"] == "passed" for v in doc["validations"])
    assert doc["export"]["available_formats"] == ["csv", "xlsx"]


def test_cdb_principal_only_does_not_allow_truncated_positions():
    pages = [report_pages("investment")[0].replace("100,00 100,00 0,00 0,00 0,00 100,00 0,00\nTotal", "100,00\nTotal")]
    with pytest.raises(PdfFinancialValidationError):
        convert_report("investment", pages)


def test_cdb_native_metadata_values_before_labels_are_not_investment_rows():
    page = report_pages("investment")[0].replace("Período de:01/08/2026 até 31/08/2026",
        "01/08/2026 31/08/2026atéPeríodo de:\n29/09/2026\nData emissão do\nextrato:")
    result = convert_report("investment", [page])
    assert result.transactions_total == 2
    assert len(result.financial_document["observations"]) == 2


def test_caixa_missing_opening_keeps_explicit_movements_without_inventing_balance():
    source = caixa_tilted_rows()
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[line for line in page.lines if not .19 < line.bbox["top"] < .21])])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [row.amount for row in result.transactions] == [-20, 10]
    assert [row.running_balance for row in result.canonical_transactions] == [80, 90]
    assert "verified_opening_balance" not in result.parse_metrics
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


APP = """Extrato
DEB PIX CH 27/02/2026
001234 20,00 D
Saldo 80,00 C
CRE PIX CH 27/02/2026
001235 50,00 C
Saldo 130,00 C
SALDO DIA 27/02/2026
000000 130,00 C
Saldo 130,00 C
CRE PIX CH 02/03/2026
001236 10,00 C
Saldo 140,00 C
SALDO DIA 02/03/2026
000000 140,00 C
Saldo 140,00 C
Gerar Arquivo PDF
Voltar
Extrato por período
"""


def test_caixa_app_multimonth_cards_preserve_signed_movements_and_exclude_daily_controls():
    result = pdf_parser.parse_pdf_transactions(native_pdf([APP]))
    assert [row.amount for row in result.transactions] == [-20, 50, 10]
    assert [row.date for row in result.transactions] == ["2026-02-27", "2026-02-27", "2026-03-02"]
    assert [row.running_balance for row in result.canonical_transactions] == [80, 130, 140]
    assert result.parse_metrics["financial_controls_validated"] == 0
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert all(row.source_page == 1 for row in result.canonical_transactions)


@pytest.mark.parametrize("before,after", [("10,00 C", "10,00"), ("10,00 C", "1?,00 C"), ("02/03/2026", "02/??/2026")])
def test_caixa_app_unreadable_later_movement_is_not_dropped(before, after):
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(native_pdf([APP.replace(before, after)]))


def test_non_whitespace_control_glyphs_request_visual_extraction_on_any_page():
    corrupted = "Data Descrição Valor Saldo\n" + "\x01\x02\x03\x04\x05\x06\x07\x08" * 30
    quality = assess_native_text_quality(b"", ["Extrato legível 100,00 " * 100, corrupted])
    assert quality.requires_visual_extraction
    assert quality.metrics()["native_text_quality_reason"] == "unmapped_font_glyphs"


def test_ordinary_pdf_whitespace_and_few_control_glyphs_remain_native():
    quality = assess_native_text_quality(b"", [("Extrato\t01/02/2026\r\nCrédito 100,00\f" * 100) + "\x01\x02"])
    assert not quality.requires_visual_extraction


def btg_cash_pages():
    return ["""Extrato da Conta Investimento
btg pactual
Período de 01/10/26 a 31/10/26
Distribuição da Carteira
CONTA CORRENTE
100.00%
Sumário Distribuição em 31/10/26
Mercados
Saldo Bruto R$ 30/09/26
Saldo Líquido R$ 30/09/26 2
Saldo Bruto R$ 31/10/26
Saldo Líquido R$ 31/10/26 1
Conta Corrente
-
-
30,00
30,00
Total
-
-
30,00
30,00
Conta corrente Posição
Data
Valor financeiro R$
31/10/26
30,00
1
""", """Extrato da Conta Investimento
btg pactual
Período de 01/10/26 a 31/10/26
Conta corrente - Movimentação
Data
Descrição
Movimentação R$
Saldo conta investimentos R$
01/10/26
Saldo Anterior
-
19/10/26
TRANSFERÊNCIA A CRÉDITO VIA PIX CLIENTE FICTÍCIO
50,00
50,00
19/10/26
ENVIO TRANSFERÊNCIA FORNECEDOR FICTÍCIO
-20,00
30,00
31/10/26
Saldo Final + Rendimento Provisionado de Saldo Remunerado
30,00
Total de
50,00
Créditos
Total de
-20,00
Débitos
2
""", "Avisos: Fundo de Investimento não é garantido pelo FGC."]


def test_btg_cash_only_investment_account_does_not_require_fund_lots():
    result = pdf_parser.parse_pdf_transactions(native_pdf(btg_cash_pages()))
    assert [row.amount for row in result.transactions] == [50, -20]
    doc = result.financial_document
    assert doc["document_type"] == "investment_statement"
    assert len(doc["events"]) == 2
    assert all(v["status"] == "passed" for v in doc["validations"])
    assert doc["export"]["available_formats"] == ["csv", "xlsx"]
    assert all(row.source_page == 2 for row in result.canonical_transactions)
    assert doc["metadata"].get("opening_balance") is None


def cash_extraction():
    from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction

    pages = ["btg pactual\nExtrato da\nConta Investimento\nConta: 000001", "Índice\nSumário\nConta corrente", *btg_cash_pages()]
    return RawDocumentExtraction("aws_textract", "synthetic", [
        ExtractedPage(page, lines=[ExtractedLine(f"p{page}l{line}", page, line, text)
                                  for line, text in enumerate(content.splitlines(), 1)])
        for page, content in enumerate(pages, 1)], {})


@pytest.mark.parametrize("mode", ["text", "analysis"])
def test_btg_ocr_keeps_cash_ledger_before_appendix_segmentation(mode, monkeypatch):
    from tests.ocr_conversion_http_helpers import configure_ocr

    raw = configure_ocr(monkeypatch, cash_extraction(), mode)
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [row.amount for row in result.transactions] == [50, -20]
    assert all(row.source_page == 4 for row in result.canonical_transactions)
    assert result.financial_document["evidence"] == ["btg_investment_cash_ledger"]


@pytest.mark.parametrize("before,after", [
    ("-20,00\n30,00", "-2?,00\n30,00"),
    ("19/10/26\nENVIO", "??/10/26\nENVIO"),
    ("Débitos\n2", "Débitos\n20/10/26\nMOVIMENTO NÃO LIDO\n10,00\n40,00\n2"),
])
def test_btg_unreadable_or_unparsed_later_records_are_refused(before, after):
    pages = [page.replace(before, after) for page in btg_cash_pages()]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser.parse_pdf_transactions(native_pdf(pages))


def test_btg_mismatching_totals_preserve_values_with_review():
    pages = [page.replace("Total de\n50,00", "Total de\n60,00") for page in btg_cash_pages()]
    result = pdf_parser.parse_pdf_transactions(native_pdf(pages))
    assert [row.amount for row in result.transactions] == [50, -20]
    assert any(v["status"] == "failed" for v in result.financial_document["validations"])
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_visual_recovery_cannot_silently_omit_later_pages_with_transaction_candidates():
    result = pdf_parser.parse_pdf_transactions(native_pdf([APP]))
    result = replace(result, source_page_texts=(APP, "02/03/2026\nPIX\n10,00\n150,00\n03/03/2026\nPIX\n20,00\n170,00"))
    with pytest.raises(OcrFinancialAmbiguityError):
        pdf_parser._original_visual_provenance(result, b"original", b"visual")


def test_visual_recovery_allows_nondated_annex_without_transactions():
    result = pdf_parser.parse_pdf_transactions(native_pdf([APP]))
    result = replace(result, source_page_texts=(APP, "Ouvidoria e informações gerais\nTaxa anual: 10,00"))
    checked = pdf_parser._original_visual_provenance(result, b"original", b"visual")
    assert len(checked.transactions) == 3


def test_caixa_without_opening_still_requires_explicit_first_movement_sign():
    source = caixa_tilted_rows()
    page = source.pages[0]
    lines = [replace(line, text=line.text.replace("20,00 D", "20,00"))
             for line in page.lines if not .19 < line.bbox["top"] < .21]
    with pytest.raises(PdfFinancialValidationError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))


@pytest.mark.parametrize("enabled", [True, False])
def test_control_glyph_native_layer_uses_visual_route_or_refuses(monkeypatch, enabled):
    from unittest.mock import Mock

    from tests.test_embedded_ocr_quality import layered_pdf, visual_result

    raw = layered_pdf("\x01\x02\x03\x04\x05\x06\x07\x08" * 50, raster=False, invisible=False)
    monkeypatch.setenv("TEXTRACT_ENABLED", str(enabled).lower())
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    if enabled:
        result = pdf_parser.parse_pdf_transactions(raw)
        assert result.parse_metrics["native_text_quality_reason"] == "unmapped_font_glyphs"
        assert result.parse_metrics["native_text_layer_ignored"] == 1
        gateway.assert_called_once()
        assert gateway.call_args.args[0] != raw
    else:
        with pytest.raises(OcrFinancialAmbiguityError):
            pdf_parser.parse_pdf_transactions(raw)
        gateway.assert_not_called()


@pytest.mark.real_http
@pytest.mark.parametrize("kind", ["caixa", "btg", "csv"])
def test_second_batch_real_http_conversion_reports_and_negative_path(kind, tmp_path):
    from tests.ocr_conversion_http_helpers import public_client

    raw = native_pdf([APP] if kind == "caixa" else btg_cash_pages())
    filename, mime, count = "synthetic.pdf", "application/pdf", 3 if kind == "caixa" else 2
    if kind == "csv":
        filename, mime, count = "synthetic.csv", "text/csv", 1
        raw = b"date,description,amount\n2026-08-01,PIX,100\n"
    with public_client(tmp_path) as client:
        owner = {"anonymous_fingerprint": "fp-recovery-second"}
        response = client.post("/convert", data=owner, files={"file": (filename, raw, mime)})
        assert response.status_code == 200, response.text
        analysis = response.json()["analysis"]
        assert analysis["transactions_total"] == count
        analysis_id = analysis["analysis_id"]
        assert client.get(f"/report/{analysis_id}", params=owner).status_code == 200
        for fmt in ("csv", "xlsx"):
            exported = client.get(f"/convert-report/{analysis_id}", params={**owner, "format": fmt})
            assert exported.status_code == 200, exported.text
        if kind == "btg":
            assert client.get(f"/convert-report/{analysis_id}", params={**owner, "format": "ofx"}).status_code == 400
        assert client.get("/report/an_second_missing", params=owner).status_code == 404
        assert client.post("/analyze").status_code == 405
