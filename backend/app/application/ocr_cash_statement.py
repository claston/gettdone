"""Shared OCR cash projection for positioned text and document tables."""

from dataclasses import replace

from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction
from app.application.ocr_financial_interpretation import CashTablePolicy, interpret_cash_table
from app.application.ocr_structure import OcrColumn, OcrTableSchema, reconstruct_ocr_table
from app.application.parsers.pdf.models import _ParsedTransaction
from app.application.parsers.pdf.verified_statements import VerifiedStatement
from app.application.pdf_layout_inference import infer_pdf_layout


def project_shared_cash_extraction(extraction, *, layout=None):
    from app.application.parsers.pdf.statement_blocks import combine_statement_results, slice_extraction, statement_blocks
    from app.application.pdf_parser import _build_verified_statement_result

    texts = tuple("\n".join(line.text for line in page.lines) for page in extraction.pages)
    layout = layout or infer_pdf_layout("\n".join(texts))
    if layout.layout_name != "asaas_extrato_conta_digital_movimentacoes_v1":
        raise InvalidFileContentError("Este modelo de OCR ainda não é compatível com o piloto Asaas.")
    blocks = statement_blocks(texts)
    if blocks:
        results = [project_shared_cash_extraction(slice_extraction(extraction, block), layout=layout) for block in blocks]
        return combine_statement_results(results, blocks, texts, original_extraction=extraction)
    # Pilot semantic policy; reconstruction and financial interpretation are shared.
    schema = OcrTableSchema(
        (
            OcrColumn("date", ("Data",), r"\d{2}/\d{2}/\d{4}"),
            OcrColumn("description", ("Movimentações", "Histórico", "Descrição")),
            OcrColumn("amount", ("Valor", "Valor (R$)"), r"(?:R\$\s*)?[+-]?\s*(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}[+-]?"),
        )
    )
    policy = CashTablePolicy(("Saldo inicial do período", "Saldo inicial"), ("Saldo final do período", "Saldo final"))
    structure = reconstruct_ocr_table(extraction, schema)
    interpreted = interpret_cash_table(extraction, structure, policy)
    if not interpreted.movements:
        raise InvalidFileContentError("Não foi possível identificar movimentos legíveis neste documento por OCR.")
    pages = {page.page_number: page for page in extraction.pages}
    rows = []
    for movement in interpreted.movements:
        page = pages[movement.source_page]
        refs = tuple(
            (page.page_number, line.line_index) for line in page.lines if set(line.word_ids).intersection(movement.source_word_ids)
        )
        warnings = ["manual_review_recommended"] if interpreted.requires_review else []
        if not interpreted.balance_matches:
            warnings.append("financial_validation_failed")
        if interpreted.unresolved_rows or any(
            i.code in {"row_coverage_mismatch", "unassigned_column_anchor", "geometry_unavailable", "header_unavailable", "no_row_anchors"}
            for i in interpreted.issues
        ):
            warnings.append("partial_statement")
        transaction = NormalizedTransaction(
            date=movement.date,
            description=movement.description,
            amount=float(movement.amount),
            type="inflow" if movement.amount >= 0 else "outflow",
        )
        rows.append(
            _ParsedTransaction(
                transaction=transaction,
                source_page=page.page_number,
                source_line=refs[0][1] if refs else None,
                source_regions=list(movement.source_regions),
                source_line_refs=refs,
                warnings=warnings,
                has_explicit_amount_sign=True,
            )
        )
    statement = VerifiedStatement(
        rows,
        layout.layout_name,
        interpreted.opening,
        interpreted.closing,
        candidate_rows=structure.expected_rows,
        printed_balance_checks=2 if interpreted.balance_matches else 0,
    )
    result = _build_verified_statement_result(statement, list(texts))
    metrics = {
        **result.parse_metrics,
        **extraction.metrics,
        "selected_parser": "shared_ocr_cash_table",
        "financial_controls_validated": int(interpreted.balance_matches and not interpreted.requires_review),
        "balance_validation_status": "review_required" if interpreted.requires_review else "validated",
        "ocr_unresolved_rows": len(interpreted.unresolved_rows),
        "statement_rows_expected": structure.expected_rows,
        "statement_rows_parsed": len(rows),
        "confidence_band": "low" if interpreted.requires_review else "high",
        "export_recommendation": "review_recommended" if interpreted.requires_review else "ready",
        "export_recommendation_reason": "ocr_evidence_requires_review" if interpreted.requires_review else "printed_controls_validated",
    }
    return replace(
        result,
        parse_metrics=metrics,
        source_extraction=extraction,
        source_page_texts=texts,
        source_layout_lines=tuple(tuple(page.lines) for page in extraction.pages),
    )


def maybe_parse_shared_cash(extraction):
    text = "\n".join(line.text for page in extraction.pages for line in page.lines)
    if infer_pdf_layout(text).layout_name != "asaas_extrato_conta_digital_movimentacoes_v1":
        return None
    if not any(page.tables or any(word.bbox for word in page.words) for page in extraction.pages):
        return None
    return project_shared_cash_extraction(extraction)
