"""Fictional reproductions of wrapped Caixa rows, mixed PDFs and pagination."""
import zlib
from io import BytesIO
from unittest.mock import Mock

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, NameObject

from app.application import pdf_parser
from app.application.conversion.document_preflight_service import DocumentPreflightService
from app.application.default_conversion_pipeline import build_default_conversion_pipeline
from app.application.errors import InvalidFileContentError, MaxPagesPerFileExceededError
from app.application.parsers.pdf.sicredi_combined_ocr import parse_sicredi_combined
from app.application.parsers.pdf.statement_completeness import validate_statement_completeness
from app.application.pdf_representation_classifier import classify_pdf_representation
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.test_embedded_ocr_quality import layered_pdf, visual_result
from tests.test_pdf_representation_classifier import _full_page_raster_pdf
from tests.test_recovery_sweep_ocr import extraction


def sicredi_decimal_dot_opening():
    return extraction([
        ("Sicredi Extrato Dados referentes ao período 01/09/2026 a 30/09/2026", .1, .02),
        ("Data", .1, .10), ("Descrição", .2, .10), ("Documento", .6, .10),
        ("Valor (R$)", .75, .10), ("Saldo (R$)", .88, .10),
        ("SALDO", .2, .15), ("1.00", .88, .15),
        ("02/09/2026", .1, .20), ("PAGAMENTO TESTE", .2, .20), ("100001", .6, .20),
        ("-40,00", .75, .20), ("-39,00", .88, .20),
        ("03/09/2026", .1, .25), ("RECEBIMENTO TESTE", .2, .25), ("100002", .6, .25),
        ("50,00", .75, .25), ("11,00", .88, .25),
    ])


def test_sicredi_ocr_decimal_dot_opening_keeps_printed_signs_and_all_controls():
    statement = parse_sicredi_combined(sicredi_decimal_dot_opening())
    assert statement.opening == 1
    assert statement.closing == 11
    assert [row.transaction.amount for row in statement.rows] == [-40, 50]
    assert statement.printed_balance_checks == 2


def test_sicredi_ocr_us_grouping_preserves_digits_and_signed_controls():
    from dataclasses import replace

    source = sicredi_decimal_dot_opening()
    values = {"-40,00": "2,000.00", "-39,00": "2,001.00", "50,00": "-2,000.00", "11,00": "1.00"}
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text=values.get(line.text, line.text)) for line in page.lines])])
    statement = parse_sicredi_combined(source)
    assert [row.transaction.amount for row in statement.rows] == [2000, -2000]
    assert statement.opening == statement.closing == 1


@pytest.mark.parametrize("balance", ["-1,999,00", "(-1.999,00"])
def test_sicredi_exact_separator_and_margin_annotation_keep_financial_controls(balance):
    from dataclasses import replace

    source = sicredi_decimal_dot_opening()
    values = {"-40,00": "-2.000,00", "-39,00": balance, "50,00": "2.000,00", "11,00": "1,00"}
    page = source.pages[0]
    source = replace(source, pages=[replace(page, lines=[replace(line, text=values.get(line.text, line.text)) for line in page.lines])])
    statement = parse_sicredi_combined(source)
    assert [row.transaction.amount for row in statement.rows] == [-2000, 2000]
    assert statement.opening == statement.closing == 1
    if balance.startswith("("):
        assert "manual_review_recommended" in statement.rows[0].warnings


def test_sicredi_merged_ocr_word_uses_token_order_and_original_evidence_box():
    from dataclasses import replace

    from app.application.document_extraction_models import ExtractedWord

    source = sicredi_decimal_dot_opening()
    page = source.pages[0]
    value = next(line for line in page.lines if line.text == "-40,00")
    balance = next(line for line in page.lines if line.text == "-39,00")
    box = {**value.bbox, "width": balance.bbox["left"] + balance.bbox["width"] - value.bbox["left"]}
    word = ExtractedWord("merged", 1, 1, "-40,00 -39,00", bbox=box, text_type="PRINTED")
    combined = replace(value, text=word.text, bbox=box, word_ids=(word.id,))
    lines = [combined if line.id == value.id else line for line in page.lines if line.id != balance.id]
    statement = parse_sicredi_combined(replace(source, pages=[replace(page, lines=lines, words=[word])]))
    assert [row.transaction.amount for row in statement.rows] == [-40, 50]
    assert statement.printed_balance_checks == 2
    assert statement.rows[0].source_regions
    assert any(region.left == box["left"] and region.width == box["width"] for region in statement.rows[0].source_regions)


@pytest.mark.parametrize("confirmed", [True, False])
def test_sicredi_trailing_ocr_mark_needs_independent_printed_balance(confirmed):
    from dataclasses import replace

    source = sicredi_decimal_dot_opening()
    page = source.pages[0]
    values = {"-40,00": "-40,004", "-39,00": "-39,00" if confirmed else "-38,00"}
    source = replace(source, pages=[replace(page, lines=[replace(line, text=values.get(line.text, line.text)) for line in page.lines])])
    if not confirmed:
        from app.application.errors import PdfFinancialValidationError

        with pytest.raises(PdfFinancialValidationError):
            parse_sicredi_combined(source)
        return
    statement = parse_sicredi_combined(source)
    assert [row.transaction.amount for row in statement.rows] == [-40, 50]
    assert "ocr_amount_precision_recovered" in statement.rows[0].warnings


def caixa_wrapped_lines():
    return [
        "CAIXA", "Extrato por período", "Lançamentos de 01/09/2026 a 30/09/2026",
        "Data de lançamento Data de movimento Documento Histórico Valor(R$) Saldo(R$)",
        "01/09/2026 01/09/2026 100001 CREDITO TESTE 140,00 R$ 140,00",
        "02/09/2026 02/09/2026 100002 ENVIO DE TED -", "30,00 R$ 110,00",
        "02/09/2026 02/09/2026 0 TARIFA MANUTENCAO CONTA", "A - 2,00 R$ 108,00",
        "03/09/2026 03/09/2026 100003 PAGAMENTO TESTE - 10,00 R$ 98,00",
        "03/09/2026 03/09/2026 0 SALDO DIA 0,00 R$ 98,00",
    ]


def caixa_empty_lines():
    return [
        "Extrato #PESSOAL", "Cliente: CLIENTE SINTETICO", "Conta: 1234/3701/000000012345-6",
        "Data: 01/10/2026 12:00:00", "Saldo próprio R$ 0,00 C", "Saldo bloqueado R$ 0,00 C",
        "Limite contratado R$ 0,00 C", "R$ 200,00 D", "Saldo:",
        "*650 - Sujeito a alteração até o final do expediente bancário",
        "Movimentações desde o dia 01/09/2026 até o dia 30/09/2026 Saldo anterior a 01/09/2026 R$ 150,00 D",
        "Não há lançamentos para o período.",
    ]


def test_caixa_wrapped_signed_amount_and_description_are_complete(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = generate_native_text_pdf((tuple(caixa_wrapped_lines()),))
    data = build_default_conversion_pipeline().run(filename="synthetic.pdf", raw_bytes=raw, analysis_id="an_wrapped").analysis_data
    assert [row.amount for row in data.report_transactions] == [140, -30, -2, -10]
    assert [row.date for row in data.report_transactions] == ["2026-09-01", "2026-09-02", "2026-09-02", "2026-09-03"]
    assert data.pdf_processing_metrics["balance_consistency_failed"] == 0
    assert "TARIFA MANUTENCAO CONTA A" in data.report_transactions[2].description


def test_caixa_wrapped_fee_that_zeros_balance_does_not_change_next_credit():
    lines = [line.replace("A - 2,00 R$ 108,00", "A - 110,00 R$ 0,00")
             .replace("PAGAMENTO TESTE - 10,00 R$ 98,00", "CREDITO TESTE 40,00 R$ 40,00")
             .replace("SALDO DIA 0,00 R$ 98,00", "SALDO DIA 0,00 R$ 40,00") for line in caixa_wrapped_lines()]
    result = pdf_parser._parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    assert [row.amount for row in result.transactions] == [140, -30, -110, 40]
    assert result.parse_metrics["balance_consistency_failed"] == 0


@pytest.mark.parametrize("conflicting", [False, True])
def test_caixa_ocr_checks_explicit_sign_before_currency(conflicting):
    from dataclasses import replace

    from tests.fixtures.late_recovery_statements import caixa_manager

    source = caixa_manager()
    pages = []
    for page in source.pages:
        lines = []
        for line in page.lines:
            if line.text == "R$ 30,00":
                line = replace(line, text=("+" if conflicting else "-") + " R$ 30,00")
            lines.append(line)
        pages.append(replace(page, lines=lines))
    source = replace(source, pages=pages)
    if conflicting:
        from app.application.errors import PdfFinancialValidationError

        with pytest.raises(PdfFinancialValidationError):
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    else:
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
        assert result.parse_metrics["balance_consistency_failed"] == 0


def test_wrapped_caixa_amount_never_borrows_from_next_dated_row():
    lines = caixa_wrapped_lines()
    lines.remove("30,00 R$ 110,00")
    parsed = pdf_parser._parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    assert all(row.amount != -30 for row in parsed.transactions)
    assert parsed.parse_metrics["export_recommendation"] == "review_recommended"


def test_caixa_daily_summaries_are_not_unparsed_movement_candidates():
    text = "\n".join([
        "CAIXA Extrato por período", "Cliente: EMPRESA SINTETICA Conta: 12345-6",
        "Mês: Setembro/2026", "Data Mov. Nr. Doc. Histórico Valor Saldo",
        "01/09/2026 000000 SALDO DIA 0,00 C 100,00 C",
        "02/09/2026 100001 CRED PIX 40,00 C 140,00 C",
        *[f"{day:02d}/09/2026 000000 SALDO DIA 0,00 C 140,00 C" for day in range(2, 20)],
        "20/09/2026 100002 PAGAMENTO TESTE 20,00 D 120,00 C",
        "20/09/2026 000000 SALDO DIA 0,00 C 120,00 C",
    ])
    parsed = pdf_parser._parse_pdf_transactions_from_page_texts([text])
    assert [row.amount for row in parsed.transactions] == [40, -20]
    assert parsed.parse_metrics["tabular_candidates_count"] == 2
    assert parsed.parse_metrics["confidence_band"] != "low"


@pytest.mark.parametrize("mutation", [None, "missing_first", "missing_middle", "missing_last", "duplicate"])
def test_sicredi_actual_page_headers_and_continuation_references(mutation):
    pages = [f"SICREDI EXTRATO\nPAG.: {number:05d}\n01/09/2026 PIX TESTE 10,00\n"
             + (f"Continua na página {number + 1:03d}" if number < 3 else "") for number in range(1, 4)]
    if mutation == "missing_first":
        pages.pop(0)
    elif mutation == "missing_middle":
        pages.pop(1)
    elif mutation == "missing_last":
        pages.pop()
    elif mutation == "duplicate":
        pages[1] = pages[0]
    assert validate_statement_completeness(pages) is (mutation is not None)


def test_caixa_personal_without_text_logo_confirms_inactivity_and_period_balance(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = generate_native_text_pdf((tuple(caixa_empty_lines()),))
    data = build_default_conversion_pipeline().run(filename="synthetic.pdf", raw_bytes=raw, analysis_id="an_empty").analysis_data
    assert data.transactions_total == 0
    assert data.bank_code == "104"
    assert data.bank_branch == "1234"
    assert data.account_number == "000000012345-6"
    assert data.opening_balance == -150
    assert data.closing_balance == -150  # The current snapshot of -200 is outside the statement period.
    assert data.pdf_processing_metrics["statement_no_activity"] == 1


@pytest.mark.parametrize("mutation", [
    "no_declaration", "transaction", "unknown_money", "no_account", "unknown_template", "malformed_balance",
])
def test_caixa_inactivity_never_swallows_ambiguous_or_active_documents(mutation):
    lines = caixa_empty_lines()
    if mutation == "no_declaration":
        lines.pop()
    elif mutation == "transaction":
        lines.append("02/09/2026 PIX RECEBIDO 10,00")
    elif mutation == "unknown_money":
        lines.append("VALOR NAO IDENTIFICADO R$ 10,00")
    elif mutation == "no_account":
        lines = [line for line in lines if not line.startswith("Conta:")]
    elif mutation == "malformed_balance":
        lines = [line.replace("150,00 D", "1.50,00 D") for line in lines]
    else:
        lines[0] = "Documento genérico"
    try:
        result = pdf_parser._parse_pdf_transactions_from_page_texts(["\n".join(lines)])
    except InvalidFileContentError:
        return
    assert not result.parse_metrics.get("statement_no_activity")


@pytest.mark.parametrize("merged", [False, True])
def test_sicredi_ocr_recovery_rejects_handwritten_monetary_evidence(merged):
    from dataclasses import replace

    from app.application.document_extraction_models import ExtractedWord
    from app.application.errors import PdfFinancialValidationError

    source = sicredi_decimal_dot_opening()
    page = source.pages[0]
    value = next(line for line in page.lines if line.text == "-40,00")
    balance = next(line for line in page.lines if line.text == "-39,00")
    text = "-40,00 -39,00" if merged else "-40,004"
    box = {**value.bbox, "width": balance.bbox["left"] + balance.bbox["width"] - value.bbox["left"]} if merged else value.bbox
    word = ExtractedWord("handwritten", 1, 1, text, bbox=box, text_type="HANDWRITING")
    lines = [replace(line, text=text, bbox=box, word_ids=(word.id,)) if line.id == value.id else line
             for line in page.lines if not merged or line.id != balance.id]
    with pytest.raises(PdfFinancialValidationError):
        parse_sicredi_combined(replace(source, pages=[replace(page, lines=lines, words=[word])]))


def sparse_mixed_pdf(pages=3, *, inline=False):
    writer = PdfWriter()
    if inline:
        for index in range(pages):
            raw = generate_native_text_pdf((("Extrato sintético emitido em 01/10/2026",) if index == 0 else (),))
            page = writer.add_page(PdfReader(BytesIO(raw)).pages[0])
            raster = (b"q 595 0 0 842 0 0 cm BI /W 600 /H 800 /CS /DeviceGray /BPC 8 /F /FlateDecode ID "
                      + zlib.compress(b"\xff" * (600 * 800)) + b" EI Q\n")
            stream = DecodedStreamObject()
            stream.set_data(raster + page.get_contents().get_data())
            page[NameObject("/Contents")] = writer._add_object(stream)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    header = layered_pdf("Extrato sintético emitido em 01/10/2026", invisible=False)
    writer.add_page(PdfReader(BytesIO(header)).pages[0])
    for _ in range(pages - 1):
        writer.add_page(PdfReader(BytesIO(_full_page_raster_pdf())).pages[0])
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize("inline", [False, True])
def test_sparse_native_header_does_not_hide_raster_statement_pages(monkeypatch, inline):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    monkeypatch.setenv("TEXTRACT_FORCE", "false")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    raw = sparse_mixed_pdf(inline=inline)
    assert classify_pdf_representation(raw).requires_visual_extraction
    preflight = DocumentPreflightService().inspect_raw_bytes(filename="synthetic.pdf", raw_bytes=raw)
    assert preflight.scanned_likely is True
    assert preflight.estimated_pages_count == 3
    result = pdf_parser.parse_pdf_transactions(raw, max_ocr_pages=3)
    assert gateway.call_count == 1
    assert result.transactions[0].amount == 100
    assert result.parse_metrics["textract_attempted"] == 1


def test_sparse_mixed_pdf_counts_all_pages_before_starting_remote_ocr(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    with pytest.raises(MaxPagesPerFileExceededError):
        pdf_parser.parse_pdf_transactions(sparse_mixed_pdf(), max_ocr_pages=2)
    gateway.assert_not_called()


def test_preflight_of_long_native_statement_does_not_reextract_every_page(monkeypatch):
    from pypdf._page import PageObject

    calls = []
    original = PageObject.extract_text

    def counted(page, *args, **kwargs):
        calls.append(page)
        return original(page, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", counted)
    lines = ("EXTRATO SINTETICO NATIVO COM TEXTO LEGIVEL E COMPLETO", "01/09/2026 TESTE 10,00")
    raw = generate_native_text_pdf(tuple(lines for _ in range(20)))
    result = DocumentPreflightService().inspect_raw_bytes(filename="synthetic.pdf", raw_bytes=raw)
    assert result.scanned_likely is False
    assert result.estimated_pages_count == 20
    assert len(calls) == 1


def test_sparse_native_header_does_not_hide_vector_statement_pages(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "true")
    gateway = Mock(return_value=visual_result())
    monkeypatch.setattr(pdf_parser, "_parse_scanned_pdf_with_textract_gateway", gateway)
    writer = PdfWriter()
    header = generate_native_text_pdf((("Extrato sintético emitido em 01/10/2026",),))
    for index in range(3):
        page = writer.add_page(PdfReader(BytesIO(header)).pages[0])
        stream = DecodedStreamObject()
        # Dense painted outlines reproduce a virtual printer's text as paths.
        paths = b"10 10 m 20 10 l 20 20 l 10 20 l h f\n" * 400
        stream.set_data(paths + (page.get_contents().get_data() if index == 0 else b""))
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    raw = output.getvalue()
    assert classify_pdf_representation(raw).requires_visual_extraction
    assert DocumentPreflightService().inspect_raw_bytes(filename="synthetic.pdf", raw_bytes=raw).scanned_likely
    assert pdf_parser.parse_pdf_transactions(raw, max_ocr_pages=3).transactions[0].amount == 100
    assert gateway.call_count == 1
    with pytest.raises(MaxPagesPerFileExceededError):
        pdf_parser.parse_pdf_transactions(raw, max_ocr_pages=2)
    assert gateway.call_count == 1
