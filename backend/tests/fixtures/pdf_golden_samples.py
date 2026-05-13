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

INLINE_MINIMAL_SAMPLE = "10/04 Ajuste manual 10,00"
ITAU_INLINE_MINIMAL_SAMPLE = "ITAU EMPRESAS\n10/04 Transferencia recebida 100,00"
SANTANDER_INLINE_MINIMAL_SAMPLE = "SANTANDER EMPRESARIAL\n11/04 Pagamento fornecedor 25,00"
NUBANK_INLINE_MINIMAL_SAMPLE = "NUBANK\n12/04 Compra cartao 18,90"
BRADESCO_INLINE_MINIMAL_SAMPLE = "BRADESCO EMPRESAS\n13/04 Transferencia recebida 77,00"
BANCO_DO_BRASIL_INLINE_MINIMAL_SAMPLE = "BANCO DO BRASIL\n14/04 Pagamento boleto 35,40"
CAIXA_INLINE_MINIMAL_SAMPLE = "CAIXA ECONOMICA FEDERAL\n15/04 PIX recebido 52,10"
INTER_INLINE_MINIMAL_SAMPLE = "BANCO INTER\n16/04 TED recebida 90,00"
SICREDI_INLINE_MINIMAL_SAMPLE = "SICREDI\n17/04 Tarifa pacote 12,00"
QUASI_REAL_INLINE_NOISE_SAMPLE = "\n".join(
    [
        "BANCO EXEMPLO S.A.",
        "EXTRATO CONTA CORRENTE - PERIODO 01/04/2026 A 30/04/2026",
        "SALDO ANTERIOR 1.234,56",
        "03/04 PIX RECEBIDO CLIENTE ACME LTDA 250,00",
        "05/04 PAGAMENTO BOLETO ENERGIA ELETRICA 120,40",
        "07/04 TARIFA MANUTENCAO MENSAL PACOTE EMPRESARIAL 90,00",
        "SALDO DO DIA 1.274,16",
    ]
)
QUASI_REAL_MULTIPAGE_PAGE_ONE_SAMPLE = "\n".join(
    [
        "BANCO EXEMPLO S.A.",
        "EXTRATO CONTA CORRENTE - PERIODO 01/05/2026 A 31/05/2026",
        "SALDO ANTERIOR 2.345,67",
        "09/05 PAGAMENTO FORNECEDOR ALFA INDUSTRIA E COMERCIO LTDA 150,25",
        "10/05 PIX RECEBIDO CLIENTE BETA SERVICOS DIGITAIS LTDA 500,00",
    ]
)
QUASI_REAL_MULTIPAGE_PAGE_TWO_SAMPLE = "\n".join(
    [
        "CONTINUACAO EXTRATO MAIO/2026",
        "11/05 TARIFA PROCESSAMENTO COBRANCA AVANCADA 35,50",
        "SALDO DO DIA 2.659,92",
    ]
)
YEAR_ROLLOVER_PAGE_ONE_SAMPLE = "EXTRATO PERIODO 20/12/2025 A 10/01/2026"
YEAR_ROLLOVER_PAGE_TWO_SAMPLE = "\n".join(
    [
        "31/12 Compra mercado 10,00",
        "02/01 PIX recebido 20,00",
    ]
)


COLUMNAR_MINIMAL_SAMPLE = "\n".join(
    [
        "10/04",
        "Pagamento Cartao",
        "DEBITO",
        "10,00",
    ]
)


PDF_GOLDEN_MINIMAL_EXPECTATIONS = {
    "grouped": {
        "selected_parser": "grouped",
        "transactions_count": 2,
        "inline_candidates_count": 0,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {"date": "2026-03-16", "amount": -240.24, "type": "outflow", "source_page": 1, "source_line": 2},
        "last_transaction": {
            "date": "2026-03-25",
            "amount": -241.05,
            "type": "outflow",
            "description": "COMPRA AGIR CONTABILIDADE E ASSESSORIA LTDA",
            "source_page": 1,
            "source_line": 5,
        },
    },
    "inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-10",
            "amount": 10.0,
            "type": "inflow",
            "description": "AJUSTE MANUAL",
            "source_page": 1,
            "source_line": 1,
        },
        "last_transaction": {
            "date": "2026-04-10",
            "amount": 10.0,
            "type": "inflow",
            "description": "AJUSTE MANUAL",
            "source_page": 1,
            "source_line": 1,
        },
    },
    "tabular": {
        "selected_parser": "tabular",
        "transactions_count": 2,
        "inline_candidates_count": 0,
        "balance_consistency_checked": 1,
        "balance_consistency_failed": 0,
        "first_transaction": {"date": "2024-10-01", "amount": 1000.0, "type": "inflow", "source_page": 1, "source_line": 3},
        "last_transaction": {"date": "2024-10-02", "amount": -12.34, "type": "outflow", "source_page": 1, "source_line": 4},
    },
    "columnar": {
        "selected_parser": "columnar",
        "transactions_count": 1,
        "inline_candidates_count": 0,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-10",
            "amount": -10.0,
            "type": "outflow",
            "description": "Pagamento Cartao",
            "source_page": 1,
            "source_line": 1,
        },
        "last_transaction": {
            "date": "2026-04-10",
            "amount": -10.0,
            "type": "outflow",
            "description": "Pagamento Cartao",
            "source_page": 1,
            "source_line": 1,
        },
    },
    "itau_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-10",
            "amount": 100.0,
            "type": "inflow",
            "description": "TRANSFERENCIA RECEBIDA",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-10",
            "amount": 100.0,
            "type": "inflow",
            "description": "TRANSFERENCIA RECEBIDA",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "santander_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-11",
            "amount": -25.0,
            "type": "outflow",
            "description": "PAGAMENTO FORNECEDOR",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-11",
            "amount": -25.0,
            "type": "outflow",
            "description": "PAGAMENTO FORNECEDOR",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "nubank_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-12",
            "amount": -18.9,
            "type": "outflow",
            "description": "COMPRA CARTAO",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-12",
            "amount": -18.9,
            "type": "outflow",
            "description": "COMPRA CARTAO",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "bradesco_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-13",
            "amount": 77.0,
            "type": "inflow",
            "description": "TRANSFERENCIA RECEBIDA",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-13",
            "amount": 77.0,
            "type": "inflow",
            "description": "TRANSFERENCIA RECEBIDA",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "banco_do_brasil_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-14",
            "amount": -35.4,
            "type": "outflow",
            "description": "PAGAMENTO BOLETO",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-14",
            "amount": -35.4,
            "type": "outflow",
            "description": "PAGAMENTO BOLETO",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "caixa_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-15",
            "amount": 52.1,
            "type": "inflow",
            "description": "PIX RECEBIDO",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-15",
            "amount": 52.1,
            "type": "inflow",
            "description": "PIX RECEBIDO",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "inter_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-16",
            "amount": 90.0,
            "type": "inflow",
            "description": "TED RECEBIDA",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-16",
            "amount": 90.0,
            "type": "inflow",
            "description": "TED RECEBIDA",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "sicredi_inline": {
        "selected_parser": "inline",
        "transactions_count": 1,
        "inline_candidates_count": 1,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-17",
            "amount": -12.0,
            "type": "outflow",
            "description": "TARIFA PACOTE",
            "source_page": 1,
            "source_line": 2,
        },
        "last_transaction": {
            "date": "2026-04-17",
            "amount": -12.0,
            "type": "outflow",
            "description": "TARIFA PACOTE",
            "source_page": 1,
            "source_line": 2,
        },
    },
    "quasi_real_inline_noise": {
        "selected_parser": "inline",
        "transactions_count": 3,
        "inline_candidates_count": 3,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-04-03",
            "amount": 250.0,
            "type": "inflow",
            "description": "PIX RECEBIDO CLIENTE ACME LTDA",
            "source_page": 1,
            "source_line": 4,
        },
        "last_transaction": {
            "date": "2026-04-07",
            "amount": -90.0,
            "type": "outflow",
            "description": "TARIFA MANUTENCAO MENSAL PACOTE EMPRESARIAL",
            "source_page": 1,
            "source_line": 6,
        },
    },
    "quasi_real_multipage_inline": {
        "selected_parser": "inline",
        "transactions_count": 3,
        "inline_candidates_count": 3,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2026-05-09",
            "amount": -150.25,
            "type": "outflow",
            "description": "PAGAMENTO FORNECEDOR ALFA INDUSTRIA E COMERCIO LTDA",
            "source_page": 1,
            "source_line": 4,
        },
        "last_transaction": {
            "date": "2026-05-11",
            "amount": -35.5,
            "type": "outflow",
            "description": "TARIFA PROCESSAMENTO COBRANCA AVANCADA",
            "source_page": 2,
            "source_line": 2,
        },
    },
    "year_rollover_inline": {
        "selected_parser": "inline",
        "transactions_count": 2,
        "inline_candidates_count": 2,
        "balance_consistency_checked": 0,
        "balance_consistency_failed": 0,
        "first_transaction": {
            "date": "2025-12-31",
            "amount": -10.0,
            "type": "outflow",
            "description": "COMPRA MERCADO",
            "source_page": 2,
            "source_line": 1,
        },
        "last_transaction": {
            "date": "2026-01-02",
            "amount": 20.0,
            "type": "inflow",
            "description": "PIX RECEBIDO",
            "source_page": 2,
            "source_line": 2,
        },
    },
}


PDF_GOLDEN_MINIMAL_SCENARIOS = {
    "grouped": {
        "sample_text": GROUPED_INLINE_MULTILINE_SAMPLE,
        "layout_name": None,
    },
    "inline": {
        "sample_text": INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "tabular": {
        "sample_text": VIACREDI_TABULAR_BALANCE_OK,
        "layout_name": "viacredi_ailos_extrato_conta_corrente_v1",
    },
    "columnar": {
        "sample_text": COLUMNAR_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "itau_inline": {
        "sample_text": ITAU_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "santander_inline": {
        "sample_text": SANTANDER_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "nubank_inline": {
        "sample_text": NUBANK_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "bradesco_inline": {
        "sample_text": BRADESCO_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "banco_do_brasil_inline": {
        "sample_text": BANCO_DO_BRASIL_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "caixa_inline": {
        "sample_text": CAIXA_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "inter_inline": {
        "sample_text": INTER_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "sicredi_inline": {
        "sample_text": SICREDI_INLINE_MINIMAL_SAMPLE,
        "layout_name": None,
    },
    "quasi_real_inline_noise": {
        "sample_text": QUASI_REAL_INLINE_NOISE_SAMPLE,
        "layout_name": None,
    },
    "quasi_real_multipage_inline": {
        "sample_pages": [QUASI_REAL_MULTIPAGE_PAGE_ONE_SAMPLE, QUASI_REAL_MULTIPAGE_PAGE_TWO_SAMPLE],
        "layout_name": None,
    },
    "year_rollover_inline": {
        "sample_pages": [YEAR_ROLLOVER_PAGE_ONE_SAMPLE, YEAR_ROLLOVER_PAGE_TWO_SAMPLE],
        "layout_name": None,
    },
}



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
    "canonical_source_parser_grouped_count": 2,
    "canonical_source_parser_inline_count": 0,
    "canonical_source_parser_tabular_count": 0,
    "canonical_source_parser_columnar_count": 0,
    "canonical_source_parser_types_count": 1,
    "canonical_source_parser_types": "grouped",
    "canonical_source_parser_types_list": "grouped",
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
    "canonical_source_parser_grouped_count": 0,
    "canonical_source_parser_inline_count": 2,
    "canonical_source_parser_tabular_count": 0,
    "canonical_source_parser_columnar_count": 0,
    "canonical_source_parser_types_count": 1,
    "canonical_source_parser_types": "inline",
    "canonical_source_parser_types_list": "inline",
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
