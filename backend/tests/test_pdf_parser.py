import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import parse_pdf_transactions


def test_parse_pdf_transactions_handles_inline_and_multiline_amount_rows(monkeypatch) -> None:
    page_text = "\n".join(
        [
            "TRANSAÇÕES DE 08 MAR A 08 ABR",
            "16 MAR 2026 Pagamento em 16 MAR −R$ 240,24",
            "25 MAR 2026",
            "Compra AGIR CONTABILIDADE E ASSESSORIA LTDA",
            "R$ 241,05",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert len(result.canonical_transactions) == 2
    assert result.parse_metrics["selected_parser"] == "grouped"
    assert result.transactions[0].date == "2026-03-16"
    assert result.transactions[0].amount == -240.24
    assert result.transactions[1].date == "2026-03-25"
    assert result.transactions[1].amount == -241.05
    assert result.canonical_transactions[0].layout_name == result.layout.layout_name
    assert result.canonical_transactions[0].confidence == result.layout.confidence
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 2


def test_parse_pdf_transactions_parses_unicode_minus_with_currency_prefix(monkeypatch) -> None:
    page_text = "10 ABR 2026 Ajuste manual −R$ 10,00"
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 1
    assert len(result.canonical_transactions) == 1
    assert result.transactions[0].date == "2026-04-10"
    assert result.transactions[0].amount == -10.0
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 1


def test_parse_pdf_transactions_does_not_run_ocr_fallback(monkeypatch) -> None:
    monkeypatch.setenv("PDF_OCR_ENABLED", "true")
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [])

    with pytest.raises(InvalidFileContentError, match="OCR fallback is disabled"):
        parse_pdf_transactions(b"%PDF synthetic")


def test_parse_pdf_transactions_uses_declarative_credit_debit_columns(monkeypatch) -> None:
    page_text = "\n".join(
        [
            "VIACREDI COOPERATIVA AILOS",
            "DATA DESCRICAO DOCUMENTO CREDITO (R$) DEBITO (R$) SALDO (R$)",
            "01/10/2024 PIX RECEBIDO CLIENTE 123 1.000,00 0,00 1.500,00",
            "02/10/2024 TARIFA PACOTE SERVICOS 456 0,00 12,34 1.487,66",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_text])
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="viacredi_ailos_extrato_conta_corrente_v1",
            confidence=0.9,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
    assert result.parse_metrics["selected_parser"] == "tabular"
    assert result.layout.layout_name == "viacredi_ailos_extrato_conta_corrente_v1"
    assert result.transactions[0].amount == 1000.0
    assert result.transactions[0].description == "PIX RECEBIDO CLIENTE 123"
    assert result.transactions[1].amount == -12.34
    assert result.transactions[1].description == "TARIFA PACOTE SERVICOS 456"
    assert result.canonical_transactions[0].bank_name == "Viacredi"
    assert result.canonical_transactions[0].layout_name == "viacredi_ailos_extrato_conta_corrente_v1"
    assert result.canonical_transactions[0].source_page == 1
    assert result.canonical_transactions[0].source_line == 3
    assert result.canonical_transactions[0].external_reference_id == "123"
    assert result.canonical_transactions[0].running_balance == 1500.0
    assert result.canonical_transactions[1].external_reference_id == "456"
    assert result.canonical_transactions[1].running_balance == 1487.66
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["canonical_transactions_count"] == 2
    assert result.parse_metrics["canonical_with_running_balance_count"] == 2
    assert result.parse_metrics["canonical_with_external_reference_count"] == 2
    assert result.parse_metrics["canonical_warning_count"] == 0
    assert result.parse_metrics["canonical_balance_warning_count"] == 0


def test_parse_pdf_transactions_marks_balance_consistency_warning(monkeypatch) -> None:
    page_text = "\n".join(
        [
            "VIACREDI COOPERATIVA AILOS",
            "DATA DESCRICAO DOCUMENTO CREDITO (R$) DEBITO (R$) SALDO (R$)",
            "01/10/2024 PIX RECEBIDO CLIENTE 123 1.000,00 0,00 1.500,00",
            "02/10/2024 TARIFA PACOTE SERVICOS 456 0,00 12,34 1.400,00",
        ]
    )
    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts", lambda raw_bytes: [page_text])
    monkeypatch.setattr(
        pdf_parser_module,
        "infer_pdf_layout",
        lambda text: pdf_parser_module.PdfLayoutInference(
            layout_name="viacredi_ailos_extrato_conta_corrente_v1",
            confidence=0.9,
            used_fallback=False,
        ),
    )

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.canonical_transactions) == 2
    assert result.parse_metrics["balance_consistency_checked"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert "balance_consistency_failed" in result.canonical_transactions[1].warnings
    assert result.parse_metrics["canonical_warning_count"] == 1
    assert result.parse_metrics["canonical_balance_warning_count"] == 1
