"""Synthetic invoices only; no customer documents or extracted customer text."""

from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application import pdf_parser as pdf_parser_module
from app.application.bank_identity import resolve_bank_name
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError
from app.application.pdf_layout_inference import infer_pdf_layout
from app.application.pdf_parser import parse_pdf_transactions

LAYOUT = "cartao_credito_fatura_digital_dia_mes_v1"
HEADER = "Data Lançamentos Moeda de Origem Valor (US$) Cotação (US$) Valor (R$)"


def invoice_pages() -> list[str]:
    return [
        f"""Valor da fatura: R$ 80,00
Fatura Data 10/02/2027 - 10:30:00
Cartão de crédito
Data de vencimento:
Total da fatura:
Forma de pagamento:
Melhor data de compra:
20/01/2026
R$ 136,00
Débito em conta
21
Valor da fatura anterior: R$ 100,00
{HEADER}
Cartão: Final 1111 | CLIENTE FICTÍCIO
19
DEZ
SALDO ANTERIOR 100,00
21
DEZ
LOJA A 40,00
LOJA B 10,00
""",
        f"""Valor da fatura: R$ 80,00
02
JAN
PAGAMENTO EM CONTA -100,00
LOJA C (03/10) 30,00
{HEADER}
Cartão: Final 2222 | CLIENTE FICTÍCIO
""",
        f"""Valor da fatura: R$ 46,00
Valor da fatura: R$ 10,00
21
DEZ
LOJA A 40,00
ENCARGOS CRÉDITO PARCELADO (01/03) 5,00
IOF PARCELADO (01/03) 1,00
{HEADER}
Cartão: Final 3333 | CLIENTE FICTÍCIO
04
JAN
ESTORNO LOJA -10,00
LOJA D 20,00
Total da fatura (final 1111 + 2222 + 3333): R$ 136,00
Resumo das despesas Real
Saldo anterior 100,00
(-)Pagamentos/Créditos: 110,00
(+)Despesas nacionais: 146,00
Pagamento mínimo: 13,60
(=)Total da fatura: 136,00
""",
        "Encargos e instruções de financiamento\n05\nJAN\nEXEMPLO DE JUROS 1,99\n",
    ]


def native_pdf(pages: list[str]) -> bytes:
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Courier"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for text in pages:
        page = writer.add_blank_page(width=850, height=1000)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        instructions = [b"BT /F1 10 Tf 12 TL 30 960 Td"]
        for line in text.splitlines():
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            instructions.append(b"(" + escaped.encode("cp1252") + b") Tj T*")
        instructions.append(b"ET")
        stream = DecodedStreamObject()
        stream.set_data(b"\n".join(instructions))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.add_metadata({"/CreationDate": "D:20270210103000"})
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def prohibit_ocr(monkeypatch) -> None:
    monkeypatch.setattr(pdf_parser_module, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser_module, "is_textract_forced", lambda: False)

    def unexpected(*args, **kwargs):
        pytest.fail("Readable complete invoices must not invoke OCR or generic layout recovery")

    monkeypatch.setattr(pdf_parser_module, "_extract_pdf_page_texts_with_ocr", unexpected)
    monkeypatch.setattr(pdf_parser_module, "_try_parse_native_layout_text", unexpected)


def test_recognizes_split_day_month_invoice_without_guessing_bank() -> None:
    text = "\n".join(invoice_pages())
    inference = infer_pdf_layout(text)
    assert inference.layout_name == LAYOUT
    assert not inference.used_fallback
    assert resolve_bank_name(layout_inference_name=LAYOUT, extracted_text=text) is None


@pytest.mark.parametrize(
    "text",
    [
        "Data de vencimento: 20/01/2026\nTotal da fatura: 100,00\n01/01 LOJA 100,00",
        "EXTRATO CONTA CORRENTE\nData Lançamentos Valor (R$)\nSaldo anterior 100,00",
    ],
)
def test_common_invoice_or_statement_terms_do_not_select_this_layout(text: str) -> None:
    assert infer_pdf_layout(text).layout_name != LAYOUT


def test_native_multi_card_invoice_preserves_all_movements_and_source_pages(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    result = parse_pdf_transactions(native_pdf(invoice_pages()))
    assert result.layout.layout_name == LAYOUT
    assert result.parse_metrics["selected_parser"] == "layout_specific_digital_credit_card"
    assert [row.amount for row in result.transactions] == [-40, -10, 100, -30, -40, -5, -1, 10, -20]
    assert [row.date for row in result.transactions] == [
        "2025-12-21",
        "2025-12-21",
        "2026-01-02",
        "2026-01-02",
        "2025-12-21",
        "2025-12-21",
        "2025-12-21",
        "2026-01-04",
        "2026-01-04",
    ]
    assert [row.source_page for row in result.canonical_transactions] == [1, 1, 2, 2, 3, 3, 3, 3, 3]
    assert all(row.source_line for row in result.canonical_transactions)
    assert [row.description.split("CARTÃO FINAL ")[-1] for row in result.transactions] == [
        "1111",
        "1111",
        "1111",
        "1111",
        "2222",
        "2222",
        "2222",
        "3333",
        "3333",
    ]
    assert "(03/10)" in result.transactions[3].description
    assert all("SALDO" not in row.description and "EXEMPLO" not in row.description for row in result.transactions)
    assert result.parse_metrics["textract_attempted"] == 0


def test_pipeline_exports_credit_card_debt_balances_and_keeps_distinct_cards(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    data = (
        build_default_conversion_pipeline()
        .run(filename="fatura-sintetica.pdf", raw_bytes=native_pdf(invoice_pages()), analysis_id="synthetic-invoice")
        .analysis_data
    )
    assert data.transactions_total == 9
    assert data.opening_balance == -100
    assert data.closing_balance == -136
    assert data.total_inflows == 110
    assert data.total_outflows == -146
    assert data.ofx_account_type == "credit_card"
    assert data.potential_duplicates == 0


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("R$ 136,00", "R$ 137,00"),
        ("20/01/2026", ""),
        ("20/01/2026", "31/02/2026"),
        ("21\nDEZ\nLOJA A", "32\nDEZ\nLOJA A"),
        ("LOJA B 10,00", "LOJA B SEM VALOR"),
        ("SALDO ANTERIOR 100,00", "SALDO ANTERIOR 99,00"),
        ("(=)Total da fatura: 136,00", "(=)Total da fatura: 137,00"),
        ("(-)Pagamentos/Créditos: 110,00", "(-)Pagamentos/Créditos: 111,00"),
        ("(+)Despesas nacionais: 146,00", "(+)Despesas nacionais: 147,00"),
        ("Cartão: Final 1111 |", "Cartão sem identificação |"),
    ],
)
def test_incomplete_or_inconsistent_reading_is_rejected_without_paid_ocr(monkeypatch, old, new) -> None:
    prohibit_ocr(monkeypatch)
    pages = [page.replace(old, new) for page in invoice_pages()]
    with pytest.raises(InvalidFileContentError) as caught:
        parse_pdf_transactions(native_pdf(pages))
    assert type(caught.value).__name__ == "PdfFinancialValidationError"
    assert caught.value._parse_observability["ocr_retry_skip_reason"] == "financial_validation_failed"


def test_missing_grand_total_does_not_fall_back_to_partial_transactions(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    pages = invoice_pages()
    pages[2] = pages[2].split("Total da fatura (final")[0]
    with pytest.raises(InvalidFileContentError) as caught:
        parse_pdf_transactions(native_pdf(pages))
    assert type(caught.value).__name__ == "PdfFinancialValidationError"


def test_wrapped_description_and_date_group_continue_across_page(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    pages = invoice_pages()
    pages[0] = pages[0].replace("LOJA B 10,00\n", "LOJA B\n")
    pages[1] = pages[1].replace("R$ 80,00\n", "R$ 80,00\nCOMPLEMENTO 10,00\n", 1)
    result = parse_pdf_transactions(native_pdf(pages))
    assert len(result.transactions) == 9
    assert result.transactions[1].description == "LOJA B COMPLEMENTO CARTÃO FINAL 1111"
    assert result.canonical_transactions[1].source_page == 2


def test_settled_invoice_with_zero_total_is_supported(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    pages = invoice_pages()
    pages[2] = pages[2].replace("LOJA D 20,00", "LOJA D 20,00\nPAGAMENTO COMPLEMENTAR -136,00")
    pages = [p.replace("136,00", "0,00").replace("110,00", "246,00") if i != 2 else p for i, p in enumerate(pages)]
    pages[2] = pages[2].replace("R$ 136,00", "R$ 0,00").replace("fatura: 136,00", "fatura: 0,00").replace("110,00", "246,00")
    data = (
        build_default_conversion_pipeline()
        .run(filename="quitada.pdf", raw_bytes=native_pdf(pages), analysis_id="synthetic-paid")
        .analysis_data
    )
    assert data.transactions_total == 10
    assert data.closing_balance == 0


def test_merchant_normalization_keeps_card_reference(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    pages = [page.replace("LOJA A", "UBER VIAGEM") for page in invoice_pages()]
    data = (
        build_default_conversion_pipeline()
        .run(filename="cartoes.pdf", raw_bytes=native_pdf(pages), analysis_id="synthetic-cards")
        .analysis_data
    )
    assert data.preview_transactions[0].description == "UBER CARTAO FINAL 1111"
    assert data.preview_transactions[4].description == "UBER CARTAO FINAL 2222"
    assert data.potential_duplicates == 0


def test_same_card_possible_duplicates_are_still_flagged(monkeypatch) -> None:
    prohibit_ocr(monkeypatch)
    pages = [page.replace("136,00", "176,00").replace("146,00", "186,00") for page in invoice_pages()]
    pages[0] = pages[0].replace("LOJA A 40,00", "LOJA A 40,00\nLOJA A 40,00")
    data = (
        build_default_conversion_pipeline()
        .run(filename="duplicadas.pdf", raw_bytes=native_pdf(pages), analysis_id="synthetic-duplicates")
        .analysis_data
    )
    assert data.potential_duplicates == 1
    assert data.preview_transactions[0].reconciliation_status == "grouped"
    assert data.preview_transactions[1].reconciliation_status == "grouped"
