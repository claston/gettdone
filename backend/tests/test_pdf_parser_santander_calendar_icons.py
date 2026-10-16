from io import BytesIO

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.application import pdf_parser
from app.application.ofx_writer import build_ofx_statement

# Entirely synthetic: the font maps byte 7f to a calendar icon in the private-use area.
SYNTHETIC_STATEMENT = """Lançamentos da conta
CLIENTE FICTICIO Agência 0001 Conta 123456-7
Março 2026
\uf166 Quarta, 04 de março de 2026
Débito em conta Débito R$ 21,50
Pagamento fornecedor Crédito R$ 300,00
\uf166 Segunda, 09 de março de 2026
Débito em conta Débito R$ 18,25
Transferência entre contas Crédito R$ 80,00
\uf166 Sexta, 13 de março de 2026
Tarifa extrato consolidado Débito R$ 5,00
\uf166 Segunda, 16 de março de 2026
PIX ENVIADO Débito R$ 40,00
PIX RECEBIDO Crédito R$ 125,00
Central de Atendimento Santander
"""


def build_synthetic_calendar_statement_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    cmap = DecodedStreamObject()
    cmap.set_data(b"""/CIDInit /ProcSet findresource begin
12 dict begin begincmap
/CIDSystemInfo << /Registry (Synthetic) /Ordering (Icons) /Supplement 0 >> def
/CMapName /SyntheticCalendar def /CMapType 2 def
1 begincodespacerange <00> <ff> endcodespacerange
1 beginbfchar <7f> <f166> endbfchar
endcmap CMapName currentdict /CMap defineresource pop end end
""")
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        NameObject("/ToUnicode"): writer._add_object(cmap),
    })
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
    })
    content = DecodedStreamObject()
    operators = [b"BT /F1 10 Tf 40 750 Td 16 TL"]
    for line in SYNTHETIC_STATEMENT.splitlines():
        encoded = line.replace("\uf166", "\x7f").encode("cp1252").hex().encode("ascii")
        operators.append(b"<" + encoded + b"> Tj T*")
    content.set_data(b"\n".join([*operators, b"ET"]))
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_santander_calendar_dates_parse_native_pdf_without_ocr(monkeypatch) -> None:
    raw = build_synthetic_calendar_statement_pdf()
    assert "\uf166 Quarta" in pdf_parser._read_native_pdf_page_texts(raw)[0]

    def unexpected_ocr(*args, **kwargs):
        raise AssertionError("Native calendar dates must not trigger OCR")

    monkeypatch.setattr(pdf_parser, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser, "is_textract_forced", lambda: False)
    monkeypatch.setattr(pdf_parser, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", unexpected_ocr)
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts_with_ocr", unexpected_ocr)

    result = pdf_parser.parse_pdf_transactions(raw)

    assert [row.date for row in result.transactions] == [
        "2026-03-04", "2026-03-04", "2026-03-09", "2026-03-09",
        "2026-03-13", "2026-03-16", "2026-03-16",
    ]
    assert [row.amount for row in result.transactions] == [-21.50, 300.00, -18.25, 80.00, -5.00, -40.00, 125.00]
    assert [row.description for row in result.transactions] == [
        "Débito em conta Débito", "Pagamento fornecedor Crédito",
        "Débito em conta Débito", "Transferência entre contas Crédito",
        "Tarifa extrato consolidado Débito", "PIX ENVIADO Débito", "PIX RECEBIDO Crédito",
    ]
    assert result.parse_metrics["textract_attempted"] == 0
    assert result.parse_metrics["native_text_detected"] == 1
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert all(row.source_page == 1 for row in result.canonical_transactions)
    assert all(row.running_balance is None for row in result.canonical_transactions)
    assert "\uf166 Quarta" in result.extracted_text
    assert build_ofx_statement(result.transactions).count("<STMTTRN>") == 7


@pytest.mark.parametrize("description", [
    "Central de Atendimento Santander",
    "PIX PARA Central de Atendimento Santander",
])
def test_santander_footer_detection_preserves_transaction_descriptions(description: str) -> None:
    text = f"Quarta, 04 de março de 2026\n{description} Débito R$ 10,00\nPIX recebido Crédito R$ 20,00"

    result = pdf_parser._parse_pdf_transactions_from_page_texts([text])

    assert [row.amount for row in result.transactions] == [-10.00, 20.00]
    assert result.transactions[0].description == f"{description} Débito"


def test_santander_contact_footer_does_not_hide_movements_on_next_page() -> None:
    pages = [
        "\uf166 Quarta, 04 de março de 2026\nPIX recebido Crédito R$ 20,00\n"
        "Central de Atendimento Santander 0800 000 0000\nSAC: texto legal fictício",
        "\uf166 Segunda, 09 de março de 2026\nPIX enviado Débito R$ 10,00\n"
        "Central de Atendimento Santander 0800 000 0000\nSAC: texto legal fictício",
    ]

    result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)

    assert [(row.date, row.description, row.amount) for row in result.transactions] == [
        ("2026-03-04", "PIX recebido Crédito", 20.00),
        ("2026-03-09", "PIX enviado Débito", -10.00),
    ]
