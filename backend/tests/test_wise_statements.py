"""Synthetic Wise regressions; no customer documents or identifiers are fixtures."""

import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError
from app.application.export_artifact_service import ExportArtifactService
from app.application.pdf_parser import parse_pdf_transactions
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


def wise_lines(currency="BRL", *, empty=False, balance="1,50"):
    rows = [] if empty else [
        "Enviou dinheiro para Fornecedor Sintético -1.378,50 1,50",
        "10 de abril de 2026 | Transação: TRANSFER-100006",
        "Recebeu dinheiro de Cliente C 30,00 1.380,00",
        "10 de abril de 2026 | Transação: TRANSFER-100005",
        "Recebeu dinheiro de Cliente B 450,00 1.350,00",
        "10 de abril de 2026 | Transação: TRANSFER-100004",
        "Recebeu dinheiro de Cliente A 900,00 900,00",
        "10 de abril de 2026 | Transação: TRANSFER-100003",
        "Obtenção dos dados bancários da Wise -40,00 0,00",
        "7 de abril de 2026 | Transação: BANK_DETAILS_ORDER_CHECKOUT-invoice-100002",
        "Dinheiro adicionado à conta 40,00 40,00",
        "7 de abril de 2026 | Transação: TRANSFER-100001",
    ]
    return [
        "ref:synthetic-wise 1 / 1",
        "Wise Brasil Instituição de Pagamento Ltda." if currency == "BRL" else "Wise Payments Ltd.",
        f"Extrato em {currency}",
        "1 de abril de 2026 [GMT-03:00] - 30 de abril de 2026 [GMT-03:00]",
        "Gerado em: 13 de outubro de 2026",
        "Titular da Conta",
        "EMPRESA SINTETICA LTDA",
        "Número da conta",
        "12345678",
        f"{currency} em 30 de abril de 2026 [GMT-03:00] {balance} {currency}",
        "Descrição Entrada Saída Valor",
        *rows,
        "A Wise Brasil Pagamentos LTDA é uma Instituição de Pagamentos.",
        "Precisa de ajuda? Visite wise.com/help",
    ]


@pytest.fixture(autouse=True)
def disable_remote_ocr(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def convert(lines):
    return build_default_conversion_pipeline().run(
        filename="wise.pdf", raw_bytes=generate_native_text_pdf((tuple(lines),)), analysis_id="an_wise_synthetic"
    ).analysis_data


@pytest.mark.parametrize("currency", ["BRL", "USD"])
def test_wise_preserves_six_movements_dates_balances_references_and_clean_descriptions(tmp_path, currency):
    raw = generate_native_text_pdf((tuple(wise_lines(currency)),))
    parsed = parse_pdf_transactions(raw)
    assert parsed.layout.layout_name == f"wise_extrato_{currency.lower()}_v1"
    assert [row.amount for row in parsed.transactions] == [40, -40, 900, 450, 30, -1378.50]
    assert [row.date for row in parsed.transactions] == ["2026-04-07"] * 2 + ["2026-04-10"] * 4
    assert [row.running_balance for row in parsed.canonical_transactions] == [40, 0, 900, 1350, 1380, 1.5]
    assert [row.external_reference_id for row in parsed.canonical_transactions] == [
        "TRANSFER-100001", "BANK_DETAILS_ORDER_CHECKOUT-invoice-100002",
        "TRANSFER-100003", "TRANSFER-100004", "TRANSFER-100005", "TRANSFER-100006",
    ]
    assert parsed.parse_metrics["balance_consistency_failed"] == 0
    assert all("wise.com" not in row.description.lower() for row in parsed.transactions)
    analysis = convert(wise_lines(currency))
    assert analysis.transactions_total == 6
    assert analysis.opening_balance == 0
    assert analysis.closing_balance == 1.5
    assert analysis.bank_name == "Wise"
    ExportArtifactService().write_convert_artifacts(
        tmp_path, report_rows=analysis.report_transactions, layout_inference_name=analysis.layout_inference_name,
        opening_balance=analysis.opening_balance, closing_balance=analysis.closing_balance,
    )
    ofx = (tmp_path / "converted.ofx").read_text(encoding="utf-8")
    assert ofx.count("<STMTTRN>") == 6
    assert f"<CURDEF>{currency}" in ofx
    assert "<BALAMT>1.50" in ofx


@pytest.mark.parametrize("currency,balance", [("USD", "0,00"), ("USD", "57,25"), ("BRL", "80,00")])
def test_complete_wise_empty_statement_preserves_balance_and_export_currency(currency, balance, tmp_path):
    analysis = convert(wise_lines(currency, empty=True, balance=balance))
    assert analysis.transactions_total == 0
    assert analysis.pdf_processing_metrics["statement_no_activity"] == 1
    assert analysis.pdf_processing_metrics["selected_parser"] == "layout_confirmed_no_activity"
    assert analysis.bank_name == "Wise"
    assert analysis.opening_balance == float(balance.replace(",", "."))
    assert analysis.closing_balance == analysis.opening_balance
    ExportArtifactService().write_convert_artifacts(
        tmp_path, report_rows=analysis.report_transactions, layout_inference_name=analysis.layout_inference_name,
        closing_balance=analysis.closing_balance,
    )
    ofx = (tmp_path / "converted.ofx").read_text(encoding="utf-8")
    assert "<STMTTRN>" not in ofx
    assert f"<CURDEF>{currency}" in ofx


@pytest.mark.parametrize("mutation", [
    "missing_date", "invalid_date", "outside_period", "missing_amount", "missing_footer", "missing_page",
    "unknown_row", "currency_conflict", "closing_mismatch", "missing_account", "duplicate_reference",
])
def test_damaged_wise_statement_never_falls_back_to_partial_generic_conversion(mutation):
    lines = wise_lines()
    if mutation == "missing_date":
        lines.remove("10 de abril de 2026 | Transação: TRANSFER-100006")
    elif mutation == "invalid_date":
        lines = [line.replace("10 de abril", "31 de abril") for line in lines]
    elif mutation == "outside_period":
        lines = [line.replace("10 de abril", "10 de maio") for line in lines]
    elif mutation == "missing_amount":
        lines = [line.replace("-1.378,50", "ilegível") for line in lines]
    elif mutation == "missing_footer":
        lines = lines[:-2]
    elif mutation == "missing_page":
        lines[0] = "ref:synthetic-wise 1 / 2"
    elif mutation == "unknown_row":
        lines.insert(-2, "Lançamento ilegível 99,00")
    elif mutation == "currency_conflict":
        lines = [line.replace("BRL em 30", "USD em 30") for line in lines]
    elif mutation == "closing_mismatch":
        lines = [line.replace("1,50 BRL", "2,50 BRL") for line in lines]
    elif mutation == "missing_account":
        lines.remove("12345678")
    else:
        lines = [line.replace("TRANSFER-100005", "TRANSFER-100006") for line in lines]
    if mutation in {"missing_page", "closing_mismatch"}:
        result = convert(lines)
        assert result.transactions_total == 6
        assert result.pdf_processing_metrics["export_recommendation"] == "review_recommended"
    else:
        with pytest.raises(InvalidFileContentError):
            convert(lines)


def test_wise_empty_requires_complete_table_and_document():
    for marker in ["Descrição Entrada Saída Valor", "Número da conta", "Precisa de ajuda? Visite wise.com/help"]:
        lines = wise_lines("USD", empty=True, balance="0,00")
        lines.remove(marker)
        with pytest.raises(InvalidFileContentError):
            convert(lines)


def test_ocr_cannot_prove_wise_inactivity_by_missing_all_transaction_rows(monkeypatch):
    from app.application import pdf_parser

    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts_with_ocr", lambda *args, **kwargs: [
        "\n".join(wise_lines("USD", empty=True, balance="0,00")),
    ])
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_local_ocr_adapter(b"pdf", on_ocr_progress=None)


def test_wise_zero_amount_without_date_is_not_absorbed_into_next_description():
    lines = wise_lines()
    lines.insert(11, "Ajuste sem valor 0,00 1,50")
    with pytest.raises(InvalidFileContentError):
        convert(lines)


def test_wise_preserves_same_value_and_date_with_different_transaction_references():
    lines = wise_lines()
    lines[11:11] = [
        "Ajuste de saldo 0,00 1,50", "10 de abril de 2026 | Transação: TRANSFER-100008",
        "Ajuste de saldo 0,00 1,50", "10 de abril de 2026 | Transação: TRANSFER-100007",
    ]
    result = convert(lines)
    assert result.transactions_total == 8
    assert result.closing_balance == 1.5


def test_wise_wrapped_description_and_repeated_page_header_preserve_all_movements():
    lines = wise_lines()
    split = lines.index("Recebeu dinheiro de Cliente A 900,00 900,00")
    first = lines[:split]
    second = ["ref:synthetic-wise 2 / 2", "Wise Brasil Instituição de Pagamento Ltda.",
              "Descrição Entrada Saída Valor", *lines[split:]]
    first[0] = "ref:synthetic-wise 1 / 2"
    first[11:12] = ["Enviou dinheiro para", "Fornecedor Sintético -1.378,50 1,50"]
    result = parse_pdf_transactions(generate_native_text_pdf((tuple(first), tuple(second))))
    assert len(result.transactions) == 6
    assert result.transactions[-1].description == "Enviou dinheiro para Fornecedor Sintético"
    assert [row.source_page for row in result.canonical_transactions] == [2, 2, 2, 1, 1, 1]
    assert result.parse_metrics["balance_consistency_failed"] == 0
