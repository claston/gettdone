"""Unreliable hidden money/sign glyphs require fresh visual evidence."""
from io import BytesIO
from unittest.mock import Mock

import pytest
from pypdf import PdfReader

from app.application import pdf_parser
from app.application.errors import MaxPagesPerFileExceededError, OcrFinancialAmbiguityError
from app.application.pdf_representation_classifier import classify_pdf_representation
from tests.test_embedded_ocr_quality import layered_pdf, visual_result

SIGNED_CASH = "\n".join([
    "EXTRATO DE CONTA CORRENTE",
    "DATA HISTORICO VALOR",
    "01/07 PIX RECEBIDO R$ 500,00C",
    "02/07 PIX EMITIDO R$ 20,00D",
    "02/07 PIX EMITIDO R$ 18,440",
    "02/07 SALDO DO DIA R$ 461,56C",
])


def test_hidden_signed_cash_with_three_fraction_digits_requires_visual_evidence():
    result = classify_pdf_representation(layered_pdf(SIGNED_CASH))
    assert result.requires_visual_extraction
    assert "malformed_signed_cash_values" in result.evidence


@pytest.mark.parametrize("invisible,raster", [(False, True), (True, False)])
def test_visible_or_non_raster_text_is_not_treated_as_broken_hidden_ocr(invisible, raster):
    assert not classify_pdf_representation(layered_pdf(SIGNED_CASH, invisible=invisible, raster=raster)).requires_visual_extraction


@pytest.mark.parametrize("text", [
    SIGNED_CASH.replace("18,440", "18,44D"),
    "Relatório de cotação R$ 18,440\nQuantidade 1,250\nPreço unitário de referência para os produtos do mês",
])
def test_healthy_cash_and_other_reports_with_three_decimal_precision_do_not_force_ocr(text):
    assert not classify_pdf_representation(layered_pdf(text)).requires_visual_extraction


def test_reread_discards_hidden_layer_without_rewriting_its_money_or_sign(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    original = layered_pdf(SIGNED_CASH)
    result = pdf_parser.parse_pdf_transactions(original)
    gateway.assert_called_once()
    assert "18,440" in PdfReader(BytesIO(original)).pages[0].extract_text()
    assert not PdfReader(BytesIO(gateway.call_args.args[0])).pages[0].extract_text()
    assert result.parse_metrics["native_text_layer_ignored"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"


def test_no_visual_reader_never_guesses_a_sign_from_three_fraction_digits(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    with pytest.raises(OcrFinancialAmbiguityError):
        pdf_parser.parse_pdf_transactions(layered_pdf(SIGNED_CASH))


def test_reread_keeps_configured_ocr_page_limit(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    with pytest.raises(MaxPagesPerFileExceededError):
        pdf_parser.parse_pdf_transactions(layered_pdf(SIGNED_CASH, pages=2), max_ocr_pages=1)
    gateway.assert_not_called()
