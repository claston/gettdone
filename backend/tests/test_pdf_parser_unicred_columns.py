from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject

from app.application import pdf_parser
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.ofx_writer import build_ofx_statement


def build_unicred_columns_pdf(*, bank="UNICRED", wrong_balance=False, conflicting_credit=False,
                              document_heading="Nr.Docum.", credit_description="PAGAMENTO RECEBIDO",
                              missing_movement=False, iof_description="IOF") -> bytes:
    """Fictional native statement: unsigned columns, wrapped names and nonfinancial notices."""
    writer = PdfWriter()
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Courier"), NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        NameObject("/FirstChar"): NumberObject(32), NameObject("/LastChar"): NumberObject(255),
        NameObject("/Widths"): ArrayObject([NumberObject(600)] * 224),
    })
    font_ref = writer._add_object(font)
    for page_index in range(2):
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
        })
        ops = []

        def cell(x, y, text):
            encoded = text.encode("cp1252").hex()
            ops.append(f"BT /F1 10 Tf 1 0 0 1 {x} {y} Tm <{encoded}> Tj ET")

        def money(right, y, text):
            cell(right - len(text) * 6, y, text)

        cell(25, 760, bank)
        cell(25, 740, "EXTRATO DE CONTA CORRENTE")
        cell(25, 720, "Conta: 123456-7 Agência: 0001")
        # Composite TJ header reproduces pypdf's expanded horizontal spacing.
        tokens = [(25, "Data"), (95, document_heading), (390, "Débito"), (465, "Crédito"), (550, "Saldo")]
        pieces = []
        previous_right = 25
        for x, label in tokens:
            pieces.extend([str(-(x - previous_right) * 100), "<" + label.encode("cp1252").hex() + ">"])
            previous_right = x + len(label) * 6
        ops.append("BT /F1 10 Tf 1 0 0 1 25 700 Tm [" + " ".join(pieces) + "] TJ ET")
        cell(155, 700, "Histórico")
        cell(155, 680, "Saldo Anterior" if page_index == 0 else "De Transporte")
        money(580, 680, "1.000,00" if page_index == 0 else "1.340,00")
        if page_index == 0:
            rows = [
                ("04/03/2026", "SALDO DEV", iof_description, None if missing_movement else "10,00", None, "990,00"),
                ("04/03/2026", "CRED PIX", credit_description, None, "500,00", None),
                ("05/03/2026", "000123", "CHEQUE COMPENSADO", "120,00", None, "1.370,00"),
                ("05/03/2026", "000124", "ALTERAÇÃO TAXA 2,50", None, None, None),
                ("06/03/2026", "DEB PIX", "DEBITO PARA CLIENTE", "30,00", None, "1.340,00"),
            ]
        else:
            rows = [
                ("09/03/2026", "CRED PIX", "CRED RECEBIMENTO PIX", None,
                 "-20,00" if conflicting_credit else "20,00", "1.360,00"),
                ("10/03/2026", "0", "PJ CONTA DEMONSTRAÇÃO", "15,00", None, "1.340,00" if wrong_balance else "1.345,00"),
            ]
        for index, (date, document, description, debit, credit, balance) in enumerate(rows):
            y = 650 - 36 * index
            cell(25, y, date)
            cell(95, y, document)
            # Draw numeric cells before the history, as in the production PDF.
            if debit:
                money(425, y - 2, debit)
            if credit:
                money(505, y - 2, credit)
            if balance:
                money(580, y - 1, balance)
            if document == "CRED PIX":
                cell(155, y - 12, "EMPRESA FICTÍCIA")
            cell(155, y - 1, description)
        footer_y = 650 - 36 * (len(rows) - 1) - 20
        cell(130, footer_y, "A Transportar" if page_index == 0 else "Saldo Atual")
        money(580, footer_y, "1.340,00" if page_index == 0 else "1.345,00")
        content = DecodedStreamObject()
        content.set_data("\n".join(ops).encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("document_heading", ["Nr.Docum.", "N.Doc."])
def test_native_unicred_columns_recover_iof_and_exclude_rate_notice_without_ocr(monkeypatch, document_heading):
    def no_ocr(*args, **kwargs):
        raise AssertionError("Native Unicred columns must not invoke OCR")

    monkeypatch.setattr(pdf_parser, "is_textract_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser, "is_textract_forced", lambda: False)
    monkeypatch.setattr(pdf_parser, "is_pdf_ocr_enabled", lambda: True)
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", no_ocr)
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts_with_ocr", no_ocr)

    result = pdf_parser.parse_pdf_transactions(build_unicred_columns_pdf(document_heading=document_heading))

    assert result.parse_metrics["selected_parser"] == "layout_specific_unicred_native_columns"
    assert [t.amount for t in result.transactions] == [-10, 500, -120, -30, 20, -15]
    assert [t.description for t in result.transactions] == [
        "IOF", "PAGAMENTO RECEBIDO EMPRESA FICTÍCIA", "CHEQUE COMPENSADO", "DEBITO PARA CLIENTE",
        "CRED RECEBIMENTO PIX EMPRESA FICTÍCIA", "PJ CONTA DEMONSTRAÇÃO",
    ]
    assert [t.running_balance for t in result.canonical_transactions] == [990, None, 1370, 1340, 1360, 1345]
    assert [t.external_reference_id for t in result.canonical_transactions] == [
        "SALDO DEV", "CRED PIX", "000123", "DEB PIX", "CRED PIX", "0",
    ]
    assert [t.source_page for t in result.canonical_transactions] == [1, 1, 1, 1, 2, 2]
    assert result.parse_metrics["balance_consistency_failed"] == 0
    assert result.parse_metrics["textract_attempted"] == 0
    assert result.parse_metrics["native_text_detected"] == 1
    assert build_ofx_statement(result.transactions).count("<STMTTRN>") == 6


def test_native_unicred_pipeline_preserves_bank_and_financial_totals(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    result = build_default_conversion_pipeline().run(
        filename="synthetic.pdf", raw_bytes=build_unicred_columns_pdf(), analysis_id="an_synthetic_unicred",
    ).analysis_data

    assert result.transactions_total == 6
    assert result.bank_name == "Unicred" and result.bank_code == "136"
    assert result.opening_balance == 1000
    assert result.closing_balance == 1345
    assert result.total_inflows == 520 and result.total_outflows == -175 and result.net_total == 345


def test_native_unicred_keeps_real_balance_discrepancies_visible():
    result = pdf_parser.parse_pdf_transactions(build_unicred_columns_pdf(wrong_balance=True))

    assert result.parse_metrics["selected_parser"] == "layout_specific_unicred_native_columns"
    assert result.parse_metrics["balance_consistency_failed"] == 1
    assert "balance_consistency_failed" in result.canonical_transactions[-1].warnings


@pytest.mark.parametrize("description", ["PAGAMENTO RECEBIDO", "UNICRED CLIENTE"])
def test_native_unicred_does_not_capture_other_bank(description):
    result = pdf_parser.parse_pdf_transactions(build_unicred_columns_pdf(bank="SICREDI", credit_description=description))

    assert result.parse_metrics["selected_parser"] != "layout_specific_unicred_native_columns"


def test_native_unicred_rejects_negative_value_in_credit_column():
    from app.application.errors import InvalidFileContentError

    with pytest.raises(InvalidFileContentError, match="credit column"):
        pdf_parser.parse_pdf_transactions(build_unicred_columns_pdf(conflicting_credit=True))


def test_native_unicred_rejects_incomplete_financial_row():
    from app.application.errors import InvalidFileContentError

    with pytest.raises(InvalidFileContentError, match="missing monetary cells"):
        pdf_parser.parse_pdf_transactions(build_unicred_columns_pdf(missing_movement=True))


@pytest.mark.parametrize("description", ["TRANSPORTE", "SALDO ATUAL"])
def test_native_unicred_preserves_financial_histories_that_resemble_footer_labels(description):
    result = pdf_parser.parse_pdf_transactions(build_unicred_columns_pdf(iof_description=description))

    assert result.transactions[0].description == description
    assert len(result.transactions) == 6


@pytest.mark.parametrize("scale", [0.75, 1.5, 3])
def test_native_unicred_column_positions_follow_page_scaling(scale):
    reader = PdfReader(BytesIO(build_unicred_columns_pdf()))
    writer = PdfWriter()
    for page in reader.pages:
        page.scale_by(scale)
        writer.add_page(page)
    raw = BytesIO()
    writer.write(raw)

    result = pdf_parser.parse_pdf_transactions(raw.getvalue())

    assert [t.amount for t in result.transactions] == [-10, 500, -120, -30, 20, -15]
    assert result.transactions[1].description == "PAGAMENTO RECEBIDO EMPRESA FICTÍCIA"
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_native_unicred_preserves_physical_page_numbers_after_blank_page():
    reader = PdfReader(BytesIO(build_unicred_columns_pdf()))
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    for page in reader.pages:
        writer.add_page(page)
    raw = BytesIO()
    writer.write(raw)

    result = pdf_parser.parse_pdf_transactions(raw.getvalue())

    assert [t.source_page for t in result.canonical_transactions] == [2, 2, 2, 2, 3, 3]
    assert result.source_page_texts[0] == ""
    assert result.parse_metrics["page_count"] == 3
