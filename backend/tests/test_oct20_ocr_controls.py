import pytest

from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_parser import _parse_scanned_pdf_with_textract_text_pages


@pytest.mark.parametrize("module", ["app.api.conversion.conversion_observability",
                                    "app.application.conversion.document_conversion_pipeline"])
def test_financial_validation_has_a_parse_reason_instead_of_processing_or_missing_text(module):
    from importlib import import_module

    from app.application.errors import OcrFinancialAmbiguityError

    resolve = import_module(module)._resolve_error_observability
    assert resolve(PdfFinancialValidationError("A data está cortada."))[:2] == ("parse", "financial_validation_failed")
    assert resolve(OcrFinancialAmbiguityError("OCR leu valores ambíguos."))[:2] == ("parse", "ocr_financial_ambiguity")


def extraction(lines):
    return RawDocumentExtraction(
        "aws_textract",
        "synthetic",
        [
            ExtractedPage(
                1,
                lines=[
                    ExtractedLine(str(i), 1, i, text, bbox={"left": x, "top": y, "width": 0.07, "height": 0.007})
                    for i, (text, x, y) in enumerate(lines, 1)
                ],
            )
        ],
        {},
    )


def itau_receipt():
    return extraction(
        [
            ("BCO ITAU EXTRATO CTA CORRENTE", 0.1, 0.04),
            ("DIAHISTORICO", 0.1, 0.08),
            ("VALOR", 0.6, 0.08),
            ("31 SALDO ANTERIOR 31/08", 0.1, 0.10),
            ("1.000,00", 0.6, 0.10),
            ("SETEMBRO/2026", 0.1, 0.12),
            ("01 PIX ENVIADO EXEMPLO 9127", 0.1, 0.15),
            ("100,00-", 0.6, 0.15),
            ("01 REDE MAST CD000001 9026", 0.1, 0.18),
            ("50,00", 0.6, 0.18),
            ("01 SDO CTA/APL AUTOMATICAS", 0.1, 0.21),
            ("950,00", 0.6, 0.21),
        ]
    )


def test_itau_day_only_ocr_does_not_export_only_rows_with_embedded_pix_dates():
    result = _parse_scanned_pdf_with_textract_text_pages(itau_receipt())
    assert [r.amount for r in result.transactions] == [-100, 50]
    assert [r.date for r in result.transactions] == ["2026-09-01", "2026-09-01"]
    assert result.parse_metrics["verified_opening_balance"] == 1000
    assert result.parse_metrics["verified_closing_balance"] == 950


def test_itau_day_only_ocr_missing_amount_cannot_fall_back_to_partial_export():
    ext = itau_receipt()
    ext.pages[0].lines.pop(8)
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_sicredi_vector_ocr_with_explicit_continuation_is_not_a_complete_statement():
    ext = extraction(
        [
            ("SICREDI EXTRATO DE CONTA CORRENTE", 0.1, 0.05),
            ("DATA DOCUMENTO HISTORICO DEBITO CREDITO SALDO", 0.1, 0.1),
            ("Continua na pagina 002", 0.1, 0.9),
        ]
    )
    with pytest.raises(PdfFinancialValidationError, match="página|completo"):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_caixa_obscured_date_cannot_drop_row_and_create_opening_from_a_later_balance():
    ext = extraction(
        [
            ("SAC CAIXA: 0800 726 0101", 0.1, 0.9),
            ("0/07/2026", 0.1, 0.1),
            ("000000", 0.25, 0.1),
            ("REM BASICA", 0.35, 0.1),
            ("2,75 C", 0.65, 0.1),
            ("53.089,87 C", 0.8, 0.1),
            ("20/07/2026", 0.1, 0.14),
            ("000000", 0.25, 0.14),
            ("CRED JUROS", 0.35, 0.14),
            ("8,63 C", 0.65, 0.14),
            ("53.098,50 C", 0.8, 0.14),
        ]
    )
    with pytest.raises(PdfFinancialValidationError):
        _parse_scanned_pdf_with_textract_text_pages(ext)


def test_stone_fragment_starting_with_a_credit_does_not_assume_its_fee_is_in_the_same_file():
    from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts

    page = "\n".join(
        [
            "stone",
            "Extrato de conta corrente",
            "Página 10 de 34",
            "Período: de 01/06/2026 a 30/06/2026",
            "DATA TIPO DESCRIÇÃO VALOR SALDO CONTRAPARTE",
            "18/06/26 Entrada Pix Maquininha R$ 58,99 R$ 1.910,29",
            "18/06/26 Saída Tarifa R$ 0,40 R$ 1.851,68",
            "18/06/26 Entrada Pix Maquininha R$ 61,99 R$ 1.851,68",
            "18/06/26 Saída Tarifa R$ 0,38 R$ 1.790,09",
            "18/06/26 Entrada Pix Maquininha R$ 58,98 R$ 1.790,09",
        ]
    )
    result = _parse_pdf_transactions_from_page_texts([page])
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert "partial_statement" in result.canonical_transactions[0].warnings


def test_caixa_keeps_all_movements_and_uses_daily_balance_as_control():
    ext = extraction(
        [
            ("CAIXA", 0.1, 0.03),
            ("20/07/2026", 0.1, 0.1),
            ("000000", 0.25, 0.1),
            ("REM BASICA", 0.35, 0.1),
            ("2,75 C", 0.65, 0.1),
            ("1.002,75 C", 0.8, 0.1),
            ("20/07/2026", 0.1, 0.14),
            ("000000", 0.25, 0.14),
            ("CRED JUROS", 0.35, 0.14),
            ("8,63 C", 0.65, 0.14),
            ("1,011,38 C", 0.8, 0.14),
            ("20/07/2026", 0.1, 0.18),
            ("000000", 0.25, 0.18),
            ("SALDO DIA", 0.35, 0.18),
            ("0,00 C", 0.65, 0.18),
            ("1.011,38 C", 0.8, 0.18),
        ]
    )
    result = _parse_scanned_pdf_with_textract_text_pages(ext)
    assert [t.amount for t in result.transactions] == [2.75, 8.63]
    assert result.parse_metrics["verified_opening_balance"] == 1000
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_warning_count"] == 1


def test_ocr_validation_failure_records_that_textract_already_ran(monkeypatch):
    from app.application import pdf_parser

    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setattr(pdf_parser, "_read_native_pdf_page_texts", lambda raw: [])
    monkeypatch.setattr(pdf_parser.TextractGateway, "__init__", lambda self: None)
    monkeypatch.setattr(
        pdf_parser.TextractGateway,
        "analyze_pdf",
        lambda self, **kwargs: {
            "document_hash": "synthetic",
            "page_count": 1,
            "blocks": [],
            "metrics": {"textract_used": 1, "textract_mode": "text"},
        },
    )
    monkeypatch.setattr(
        pdf_parser, "_parse_scanned_pdf_with_textract_text_pages", lambda ext: (_ for _ in ()).throw(PdfFinancialValidationError())
    )
    with pytest.raises(PdfFinancialValidationError) as failure:
        pdf_parser.parse_pdf_transactions(b"synthetic")
    assert failure.value._parse_observability["textract_attempted"] == 1
    assert failure.value._parse_observability["textract_used"] == 1
