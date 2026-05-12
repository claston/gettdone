from app.application.models import NormalizedTransaction
from app.application.pdf_layout_inference import PdfLayoutInference
from app.application.pdf_parser import PdfParseResult

VIACREDI_TABULAR_BALANCE_OK = "\n".join(
    [
        "VIACREDI COOPERATIVA AILOS",
        "DATA DESCRICAO DOCUMENTO CREDITO (R$) DEBITO (R$) SALDO (R$)",
        "01/10/2024 PIX RECEBIDO CLIENTE 123 1.000,00 0,00 1.500,00",
        "02/10/2024 TARIFA PACOTE SERVICOS 456 0,00 12,34 1.487,66",
    ]
)


VIACREDI_TABULAR_BALANCE_FAIL = "\n".join(
    [
        "VIACREDI COOPERATIVA AILOS",
        "DATA DESCRICAO DOCUMENTO CREDITO (R$) DEBITO (R$) SALDO (R$)",
        "01/10/2024 PIX RECEBIDO CLIENTE 123 1.000,00 0,00 1.500,00",
        "02/10/2024 TARIFA PACOTE SERVICOS 456 0,00 12,34 1.400,00",
    ]
)


GROUPED_INLINE_MULTILINE_SAMPLE = "\n".join(
    [
        "TRANSAÇÕES DE 08 MAR A 08 ABR",
        "16 MAR 2026 Pagamento em 16 MAR −R$ 240,24",
        "25 MAR 2026",
        "Compra AGIR CONTABILIDADE E ASSESSORIA LTDA",
        "R$ 241,05",
    ]
)


UNICODE_MINUS_SINGLE_ROW_SAMPLE = "10 ABR 2026 Ajuste manual −R$ 10,00"


PDF_PARSE_METRICS_GROUPED_CANONICAL_OK = {
    "page_count": 1,
    "extracted_char_count": 72,
    "flattened_line_count": 3,
    "grouped_transactions_count": 2,
    "inline_candidates_count": 0,
    "inline_transactions_count": 0,
    "selected_parser": "grouped",
    "balance_consistency_checked": 1,
    "balance_consistency_failed": 0,
    "canonical_transactions_count": 2,
    "canonical_with_running_balance_count": 2,
    "canonical_with_external_reference_count": 2,
    "canonical_warning_count": 0,
    "canonical_balance_warning_count": 0,
    "canonical_warning_transactions_count": 0,
    "canonical_warning_types_count": 0,
    "canonical_warning_types": "",
    "canonical_warning_types_list": "",
    "canonical_running_balance_coverage_rate": 1.0,
    "canonical_external_reference_coverage_rate": 1.0,
    "canonical_warning_transaction_rate": 0.0,
}


PDF_PARSE_METRICS_INLINE_CANONICAL_EMPTY = {
    "page_count": 1,
    "extracted_char_count": 47,
    "flattened_line_count": 2,
    "grouped_transactions_count": 0,
    "inline_candidates_count": 2,
    "inline_transactions_count": 2,
    "selected_parser": "inline",
    "balance_consistency_checked": 0,
    "balance_consistency_failed": 0,
    "canonical_transactions_count": 2,
    "canonical_with_running_balance_count": 0,
    "canonical_with_external_reference_count": 0,
    "canonical_warning_count": 0,
    "canonical_balance_warning_count": 0,
    "canonical_warning_transactions_count": 0,
    "canonical_warning_types_count": 0,
    "canonical_warning_types": "",
    "canonical_warning_types_list": "",
    "canonical_running_balance_coverage_rate": 0.0,
    "canonical_external_reference_coverage_rate": 0.0,
    "canonical_warning_transaction_rate": 0.0,
}


def build_pdf_parse_result(
    *,
    transactions: list[NormalizedTransaction],
    layout_name: str,
    confidence: float,
    extracted_text: str,
    parse_metrics: dict[str, int | float | str],
) -> PdfParseResult:
    return PdfParseResult(
        transactions=transactions,
        layout=PdfLayoutInference(
            layout_name=layout_name,
            confidence=confidence,
            used_fallback=False,
        ),
        extracted_text=extracted_text,
        parse_metrics=parse_metrics,
    )
