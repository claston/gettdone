from __future__ import annotations

import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.errors import UnsupportedDocumentContentError
from app.application.pdf_parser import parse_pdf_transactions

BILLING_REPORT_TEXT = """
RELATÓRIO DE FATURAMENTO EMISSÃO: 21/09/2026
EMPRESA EXEMPLO LTDA
ENDEREÇO RUA EXEMPLO 123
CNPJ: 00.000.000/0001-00
PERÍODO: 01/09/2025 A 31/08/2026
MÊS ANO CANCELAMENTOS FATURAMENTO IMPOSTOS ALÍQUOTA
SETEMBRO 2025 0,00 26.817,31 2.571,95 9,5906
OUTUBRO 2025 0,00 48.028,23 4.572,74 9,5209
NOVEMBRO 2025 0,00 29.917,72 2.832,79 9,4685
DEZEMBRO 2025 0,00 42.138,96 3.938,88 9,3473
JANEIRO 2026 0,00 27.414,89 2.548,00 9,2942
FEVEREIRO 2026 0,00 23.536,96 2.153,95 9,1512
MARÇO 2026 0,00 29.475,49 2.666,61 9,0469
ABRIL 2026 0,00 26.188,94 2.359,60 9,0099
MAIO 2026 0,00 30.212,45 2.681,32 8,8748
JUNHO 2026 0,00 28.259,26 2.477,66 8,7675
JULHO 2026 0,00 37.730,98 3.314,41 8,7843
AGOSTO 2026 0,00 38.553,80 3.417,94 8,8653
TOTAIS 0,00 388.274,99
CONTADOR EXEMPLO REGISTRO NO C.R.C.: 000000/O8
"""


def test_parser_rejects_billing_report_without_retrying_ocr(monkeypatch) -> None:
    ocr_calls = 0

    def extract_ocr(*args, **kwargs):
        nonlocal ocr_calls
        ocr_calls += 1
        return []

    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [BILLING_REPORT_TEXT])
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "extract_pdf_page_texts_with_ocr", extract_ocr)

    with pytest.raises(UnsupportedDocumentContentError) as exc_info:
        parse_pdf_transactions(b"%PDF synthetic")

    assert str(exc_info.value) == (
        "Este arquivo é um relatório de faturamento, não um extrato bancário. "
        "Envie o extrato da conta em PDF, CSV, XLSX ou OFX."
    )
    assert exc_info.value.document_type == "billing_report"
    assert ocr_calls == 0
    assert getattr(exc_info.value, "_parse_observability", {})["ocr_retry_skip_reason"] == (
        "unsupported_document_type"
    )


def test_parser_does_not_reject_statement_with_billing_report_in_transaction_description(monkeypatch) -> None:
    statement_text = """
    EXTRATO DE CONTA CORRENTE
    DATA DESCRIÇÃO VALOR
    01/09/2026 PAGAMENTO RELATÓRIO DE FATURAMENTO -100,00
    02/09/2026 PIX RECEBIDO 250,00
    """
    monkeypatch.setattr(pdf_parser_module, "_read_native_pdf_page_texts", lambda raw_bytes: [statement_text])

    result = parse_pdf_transactions(b"%PDF synthetic")

    assert len(result.transactions) == 2
