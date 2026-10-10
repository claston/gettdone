from __future__ import annotations

from app.application import pdf_parser as pdf_parser_module
from app.application.bank_resolver import resolve_bank_code
from app.application.pdf_layout_inference import infer_pdf_layout

SISPRIME_LAYOUT = "sisprime_extrato_conta_debito_credito_saldo_v1"


def _fixed_width_line(
    date: str,
    document: str,
    description: str,
    *,
    debit: str = "",
    credit: str = "",
    balance: str,
) -> str:
    return f"{date:<12}{document:<15}{description:<30}{'':<23}{debit:>12}{credit:>18}{balance:>18}"


def _sample_page() -> str:
    return "\n".join(
        (
            "Extrato de Conta",
            "Período do extrato: 01/09/2026 a 30/09/2026",
            "Lançamentos Saldo Anterior 1.000,00",
            f"{'Data':<12}{'Documento':<15}{'Histórico':<30}{'Descrição':<23}{'Débito':>12}{'Crédito':>18}{'Saldo':>18}",
            _fixed_width_line(
                "01/09/2026",
                "1001",
                "PIX RECEBIDO",
                credit="100,00",
                balance="1.100,00",
            ),
            _fixed_width_line(
                "02/09/2026",
                "1002",
                "CRÉDITO TARIFA ESTORNADA",
                debit="25,00",
                balance="1.075,00",
            ),
            _fixed_width_line(
                "03/09/2026",
                "1003",
                "RESGATE RDC",
                credit="50,00",
                balance="1.125,00",
            ),
            _fixed_width_line(
                "04/09/2026",
                "1004",
                "PAGAMENTO FORNECEDOR",
                debit="10,00",
                balance="999,00",
            ),
        )
    )


def test_recognizes_sisprime_statement_without_text_logo() -> None:
    result = infer_pdf_layout(_sample_page())

    assert result.layout_name == SISPRIME_LAYOUT
    assert result.confidence >= 0.85
    assert result.used_fallback is False
    assert resolve_bank_code(layout_inference_name=SISPRIME_LAYOUT) == "084"


def test_parses_sisprime_columns_and_keeps_real_balance_warning() -> None:
    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [_sample_page()],
        preserve_layout_spacing=True,
    )

    assert result.parse_metrics["selected_parser"] == "layout_specific_sisprime"
    assert [(row.description, row.amount) for row in result.transactions] == [
        ("PIX RECEBIDO", 100.0),
        ("CRÉDITO TARIFA ESTORNADA", -25.0),
        ("RESGATE RDC", 50.0),
        ("PAGAMENTO FORNECEDOR", -10.0),
    ]
    assert [row.running_balance for row in result.canonical_transactions or []] == [
        1100.0,
        1075.0,
        1125.0,
        999.0,
    ]
    assert result.parse_metrics["balance_consistency_checked"] == 3
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert result.parse_metrics["canonical_warning_transactions_count"] == 1


def test_parses_sisprime_when_pdf_text_replaces_accents() -> None:
    corrupted_page = (
        _sample_page()
        .replace("Período", "Per�odo")
        .replace("Lançamentos", "Lan�amentos")
        .replace("Histórico", "Hist�rico")
        .replace("Descrição", "Descri��o")
        .replace("Débito", "D�bito")
        .replace("Crédito", "Cr�dito")
    )

    result = pdf_parser_module._parse_pdf_transactions_from_page_texts(
        [corrupted_page],
        preserve_layout_spacing=True,
    )

    assert result.layout.layout_name == SISPRIME_LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_sisprime"
    assert len(result.transactions) == 4
