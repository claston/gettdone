import pytest

from app.application import pdf_parser
from app.application.errors import InvalidFileContentError
from app.application.pdf_parser import parse_pdf_transactions
from synthetic_pdf_corpus.native_text_pdf import generate_native_text_pdf
from tests.fixtures.empty_statements import inactive_santander_pages


def statement_lines(message: str) -> tuple[str, ...]:
    return (
        "Banco Teste - Extrato de conta corrente",
        "Agência: 0001 Conta: 12345-6",
        "Período: 01/09/2026 a 30/09/2026",
        "Saldo anterior: R$ 150,00",
        message,
        "Saldo final: R$ 150,00",
    )


def test_complete_native_santander_summary_without_movements_is_empty(monkeypatch):
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    raw = generate_native_text_pdf(inactive_santander_pages())
    result = build_default_conversion_pipeline().run(filename="extrato.pdf", raw_bytes=raw, analysis_id="an_empty")
    analysis = result.analysis_data
    assert analysis.transactions_total == 0
    assert analysis.bank_name == "Santander"
    assert analysis.bank_code == "033"
    assert analysis.pdf_processing_metrics["statement_no_activity"] == 1
    assert analysis.pdf_processing_metrics["selected_parser"] == "layout_confirmed_no_activity"
    # Pricing and economic indicators must never become account balances.
    assert analysis.opening_balance is None
    assert analysis.closing_balance is None


@pytest.mark.parametrize("mutation", ["missing_page", "blank_page", "unknown_value", "movement_header", "transaction", "no_account"])
def test_incomplete_or_conflicting_santander_template_is_not_confirmed_empty(mutation):
    from app.application.parsers.pdf.empty_statement import has_complete_santander_inactive_summary

    pages = ["\n".join(lines) for lines in inactive_santander_pages()]
    if mutation == "missing_page":
        pages.pop()
    elif mutation == "blank_page":
        pages[0] = ""
    elif mutation == "unknown_value":
        pages[2] += "\nVALOR NAO ASSOCIADO 120,00"
    elif mutation == "movement_header":
        pages[1] += "\nMovimentação\nData Descrição Documento Movimento (R$) Saldo (R$)"
    elif mutation == "transaction":
        pages[2] += "\n02/01 PIX RECEBIDO 125,00"
    else:
        pages[1] = pages[1].replace("12.345678-9", "ilegível")
    assert not has_complete_santander_inactive_summary(pages)


def test_ocr_without_positive_no_activity_message_cannot_use_native_template_proof():
    pages = ["\n".join(lines) for lines in inactive_santander_pages()]
    try:
        result = pdf_parser._parse_pdf_transactions_from_page_texts(pages)
    except InvalidFileContentError:
        return
    assert not result.parse_metrics.get("statement_no_activity")


@pytest.mark.parametrize("message", [
    "Sem movimentação no período.",
    "Não houve movimentações no período consultado.",
    "Nenhum lançamento no período.",
    "Não existem lançamentos para o período selecionado.",
    "Não há movimentações neste período.",
    "SEM MOVIMENTACAO",
    "Não foram encontrados lançamentos no período.",
])
def test_readable_empty_pdf_completes_without_inventing_transactions(message, monkeypatch):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    raw = generate_native_text_pdf((statement_lines(message),))
    result = parse_pdf_transactions(raw)
    assert result.transactions == []
    assert result.canonical_transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["selected_parser"] == "explicit_no_activity"
    assert result.source_page_texts and message in result.extracted_text


def test_empty_ocr_pages_use_the_same_statement_detection(monkeypatch):
    monkeypatch.setattr(pdf_parser, "_extract_pdf_page_texts_with_ocr", lambda *a, **k: [
        "\n".join(statement_lines("Sem movimentação no período.")),
    ])
    result = pdf_parser._parse_scanned_pdf_with_local_ocr_adapter(b"pdf", on_ocr_progress=None)
    assert result.transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1


@pytest.mark.parametrize("mode", ["text", "tables"])
def test_textract_empty_statement_keeps_evidence_and_completes_in_both_modes(monkeypatch, mode):
    from types import SimpleNamespace

    from app.application.document_extraction_models import ExtractedLine, ExtractedPage, RawDocumentExtraction

    extraction = RawDocumentExtraction(provider="aws_textract", document_hash="synthetic", metrics={}, pages=[
        ExtractedPage(page_number=1, lines=[
            ExtractedLine(id=f"line-{i}", page_number=1, line_index=i, text=text)
            for i, text in enumerate(statement_lines("Sem movimentação no período."))
        ]),
    ])
    monkeypatch.setattr(pdf_parser, "TextractGateway", lambda: SimpleNamespace(
        analyze_pdf=lambda **kwargs: {"metrics": {"textract_mode": mode}},
    ))
    monkeypatch.setattr(pdf_parser, "map_textract_blocks_to_extraction", lambda **kwargs: extraction)
    result = pdf_parser._parse_scanned_pdf_with_textract_gateway(b"synthetic")
    assert result.transactions == []
    assert result.parse_metrics["statement_no_activity"] == 1
    assert result.parse_metrics["extraction_provider"] == "aws_textract"
    assert result.source_extraction is extraction
    assert result.source_layout_lines[0][0].text.startswith("Banco Teste")


@pytest.mark.parametrize("first", [True, False])
def test_unreadable_page_does_not_turn_partly_readable_pdf_into_an_empty_statement(monkeypatch, first):
    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    monkeypatch.setenv("PDF_OCR_ENABLED", "false")
    empty = statement_lines("Sem movimentação no período.")
    raw = generate_native_text_pdf(((), empty) if first else (empty, ()))
    with pytest.raises(InvalidFileContentError):
        parse_pdf_transactions(raw)


@pytest.mark.parametrize("text", [
    "Documento qualquer\nSem movimentação no período.",
    "Extrato de conta corrente\nAgência: 0001 Conta: 12345-6\nSem movimentação no período.",
    "\n".join(statement_lines("")),
    "\n".join(statement_lines("Sem movimentação no período.")) + "\n01/09/2026 PIX ilegível 12,00",
    "\n".join(statement_lines("Sem movimentação no período.")) + "\n01/09 PIX SALDO 12,00",
    "\n".join(statement_lines("Sem movimentação no período.")) + "\n01/09 PIX valor ilegível",
    "\n".join(statement_lines("Sem movimentação no período anterior.")),
])
def test_missing_or_conflicting_evidence_is_not_reported_as_empty(text):
    try:
        result = pdf_parser._parse_pdf_transactions_from_page_texts([text])
    except InvalidFileContentError:
        return
    assert not result.parse_metrics.get("statement_no_activity")


def test_one_empty_page_does_not_hide_movements_on_another_page():
    result = pdf_parser._parse_pdf_transactions_from_page_texts([
        "\n".join(statement_lines("Sem movimentação no período.")),
        "\n".join(statement_lines("01/09/2026 PIX RECEBIDO 12,00")),
    ])
    assert not result.parse_metrics.get("statement_no_activity")
    assert any(row.amount == 12 for row in result.transactions)


def test_empty_statement_ofx_contains_no_transaction_blocks():
    from app.application.ofx_writer import build_ofx_statement

    ofx = build_ofx_statement([])
    assert "<BANKTRANLIST>" in ofx
    assert "<STMTTRN>" not in ofx


@pytest.mark.parametrize("confirmed", [True, False])
def test_empty_snapshot_editing_requires_positive_confirmation(tmp_path, monkeypatch, confirmed):
    from app.application.conversion.result_persistence import persist_conversion_result
    from app.application.default_conversion_pipeline import build_default_conversion_pipeline
    from app.application.errors import AnalysisNotFoundError
    from app.application.storage_service import TempAnalysisStorage

    monkeypatch.setenv("TEXTRACT_ENABLED", "false")
    result = build_default_conversion_pipeline().run(filename="empty.pdf", analysis_id="an_empty_edit",
        raw_bytes=generate_native_text_pdf((statement_lines("Sem movimentação no período."),)))
    result.analysis_data.pdf_processing_metrics["statement_no_activity"] = int(confirmed)
    storage = TempAnalysisStorage(root_dir=tmp_path / "analyses")
    persist_conversion_result(storage=storage, pipeline_result=result)
    if not confirmed:
        with pytest.raises(AnalysisNotFoundError):
            storage.apply_convert_edits("an_empty_edit", edits=[], bank_code="033")
        with pytest.raises(AnalysisNotFoundError):
            storage.get_convert_report_path("an_empty_edit", "ofx", bank_code="033")
        return
    saved = storage.apply_convert_edits("an_empty_edit", edits=[{
        "action": "insert", "date": "2026-09-10", "description": "Inclusão manual",
        "credit": 10, "debit": None,
    }])
    assert saved["transactions_total"] == 1
    assert saved["preview_transactions"][0]["transaction_id"]
    ofx = storage.get_convert_report_path("an_empty_edit", "ofx").read_text()
    assert ofx.count("<STMTTRN>") == 1
