"""Fictional regressions for the second October 28 failure audit."""

from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter, Transformation
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NumberObject, RectangleObject, TextStringObject

from app.application import pdf_parser
from app.application.conversion.contracts.preflight import DocumentPreflightResult
from app.application.conversion.document_preflight_service import DocumentPreflightService
from app.application.errors import InvalidFileContentError, MaxPagesPerFileExceededError
from app.application.parsers.pdf.native_table_source import read_native_table_source
from app.application.parsers.pdf.statement_blocks import statement_blocks
from tests.fixtures.recovery_acceptance import grid_pdf
from tests.test_oct27_mercado_grid import mercado_grid
from tests.test_oct28_remaining_statements import sicoob_descending
from tests.test_recovery_sweep_ocr import extraction
from tests.test_sicredi_financial_columns import matrix_text, parse


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")


def caixa_tilted_rows():
    return extraction(
        [
            ("GERENCIADOR CAIXA Extrato por período Data Mov.", 0.05, 0.02),
            ("Mês: Julho/2026", 0.05, 0.05),
            ("Período: 1 - 31", 0.05, 0.08),
            ("Histórico", 0.35, 0.12),
            ("Nr. Doc.", 0.2, 0.12),
            ("Valor", 0.65, 0.12),
            ("Saldo", 0.80, 0.12),
            ("SALDO ANTERIOR", 0.35, 0.20),
            ("0,00", 0.65, 0.197),
            ("100,00 C", 0.80, 0.194),
            ("01/07/2026", 0.05, 0.219),
            ("1001", 0.2, 0.219),
            ("PAGAMENTO TESTE", 0.35, 0.219),
            ("20,00 D", 0.65, 0.216),
            ("80,00 C", 0.80, 0.213),
            ("02/07/2026", 0.05, 0.26),
            ("1002", 0.2, 0.26),
            ("CRÉDITO TESTE", 0.35, 0.26),
            ("10,00 C", 0.65, 0.257),
            ("90,00 C", 0.80, 0.254),
        ]
    )


def test_caixa_tilt_does_not_merge_opening_and_first_transaction_balances():
    source = caixa_tilted_rows()
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [-20, 10]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 90
    assert result.source_extraction == source


def bb_duplicate_opening(*, duplicate=True, conflicting=False):
    cells = [
        ("Cliente Conta atual Dt. balancete Dt. movimento Ag. origem", 0.1, 0.02),
        ("Histórico", 0.35, 0.1),
        ("Documento", 0.65, 0.1),
        ("Valor R$", 0.75, 0.1),
        ("Saldo", 0.86, 0.1),
        ("31/07/2026", 0.1, 0.2),
        ("Saldo Anterior", 0.35, 0.2),
        ("100,00 C", 0.75, 0.2),
        ("03/08/2026", 0.1, 0.3),
        ("821 Pix Recebido", 0.35, 0.3),
        ("1001", 0.65, 0.3),
        ("50,00 C", 0.75, 0.3),
        ("150,00 C", 0.86, 0.3),
        ("31/08/2026", 0.1, 0.4),
        ("999 S A L D O", 0.35, 0.4),
        ("150,00 C", 0.86, 0.4),
    ]
    if duplicate:
        cells.insert(9, ("101,00 C" if conflicting else "100,00 C", 0.86, 0.2))
    return extraction(cells)


@pytest.mark.parametrize("duplicate", [False, True])
def test_bb_printed_opening_in_value_column_is_not_an_extra_movement(duplicate):
    source = bb_duplicate_opening(duplicate=duplicate)
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [50]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 150
    assert result.source_extraction == source


def test_bb_conflicting_printed_opening_copies_remain_ambiguous():
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(bb_duplicate_opening(conflicting=True))


def test_bb_reference_and_closing_follow_the_skewed_financial_row():
    source = bb_duplicate_opening()
    page = source.pages[0]
    cells = []
    for line in page.lines:
        if line.bbox["left"] >= 0.65:
            line = replace(line, bbox={**line.bbox, "top": line.bbox["top"] - 0.01 * (line.bbox["left"] - 0.1)})
        cells.append(line)
    source = replace(source, pages=[replace(page, lines=cells)])
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [t.amount for t in result.transactions] == [50]
    assert result.canonical_transactions[0].external_reference_id == "1001"
    assert result.parse_metrics["verified_closing_balance"] == 150


def test_bb_merged_final_control_excludes_nearby_footer_balances():
    source = bb_duplicate_opening()
    page = source.pages[0]
    lines = [replace(line, text="00000999SALDO") if line.text == "999 S A L D O" else line for line in page.lines]
    closing = next(line for line in lines if line.text == "150,00 C" and line.bbox["top"] == 0.4)
    lines.append(replace(closing, id="footer", line_index=99, text="999,00 C", bbox={**closing.bbox, "top": 0.42}))
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[replace(page, lines=lines)]))
    assert [t.amount for t in result.transactions] == [50]
    assert result.parse_metrics["verified_closing_balance"] == 150


@pytest.mark.parametrize("printed_sign", ["D", None])
def test_caixa_detached_printed_sign_is_used_and_missing_sign_stays_ambiguous(printed_sign):
    source = caixa_tilted_rows()
    page = source.pages[0]
    cells = [replace(line, text="20,00") if line.text == "20,00 D" else line for line in page.lines]
    amount = next(line for line in cells if line.text == "20,00")
    if printed_sign:
        cells.append(
            replace(
                amount,
                id="detached-sign",
                line_index=99,
                text=printed_sign,
                bbox={**amount.bbox, "left": amount.bbox["left"] + amount.bbox["width"] + 0.005, "width": 0.01},
            )
        )
    source = replace(source, pages=[replace(page, lines=cells)])
    if printed_sign:
        result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
        assert [t.amount for t in result.transactions] == [-20, 10]
        assert result.source_extraction == source
    else:
        with pytest.raises(InvalidFileContentError):
            pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


def test_sicredi_summary_ignores_separate_forecast_panel_without_creating_movements():
    text = matrix_text().replace("DEBITOS.............: 7.564,91", "DEBITOS.............: 7.564,91   |  05/11/2026 JUROS -119,85")
    text = text.replace("CREDITOS............: 2.600,00", "CREDITOS............: 2.600,00   |  05/11/2026 JUROS -16,29")
    result = parse(text)
    assert [t.amount for t in result.transactions] == [2600, -250, -11.61, -7303.3]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def test_sicredi_vertical_bars_inside_history_do_not_end_a_transaction():
    text = matrix_text().replace("RECEB. COB HIBRIDA CRED_COBPIX", "RECEB. | COB HIBRIDA | PIX   ")
    result = parse(text)
    assert [t.amount for t in result.transactions] == [2600, -250, -11.61, -7303.3]
    assert "|" in result.transactions[0].description


def test_sicoob_right_aligned_large_value_can_start_before_its_header(monkeypatch):
    from app.application.parsers.pdf import followup_native_tables

    original = followup_native_tables.read_native_table_source

    def wide(raw, **kwargs):
        source = original(raw, **kwargs)
        return replace(
            source,
            pages=[
                replace(
                    p,
                    lines=[
                        replace(line, bbox={**line.bbox, "left": 0.69, "width": line.bbox["width"] + 0.06})
                        if line.text == "200,00D"
                        else line
                        for line in p.lines
                    ],
                )
                for p in source.pages
            ],
        )

    monkeypatch.setattr(followup_native_tables, "read_native_table_source", wide)
    result = pdf_parser.parse_pdf_transactions(sicoob_descending())
    assert [t.amount for t in result.transactions] == [50, -200]
    assert result.parse_metrics["balance_consistency_failed"] == 0


def mercado_without_continuation_header():
    writer = PdfWriter(clone_from=BytesIO(mercado_grid(split_description=True)))
    headers = {"Data", "Descrição", "ID da operação", "Valor", "Saldo"}
    page = writer.pages[1]
    stream = page.get_contents()
    stream.operations = [
        (operands, operator) for operands, operator in stream.operations if not (operator == b"Tj" and str(operands[0]) in headers)
    ]
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_mercado_continuation_without_header_keeps_shared_reference_and_page_provenance():
    from app.application.parsers.pdf.mercado_pago_native_grid import parse_mercado_pago_native_grid

    raw = mercado_without_continuation_header()
    assert not any(line.text == "Data" for line in read_native_table_source(raw).pages[1].lines)
    statement = parse_mercado_pago_native_grid(raw, read_native_table_source(raw))
    assert statement is not None
    assert [r.transaction.amount for r in statement.rows] == [10, -5]
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [10, -5]
    assert [t.external_reference_id for t in result.canonical_transactions] == ["100000001"] * 2
    assert {p for p, _ in result.canonical_transactions[1].source_line_refs} == {1, 2}


def test_mercado_headerless_continuation_with_changed_columns_cannot_drop_rows():
    writer = PdfWriter(clone_from=BytesIO(mercado_without_continuation_header()))
    writer.pages[1].add_transformation(Transformation().translate(tx=180))
    output = BytesIO()
    writer.write(output)
    with pytest.raises(InvalidFileContentError):
        pdf_parser.parse_pdf_transactions(output.getvalue())


def append_image_page(raw, *, white=True):
    writer = PdfWriter(clone_from=BytesIO(raw))
    page = writer.add_blank_page(width=1000, height=800)
    image = DecodedStreamObject()
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(1),
            NameObject("/Height"): NumberObject(1),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    image.set_data(b"\xff\xff\xff" if white else b"\x00\x00\x00")
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/XObject"): DictionaryObject({NameObject("/White"): writer._add_object(image)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"q 1000 0 0 800 0 0 cm /White Do Q")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_visually_white_image_page_is_preserved_as_an_empty_physical_page():
    raw = append_image_page(sicoob_descending())
    source = read_native_table_source(raw)
    assert [p.page_number for p in source.pages] == [1, 2]
    assert not source.pages[1].lines
    result = pdf_parser.parse_pdf_transactions(raw)
    assert [t.amount for t in result.transactions] == [50, -200]
    assert all(p == 1 for t in result.canonical_transactions for p, _ in t.source_line_refs)


def test_image_page_with_real_content_cannot_be_silently_ignored():
    with pytest.raises(InvalidFileContentError):
        read_native_table_source(append_image_page(sicoob_descending(), white=False))


def brb_lot_statement(*, rotated=False):
    raw = grid_pdf(
        [
            [
                ("Cliente: EMPRESA FICTÍCIA", 0.03, 0.03),
                ("Ag.", 0.06, 0.07),
                ("Conta", 0.11, 0.07),
                ("Emissão", 0.24, 0.07),
                ("Extrato", 0.36, 0.07),
                ("Saldo Anterior", 0.51, 0.07),
                ("0001 - 12345.6 CCOR", 0.05, 0.10),
                ("30/01/2026", 0.23, 0.10),
                ("100,00", 0.55, 0.10),
                ("Lote", 0.04, 0.15),
                ("Ori.", 0.085, 0.15),
                ("Data", 0.127, 0.15),
                ("Histórico", 0.22, 0.15),
                ("Doc", 0.38, 0.15),
                ("Lançamento", 0.43, 0.15),
                ("Saldo", 0.565, 0.15),
                ("0063423 525 02/01/26 DEBITO PIX", 0.03, 0.20),
                ("20,00-", 0.455, 0.20),
                ("80,00", 0.55, 0.20),
                ("8888888 033# 06/01/26 CREDITO DE TED 634665", 0.03, 0.25),
                ("50,00", 0.45, 0.25),
                ("0070058 525 06/01/26 CREDITO PIX", 0.03, 0.30),
                ("0,01", 0.47, 0.30),
                ("130,01", 0.55, 0.30),
            ]
        ]
    )
    if rotated:
        writer = PdfWriter(clone_from=BytesIO(raw))
        page = writer.pages[0]
        page.add_transformation(Transformation().rotate(90).translate(tx=800))
        page.mediabox = RectangleObject((0, 0, 800, 1000))
        page.rotate(90)
        output = BytesIO()
        writer.write(output)
        raw = output.getvalue()
    return raw


@pytest.mark.parametrize("rotated", [False, True])
def test_lot_statement_preserves_origin_short_dates_signs_and_rotated_source(rotated):
    result = pdf_parser.parse_pdf_transactions(brb_lot_statement(rotated=rotated))
    assert [(t.date, t.amount) for t in result.transactions] == [("2026-01-02", -20), ("2026-01-06", 50), ("2026-01-06", 0.01)]
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130.01
    assert all(t.source_regions for t in result.canonical_transactions)


def banrisul_month_source():
    return extraction(
        [
            ("BANRISUL", 0.06, 0.02),
            ("PERIODO: ABRIL/2026", 0.06, 0.05),
            ("DIA", 0.06, 0.10),
            ("HISTORICO", 0.11, 0.10),
            ("DOCUMENTO", 0.35, 0.10),
            ("V A L O R", 0.60, 0.10),
            ("MOVIMENTOS DA CONTA CORRENTE", 0.06, 0.13),
            ("SALDO ANT EM 31/03/2026", 0.11, 0.18),
            ("100,00", 0.60, 0.18),
            ("MOVIMENTOS ABR/2026", 0.11, 0.21),
            ("01", 0.06, 0.25),
            ("PIX", 0.11, 0.25),
            ("100001", 0.35, 0.25),
            ("20,00-", 0.60, 0.245),
            ("NOME: FORNECEDOR FICTÍCIO", 0.12, 0.28),
            ("CRÉDITO VERO", 0.11, 0.32),
            ("100002", 0.35, 0.32),
            ("50,00", 0.60, 0.315),
            ("SALDO NA DATA", 0.11, 0.36),
            ("130,00", 0.60, 0.355),
            ("02", 0.06, 0.40),
            ("REND CDB AUT", 0.11, 0.40),
            ("0000RC", 0.35, 0.40),
            ("0,01", 0.60, 0.40),
            ("SALDO NA DATA", 0.11, 0.44),
            ("130,01", 0.60, 0.44),
        ]
    )


def test_monthly_day_only_statement_uses_printed_context_and_all_balance_controls():
    source = banrisul_month_source()
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)
    assert [(t.date, t.amount) for t in result.transactions] == [("2026-04-01", -20), ("2026-04-01", 50), ("2026-04-02", 0.01)]
    assert "FORNECEDOR FICTÍCIO" in result.transactions[0].description
    assert result.parse_metrics["verified_opening_balance"] == 100
    assert result.parse_metrics["verified_closing_balance"] == 130.01
    assert result.source_extraction == source


def test_monthly_detector_does_not_override_the_existing_portal_reader():
    from app.application.parsers.pdf.positional_monthly_statements import positional_kind
    from tests.test_oct20_followup_native import portal_pages
    from tests.test_oct20_followup_ocr import banrisul_ocr

    assert positional_kind("\n".join(portal_pages())) is None
    assert positional_kind("\n".join(line.text for page in banrisul_ocr().pages for line in page.lines)) is None


def test_monthly_statement_with_unreadable_amount_never_returns_a_partial_success():
    source = banrisul_month_source()
    page = source.pages[0]
    source = replace(
        source, pages=[replace(page, lines=[replace(line, text="2?,00-") if line.text == "20,00-" else line for line in page.lines])]
    )
    with pytest.raises(InvalidFileContentError):
        pdf_parser._parse_scanned_pdf_with_textract_text_pages(source)


def test_monthly_day_only_ocr_documents_are_combined_through_shared_statement_blocks():
    source = banrisul_month_source()
    page = source.pages[0]
    changes = {
        "PERIODO: ABRIL/2026": "PERIODO: MAIO/2026",
        "MOVIMENTOS ABR/2026": "MOVIMENTOS MAI/2026",
        "SALDO ANT EM 31/03/2026": "SALDO ANT EM 30/04/2026",
        "100,00": "130,01",
        "130,00": "160,01",
        "130,01": "160,02",
    }
    second = replace(
        page,
        page_number=2,
        lines=[replace(line, id="p2-" + line.id, page_number=2, text=changes.get(line.text, line.text)) for line in page.lines],
    )
    result = pdf_parser._parse_scanned_pdf_with_textract_text_pages(replace(source, pages=[page, second]))
    assert [t.amount for t in result.transactions] == [-20, 50, 0.01, -20, 50, 0.01]
    assert [t.date[:7] for t in result.transactions] == ["2026-04"] * 3 + ["2026-05"] * 3
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert {p for t in result.canonical_transactions for p, _ in t.source_line_refs} == {1, 2}


def test_lot_origin_repeated_monthly_opening_is_a_new_scope_without_inventing_coverage():
    first = (
        "Cliente Conta Emissão 30/01/2026 Saldo Anterior\nLote Ori. Data Histórico Doc Lançamento Saldo\n"
        "0063423 525 02/01/26 DEBITO PIX 20,00-"
    )
    second = first.replace("30/01/2026", "28/02/2026").replace("02/01/26", "02/02/26")
    blocks = statement_blocks([first, second])
    assert [(b.start, b.stop, b.period) for b in blocks] == [(0, 1, None), (1, 2, None)]


def test_lot_origin_two_months_export_all_movements_with_review_for_unconfirmed_continuity():
    raw = brb_lot_statement()
    second = PdfWriter(clone_from=BytesIO(raw))
    stream = second.pages[0].get_contents()
    operations = []
    for operands, operator in stream.operations:
        if operator == b"Tj":
            text = str(operands[0])
            for old, new in [("30/01/2026", "28/02/2026"), ("02/01/26", "02/02/26"), ("06/01/26", "06/02/26")]:
                text = text.replace(old, new)
            operands = [TextStringObject(text)]
        operations.append((operands, operator))
    stream.operations = operations
    second.pages[0][NameObject("/Contents")] = second._add_object(stream)
    second_bytes = BytesIO()
    second.write(second_bytes)
    combined = PdfWriter()
    combined.append(BytesIO(raw))
    combined.append(BytesIO(second_bytes.getvalue()))
    output = BytesIO()
    combined.write(output)
    result = pdf_parser.parse_pdf_transactions(output.getvalue())
    assert [t.amount for t in result.transactions] == [-20, 50, 0.01] * 2
    assert [t.date[:7] for t in result.transactions] == ["2026-01"] * 3 + ["2026-02"] * 3
    assert result.parse_metrics["statement_blocks_count"] == 2
    assert result.parse_metrics["balance_bounds_unavailable"] == 1
    assert result.parse_metrics["export_recommendation"] == "review_recommended"
    assert {p for t in result.canonical_transactions for p, _ in t.source_line_refs} == {1, 2}


@pytest.mark.parametrize("pages,scanned,ceiling", [(20, True, 10), (23, True, 10), (283, False, 250), (677, False, 250)])
def test_existing_plan_and_native_page_limits_remain_enforced(monkeypatch, pages, scanned, ceiling):
    monkeypatch.setenv("PDF_OCR_MAX_PAGES", "12")
    identity = SimpleNamespace(max_pages_per_file=250, max_pages_per_file_ocr=10, max_upload_size_bytes=10 * 1024 * 1024)
    with pytest.raises(MaxPagesPerFileExceededError) as error:
        DocumentPreflightService().build_policy(
            identity=identity,
            filename="statement.pdf",
            staged_upload_size_bytes=1000,
            preflight_result=DocumentPreflightResult(scanned_likely=scanned, estimated_pages_count=pages),
        )
    assert error.value.max_pages_per_file == ceiling


def test_image_with_truncated_invisible_money_values_requires_visual_extraction():
    from app.application.native_text_quality import assess_native_text_quality
    from tests.test_embedded_ocr_quality import layered_pdf

    text = "SALDO ANTERIOR .684,29\nSALDO NA DATA .096,30\n" + "EXTRATO TESTE " * 8
    raw = layered_pdf(text)
    result = assess_native_text_quality(raw, [text])
    assert result.requires_visual_extraction
    assert "truncated_money_values" in result.evidence
