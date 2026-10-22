from dataclasses import replace

import pytest

from app.application import pdf_parser
from app.application.bank_identity import resolve_bank_name
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.parsers.pdf.layout_specific.contract import LayoutSpecificParseContext
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.structured_statements import (
    bradesco_monthly_extraction,
    conta_azul_pages,
    cresol_inactive_pages,
    santander_pages,
    sumup_pages,
)


def parse(pages):
    return pdf_parser._parse_pdf_transactions_from_page_texts(
        pages, preserve_layout_spacing=True, context=LayoutSpecificParseContext(is_native_text=True),
    )


def test_sumup_native_date_time_columns_preserve_descriptions_and_closing_balance(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in sumup_pages()))
    result = build_default_conversion_pipeline().run(filename="sumup-extrato.pdf", raw_bytes=raw, analysis_id="an_sumup")
    analysis = result.analysis_data
    assert analysis.layout_inference_name == "sumup_extrato_periodo_data_hora_v1"
    assert analysis.bank_name == "SumUp Bank"
    assert analysis.transactions_total == 4
    assert analysis.opening_balance == 100
    assert analysis.closing_balance == 50
    assert analysis.pdf_processing_metrics["textract_attempted"] == 0
    assert analysis.pdf_processing_metrics["native_text_detected"] == 1
    assert analysis.pdf_processing_metrics["financial_controls_validated"] == 0
    assert analysis.pdf_processing_metrics["balance_validation_status"] == "opening_inferred_from_closing"
    assert analysis.pdf_processing_metrics["export_recommendation"] == "review_recommended"
    rows = analysis.report_transactions
    assert any("PIX RECEBIDO COMERCIO TESTE" in row.description for row in rows)
    assert any("DESTINATARIO TESTE" in row.description for row in rows)
    assert not any(row.description == "20 23" for row in rows)


def test_santander_printed_grouped_variant_reads_movements_after_contact_block():
    result = parse(santander_pages())
    assert result.layout.layout_name == "santander_internet_banking_empresarial_periodo_agrupado_v1"
    assert len(result.transactions) == 4
    assert sorted(row.amount for row in result.transactions) == [-100, -50, 50, 100]
    assert {row.date for row in result.transactions} == {"2026-09-25", "2026-09-30"}
    assert result.parse_metrics["statement_row_coverage_rate"] == 1
    assert result.parse_metrics["financial_controls_validated"] == 0


def test_santander_native_grouped_export_does_not_require_layout_text(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    monkeypatch.setattr(pdf_parser, "_read_layout_native_pdf_page_texts", lambda _: [])
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in santander_pages()))
    result = pdf_parser.parse_pdf_transactions(raw)
    assert len(result.transactions) == 4
    assert result.parse_metrics["native_text_detected"] == 1
    assert result.parse_metrics["textract_attempted"] == 0


def test_cresol_native_inactive_template_does_not_export_opening_as_movement(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in cresol_inactive_pages()))
    result = build_default_conversion_pipeline().run(filename="cresol-sem-movimento.pdf", raw_bytes=raw, analysis_id="an_inactive")
    analysis = result.analysis_data
    assert analysis.layout_inference_name == "cresol_posicao_consolidada_sem_movimento_v1"
    assert analysis.bank_name == "Cresol"
    assert analysis.transactions_total == 0
    assert analysis.opening_balance == analysis.closing_balance == 30.93
    assert analysis.pdf_processing_metrics["statement_no_activity"] == 1
    assert analysis.pdf_processing_metrics["textract_attempted"] == 0


def test_conta_azul_report_validates_printed_totals_and_keeps_description_columns():
    result = parse(conta_azul_pages())
    assert result.layout.layout_name == "conta_azul_relatorio_extrato_valor_saldo_v1"
    assert len(result.transactions) == 2
    assert result.transactions[0].description == "Pix recebido CLIENTE TESTE"
    assert "COMPLEMENTO DA DESCRICAO" in result.transactions[1].description
    assert "Receitas de Vendas" not in result.transactions[0].description
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 190
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert resolve_bank_name(layout_inference_name=result.layout.layout_name, extracted_text=result.extracted_text) is None


def test_bradesco_monthly_ocr_keeps_day_columns_and_suffix_debit_sign():
    result = pdf_parser._parse_textract_text_pages(bradesco_monthly_extraction())
    assert result.layout.layout_name == "bradesco_extrato_mensal_dia_historico_valor_ocr_v1"
    assert [row.date for row in result.transactions] == ["2026-09-11", "2026-09-15"]
    assert [row.amount for row in result.transactions] == [50, -20]
    assert result.canonical_transactions[0].external_reference_id == "1234567"
    assert "ESTABELECIMENTO TESTE" in result.transactions[1].description
    assert result.parse_metrics["verified_closing_balance"] == 130
    assert result.parse_metrics["financial_controls_validated"] == 1
    assert all(row.source_regions for row in result.canonical_transactions)


@pytest.mark.parametrize("factory,before,after", [
    (sumup_pages, "30,00 Pix recebido", "ilegível Pix recebido"),
    (sumup_pages, "- 20,00 Pix enviado", "20,00 Pix enviado"),
    (sumup_pages, "29/09/2026 11:00", "31/09/2026 11:00"),
    (santander_pages, "PIX ENVIADO DEBITO R$ 50,00", "PIX ENVIADO R$ 50,00"),
    (santander_pages, "CREDITO R$ 50,00", "CREDITO R$ ilegível"),
    (conta_azul_pages, "Realizadas (R$) 120,00", "Realizadas (R$) 120,01"),
    (conta_azul_pages, "- 30,00", "- 31,00"),
    (conta_azul_pages, "Página 1 de 1", "Página 1 de 2"),
    (conta_azul_pages, "R$ 190,00", "R$ 191,00"),
    (cresol_inactive_pages, "+ R$ 30,93Saldo", "+ R$ 31,93Saldo"),
    (cresol_inactive_pages, "Página 1 de 1", "Página 1 de 2"),
])
def test_known_structures_reject_corrupted_values_and_missing_pages(factory, before, after):
    pages = factory()
    assert any(before in page for page in pages)
    with pytest.raises(PdfFinancialValidationError):
        parse([page.replace(before, after) for page in pages])


def test_cresol_inactivity_declaration_cannot_hide_an_actual_movement():
    with pytest.raises(PdfFinancialValidationError):
        parse([cresol_inactive_pages()[0] + "\n02/09/2026 PIX + R$ 10,00"])


@pytest.mark.parametrize("value", ["R$ 10,00", "VALOR 10,00", "PIX 10,00"])
def test_cresol_inactivity_does_not_accept_unexplained_money(value):
    with pytest.raises(PdfFinancialValidationError):
        parse([cresol_inactive_pages()[0] + "\n" + value])


@pytest.mark.parametrize("mode", ["text", "tables"])
def test_bradesco_gateway_uses_geometry_in_both_textract_modes(monkeypatch, mode):
    from types import SimpleNamespace

    extraction = bradesco_monthly_extraction()
    monkeypatch.setattr(pdf_parser, "TextractGateway", lambda: SimpleNamespace(
        analyze_pdf=lambda **kwargs: {"metrics": {"textract_mode": mode}},
    ))
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: extraction)
    result = pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic")
    assert len(result.transactions) == 2
    assert result.parse_metrics["textract_used"] == 1
    assert result.parse_metrics["extraction_provider"] == "aws_textract"
    assert result.source_extraction is extraction


@pytest.mark.parametrize("mutation", ["amount", "date", "unknown_value", "unreadable_page", "missing_page"])
def test_bradesco_ocr_rejects_inconsistent_or_unreadable_ledger(mutation):
    extraction = bradesco_monthly_extraction()
    page = extraction.pages[0]
    lines = list(page.lines)
    if mutation == "amount":
        lines = [replace(line, text="21,00-") if line.text == "20,00-" else line for line in lines]
    elif mutation == "date":
        lines = [replace(line, text="31") if line.text == "15" else line for line in lines]
    elif mutation == "unknown_value":
        lines.append(replace(lines[14], id="orphan", text="10,00", line_index=40,
                             bbox={"left": .72, "top": .28, "width": .1, "height": .01}))
    else:
        lines = [replace(line, text="Página: 1 de 2") if line.text == "Página: 1 de 1" else line for line in lines]
    pages = [replace(page, lines=lines)]
    if mutation == "unreadable_page":
        pages.append(replace(page, page_number=2, lines=[
            replace(lines[0], text="bradesco", page_number=2),
            replace(lines[-1], text="Página: 2 de 2", page_number=2),
        ]))
    with pytest.raises(PdfFinancialValidationError) as error:
        pdf_parser._parse_textract_text_pages(replace(extraction, pages=pages))
    assert error.value._parse_observability["layout_inference_name"] == "bradesco_extrato_mensal_dia_historico_valor_ocr_v1"
