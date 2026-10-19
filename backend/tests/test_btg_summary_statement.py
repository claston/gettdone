import pytest

from app.application import pdf_parser as pdf_parser_module
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import PdfFinancialValidationError
from app.application.pdf_layout_inference import infer_pdf_layout
from app.application.pdf_parser import _parse_pdf_transactions_from_page_texts, parse_pdf_transactions
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_pdf_parser_btg_pactual_examples import BTG_PJ_STATEMENT_TEXT

LAYOUT = "btg_pactual_extrato_conta_corrente_resumo_v1"
AMOUNTS_DESCENDING = [-1.0, -9.0, 10.0, -20.0, -30.0, -40.0, -50.0, 60.0, 45.0, 25.0, 10.0, -5.0, 5.0]
BALANCES_DESCENDING = [0.0, 1.0, 10.0, 0.0, 20.0, 50.0, 90.0, 140.0, 80.0, 35.0, 10.0, 0.0, 5.0]


def btg_summary_pages():
    header = ["Data de", "lançamento Descrição do lançamento Entradas/Saídas Saldo"]
    period = "Período de 04/06/2026 a 05/06/2026"
    summary = [
        "Resumo do saldo", "Saldo total", "Saldo de abertura 04/06/2026 R$ 0,00",
        "Saldo de fechamento 05/06/2026 R$ 0,00", "Saldo bloqueado 08/06/2026 R$ 0,00",
        "Total de entradas R$ 155,00", "Recebíveis de cartão 93.54% R$ 145,00", "PIX 6.45% R$ 10,00",
        "Total de saídas R$ 155,00", "Outros 100.00% R$ 155,00", "Entradas e saídas",
        "Comparativo de entradas e saídas no período selecionado:", "Entradas Saídas", "Lançamentos", period,
    ]
    movements = [
        "05/06/2026 DÉBITO EM C/C VIA CIP - FORNECEDOR SINTÉTICO - R$ 1,00 R$ 0,00",
        "05/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 9,00 R$ 1,00",
        "05/06/2026 TRANSFERÊNCIA A CRÉDITO VIA PIX - CLIENTE\nSINTÉTICO R$ 10,00 R$ 10,00",
        "05/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 20,00 R$ 0,00",
        "05/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 30,00 R$ 20,00",
        "05/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 40,00 R$ 50,00",
        "05/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 50,00 R$ 90,00",
        "05/06/2026\nCrédito/Depósito ref. a recebíveis (cartão crédito). - BTG_Pactual\n"
        "CREDENCIADORA SINTÉTICA - Mastercard\nCrédito | CNPJ: 00000000000000\nR$ 60,00 R$ 140,00",
        "05/06/2026\nCrédito/Depósito ref. a recebíveis (cartão crédito). - BTG_Pactual\n"
        "CREDENCIADORA SINTÉTICA - Visa Crédito |\nCNPJ: 00000000000000\nR$ 45,00 R$ 80,00",
        "05/06/2026 Crédito/Depósito ref. a recebíveis (cartão crédito). - BTG_Pactual\n"
        "CREDENCIADORA SINTÉTICA - Elo Crédito | CNPJ: 00000000000000 R$ 25,00 R$ 35,00",
        "05/06/2026\nCrédito/Depósito ref. a recebíveis (cartão crédito). - BTG_Pactual\n"
        "CREDENCIADORA SINTÉTICA - Elo Crédito |\nCNPJ: 00000000000000\nR$ 10,00 R$ 10,00",
        "04/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 5,00 R$ 0,00",
        "04/06/2026 Crédito/Depósito ref. a recebíveis (cartão crédito). - BTG_Pactual\n"
        "CREDENCIADORA SINTÉTICA - Elo Crédito | CNPJ: 00000000000000 R$ 5,00 R$ 5,00",
    ]

    def footer(page):
        return [
            "Extrato conta corrente", period + " - EMPRESA SINTÉTICA",
            "CNPJ 00.000.000/0001-00 · Banco 208 · Agência 1 · Conta 12345678 · PDF gerado em 08/06/2026 - 10:00:00",
            "Fale com nossa central de atendimento", "Atendimento 24 horas por dia, 7 dias por semana",
            "Ouvidoria", f"© 2026 BTG Pactual. {page} / 2",
        ]

    first = [*summary, *header, "05/06/2026 Saldo de fechamento R$ 0,00", *movements[:10],
             "R$ 0", "R$ 7.500", *footer(1)]
    second = [*header, *movements[10:], "04/06/2026 Saldo de abertura R$ 0,00", *footer(2)]
    return ["\n".join(first), "\n".join(second)]


def test_recognizes_the_btg_summary_format_without_changing_the_legacy_profile():
    result = infer_pdf_layout("\n".join(btg_summary_pages()))
    assert result.layout_name == LAYOUT
    assert result.confidence >= 0.9
    assert not result.used_fallback
    assert infer_pdf_layout(BTG_PJ_STATEMENT_TEXT).layout_name == "btg_pactual_conta_corrente_pj_v1"


def test_btg_summary_parses_all_wrapped_rows_and_separates_amounts_from_balances():
    result = _parse_pdf_transactions_from_page_texts(btg_summary_pages())
    assert result.layout.layout_name == LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_btg_summary"
    assert result.parse_metrics["confidence_band"] == "high"
    assert result.parse_metrics["export_recommendation"] == "safe_to_export"
    assert [row.amount for row in result.transactions] == list(reversed(AMOUNTS_DESCENDING))
    assert [row.running_balance for row in result.canonical_transactions] == list(reversed(BALANCES_DESCENDING))
    assert [row.source_page for row in result.canonical_transactions] == [2] * 3 + [1] * 10
    assert all(row.source_line and row.source_line_refs for row in result.canonical_transactions)
    assert all(not row.warnings for row in result.canonical_transactions)
    assert not any("SALDO DE" in row.description.upper() for row in result.transactions)
    assert "Mastercard" in result.transactions[5].description
    assert "CNPJ: 00000000000000" in result.transactions[5].description


def test_native_btg_pdf_reconciles_the_printed_summary_and_preserves_provenance(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in btg_summary_pages()))
    analysis = build_default_conversion_pipeline().run(filename="btg.pdf", raw_bytes=raw, analysis_id="an_btg_summary").analysis_data
    rows = analysis.report_transactions or analysis.preview_transactions
    assert analysis.transactions_total == 13
    assert [row.amount for row in rows] == list(reversed(AMOUNTS_DESCENDING))
    assert analysis.total_inflows == 155.0
    assert analysis.total_outflows == -155.0
    assert analysis.net_total == 0
    assert analysis.opening_balance == analysis.closing_balance == 0
    assert analysis.bank_name == "BTG Pactual"
    assert analysis.bank_code == "208"
    assert [row.source_sequence for row in rows] == list(range(13, 0, -1))
    assert all(row.source_regions for row in rows)


def test_other_bank_code_does_not_activate_the_btg_summary_profile():
    text = "\n".join(btg_summary_pages()).replace("Banco 208", "Banco 341")
    assert infer_pdf_layout(text).layout_name != LAYOUT


def test_validated_native_btg_ledger_is_not_replaced_by_generic_layout_text(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")

    def reject_layout_retry(*_args, **_kwargs):
        raise AssertionError("A fully reconciled BTG ledger must retain its validated values")

    monkeypatch.setattr(pdf_parser_module, "_read_layout_native_pdf_page_texts", reject_layout_retry)
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in btg_summary_pages()))
    assert len(parse_pdf_transactions(raw).transactions) == 13


def test_nonzero_negative_opening_and_closing_balances_are_preserved(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    lines = (
        "Resumo do saldo", "Saldo total", "Saldo de abertura 04/06/2026 - R$ 50,00",
        "Saldo de fechamento 05/06/2026 - R$ 44,00", "Saldo bloqueado 08/06/2026 R$ 0,00",
        "Total de entradas R$ 10,00", "Total de saídas R$ 4,00", "Lançamentos",
        "Período de 04/06/2026 a 05/06/2026",
        "Data de lançamento Descrição do lançamento Entradas/Saídas Saldo",
        "05/06/2026 Saldo de fechamento - R$ 44,00", "05/06/2026 DÉBITO - R$ 4,00 - R$ 44,00",
        "04/06/2026 CRÉDITO R$ 10,00 - R$ 40,00", "04/06/2026 Saldo de abertura - R$ 50,00",
        "Extrato conta corrente", "Período de 04/06/2026 a 05/06/2026",
        "Banco 208 · Agência 1 · Conta 12345678 · PDF gerado em 08/06/2026",
        "© 2026 BTG Pactual. 1 / 1",
    )
    raw = generate_native_text_pdf((lines,))
    analysis = build_default_conversion_pipeline().run(filename="btg.pdf", raw_bytes=raw, analysis_id="an_btg_overdraft").analysis_data
    assert analysis.transactions_total == 2
    assert analysis.opening_balance == -50.0
    assert analysis.closing_balance == -44.0
    assert analysis.net_total == 6.0


@pytest.mark.parametrize("defect", [
    "missing_page", "missing_row", "missing_amount", "missing_balance", "lost_debit_sign", "wrong_balance",
    "wrong_inflows", "wrong_outflows", "wrong_opening", "wrong_closing", "invalid_date", "outside_period",
    "conflicting_period", "wrong_page_count", "missing_page_counter", "incomplete_last_row", "only_continuation_page",
    "missing_summary_totals", "different_account", "different_bank", "reversed_pages",
])
def test_btg_financial_ambiguities_do_not_fall_back_to_generic_conversion(defect):
    pages = btg_summary_pages()
    if defect == "missing_page":
        pages = pages[:1]
    elif defect == "only_continuation_page":
        pages = pages[1:]
    elif defect == "missing_summary_totals":
        pages[0] = pages[0].replace("Total de entradas R$ 155,00\n", "").replace("Total de saídas R$ 155,00\n", "")
    elif defect == "different_account":
        pages[1] = pages[1].replace("Conta 12345678", "Conta 98765432")
    elif defect == "different_bank":
        pages[1] = pages[1].replace("Banco 208", "Banco 341")
    elif defect == "reversed_pages":
        pages.reverse()
    elif defect == "missing_row":
        pages[0] = pages[0].replace("05/06/2026 LIQ. DE FATURA - BANCO BTG PACTUAL S A - R$ 9,00 R$ 1,00\n", "")
    elif defect == "missing_amount":
        pages[0] = pages[0].replace("- R$ 9,00 R$ 1,00", "R$ 1,00")
    elif defect == "missing_balance":
        pages[0] = pages[0].replace("- R$ 9,00 R$ 1,00", "- R$ 9,00")
    elif defect == "lost_debit_sign":
        pages[0] = pages[0].replace("- R$ 9,00 R$ 1,00", "R$ 9,00 R$ 1,00")
    elif defect == "wrong_balance":
        pages[0] = pages[0].replace("- R$ 9,00 R$ 1,00", "- R$ 9,00 R$ 2,00")
    elif defect in {"wrong_inflows", "wrong_outflows"}:
        label = "entradas" if defect == "wrong_inflows" else "saídas"
        pages[0] = pages[0].replace(f"Total de {label} R$ 155,00", f"Total de {label} R$ 156,00")
    elif defect in {"wrong_opening", "wrong_closing"}:
        label, date = ("abertura", "04/06/2026") if defect == "wrong_opening" else ("fechamento", "05/06/2026")
        pages[0] = pages[0].replace(f"Saldo de {label} {date} R$ 0,00", f"Saldo de {label} {date} R$ 1,00")
    elif defect in {"invalid_date", "outside_period"}:
        date = "32/06/2026" if defect == "invalid_date" else "03/06/2026"
        pages[0] = pages[0].replace("05/06/2026 DÉBITO", f"{date} DÉBITO")
    elif defect == "conflicting_period":
        pages[1] = pages[1].replace("Período de 04/06/2026 a 05/06/2026", "Período de 03/06/2026 a 05/06/2026")
    elif defect == "wrong_page_count":
        pages[0] = pages[0].replace("1 / 2", "1 / 3")
    elif defect == "missing_page_counter":
        pages[1] = pages[1].replace("2 / 2", "")
    else:
        pages[1] = pages[1].replace("04/06/2026 Saldo de abertura", "04/06/2026 LINHA INCOMPLETA\n04/06/2026 Saldo de abertura")
    with pytest.raises(PdfFinancialValidationError, match="BTG"):
        _parse_pdf_transactions_from_page_texts(pages)


def test_invalid_native_btg_date_remains_visible_to_financial_validation(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    pages = btg_summary_pages()
    pages[0] = pages[0].replace("05/06/2026 DÉBITO", "32/06/2026 DÉBITO")
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in pages))
    with pytest.raises(PdfFinancialValidationError, match="BTG"):
        parse_pdf_transactions(raw)
