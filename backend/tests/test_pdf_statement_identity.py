import pytest

from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import UnsupportedDocumentContentError
from app.application.pdf_document_segmentation import segment_pdf_pages
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf


def statement_page(
    *,
    owner_account="123456789-0",
    owner_agency="0001",
    counterparty_agency="1762",
    counterparty_account="987654-3",
    movement="01/09/2026 PIX RECEBIDO CLIENTE 100,00 1.100,00",
    header_kind="nubank",
    month="set",
):
    if header_kind in {"nubank", "nubank_cpf"}:
        identifier = "111.111.111-11" if header_kind == "nubank_cpf" else "11.111.111/1111-11"
        label = "CPF" if header_kind == "nubank_cpf" else "CNPJ"
        identity = f"{identifier} {owner_agency} {label} Agência Conta\n{owner_account}"
    elif header_kind == "account_first":
        identity = f"Conta {owner_account} Agência {owner_agency}"
    elif header_kind == "agency_first":
        identity = f"Ag {owner_agency} CC {owner_account}"
    elif header_kind == "no_agency":
        identity = f"Conta {owner_account}"
    else:
        identity = ""
    return (
        f"Nubank\nEXTRATO MENSAL\nTITULAR SINTÉTICO\n{identity}\n{month} 2026\n"
        "Saldo inicial 1.000,00\nData Descrição Valor Saldo\n"
        f"{movement}\nTransferência pelo Pix\n"
        f"BANCO DA CONTRAPARTE Agência: {counterparty_agency} Conta: {counterparty_account}\n"
    )


@pytest.mark.parametrize("header_kind", ["nubank", "nubank_cpf", "account_first", "agency_first", "no_agency", "absent"])
@pytest.mark.parametrize("second_agency", ["3593", "1"])
def test_pix_counterparties_do_not_define_the_statement_owner(header_kind, second_agency):
    first = statement_page(header_kind=header_kind)
    second = statement_page(
        header_kind=header_kind, counterparty_agency=second_agency, counterparty_account="456789-1",
        movement="02/09/2026 PIX ENVIADO FORNECEDOR -30,00 1.070,00",
    )
    result = segment_pdf_pages([first, second])
    assert result.page_texts == (first, second)
    assert result.source_page_texts == (first, second)


@pytest.mark.parametrize("header_kind", ["nubank", "nubank_cpf", "account_first", "agency_first"])
@pytest.mark.parametrize("change", [{"owner_account": "456789123-4"}, {"owner_agency": "0002"}])
def test_different_owner_accounts_and_agencies_remain_rejected(header_kind, change):
    first = statement_page(header_kind=header_kind)
    second = statement_page(header_kind=header_kind, **change)
    with pytest.raises(UnsupportedDocumentContentError, match="contas diferentes"):
        segment_pdf_pages([first, second])


@pytest.mark.parametrize("date", ["02/09/2026", "2026-09-02", "02 SET 2026"])
def test_counterparties_on_a_date_continuation_do_not_become_owner_metadata(date):
    first = statement_page(header_kind="no_agency", movement="01/09/2026\nPIX RECEBIDO 100,00")
    second = statement_page(
        header_kind="no_agency", movement=f"{date}\nPIX ENVIADO -30,00",
        counterparty_agency="3593", counterparty_account="456789-1",
    )
    assert segment_pdf_pages([first, second]).page_texts == (first, second)


def test_nubank_continuation_before_the_first_date_keeps_only_header_identity():
    first = statement_page().replace("Data Descrição Valor Saldo\n", "")
    second = statement_page(counterparty_agency="3593", counterparty_account="456789-1")
    second = second.replace("Data Descrição Valor Saldo\n", "")
    period = "01 DE SETEMBRO DE 2026 a 30 DE SETEMBRO DE 2026 VALORES EM R$"
    first = first.replace("set 2026", period)
    second = second.replace("set 2026", period)
    second = second.replace("Saldo inicial 1.000,00", "BANCO DA CONTRAPARTE Agência: 8117 Conta: 321654-8")
    assert segment_pdf_pages([first, second]).page_texts == (first, second)


@pytest.mark.parametrize("separator", ["a", "até", "-"])
def test_period_date_range_before_owner_metadata_does_not_hide_different_accounts(separator):
    period = f"01/09/2026 {separator} 30/09/2026\n"
    first = period + statement_page()
    second = period + statement_page(owner_account="456789123-4")
    with pytest.raises(UnsupportedDocumentContentError, match="contas diferentes"):
        segment_pdf_pages([first, second])


def test_native_nubank_pdf_converts_without_losing_rows_or_original_pages(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    first = statement_page()
    second = statement_page(
        counterparty_agency="3593", counterparty_account="456789-1",
        movement="02/09/2026 PIX ENVIADO FORNECEDOR -30,00 1.070,00",
    )
    raw = generate_native_text_pdf(tuple(tuple(page.splitlines()) for page in (first, second)))
    result = build_default_conversion_pipeline().run(filename="nubank.pdf", raw_bytes=raw, analysis_id="an_owner_identity")
    analysis = result.analysis_data
    rows = analysis.report_transactions or analysis.preview_transactions
    assert analysis.transactions_total == 2
    assert [row.amount for row in rows] == [100.0, -30.0]
    assert [row.date for row in rows] == ["2026-09-01", "2026-09-02"]
    assert [row.source_page for row in rows] == [1, 2]
    assert all(not row.warning_types for row in rows)
    assert analysis.opening_balance == 1000.0
    assert analysis.closing_balance == 1070.0
    assert analysis.net_total == 70.0
