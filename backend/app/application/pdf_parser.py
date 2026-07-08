import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from app.application.errors import InvalidFileContentError, MaxPagesPerFileExceededError
from app.application.layout_profiles.registry import DeclarativeLayoutProfile, get_layout_profile
from app.application.models import CanonicalTransaction as CanonicalTransaction
from app.application.models import NormalizedTransaction
from app.application.normalization.balance import annotate_balance_consistency
from app.application.normalization.canonical import build_canonical_transactions
from app.application.normalization.canonical_metrics import build_canonical_quality_metrics
from app.application.normalization.date import infer_default_statement_year, parse_statement_date
from app.application.normalization.pdf_amount_tokens import find_amount_tokens, has_explicit_amount_sign, parse_pdf_amount
from app.application.normalization.pdf_columnar_block_rules import next_columnar_block_index
from app.application.normalization.pdf_columnar_row_rules import is_valid_columnar_transaction_row
from app.application.normalization.pdf_columnar_rules import apply_type_sign_hint
from app.application.normalization.pdf_grouped_amount_line_rules import parse_grouped_amount_line
from app.application.normalization.pdf_grouped_date_rules import parse_grouped_date_line
from app.application.normalization.pdf_grouped_line_rules import should_ignore_grouped_line
from app.application.normalization.pdf_grouped_section_rules import resolve_grouped_section_hint
from app.application.normalization.pdf_inline_amount_rules import extract_single_trailing_amount_match
from app.application.normalization.pdf_parse_metrics import build_pdf_parse_metrics
from app.application.normalization.pdf_parser_selection import select_parsed_rows
from app.application.normalization.pdf_row_date_rules import parse_row_date
from app.application.normalization.pdf_row_match_rules import (
    is_amount_only_row,
    is_date_only_row,
    match_inline_row,
    match_tabular_date_prefix,
)
from app.application.normalization.pdf_signed_amount_rules import compute_hint_signed_amount, compute_tabular_signed_amount
from app.application.normalization.pdf_tabular_profile_rules import resolve_tabular_profile
from app.application.normalization.pdf_tabular_rules import (
    SelectedTabularAmount,
    extract_document_reference,
    select_tabular_amount_token,
)
from app.application.normalization.pdf_text_rules import apply_sign_hints, should_ignore_line, should_skip_transaction_description
from app.application.normalization.text import normalize_upper_text
from app.application.parsers.pdf import text_extraction
from app.application.parsers.pdf.models import PdfParseResult, _ParsedTransaction, _PdfLine, _TabularColumnPositions
from app.application.pdf_layout_inference import PdfLayoutInference, infer_pdf_layout
from app.application.pdf_ocr import PDF_OCR_DISABLED_MESSAGE, extract_pdf_page_texts_with_ocr, is_pdf_ocr_enabled
from app.application.textract_extraction_mapper import map_textract_blocks_to_extraction
from app.application.textract_gateway import TextractGateway
from app.application.textract_transaction_adapter import adapt_textract_extraction_to_transactions

_SANTANDER_CREDIT_CARD_INVOICE_LAYOUT = "santander_cartao_credito_detalhamento_fatura_paisagem_v1"
_SANTANDER_IB_EMPRESARIAL_365_MOBILE_GROUPED_LAYOUT = "santander_empresarial_extrato_365_dias_mobile_grouped_v1"
_BRADESCO_UNIFICADO_POUPANCA_LAYOUT = "bradesco_extrato_unificado_pj_poupanca_facil_a4_v1"
_BANCO_NORDESTE_EXTRATO_CONSOLIDADO_LAYOUT = "banco_do_nordeste_extrato_consolidado_v1"
_BANCO_NORDESTE_FUNDOS_RENTABILIDADE_LAYOUT = "banco_do_nordeste_fundos_investimentos_rentabilidade_v1"
_SANTANDER_CREDIT_CARD_INVOICE_SECTION_HEADERS = {
    "PAGAMENTO E DEMAIS CREDITOS",
    "PARCELAMENTOS",
    "DESPESAS",
}
_PT_BR_FULL_MONTHS = {
    "JANEIRO": 1,
    "FEVEREIRO": 2,
    "MARCO": 3,
    "ABRIL": 4,
    "MAIO": 5,
    "JUNHO": 6,
    "JULHO": 7,
    "AGOSTO": 8,
    "SETEMBRO": 9,
    "OUTUBRO": 10,
    "NOVEMBRO": 11,
    "DEZEMBRO": 12,
}
_REFERENCE_MONTH_YEAR_CONTEXT: tuple[int, int] | None = None


@dataclass(frozen=True)
class _LayoutSpecificParseRows:
    rows: list[_ParsedTransaction]
    selected_parser: str
    selection_reason: str
_SANTANDER_CREDIT_CARD_INVOICE_NOISE_LINES = {
    "COMPRA",
    "DATA",
    "DESCRICAO",
    "DESCRIÇÃO",
    "PARCELA",
    "R$",
    "US$",
    ".",
    "·",
    ")))",
}


def _normalize_parse_observability_value(value: object) -> int | float | str:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _with_parse_observability(
    result: PdfParseResult,
    **values: object,
) -> PdfParseResult:
    parse_metrics = dict(result.parse_metrics)
    for key, value in values.items():
        if value is not None:
            parse_metrics[key] = _normalize_parse_observability_value(value)
    return PdfParseResult(
        transactions=result.transactions,
        canonical_transactions=result.canonical_transactions,
        layout=result.layout,
        extracted_text=result.extracted_text,
        parse_metrics=parse_metrics,
    )


def _attach_parse_observability(exc: Exception, **values: object) -> Exception:
    observability = dict(getattr(exc, "_parse_observability", {}) or {})
    for key, value in values.items():
        if value is not None:
            observability[key] = _normalize_parse_observability_value(value)
    setattr(exc, "_parse_observability", observability)
    return exc


def _should_skip_ocr_retry_for_native_parse_error(exc: InvalidFileContentError) -> bool:
    detail = str(exc)
    missing_signals_match = re.search(r"missing_signals=([a-z_,-]+)", detail)
    if missing_signals_match is None:
        return False
    missing_signals = {
        item.strip()
        for item in missing_signals_match.group(1).split(",")
        if item.strip()
    }
    if "date_pattern" not in missing_signals:
        return False
    if "amount_pattern" in missing_signals:
        return False
    if "transaction_row_pattern" not in missing_signals:
        return False
    has_amount_like_match = re.search(r"has_amount_like=(\d)", detail)
    return has_amount_like_match is not None and has_amount_like_match.group(1) == "1"


def parse_pdf_transactions(
    raw_bytes: bytes,
    on_ocr_progress: Callable[[int, int], None] | None = None,
    max_ocr_pages: int | None = None,
) -> PdfParseResult:
    try:
        reference_month_year = text_extraction.read_pdf_creation_month_year(raw_bytes)
    except InvalidFileContentError:
        reference_month_year = None
    native_read_error: InvalidFileContentError | None = None
    try:
        native_pages = _read_native_pdf_page_texts(raw_bytes)
    except InvalidFileContentError as exc:
        native_pages = []
        native_read_error = exc
    native_text_detected = bool(native_pages)
    textract_enabled = is_textract_enabled()
    textract_forced = is_textract_forced() if textract_enabled else False
    textract_attempted = textract_enabled and (not native_pages or textract_forced)

    if textract_attempted:
        _enforce_ocr_page_limit(page_count=len(native_pages), max_ocr_pages=max_ocr_pages)
        try:
            textract_result = _parse_scanned_pdf_with_textract_gateway(raw_bytes)
            return _with_parse_observability(
                textract_result,
                textract_attempted=textract_attempted,
                native_text_detected=native_text_detected,
            )
        except InvalidFileContentError as textract_error:
            if not is_pdf_ocr_enabled():
                raise _attach_parse_observability(
                    textract_error,
                    textract_attempted=textract_attempted,
                    textract_used=0,
                    textract_error_type=textract_error.__class__.__name__,
                    native_text_detected=native_text_detected,
                )
            _enforce_ocr_page_limit(page_count=len(native_pages), max_ocr_pages=max_ocr_pages)
            try:
                fallback_result = _parse_scanned_pdf_with_local_ocr_adapter(raw_bytes, on_ocr_progress=on_ocr_progress)
            except InvalidFileContentError as fallback_error:
                raise _attach_parse_observability(
                    fallback_error,
                    textract_attempted=textract_attempted,
                    textract_used=0,
                    textract_error_type=textract_error.__class__.__name__,
                    native_text_detected=native_text_detected,
                ) from textract_error
            fallback_metrics = dict(fallback_result.parse_metrics)
            fallback_metrics["textract_used"] = 0
            fallback_metrics["textract_error_type"] = textract_error.__class__.__name__
            return _with_parse_observability(
                PdfParseResult(
                    transactions=fallback_result.transactions,
                    canonical_transactions=fallback_result.canonical_transactions,
                    layout=fallback_result.layout,
                    extracted_text=fallback_result.extracted_text,
                    parse_metrics=fallback_metrics,
                ),
                textract_attempted=textract_attempted,
                native_text_detected=native_text_detected,
            )

    if native_read_error is None and not native_pages:
        return _retry_insufficient_native_text_with_ocr(
            raw_bytes,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )

    if on_ocr_progress is None:
        page_texts = _extract_pdf_page_texts(raw_bytes)
    else:
        page_texts = _extract_pdf_page_texts(raw_bytes, on_ocr_progress)
    using_native_text = bool(native_pages) and page_texts == native_pages
    try:
        previous_reference_month_year = _REFERENCE_MONTH_YEAR_CONTEXT
        globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = reference_month_year
        try:
            primary_result = _parse_pdf_transactions_from_page_texts(page_texts)
        finally:
            globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = previous_reference_month_year
    except InvalidFileContentError as native_parse_error:
        if not using_native_text:
            raise _attach_parse_observability(
                native_parse_error,
                textract_attempted=textract_attempted,
                native_text_detected=native_text_detected,
            )
        return _retry_native_parse_failure_with_ocr(
            raw_bytes,
            native_pages=native_pages,
            native_parse_error=native_parse_error,
            on_ocr_progress=on_ocr_progress,
            max_ocr_pages=max_ocr_pages,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
            reference_month_year=reference_month_year,
        )
    if using_native_text:
        primary_result = _maybe_upgrade_native_parse_with_layout_text(raw_bytes=raw_bytes, baseline_result=primary_result)
    primary_result = _with_parse_observability(
        primary_result,
        textract_attempted=textract_attempted,
        native_text_detected=native_text_detected,
    )

    if not using_native_text:
        return primary_result
    if not _should_try_ocr_reparse(primary_result, page_count=len(native_pages)):
        return primary_result
    if not is_pdf_ocr_enabled():
        return primary_result
    _enforce_ocr_page_limit(page_count=len(native_pages), max_ocr_pages=max_ocr_pages)

    ocr_pages = _extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress=on_ocr_progress)
    if not ocr_pages:
        return primary_result

    try:
        previous_reference_month_year = _REFERENCE_MONTH_YEAR_CONTEXT
        globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = reference_month_year
        try:
            ocr_result = _parse_pdf_transactions_from_page_texts(ocr_pages)
        finally:
            globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = previous_reference_month_year
    except InvalidFileContentError as ocr_error:
        raise _attach_parse_observability(
            ocr_error,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )
    ocr_result = _with_parse_observability(
        ocr_result,
        textract_attempted=textract_attempted,
        native_text_detected=native_text_detected,
    )
    if _is_better_parse_result(candidate=ocr_result, baseline=primary_result):
        return ocr_result
    return primary_result


def _retry_insufficient_native_text_with_ocr(
    raw_bytes: bytes,
    *,
    on_ocr_progress: Callable[[int, int], None] | None,
    max_ocr_pages: int | None,
    textract_attempted: bool,
    native_text_detected: bool,
) -> PdfParseResult:
    if not is_pdf_ocr_enabled():
        raise _attach_parse_observability(
            InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE),
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )
    if max_ocr_pages is not None:
        _enforce_ocr_page_limit(page_count=_read_pdf_page_count(raw_bytes), max_ocr_pages=max_ocr_pages)

    ocr_pages = _extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress=on_ocr_progress)
    if not ocr_pages:
        raise _attach_parse_observability(
            InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE),
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )

    try:
        reference_month_year = text_extraction.read_pdf_creation_month_year(raw_bytes)
        previous_reference_month_year = _REFERENCE_MONTH_YEAR_CONTEXT
        globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = reference_month_year
        try:
            ocr_result = _parse_pdf_transactions_from_page_texts(ocr_pages)
        finally:
            globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = previous_reference_month_year
    except InvalidFileContentError as ocr_error:
        raise _attach_parse_observability(
            ocr_error,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )
    parse_metrics = dict(ocr_result.parse_metrics)
    parse_metrics["ocr_retry_reason"] = "insufficient_native_text"
    return _with_parse_observability(
        PdfParseResult(
            transactions=ocr_result.transactions,
            canonical_transactions=ocr_result.canonical_transactions,
            layout=ocr_result.layout,
            extracted_text=ocr_result.extracted_text,
            parse_metrics=parse_metrics,
        ),
        textract_attempted=textract_attempted,
        native_text_detected=native_text_detected,
    )


def _retry_native_parse_failure_with_ocr(
    raw_bytes: bytes,
    *,
    native_pages: list[str],
    native_parse_error: InvalidFileContentError,
    on_ocr_progress: Callable[[int, int], None] | None,
    max_ocr_pages: int | None,
    textract_attempted: bool,
    native_text_detected: bool,
    reference_month_year: tuple[int, int] | None,
) -> PdfParseResult:
    if not is_pdf_ocr_enabled():
        raise _attach_parse_observability(
            native_parse_error,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )
    if _should_skip_ocr_retry_for_native_parse_error(native_parse_error):
        raise _attach_parse_observability(
            native_parse_error,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
            ocr_retry_skipped=1,
            ocr_retry_skip_reason="native_date_pattern_only",
        )
    _enforce_ocr_page_limit(page_count=len(native_pages), max_ocr_pages=max_ocr_pages)

    ocr_pages = _extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress=on_ocr_progress)
    if not ocr_pages:
        raise _attach_parse_observability(
            native_parse_error,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        )

    try:
        previous_reference_month_year = _REFERENCE_MONTH_YEAR_CONTEXT
        globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = reference_month_year
        try:
            ocr_result = _parse_pdf_transactions_from_page_texts(ocr_pages)
        finally:
            globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = previous_reference_month_year
    except InvalidFileContentError as ocr_error:
        raise _attach_parse_observability(
            ocr_error,
            textract_attempted=textract_attempted,
            native_text_detected=native_text_detected,
        ) from native_parse_error
    parse_metrics = dict(ocr_result.parse_metrics)
    parse_metrics["ocr_retry_reason"] = "native_parse_failed"
    return _with_parse_observability(
        PdfParseResult(
            transactions=ocr_result.transactions,
            canonical_transactions=ocr_result.canonical_transactions,
            layout=ocr_result.layout,
            extracted_text=ocr_result.extracted_text,
            parse_metrics=parse_metrics,
        ),
        textract_attempted=textract_attempted,
        native_text_detected=native_text_detected,
    )


def _extract_pdf_page_texts_with_ocr(
    raw_bytes: bytes,
    *,
    on_ocr_progress: Callable[[int, int], None] | None,
) -> list[str]:
    if on_ocr_progress is None:
        return extract_pdf_page_texts_with_ocr(raw_bytes)
    return extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress)


def _enforce_ocr_page_limit(*, page_count: int, max_ocr_pages: int | None) -> None:
    if max_ocr_pages is None:
        return
    safe_limit = max(1, int(max_ocr_pages))
    safe_pages = max(0, int(page_count))
    if safe_pages > safe_limit:
        raise MaxPagesPerFileExceededError(pages_count=safe_pages, max_pages_per_file=safe_limit)


def is_textract_enabled() -> bool:
    raw = os.getenv("TEXTRACT_ENABLED", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_textract_forced() -> bool:
    raw = os.getenv("TEXTRACT_FORCE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _parse_scanned_pdf_with_textract_gateway(raw_bytes: bytes) -> PdfParseResult:
    gateway = TextractGateway()
    gateway_result = gateway.analyze_pdf(raw_bytes=raw_bytes)
    extraction = map_textract_blocks_to_extraction(
        document_hash=str(gateway_result.get("document_hash") or ""),
        blocks=list(gateway_result.get("blocks") or []),
        page_count=int(gateway_result.get("page_count") or 0),
    )
    gateway_metrics = dict(gateway_result.get("metrics") or {})
    textract_mode = str(gateway_metrics.get("textract_mode") or "").strip().lower()
    if textract_mode == "text":
        text_mode_result = _parse_scanned_pdf_with_textract_text_pages(extraction)
        text_mode_metrics = dict(text_mode_result.parse_metrics)
        text_mode_metrics["extraction_provider"] = "aws_textract"
        text_mode_metrics["textract_enabled"] = 1
        text_mode_metrics["textract_used"] = 1
        for key, value in gateway_metrics.items():
            text_mode_metrics[str(key)] = value
        return PdfParseResult(
            transactions=text_mode_result.transactions,
            canonical_transactions=text_mode_result.canonical_transactions,
            layout=text_mode_result.layout,
            extracted_text=text_mode_result.extracted_text,
            parse_metrics=text_mode_metrics,
        )
    adapted = adapt_textract_extraction_to_transactions(extraction)
    inferred_layout = infer_pdf_layout(adapted.extracted_text)
    canonical_transactions = adapted.canonical_transactions
    balance_checked_count, balance_failed_count = annotate_balance_consistency(canonical_transactions)
    canonical_quality_metrics = build_canonical_quality_metrics(canonical_transactions)
    flattened_line_count = sum(len(page.lines) for page in extraction.pages)
    parse_metrics = build_pdf_parse_metrics(
        page_count=len(extraction.pages),
        extracted_char_count=len(adapted.extracted_text),
        flattened_line_count=flattened_line_count,
        grouped_transactions_count=len(adapted.transactions),
        inline_candidates_count=0,
        inline_transactions_count=0,
        tabular_candidates_count=int(adapted.parse_metrics.get("transaction_count", len(adapted.transactions))),
        tabular_transactions_count=len(adapted.transactions),
        columnar_candidates_count=0,
        columnar_transactions_count=0,
        selected_parser=str(adapted.parse_metrics.get("selected_parser") or "textract_table"),
        parser_selection_reason="textract",
        inline_decision="",
        tabular_decision="",
        columnar_decision="",
        layout_used_fallback=inferred_layout.used_fallback,
        balance_consistency_checked=balance_checked_count,
        balance_consistency_failed=balance_failed_count,
        canonical_quality_metrics=canonical_quality_metrics,
    )
    parse_metrics["extraction_provider"] = "aws_textract"
    parse_metrics["textract_enabled"] = 1
    parse_metrics["textract_used"] = 1
    for key, value in gateway_metrics.items():
        parse_metrics[str(key)] = value
    return PdfParseResult(
        transactions=adapted.transactions,
        canonical_transactions=canonical_transactions,
        layout=inferred_layout,
        extracted_text=adapted.extracted_text,
        parse_metrics=parse_metrics,
    )


def _parse_scanned_pdf_with_textract_text_pages(extraction) -> PdfParseResult:
    page_texts = [
        "\n".join(
            line.text
            for line in sorted(page.lines, key=lambda value: value.line_index)
            if line.text
        )
        for page in extraction.pages
    ]
    if not any(page_texts):
        raise InvalidFileContentError("Nao foi possivel extrair transacoes do OCR para revisao.")
    try:
        return _parse_pdf_transactions_from_page_texts(page_texts)
    except InvalidFileContentError:
        adapted = adapt_textract_extraction_to_transactions(extraction)
        inferred_layout = infer_pdf_layout(adapted.extracted_text)
        return PdfParseResult(
            transactions=adapted.transactions,
            canonical_transactions=adapted.canonical_transactions,
            layout=inferred_layout,
            extracted_text=adapted.extracted_text,
            parse_metrics={
                "selected_parser": str(adapted.parse_metrics.get("selected_parser") or "textract_line_window"),
                "parser_selection_reason": "textract_text_fallback",
            },
        )


def _parse_scanned_pdf_with_local_ocr_adapter(
    raw_bytes: bytes,
    *,
    on_ocr_progress: Callable[[int, int], None] | None,
) -> PdfParseResult:
    ocr_pages = _extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress=on_ocr_progress)
    if not ocr_pages:
        raise InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE)

    return _parse_pdf_transactions_from_page_texts(ocr_pages)


def _parse_pdf_transactions_from_page_texts(
    page_texts: list[str], *, preserve_layout_spacing: bool = False
) -> PdfParseResult:
    joined_text = "\n".join(page_texts)
    layout = infer_pdf_layout(joined_text)
    layout_profile = get_layout_profile(layout.layout_name)
    lines = _flatten_statement_lines(page_texts, preserve_layout_spacing=preserve_layout_spacing)
    lines, invalid_date_candidates_skipped = _filter_invalid_leading_date_candidate_lines(lines)
    specialized_selection = _parse_layout_specific_statement_rows(
        lines=lines,
        layout=layout,
    )
    if specialized_selection is not None:
        specialized_rows = _adjust_forward_year_rollover_rows(
            specialized_selection.rows,
            line_texts=_extract_line_texts(lines),
        )
        return _build_pdf_parse_result(
            parsed_rows=specialized_rows,
            layout=layout,
            layout_profile=layout_profile,
            selected_parser=specialized_selection.selected_parser,
            parser_selection_reason=specialized_selection.selection_reason,
            inline_decision="not_applicable_layout_specific",
            tabular_decision="not_applicable_layout_specific",
            columnar_decision="not_applicable_layout_specific",
            joined_text=joined_text,
            page_count=len(page_texts),
            flattened_line_count=len(lines),
            invalid_date_candidates_skipped=invalid_date_candidates_skipped,
            grouped_transactions_count=0,
            inline_candidates_count=0,
            inline_transactions_count=0,
            tabular_candidates_count=0,
            tabular_transactions_count=0,
            columnar_candidates_count=0,
            columnar_transactions_count=0,
        )
    grouped_rows = _parse_grouped_statement_lines(lines, layout_profile=layout_profile)
    selection = select_parsed_rows(
        lines=lines,
        grouped_rows=grouped_rows,
        layout_profile=layout_profile,
        parse_inline_rows=_parse_inline_statement_rows,
        parse_tabular_rows=lambda all_lines, profile: _parse_tabular_statement_rows(all_lines, layout_profile=profile),
        parse_columnar_rows=_parse_columnar_statement_blocks,
    )
    parsed_rows = selection.rows
    parsed_rows = _adjust_forward_year_rollover_rows(parsed_rows, line_texts=_extract_line_texts(lines))
    selected_parser = selection.selected_parser
    inline_candidates = selection.inline_candidates
    inline_transactions_count = selection.inline_transactions_count
    tabular_candidates_count = selection.tabular_candidates
    tabular_transactions_count = selection.tabular_transactions_count
    columnar_candidates_count = selection.columnar_candidates
    columnar_transactions_count = selection.columnar_transactions_count
    parser_selection_reason = selection.selection_reason
    inline_decision = selection.inline_decision
    tabular_decision = selection.tabular_decision
    columnar_decision = selection.columnar_decision
    return _build_pdf_parse_result(
        parsed_rows=parsed_rows,
        layout=layout,
        layout_profile=layout_profile,
        selected_parser=selected_parser,
        parser_selection_reason=parser_selection_reason,
        inline_decision=inline_decision,
        tabular_decision=tabular_decision,
        columnar_decision=columnar_decision,
        joined_text=joined_text,
        page_count=len(page_texts),
        flattened_line_count=len(lines),
        invalid_date_candidates_skipped=invalid_date_candidates_skipped,
        grouped_transactions_count=len(grouped_rows),
        inline_candidates_count=inline_candidates,
        inline_transactions_count=inline_transactions_count,
        tabular_candidates_count=tabular_candidates_count,
        tabular_transactions_count=tabular_transactions_count,
        columnar_candidates_count=columnar_candidates_count,
        columnar_transactions_count=columnar_transactions_count,
    )


def _parse_layout_specific_statement_rows(
    *,
    lines: list[_PdfLine],
    layout: PdfLayoutInference,
) -> _LayoutSpecificParseRows | None:
    if layout.layout_name == _SANTANDER_CREDIT_CARD_INVOICE_LAYOUT:
        parsed_rows = _parse_santander_credit_card_invoice_sections(lines)
        if parsed_rows:
            return _LayoutSpecificParseRows(
                rows=parsed_rows,
                selected_parser="sectioned_credit_card_invoice",
                selection_reason="layout_specific_sectioned_credit_card_invoice",
            )
    if layout.layout_name == _BRADESCO_UNIFICADO_POUPANCA_LAYOUT:
        parsed_rows = _parse_bradesco_unificado_poupanca_movimentacao_rows(lines)
        if parsed_rows:
            return _LayoutSpecificParseRows(
                rows=parsed_rows,
                selected_parser="layout_specific_bradesco_unificado_movimentacao",
                selection_reason="layout_specific_bradesco_unificado_movimentacao",
            )
    if layout.layout_name == _BANCO_NORDESTE_EXTRATO_CONSOLIDADO_LAYOUT:
        parsed_rows = _parse_banco_do_nordeste_extrato_consolidado_rows(lines)
        if parsed_rows:
            return _LayoutSpecificParseRows(
                rows=parsed_rows,
                selected_parser="layout_specific_banco_nordeste_consolidado",
                selection_reason="layout_specific_banco_nordeste_consolidado",
            )
    if layout.layout_name == _BANCO_NORDESTE_FUNDOS_RENTABILIDADE_LAYOUT:
        parsed_rows = _parse_banco_do_nordeste_fundos_rentabilidade_rows(
            lines,
            reference_month_year=_REFERENCE_MONTH_YEAR_CONTEXT,
        )
        if parsed_rows:
            return _LayoutSpecificParseRows(
                rows=parsed_rows,
                selected_parser="layout_specific_banco_nordeste_fundos_rentabilidade",
                selection_reason="layout_specific_banco_nordeste_fundos_rentabilidade",
            )
    return None


def _parse_bradesco_unificado_poupanca_movimentacao_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    movement_lines = _slice_bradesco_unificado_movimentacao_lines(lines)
    if not movement_lines:
        return []

    fallback_year = _infer_bradesco_unificado_reference_year(lines) or datetime.now().year
    parsed_rows: list[_ParsedTransaction] = []
    previous_running_balance: float | None = None
    index = 0

    while index < len(movement_lines):
        line = movement_lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue

        current_date = parse_statement_date(line.text.strip(), fallback_year)
        next_index = index + 1
        description_parts: list[str] = []
        document_value: str | None = None
        amount_value: float | None = None
        running_balance: float | None = None

        while next_index < len(movement_lines):
            current = movement_lines[next_index]
            normalized_current = _normalize_text(current.text)
            if normalized_current in {
                "DATA",
                "HISTORICO",
                "HISTÓRICO",
                "DOCUMENTO",
                "INDICES",
                "ÍNDICES",
                "CREDITO",
                "CRÉDITO",
                "DEBITO",
                "DÉBITO",
                "SALDO",
            }:
                next_index += 1
                continue
            if is_date_only_row(current.text):
                break
            if is_amount_only_row(current.text):
                parsed_amount = parse_pdf_amount(current.text)
                if amount_value is None:
                    amount_value = abs(parsed_amount)
                else:
                    running_balance = parsed_amount
                    next_index += 1
                    break
                next_index += 1
                continue

            digits_only = re.sub(r"\D", "", current.text)
            if document_value is None and amount_value is None and re.fullmatch(r"\d{6,8}", digits_only):
                document_value = digits_only
            else:
                description_parts.append(current.text.strip())
            next_index += 1

        description = " ".join(part for part in description_parts if part).strip()
        normalized_description = _normalize_text(description)
        if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
            opening_balance = running_balance if running_balance is not None else amount_value
            if opening_balance is not None:
                parsed_rows.append(
                    _build_parsed_transaction(
                        date=current_date,
                        description="SALDO ANTERIOR",
                        amount=opening_balance,
                        source_page=line.page_number,
                        source_line=line.line_number,
                        running_balance=opening_balance,
                        has_explicit_amount_sign=has_explicit_amount_sign(str(opening_balance)),
                    )
                )
                previous_running_balance = opening_balance
            index = next_index
            continue

        if description and amount_value is not None:
            full_description = description
            if document_value:
                full_description = f"{full_description} {document_value}".strip()
            signed_amount = _resolve_bradesco_unificado_signed_amount(
                raw_amount=amount_value,
                description=full_description,
                previous_running_balance=previous_running_balance,
                running_balance=running_balance,
            )
            parsed_rows.append(
                _build_parsed_transaction(
                    date=current_date,
                    description=full_description,
                    amount=signed_amount,
                    source_page=line.page_number,
                    source_line=line.line_number,
                    running_balance=running_balance,
                    external_reference_id=document_value,
                    has_explicit_amount_sign=amount_value < 0,
                )
            )
            if running_balance is not None:
                previous_running_balance = running_balance
            elif previous_running_balance is not None:
                previous_running_balance = round(previous_running_balance + signed_amount, 2)

        index = next_index

    return parsed_rows


def _slice_bradesco_unificado_movimentacao_lines(lines: list[_PdfLine]) -> list[_PdfLine]:
    start_index: int | None = None
    for index, line in enumerate(lines):
        if _normalize_text(line.text) == "DEMONSTRATIVO DA MOVIMENTACAO":
            start_index = index + 1
            break
    if start_index is None:
        return []
    return lines[start_index:]


def _infer_bradesco_unificado_reference_year(lines: list[_PdfLine]) -> int | None:
    years: list[int] = []
    for line in lines:
        for raw in re.findall(r"\b\d{2}/\d{2}/(\d{4})\b", line.text):
            year = int(raw)
            if 1900 <= year <= 2100:
                years.append(year)
    if years:
        return max(years)
    return _infer_default_statement_year_from_lines(lines)


def _resolve_bradesco_unificado_signed_amount(
    *,
    raw_amount: float,
    description: str,
    previous_running_balance: float | None,
    running_balance: float | None,
) -> float:
    amount = abs(raw_amount)
    if previous_running_balance is not None and running_balance is not None:
        if abs((previous_running_balance + amount) - running_balance) <= 0.02:
            return amount
        if abs((previous_running_balance - amount) - running_balance) <= 0.02:
            return -amount
    return apply_sign_hints(amount, description, None)


def _parse_banco_do_nordeste_extrato_consolidado_rows(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    movement_lines = _slice_banco_do_nordeste_movimentacao_lines(lines)
    statement_month_year = _infer_banco_do_nordeste_month_year(lines)
    if not movement_lines or statement_month_year is None:
        return []

    statement_month, statement_year = statement_month_year
    parsed_rows: list[_ParsedTransaction] = []
    previous_running_balance: float | None = None
    index = 0

    while index < len(movement_lines):
        line = movement_lines[index]
        if not _is_banco_do_nordeste_day_line(line.text):
            index += 1
            continue

        current_date = _build_banco_do_nordeste_iso_date(
            day=line.text.strip(),
            month=statement_month,
            year=statement_year,
        )
        next_index = index + 1
        if next_index >= len(movement_lines):
            break

        description = movement_lines[next_index].text.strip()
        normalized_description = _normalize_text(description)
        next_index += 1
        if not description or _is_banco_do_nordeste_day_line(description):
            index += 1
            continue

        document_value: str | None = None
        amount_value: float | None = None
        running_balance: float | None = None

        if next_index < len(movement_lines) and is_amount_only_row(movement_lines[next_index].text.strip()):
            amount_value = parse_pdf_amount(movement_lines[next_index].text.strip())
            next_index += 1
        elif next_index + 1 < len(movement_lines):
            candidate_document = movement_lines[next_index].text.strip()
            candidate_amount = movement_lines[next_index + 1].text.strip()
            if re.fullmatch(r"\d{1,12}", re.sub(r"\D", "", candidate_document)) and is_amount_only_row(candidate_amount):
                document_value = re.sub(r"\D", "", candidate_document)
                amount_value = parse_pdf_amount(candidate_amount)
                next_index += 2

        if amount_value is None or next_index >= len(movement_lines):
            index += 1
            continue
        if not is_amount_only_row(movement_lines[next_index].text.strip()):
            index += 1
            continue

        running_balance = parse_pdf_amount(movement_lines[next_index].text.strip())
        next_index += 1

        if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
            opening_balance = running_balance
            parsed_rows.append(
                _build_parsed_transaction(
                    date=current_date,
                    description="SALDO ANTERIOR" if "ANTERIOR" in normalized_description else "SALDO INICIAL",
                    amount=opening_balance,
                    source_page=line.page_number,
                    source_line=line.line_number,
                    running_balance=opening_balance,
                    has_explicit_amount_sign=has_explicit_amount_sign(str(amount_value)),
                )
            )
            previous_running_balance = opening_balance
            index = next_index
            continue

        full_description = description if not document_value else f"{description} {document_value}".strip()
        signed_amount = _resolve_banco_do_nordeste_signed_amount(
            raw_amount=amount_value,
            description=full_description,
            previous_running_balance=previous_running_balance,
            running_balance=running_balance,
        )
        parsed_rows.append(
            _build_parsed_transaction(
                date=current_date,
                description=full_description,
                amount=signed_amount,
                source_page=line.page_number,
                source_line=line.line_number,
                running_balance=running_balance,
                external_reference_id=document_value,
                has_explicit_amount_sign=has_explicit_amount_sign(str(amount_value)),
            )
        )
        previous_running_balance = running_balance
        index = next_index

    return parsed_rows


def _parse_banco_do_nordeste_fundos_rentabilidade_rows(
    lines: list[_PdfLine],
    *,
    reference_month_year: tuple[int, int] | None,
) -> list[_ParsedTransaction]:
    movement_lines = _slice_banco_do_nordeste_fundos_rentabilidade_lines(lines)
    statement_month_year = _infer_banco_do_nordeste_month_year(lines) or reference_month_year
    if not movement_lines or statement_month_year is None:
        return []

    statement_month, statement_year = statement_month_year
    parsed_rows: list[_ParsedTransaction] = []
    index = 0

    while index < len(movement_lines):
        line = movement_lines[index]
        inline_row = _parse_banco_do_nordeste_fundos_rentabilidade_line(
            line,
            month=statement_month,
            year=statement_year,
        )
        if inline_row is not None:
            parsed_rows.append(inline_row)
            index += 1
            continue

        if not _is_banco_do_nordeste_day_line(line.text):
            index += 1
            continue

        next_index = index + 1
        if next_index >= len(movement_lines):
            break

        description = re.sub(r"\s+", " ", movement_lines[next_index].text).strip()
        if not description or _is_banco_do_nordeste_day_line(description):
            index += 1
            continue
        next_index += 1

        while next_index < len(movement_lines) and _is_banco_do_nordeste_quantity_or_unit_line(movement_lines[next_index].text):
            next_index += 1

        if next_index >= len(movement_lines) or not is_amount_only_row(movement_lines[next_index].text.strip()):
            index += 1
            continue

        amount_value = abs(parse_pdf_amount(movement_lines[next_index].text.strip()))
        parsed_rows.append(
            _build_parsed_transaction(
                date=_build_banco_do_nordeste_iso_date(day=line.text.strip(), month=statement_month, year=statement_year),
                description=_normalize_banco_do_nordeste_fundos_description(description),
                amount=_resolve_banco_do_nordeste_fundos_signed_amount(amount=amount_value, description=description),
                source_page=line.page_number,
                source_line=line.line_number,
                has_explicit_amount_sign=False,
            )
        )
        index = next_index + 1

    return parsed_rows


def _slice_banco_do_nordeste_movimentacao_lines(lines: list[_PdfLine]) -> list[_PdfLine]:
    start_index: int | None = None
    for index, line in enumerate(lines):
        normalized_line = _normalize_text(line.text)
        if normalized_line.endswith("DEMONSTRATIVO DA MOVIMENTACAO DE CONTA CORRENTE"):
            start_index = index + 1
            break
    if start_index is None:
        return []
    return [
        line
        for line in lines[start_index:]
        if _normalize_text(line.text)
        not in {
            "DIA",
            "HISTORICO",
            "HISTORICO",
            "DOCUMENTO",
            "VALOR",
            "SALDO",
        }
    ]


def _slice_banco_do_nordeste_fundos_rentabilidade_lines(lines: list[_PdfLine]) -> list[_PdfLine]:
    start_index: int | None = None
    for index, line in enumerate(lines):
        normalized_line = _normalize_text(line.text)
        if "MOVIMENTACOES BNB" in normalized_line:
            start_index = index + 1
            break
    if start_index is None:
        return []

    movement_lines: list[_PdfLine] = []
    for line in lines[start_index:]:
        normalized_line = _normalize_text(line.text)
        if normalized_line in {
            "DIA HISTORICO QUANT. COTAS VALOR COTA VALOR EM R$",
            "DIA HISTORICO QUANT. COTAS VALOR COTA VALOR EM R $",
            "DIA",
            "HISTORICO",
            "QUANT. COTAS",
            "VALOR COTA",
            "VALOR EM R$",
            "VALOR EM R $",
        }:
            continue
        if not normalized_line:
            continue
        movement_lines.append(line)
    return movement_lines


def _infer_banco_do_nordeste_month_year(lines: list[_PdfLine]) -> tuple[int, int] | None:
    for line in lines:
        normalized = _normalize_text(line.text)
        match = re.search(
            r"\b(JANEIRO|FEVEREIRO|MARCO|ABRIL|MAIO|JUNHO|JULHO|AGOSTO|SETEMBRO|OUTUBRO|NOVEMBRO|DEZEMBRO)/(\d{4})\b",
            normalized,
        )
        if not match:
            continue
        month = _PT_BR_FULL_MONTHS.get(match.group(1))
        year = int(match.group(2))
        if month is None:
            continue
        return month, year
    return None


def _parse_banco_do_nordeste_fundos_rentabilidade_line(
    line: _PdfLine,
    *,
    month: int,
    year: int,
) -> _ParsedTransaction | None:
    match = re.match(
        r"^(?P<day>\d{1,2})\s+(?P<rest>.+?)\s+(?P<amount>\d+(?:\.\d{3})*,\d{2})$",
        line.text.strip(),
    )
    if match is None:
        return None

    description = re.sub(r"\s+\d[\d\.,]*\s+\d[\d\.,]*\s*$", "", match.group("rest")).strip()
    if not description:
        return None

    amount_value = abs(parse_pdf_amount(match.group("amount")))

    return _build_parsed_transaction(
        date=_build_banco_do_nordeste_iso_date(day=match.group("day"), month=month, year=year),
        description=_normalize_banco_do_nordeste_fundos_description(description),
        amount=_resolve_banco_do_nordeste_fundos_signed_amount(amount=amount_value, description=description),
        source_page=line.page_number,
        source_line=line.line_number,
        has_explicit_amount_sign=False,
    )


def _resolve_banco_do_nordeste_fundos_signed_amount(*, amount: float, description: str) -> float:
    normalized_description = _normalize_text(description)
    normalized_sign_description = normalized_description.replace(".", "")
    if normalized_description.startswith("SALDO INICIAL") or normalized_description.startswith("SALDO ANTERIOR"):
        return amount
    if normalized_description.startswith("APLICACAO"):
        return -amount
    if (
        "RESGATE" in normalized_sign_description
        and "IOF" not in normalized_sign_description
        and "IR FEDERAL" not in normalized_sign_description
    ):
        return amount
    if "IOF" in normalized_sign_description or "IR FEDERAL" in normalized_sign_description:
        return -amount
    return apply_sign_hints(amount, description, None)


def _normalize_banco_do_nordeste_fundos_description(description: str) -> str:
    normalized_description = _normalize_text(description)
    if normalized_description.startswith("SALDO INICIAL"):
        return "SALDO INICIAL"
    return re.sub(r"\s+", " ", description).strip()


def _is_banco_do_nordeste_quantity_or_unit_line(raw: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:\.\d{3})*,\d{3,6}", raw.strip()))


def _build_banco_do_nordeste_iso_date(*, day: str, month: int, year: int) -> str:
    return datetime(year, month, int(day)).strftime("%Y-%m-%d")


def _is_banco_do_nordeste_day_line(raw: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}", raw.strip()))


def _resolve_banco_do_nordeste_signed_amount(
    *,
    raw_amount: float,
    description: str,
    previous_running_balance: float | None,
    running_balance: float,
) -> float:
    amount = raw_amount
    if previous_running_balance is not None:
        if abs((previous_running_balance + amount) - running_balance) <= 0.02:
            return amount
        if abs((previous_running_balance - abs(amount)) - running_balance) <= 0.02:
            return -abs(amount)
        if abs((previous_running_balance + abs(amount)) - running_balance) <= 0.02:
            return abs(amount)
    return apply_sign_hints(amount, description, None)


def _parse_santander_credit_card_invoice_sections(lines: list[_PdfLine]) -> list[_ParsedTransaction]:
    fallback_year = _infer_default_statement_year_from_lines(lines) or datetime.now().year
    payment_lines: list[_PdfLine] = []
    installment_lines: list[_PdfLine] = []
    expense_lines: list[_PdfLine] = []
    current_section: str | None = None

    for index, line in enumerate(lines):
        normalized = _normalize_text(line.text)
        if normalized in _SANTANDER_CREDIT_CARD_INVOICE_SECTION_HEADERS:
            current_section = normalized
            continue
        if current_section == "PAGAMENTO E DEMAIS CREDITOS":
            payment_lines.append(line)
        elif current_section == "PARCELAMENTOS":
            installment_lines.append(line)
        elif current_section == "DESPESAS":
            expense_lines.append(line)

    parsed_rows = [
        *_parse_santander_credit_card_payment_rows(payment_lines, fallback_year=fallback_year),
        *_parse_santander_credit_card_installment_rows(installment_lines, fallback_year=fallback_year),
        *_parse_santander_credit_card_expense_rows(expense_lines, fallback_year=fallback_year),
    ]
    return parsed_rows


def _parse_santander_credit_card_payment_rows(
    lines: list[_PdfLine], *, fallback_year: int
) -> list[_ParsedTransaction]:
    staged_rows: list[tuple[str, str, float, int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue
        description_parts: list[str] = []
        amount_value: float | None = None
        amount_line_number: int | None = None
        next_index = index + 1
        while next_index < len(lines):
            current = lines[next_index]
            if is_date_only_row(current.text):
                break
            if _is_santander_credit_card_invoice_noise_line(current.text):
                next_index += 1
                continue
            if is_amount_only_row(current.text):
                amount_value = abs(parse_pdf_amount(current.text))
                amount_line_number = current.line_number
                next_index += 1
                break
            description_parts.append(current.text.strip())
            next_index += 1
        description = " ".join(part for part in description_parts if part).strip()
        if description and amount_value is not None and amount_line_number is not None:
            staged_rows.append((line.text, description, amount_value, line.page_number, amount_line_number))
        index = next_index
    return _build_santander_credit_card_invoice_rows(staged_rows, fallback_year=fallback_year)


def _parse_santander_credit_card_installment_rows(
    lines: list[_PdfLine], *, fallback_year: int
) -> list[_ParsedTransaction]:
    staged_rows: list[tuple[str, str, float, int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue
        description = ""
        installment = ""
        amount_value: float | None = None
        amount_line_number: int | None = None
        next_index = index + 1
        while next_index < len(lines):
            current = lines[next_index]
            current_text = current.text.strip()
            if re.fullmatch(r"\d{2}/\d{2}", current_text):
                if description and not installment:
                    installment = current_text
                    next_index += 1
                    continue
                break
            if is_date_only_row(current.text):
                break
            if _is_santander_credit_card_invoice_noise_line(current.text):
                next_index += 1
                continue
            if is_amount_only_row(current.text):
                amount_value = -abs(parse_pdf_amount(current.text))
                amount_line_number = current.line_number
                next_index += 1
                break
            if not description:
                description = current_text
            next_index += 1
        full_description = description
        if description and installment:
            full_description = f"{description} PARCELA {installment}"
        if full_description and amount_value is not None and amount_line_number is not None:
            staged_rows.append((line.text, full_description, amount_value, line.page_number, amount_line_number))
        index = next_index
    return _build_santander_credit_card_invoice_rows(staged_rows, fallback_year=fallback_year)


def _parse_santander_credit_card_expense_rows(
    lines: list[_PdfLine], *, fallback_year: int
) -> list[_ParsedTransaction]:
    staged_rows: list[tuple[str, str, float, int, int]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not is_date_only_row(line.text):
            index += 1
            continue
        description = ""
        amount_value: float | None = None
        amount_line_number: int | None = None
        next_index = index + 1
        while next_index < len(lines):
            current = lines[next_index]
            if is_date_only_row(current.text):
                break
            if _is_santander_credit_card_invoice_noise_line(current.text):
                next_index += 1
                continue
            if is_amount_only_row(current.text):
                amount_value = -abs(parse_pdf_amount(current.text))
                amount_line_number = current.line_number
                next_index += 1
                break
            if not description:
                description = current.text.strip()
            next_index += 1
        if description and amount_value is not None and amount_line_number is not None:
            staged_rows.append((line.text, description, amount_value, line.page_number, amount_line_number))
        index = next_index
    return _build_santander_credit_card_invoice_rows(staged_rows, fallback_year=fallback_year)


def _build_santander_credit_card_invoice_rows(
    staged_rows: list[tuple[str, str, float, int, int]],
    *,
    fallback_year: int,
) -> list[_ParsedTransaction]:
    if not staged_rows:
        return []
    dated_rows = _resolve_santander_credit_card_invoice_section_dates(
        [row[0] for row in staged_rows],
        fallback_year=fallback_year,
    )
    parsed_rows: list[_ParsedTransaction] = []
    for (raw_date, description, amount, page_number, source_line), resolved_date in zip(
        staged_rows,
        dated_rows,
        strict=False,
    ):
        parsed_rows.append(
            _build_parsed_transaction(
                date=resolved_date,
                description=description,
                amount=amount,
                source_page=page_number,
                source_line=source_line,
                has_explicit_amount_sign=True,
            )
        )
    return parsed_rows


def _resolve_santander_credit_card_invoice_section_dates(raw_dates: list[str], *, fallback_year: int) -> list[str]:
    if not raw_dates:
        return []
    resolved_dates: list[str] = []
    current_year = fallback_year
    current_month: int | None = None

    # The invoice sections are printed in ascending day order; walking backwards
    # lets us map December rows to the previous year when the section ends in January.
    for raw_date in reversed(raw_dates):
        day, month = [int(part) for part in raw_date.split("/", 1)]
        if current_month is not None and month > current_month:
            current_year -= 1
        resolved_dates.append(f"{current_year:04d}-{month:02d}-{day:02d}")
        current_month = month
    return list(reversed(resolved_dates))


def _is_santander_credit_card_invoice_noise_line(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    if normalized in _SANTANDER_CREDIT_CARD_INVOICE_NOISE_LINES:
        return True
    return not normalized


def _should_treat_grouped_date_line_as_description_continuation(
    *,
    lines: list[_PdfLine],
    index: int,
    grouped_date_match: object,
    current_date: str | None,
    description_parts: list[str],
    layout_profile: DeclarativeLayoutProfile | None,
) -> bool:
    if current_date is None or not description_parts or layout_profile is None:
        return False
    if layout_profile.profile_name != _SANTANDER_IB_EMPRESARIAL_365_MOBILE_GROUPED_LAYOUT:
        return False
    if getattr(grouped_date_match, "rest", "").strip():
        return False
    if not is_date_only_row(lines[index].text):
        return False
    if index + 1 >= len(lines):
        return False
    next_line = lines[index + 1]
    if next_line.page_number != lines[index].page_number:
        return False
    return is_amount_only_row(next_line.text)


def _should_try_ocr_reparse(result: PdfParseResult, *, page_count: int) -> bool:
    if page_count < 2:
        return False
    if result.layout.layout_name != "generic_statement_ptbr":
        return False
    tx_count = len(result.transactions)
    if tx_count > max(3, page_count):
        return False
    confidence_band = str(result.parse_metrics.get("confidence_band", "")).strip().lower()
    if tx_count <= 2:
        return True
    return confidence_band == "low"


def _is_better_parse_result(*, candidate: PdfParseResult, baseline: PdfParseResult) -> bool:
    candidate_tx = len(candidate.transactions)
    baseline_tx = len(baseline.transactions)
    if candidate_tx >= baseline_tx + 2:
        return True
    if candidate_tx < baseline_tx:
        return False

    candidate_generic = candidate.layout.layout_name == "generic_statement_ptbr"
    baseline_generic = baseline.layout.layout_name == "generic_statement_ptbr"
    candidate_failed = int(candidate.parse_metrics.get("balance_consistency_failed", 0))
    baseline_failed = int(baseline.parse_metrics.get("balance_consistency_failed", 0))

    if candidate_tx > baseline_tx and (not candidate_generic or baseline_generic):
        return candidate_failed <= baseline_failed + 2
    if candidate_tx == baseline_tx and baseline_generic and not candidate_generic:
        return candidate_failed <= baseline_failed
    if candidate_tx == baseline_tx and candidate_failed + 2 < baseline_failed:
        return True
    return False


def _adjust_forward_year_rollover_rows(
    parsed_rows: list[_ParsedTransaction], *, line_texts: list[str]
) -> list[_ParsedTransaction]:
    if len(parsed_rows) < 2:
        return parsed_rows
    if not _has_explicit_adjacent_year_hints(line_texts):
        return parsed_rows

    adjusted_rows: list[_ParsedTransaction] = [parsed_rows[0]]
    previous_date = datetime.strptime(parsed_rows[0].transaction.date, "%Y-%m-%d")

    for row in parsed_rows[1:]:
        current_date = datetime.strptime(row.transaction.date, "%Y-%m-%d")
        should_roll_forward = (
            current_date.year == previous_date.year
            and previous_date.month == 12
            and current_date.month == 1
            and current_date < previous_date
        )
        if should_roll_forward:
            rolled_date = current_date.replace(year=current_date.year + 1)
            rolled_transaction = NormalizedTransaction(
                date=rolled_date.strftime("%Y-%m-%d"),
                description=row.transaction.description,
                amount=row.transaction.amount,
                type=row.transaction.type,
            )
            adjusted_row = _ParsedTransaction(
                transaction=rolled_transaction,
                source_page=row.source_page,
                source_line=row.source_line,
                running_balance=row.running_balance,
                external_reference_id=row.external_reference_id,
            )
            adjusted_rows.append(adjusted_row)
            previous_date = rolled_date
            continue

        adjusted_rows.append(row)
        previous_date = current_date

    return adjusted_rows


def _has_explicit_adjacent_year_hints(line_texts: list[str]) -> bool:
    years: set[int] = set()
    for line in line_texts:
        for raw in re.findall(r"\b\d{4}\b", line):
            year = int(raw)
            if 1900 <= year <= 2100:
                years.add(year)
    if len(years) < 2:
        return False
    sorted_years = sorted(years)
    return any(current - previous == 1 for previous, current in zip(sorted_years, sorted_years[1:]))


def _build_pdf_parse_result(
    *,
    parsed_rows: list[_ParsedTransaction],
    layout: PdfLayoutInference,
    layout_profile: DeclarativeLayoutProfile | None,
    selected_parser: str,
    parser_selection_reason: str,
    inline_decision: str,
    tabular_decision: str,
    columnar_decision: str,
    joined_text: str,
    page_count: int,
    flattened_line_count: int,
    invalid_date_candidates_skipped: int,
    grouped_transactions_count: int,
    inline_candidates_count: int,
    inline_transactions_count: int,
    tabular_candidates_count: int,
    tabular_transactions_count: int,
    columnar_candidates_count: int,
    columnar_transactions_count: int,
) -> PdfParseResult:
    transactions = [item.transaction for item in parsed_rows]
    canonical_transactions = build_canonical_transactions(
        parsed_rows,
        bank_name=layout_profile.bank if layout_profile is not None else None,
        layout_name=layout.layout_name,
        layout_used_fallback=layout.used_fallback,
        layout_confidence=layout.confidence,
        source_parser=selected_parser,
    )
    balance_checked_count, balance_failed_count = annotate_balance_consistency(canonical_transactions)
    canonical_quality_metrics = build_canonical_quality_metrics(canonical_transactions)
    return PdfParseResult(
        transactions=transactions,
        canonical_transactions=canonical_transactions,
        layout=layout,
        extracted_text=joined_text,
        parse_metrics=build_pdf_parse_metrics(
            page_count=page_count,
            extracted_char_count=len(joined_text),
            flattened_line_count=flattened_line_count,
            invalid_date_candidates_skipped=invalid_date_candidates_skipped,
            grouped_transactions_count=grouped_transactions_count,
            inline_candidates_count=inline_candidates_count,
            inline_transactions_count=inline_transactions_count,
            tabular_candidates_count=tabular_candidates_count,
            tabular_transactions_count=tabular_transactions_count,
            columnar_candidates_count=columnar_candidates_count,
            columnar_transactions_count=columnar_transactions_count,
            selected_parser=selected_parser,
            parser_selection_reason=parser_selection_reason,
            inline_decision=inline_decision,
            tabular_decision=tabular_decision,
            columnar_decision=columnar_decision,
            layout_used_fallback=layout.used_fallback,
            balance_consistency_checked=balance_checked_count,
            balance_consistency_failed=balance_failed_count,
            canonical_quality_metrics=canonical_quality_metrics,
        ),
    )


def _extract_pdf_page_texts(
    raw_bytes: bytes,
    on_ocr_progress: Callable[[int, int], None] | None = None,
) -> list[str]:
    pages = _read_native_pdf_page_texts(raw_bytes)
    if pages:
        return pages
    if is_pdf_ocr_enabled():
        if on_ocr_progress is None:
            ocr_pages = extract_pdf_page_texts_with_ocr(raw_bytes)
        else:
            ocr_pages = extract_pdf_page_texts_with_ocr(raw_bytes, on_ocr_progress)
        if ocr_pages:
            return ocr_pages

    raise InvalidFileContentError(PDF_OCR_DISABLED_MESSAGE)


def _read_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    return text_extraction.read_native_pdf_page_texts(raw_bytes)


def _read_layout_native_pdf_page_texts(raw_bytes: bytes) -> list[str]:
    return text_extraction.read_layout_native_pdf_page_texts(raw_bytes)


def _read_pdf_page_count(raw_bytes: bytes) -> int:
    return text_extraction.read_pdf_page_count(raw_bytes)


def _flatten_statement_lines(page_texts: list[str], *, preserve_layout_spacing: bool = False) -> list[_PdfLine]:
    lines: list[_PdfLine] = []
    for page_index, page_text in enumerate(page_texts):
        for line_index, line in enumerate(page_text.splitlines()):
            cleaned = line.strip() if preserve_layout_spacing else " ".join(line.split())
            if cleaned:
                lines.append(_PdfLine(text=cleaned, page_number=page_index + 1, line_number=line_index + 1))
    return lines


def _filter_invalid_leading_date_candidate_lines(lines: list[_PdfLine]) -> tuple[list[_PdfLine], int]:
    filtered_lines: list[_PdfLine] = []
    skipped = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)

    for index, line in enumerate(lines):
        raw_date = _extract_leading_date_candidate(line.text)
        if raw_date is None:
            filtered_lines.append(line)
            continue
        try:
            parse_row_date(raw_date, fallback_year=inferred_year)
        except InvalidFileContentError:
            skipped += 1
            continue
        filtered_lines.append(line)

    return filtered_lines, skipped


def _extract_leading_date_candidate(raw_line: str) -> str | None:
    stripped_line = raw_line.strip()
    if is_date_only_row(stripped_line):
        return stripped_line
    match = match_tabular_date_prefix(stripped_line)
    if match is None:
        return None
    return match.group("date")


def _parse_grouped_statement_lines(
    lines: list[_PdfLine], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> list[_ParsedTransaction]:
    transactions: list[_ParsedTransaction] = []
    current_date: str | None = None
    current_section_hint: str | None = None
    description_parts: list[str] = []
    inferred_year = _infer_default_statement_year_from_lines(lines)
    last_transaction_index: int | None = None
    last_known_running_balance: float | None = None
    pending_opening_balance_label: str | None = None
    pending_opening_balance_line: _PdfLine | None = None
    opening_balance_amount: float | None = None
    opening_balance_amount_line: _PdfLine | None = None
    opening_balance_inserted = False
    current_page_number: int | None = None

    for index, line in enumerate(lines):
        if current_page_number is None:
            description_parts = _flush_grouped_description_continuation(
                transactions=transactions,
                last_transaction_index=last_transaction_index,
                description_parts=description_parts,
                current_date=current_date,
            )
            current_page_number = line.page_number
        elif line.page_number != current_page_number:
            # Do not carry grouped parsing context across pages; page headers can contain
            # amount-like tokens such as "Total Disponível" that must not attach to prior rows.
            current_page_number = line.page_number
            current_date = None
            current_section_hint = None
            description_parts = []
            pending_opening_balance_label = None
            pending_opening_balance_line = None

        normalized_line = _normalize_text(line.text)
        if normalized_line.startswith("SALDO ANTERIOR") or normalized_line.startswith("SALDO INICIAL"):
            pending_opening_balance_label = "SALDO ANTERIOR" if "ANTERIOR" in normalized_line else "SALDO INICIAL"
            pending_opening_balance_line = line
            current_date = None
            description_parts = []
            continue
        if (
            pending_opening_balance_label is not None
            and opening_balance_amount is None
            and current_date is None
            and is_amount_only_row(line.text)
        ):
            opening_balance_amount = parse_pdf_amount(line.text)
            opening_balance_amount_line = line
            continue

        grouped_date_match = parse_grouped_date_line(normalized_line, inferred_year=inferred_year)
        if grouped_date_match is not None:
            if _should_treat_grouped_date_line_as_description_continuation(
                lines=lines,
                index=index,
                grouped_date_match=grouped_date_match,
                current_date=current_date,
                description_parts=description_parts,
                layout_profile=layout_profile,
            ):
                description_parts = _append_grouped_description_part(
                    description_parts=description_parts,
                    raw_text=line.text,
                )
                continue
            description_parts = _flush_grouped_description_continuation(
                transactions=transactions,
                last_transaction_index=last_transaction_index,
                description_parts=description_parts,
                current_date=current_date,
            )
            current_date, current_section_hint, description_parts, inline_transaction = _parse_grouped_date_line_state(
                line=line,
                grouped_date=grouped_date_match.date,
                grouped_rest=grouped_date_match.rest,
            )
            if inline_transaction is not None:
                if (
                    not opening_balance_inserted
                    and opening_balance_amount is not None
                    and pending_opening_balance_label is not None
                ):
                    opening_row = _build_parsed_transaction(
                        date=grouped_date_match.date,
                        description=pending_opening_balance_label,
                        amount=opening_balance_amount,
                        source_page=(opening_balance_amount_line or pending_opening_balance_line or line).page_number,
                        source_line=(opening_balance_amount_line or pending_opening_balance_line or line).line_number,
                        has_explicit_amount_sign=has_explicit_amount_sign(
                            (opening_balance_amount_line or line).text
                        ),
                    )
                    transactions.append(opening_row)
                    last_transaction_index = len(transactions) - 1
                    last_known_running_balance = opening_balance_amount
                    opening_balance_inserted = True
                transactions.append(inline_transaction)
                last_transaction_index = len(transactions) - 1
                description_parts = []
            continue

        if current_date is None:
            continue

        current_section_hint, description_parts, should_continue = _update_grouped_section_state(
            normalized_line=normalized_line,
            current_section_hint=current_section_hint,
            description_parts=description_parts,
        )
        if should_continue:
            continue

        description_parts, should_continue = _handle_grouped_ignored_line(
            normalized_line=normalized_line,
            description_parts=description_parts,
        )
        if should_continue:
            continue

        inherited_date_transaction = _parse_grouped_inherited_date_line(
            current_date=current_date,
            line=line,
            section_hint=current_section_hint,
            layout_profile=layout_profile,
        )
        if inherited_date_transaction is not None:
            description_parts = _flush_grouped_description_continuation(
                transactions=transactions,
                last_transaction_index=last_transaction_index,
                description_parts=description_parts,
                current_date=current_date,
            )
            transactions.append(inherited_date_transaction)
            last_transaction_index = len(transactions) - 1
            description_parts = []
            if inherited_date_transaction.running_balance is not None:
                last_known_running_balance = inherited_date_transaction.running_balance
            continue

        pre_amount_description_parts = description_parts
        description_parts = _prepare_grouped_amount_only_description_parts(
            transactions=transactions,
            last_transaction_index=last_transaction_index,
            description_parts=description_parts,
            current_date=current_date,
            layout_profile=layout_profile,
        )
        parsed_row, description_parts, should_continue = _handle_grouped_amount_only_line(
            current_date=current_date,
            description_parts=description_parts,
            line=line,
            section_hint=current_section_hint,
        )
        if should_continue:
            if parsed_row is not None:
                if (
                    not opening_balance_inserted
                    and opening_balance_amount is not None
                    and pending_opening_balance_label is not None
                ):
                    opening_row = _build_parsed_transaction(
                        date=parsed_row.transaction.date,
                        description=pending_opening_balance_label,
                        amount=opening_balance_amount,
                        source_page=(opening_balance_amount_line or pending_opening_balance_line or line).page_number,
                        source_line=(opening_balance_amount_line or pending_opening_balance_line or line).line_number,
                        has_explicit_amount_sign=has_explicit_amount_sign(
                            (opening_balance_amount_line or line).text
                        ),
                    )
                    transactions.append(opening_row)
                    last_transaction_index = len(transactions) - 1
                    last_known_running_balance = opening_balance_amount
                    opening_balance_inserted = True
                transactions.append(parsed_row)
                last_transaction_index = len(transactions) - 1
                if parsed_row.running_balance is not None:
                    last_known_running_balance = parsed_row.running_balance
            elif _is_balance_snapshot_description(pre_amount_description_parts):
                parsed_balance_snapshot = _build_grouped_opening_balance_transaction(
                    current_date=current_date,
                    description_parts=pre_amount_description_parts,
                    line=line,
                    transactions=transactions,
                    opening_balance_inserted=opening_balance_inserted,
                )
                if parsed_balance_snapshot is not None:
                    transactions.append(parsed_balance_snapshot)
                    last_transaction_index = len(transactions) - 1
                    last_known_running_balance = parsed_balance_snapshot.transaction.amount
                    opening_balance_inserted = True
                else:
                    last_known_running_balance = parse_pdf_amount(line.text)
            elif _can_attach_grouped_running_balance(description_parts=description_parts, last_transaction_index=last_transaction_index):
                previous_balance = None
                if _has_adjacent_previous_running_balance_context(transactions, last_transaction_index):
                    previous_balance = _resolve_previous_running_balance(transactions, last_transaction_index)
                    if previous_balance is None:
                        previous_balance = last_known_running_balance
                transactions[last_transaction_index] = _attach_running_balance_and_reconcile_sign(
                    transaction=transactions[last_transaction_index],
                    running_balance=parse_pdf_amount(line.text),
                    previous_running_balance=previous_balance,
                )
                last_known_running_balance = transactions[last_transaction_index].running_balance
            continue

        description_parts = _append_grouped_description_part(
            description_parts=description_parts,
            raw_text=line.text,
        )

    _flush_grouped_description_continuation(
        transactions=transactions,
        last_transaction_index=last_transaction_index,
        description_parts=description_parts,
        current_date=current_date,
    )
    return transactions


def _parse_inline_statement_rows(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)
    pending_inline: tuple[str, str, int, int] | None = None
    pending_columnar_inline: list[tuple[str, str, int, int]] = []
    columnar_amount_mode = False
    columnar_balance_mode = False
    saw_opening_balance_label = False
    opening_balance_label = "SALDO ANTERIOR"
    columnar_running_balances: list[float] = []

    for line in lines:
        if pending_inline is not None:
            _, _, source_page, _ = pending_inline
            if line.page_number != source_page:
                pending_inline = None

        normalized_line = _normalize_text(line.text)
        if normalized_line.startswith("SALDO ANTERIOR"):
            saw_opening_balance_label = True
            opening_balance_label = "SALDO ANTERIOR"
        elif normalized_line.startswith("SALDO INICIAL"):
            saw_opening_balance_label = True
            opening_balance_label = "SALDO INICIAL"
        if pending_columnar_inline and _is_inline_columnar_amount_header(normalized_line):
            columnar_amount_mode = True
        if _is_inline_columnar_balance_header(normalized_line) and (transactions or pending_columnar_inline):
            columnar_balance_mode = True

        stripped_line = line.text.strip()
        if columnar_balance_mode and is_amount_only_row(stripped_line):
            columnar_running_balances.append(parse_pdf_amount(stripped_line))
            continue
        if columnar_balance_mode and stripped_line and not _is_inline_columnar_balance_header(normalized_line):
            columnar_balance_mode = False

        if columnar_amount_mode and pending_columnar_inline and is_amount_only_row(stripped_line):
            pending_date, pending_description, source_page, source_line = pending_columnar_inline.pop(0)
            amount = parse_pdf_amount(stripped_line)
            signed_amount = compute_hint_signed_amount(raw_amount=amount, description=pending_description)
            transactions.append(
                _build_parsed_transaction(
                    date=pending_date,
                    description=pending_description,
                    amount=signed_amount,
                    source_page=source_page,
                    source_line=source_line,
                )
            )
            candidates += 1
            pending_inline = None
            if not pending_columnar_inline:
                columnar_amount_mode = False
            continue

        if pending_inline is not None and is_amount_only_row(line.text.strip()):
            pending_date, pending_description, source_page, source_line = pending_inline
            amount = parse_pdf_amount(line.text.strip())
            signed_amount = compute_hint_signed_amount(raw_amount=amount, description=pending_description)
            transactions.append(
                _build_parsed_transaction(
                    date=pending_date,
                    description=pending_description,
                    amount=signed_amount,
                    source_page=source_page,
                    source_line=source_line,
                )
            )
            candidates += 1
            pending_inline = None
            continue
        if pending_inline is not None:
            pending_date, pending_description, source_page, source_line = pending_inline
            continuation = line.text.strip()
            if _is_inline_pending_continuation_blocker(continuation):
                pending_inline = None
                pending_columnar_inline = []
                columnar_amount_mode = False
                continue
            if _is_inline_pending_noise_line(continuation):
                continue
            if continuation and not match_inline_row(continuation) and not should_skip_transaction_description(continuation):
                pending_inline = (
                    pending_date,
                    f"{pending_description} {continuation}".strip(),
                    source_page,
                    source_line,
                )
                continue
            pending_inline = None

        parsed_row = _parse_inline_statement_line(line=line, inferred_year=inferred_year)
        if parsed_row is None:
            match = match_inline_row(line.text)
            if match:
                rest = match.group("rest").strip()
                if rest and extract_single_trailing_amount_match(rest) is None and not should_skip_transaction_description(rest):
                    pending_columnar_inline.append(
                        (
                            parse_row_date(match.group("date"), fallback_year=inferred_year),
                            rest,
                            line.page_number,
                            line.line_number,
                        )
                    )
                    pending_inline = (
                        parse_row_date(match.group("date"), fallback_year=inferred_year),
                        rest,
                        line.page_number,
                        line.line_number,
                    )
                    continue
            pending_inline = None
            pending_columnar_inline = []
            columnar_amount_mode = False
            continue
        pending_inline = None
        pending_columnar_inline = []
        columnar_amount_mode = False
        candidates += 1
        transactions.append(parsed_row)

    if saw_opening_balance_label and columnar_running_balances and transactions:
        first_transaction = transactions[0]
        inferred_opening_balance = round(columnar_running_balances[0] - first_transaction.transaction.amount, 2)
        if len(columnar_running_balances) >= 2:
            first_balance = columnar_running_balances[0]
            second_balance = columnar_running_balances[1]
            delta = round(second_balance - first_balance, 2)
            first_amount = round(first_transaction.transaction.amount, 2)
            if abs(delta - first_amount) <= 0.02:
                inferred_opening_balance = round(first_balance, 2)
        opening_transaction = _build_parsed_transaction(
            date=first_transaction.transaction.date,
            description=opening_balance_label,
            amount=inferred_opening_balance,
            source_page=first_transaction.source_page,
            source_line=first_transaction.source_line,
        )
        transactions = [opening_transaction, *transactions]

    return transactions, candidates

    return transactions, candidates


def _parse_tabular_statement_rows(
    lines: list[_PdfLine], *, layout_profile: DeclarativeLayoutProfile | None = None
) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)
    line_texts = _extract_line_texts(lines)
    tabular_profile = resolve_tabular_profile(line_texts, layout_profile=layout_profile)
    column_positions = _resolve_tabular_column_positions(line_texts) if tabular_profile is not None else None
    opening_balance_anchor_index = _resolve_opening_balance_anchor_index(lines)
    index = 0
    while index < len(lines):
        line = lines[index]
        if opening_balance_anchor_index is not None and index < opening_balance_anchor_index:
            index += 1
            continue
        if _maybe_attach_tabular_running_balance_line(transactions=transactions, line=line):
            index += 1
            continue
        parsed_row, is_candidate = _classify_tabular_statement_line(
            line=line,
            inferred_year=inferred_year,
            tabular_profile=tabular_profile,
            column_positions=column_positions,
        )
        consumed = 1
        if parsed_row is None:
            recovered_row, recovered_candidate, recovered_consumed = _recover_tabular_multiline_row(
                lines=lines,
                start_index=index,
                inferred_year=inferred_year,
            )
            if recovered_row is not None:
                parsed_row = recovered_row
                is_candidate = recovered_candidate
                consumed = recovered_consumed
        candidates = _accumulate_tabular_row(
            transactions=transactions,
            parsed_row=parsed_row,
            is_candidate=is_candidate,
            candidates=candidates,
        )
        index += max(1, consumed)

    return transactions, candidates


def _resolve_opening_balance_anchor_index(lines: list[_PdfLine]) -> int | None:
    for index, line in enumerate(lines):
        normalized = _normalize_text(line.text)
        if (
            normalized.startswith("SALDO ANTERIOR")
            or normalized.startswith("SALDO INICIAL")
            or " SALDO ANTERIOR" in normalized
            or " SALDO INICIAL" in normalized
        ):
            return index
    return None


def _resolve_tabular_column_role(
    *,
    line_text: str,
    rest_start: int,
    amount_start: int,
    amount_token_value: str,
    fallback_role: str | None,
    column_positions: _TabularColumnPositions | None,
    has_balance_token: bool = False,
) -> str | None:
    if column_positions is None or has_explicit_amount_sign(amount_token_value) or fallback_role in {"credit", "debit"}:
        return fallback_role

    absolute_amount_start = rest_start + amount_start
    first_role, first_start, first_end, second_role, second_start, second_end = (
        (
            "credit",
            column_positions.credit_start,
            column_positions.credit_end,
            "debit",
            column_positions.debit_start,
            column_positions.debit_end,
        )
        if column_positions.credit_start <= column_positions.debit_start
        else (
            "debit",
            column_positions.debit_start,
            column_positions.debit_end,
            "credit",
            column_positions.credit_start,
            column_positions.credit_end,
        )
    )
    first_center = (first_start + first_end) / 2
    second_center = (second_start + second_end) / 2
    first_second_boundary = (first_center + second_center) / 2
    second_balance_boundary = (second_end + column_positions.balance_start) / 2
    if absolute_amount_start < first_start:
        return fallback_role
    if not has_balance_token and "  " not in line_text:
        return first_role
    if absolute_amount_start < first_second_boundary:
        return first_role
    if not has_balance_token:
        return second_role
    if absolute_amount_start < second_balance_boundary:
        return second_role
    return fallback_role


def _maybe_attach_tabular_running_balance_line(
    *,
    transactions: list[_ParsedTransaction],
    line: _PdfLine,
) -> bool:
    if not transactions:
        return False
    if not is_amount_only_row(line.text):
        return False

    last_transaction = transactions[-1]
    if last_transaction.running_balance is not None:
        return False
    if last_transaction.source_page != line.page_number:
        return False
    if last_transaction.source_line is not None and line.line_number <= last_transaction.source_line:
        return False
    if last_transaction.source_line is not None and (line.line_number - last_transaction.source_line) > 12:
        return False

    previous_running_balance = _resolve_latest_running_balance(transactions[:-1])
    transactions[-1] = _attach_running_balance_and_reconcile_sign(
        transaction=last_transaction,
        running_balance=parse_pdf_amount(line.text),
        previous_running_balance=previous_running_balance,
    )
    return True


def _recover_tabular_multiline_row(
    *,
    lines: list[_PdfLine],
    start_index: int,
    inferred_year: int | None,
) -> tuple[_ParsedTransaction | None, bool, int]:
    date_line = lines[start_index]
    if not is_date_only_row(date_line.text):
        return None, False, 1

    description_parts: list[str] = []
    amount_line: _PdfLine | None = None
    running_balance: float | None = None
    last_index = start_index

    for index in range(start_index + 1, min(len(lines), start_index + 6)):
        current = lines[index]
        if current.page_number != date_line.page_number:
            break
        normalized_line = _normalize_text(current.text)
        if is_date_only_row(current.text):
            break

        if is_amount_only_row(current.text):
            if amount_line is None:
                amount_line = current
                last_index = index
                continue
            # Some OCR outputs split amount and running balance into separate lines.
            running_balance = parse_pdf_amount(current.text)
            last_index = index
            break

        if should_ignore_line(normalized_line) or should_skip_transaction_description(current.text):
            # OCR can interleave repeated page header/footer fragments between date/description/amount tokens.
            last_index = index
            continue

        description_parts.append(current.text.strip())
        last_index = index

    if amount_line is None:
        return None, False, 1
    raw_description = " ".join(part for part in description_parts if part).strip()
    if not raw_description or should_skip_transaction_description(raw_description):
        return None, True, max(1, last_index - start_index + 1)

    signed_amount = compute_hint_signed_amount(
        raw_amount=parse_pdf_amount(amount_line.text),
        description=raw_description,
    )
    parsed_row = _build_parsed_transaction(
        date=parse_row_date(date_line.text, fallback_year=inferred_year),
        description=raw_description,
        amount=signed_amount,
        source_page=date_line.page_number,
        source_line=date_line.line_number,
        running_balance=running_balance,
        has_explicit_amount_sign=has_explicit_amount_sign(amount_line.text),
    )
    return parsed_row, True, max(1, last_index - start_index + 1)

def _parse_columnar_statement_blocks(lines: list[_PdfLine]) -> tuple[list[_ParsedTransaction], int]:
    transactions: list[_ParsedTransaction] = []
    candidates = 0
    inferred_year = _infer_default_statement_year_from_lines(lines)
    line_texts = _extract_line_texts(lines)
    index = 0

    while index < len(lines):
        parsed_row = _parse_columnar_block_at_index(lines=lines, index=index, inferred_year=inferred_year)
        if parsed_row is not None:
            candidates += 1
            transactions.append(parsed_row)
        index = _resolve_next_columnar_index(line_texts=line_texts, current_index=index, parsed_row=parsed_row)

    return transactions, candidates


def _parse_columnar_block_at_index(
    *, lines: list[_PdfLine], index: int, inferred_year: int | None
) -> _ParsedTransaction | None:
    raw_date = lines[index].text.strip()
    if not is_date_only_row(raw_date):
        return None

    if index + 3 >= len(lines):
        return None

    description = lines[index + 1].text.strip()
    type_raw = lines[index + 2].text.strip()
    amount_raw = lines[index + 3].text.strip()
    if not is_valid_columnar_transaction_row(description=description, type_raw=type_raw, amount_raw=amount_raw):
        return None

    amount = apply_type_sign_hint(parse_pdf_amount(amount_raw), type_raw)
    signed_amount = compute_hint_signed_amount(raw_amount=amount, description=description)
    return _build_parsed_transaction(
        date=parse_row_date(raw_date, fallback_year=inferred_year),
        description=description,
        amount=signed_amount,
        source_page=lines[index].page_number,
        source_line=lines[index].line_number,
    )


def _parse_grouped_date_line_state(
    *,
    line: _PdfLine,
    grouped_date: str,
    grouped_rest: str,
) -> tuple[str, str | None, list[str], _ParsedTransaction | None]:
    next_date = grouped_date
    next_description_parts: list[str] = []
    next_section_hint = resolve_grouped_section_hint(grouped_rest, current_hint=None)
    inline_transaction = _build_inline_transaction_from_date_rest(
        date=next_date,
        rest=grouped_rest,
        section_hint=next_section_hint,
        source_page=line.page_number,
        source_line=line.line_number,
    )
    return next_date, next_section_hint, next_description_parts, inline_transaction


def _resolve_next_columnar_index(
    *, line_texts: list[str], current_index: int, parsed_row: _ParsedTransaction | None
) -> int:
    if parsed_row is None:
        return current_index + 1
    return next_columnar_block_index(line_texts, current_index=current_index)


def _parse_inline_statement_line(*, line: _PdfLine, inferred_year: int | None) -> _ParsedTransaction | None:
    match = match_inline_row(line.text)
    if not match:
        return None

    rest = match.group("rest").strip()
    amount_match = extract_single_trailing_amount_match(rest)
    if amount_match is None:
        return None

    raw_description = amount_match.description
    if not raw_description or should_skip_transaction_description(raw_description):
        return None

    amount = parse_pdf_amount(amount_match.amount_token.value)
    signed_amount = compute_hint_signed_amount(raw_amount=amount, description=raw_description)
    return _build_parsed_transaction(
        date=parse_row_date(match.group("date"), fallback_year=inferred_year),
        description=raw_description,
        amount=signed_amount,
        source_page=line.page_number,
        source_line=line.line_number,
    )


def _parse_tabular_statement_line(
    *,
    line: _PdfLine,
    inferred_year: int | None,
    tabular_profile: DeclarativeLayoutProfile | None,
) -> _ParsedTransaction | None:
    parsed_row, _ = _classify_tabular_statement_line(
        line=line,
        inferred_year=inferred_year,
        tabular_profile=tabular_profile,
    )
    return parsed_row


def _classify_tabular_statement_line(
    *,
    line: _PdfLine,
    inferred_year: int | None,
    tabular_profile: DeclarativeLayoutProfile | None,
    column_positions: _TabularColumnPositions | None = None,
) -> tuple[_ParsedTransaction | None, bool]:
    match = match_tabular_date_prefix(line.text)
    if not match:
        return None, False

    rest = match.group("rest").strip()
    if not rest:
        return None, False

    amount_tokens = find_amount_tokens(rest)
    if not amount_tokens:
        return None, False

    selected_amount = select_tabular_amount_token(amount_tokens, layout_profile=tabular_profile)
    if selected_amount is None:
        return None, True

    raw_description = " ".join(rest[: selected_amount.description_end].split())
    normalized_description = _normalize_text(raw_description)
    if tabular_profile is not None and (
        normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL")
    ):
        opening_balance_row = _build_tabular_opening_balance_row(
            raw_date=match.group("date"),
            inferred_year=inferred_year,
            raw_description=raw_description,
            selected_amount=selected_amount,
            source_page=line.page_number,
            source_line=line.line_number,
        )
        if opening_balance_row is not None:
            return opening_balance_row, True
    if not raw_description or should_skip_transaction_description(raw_description):
        return None, True
    if normalized_description.endswith(" SALDO"):
        return None, True

    external_reference_id = extract_document_reference(raw_description, layout_profile=tabular_profile)
    selected_role = _resolve_tabular_column_role(
        line_text=line.text,
        rest_start=match.start("rest"),
        amount_start=selected_amount.token.start,
        amount_token_value=selected_amount.token.value,
        fallback_role=selected_amount.role,
        column_positions=column_positions,
        has_balance_token=selected_amount.balance_token is not None,
    )
    amount_details = _build_tabular_amount_details(
        amount_token_value=selected_amount.token.value,
        selected_role=selected_role,
        raw_description=raw_description,
        balance_token_value=selected_amount.balance_token.value if selected_amount.balance_token else None,
    )
    compact_amount = _extract_compact_cd_amount(raw_description)
    if (
        compact_amount is not None
        and amount_details["running_balance"] is None
        and abs(amount_details["signed_amount"]) > 100
        and abs(compact_amount) <= 50
    ):
        amount_details["signed_amount"] = compact_amount
    return (
        _build_parsed_transaction(
            date=parse_row_date(match.group("date"), fallback_year=inferred_year),
            description=raw_description,
            amount=amount_details["signed_amount"],
            source_page=line.page_number,
            source_line=line.line_number,
            running_balance=amount_details["running_balance"],
            external_reference_id=external_reference_id,
            has_explicit_amount_sign=has_explicit_amount_sign(selected_amount.token.value),
        ),
        True,
    )


def _accumulate_tabular_row(
    *,
    transactions: list[_ParsedTransaction],
    parsed_row: _ParsedTransaction | None,
    is_candidate: bool,
    candidates: int,
) -> int:
    next_candidates = candidates + 1 if is_candidate else candidates
    if parsed_row is not None:
        transactions.append(_reconcile_tabular_amount_from_running_balance(parsed_row=parsed_row, transactions=transactions))
    return next_candidates


def _reconcile_tabular_amount_from_running_balance(
    *, parsed_row: _ParsedTransaction, transactions: list[_ParsedTransaction]
) -> _ParsedTransaction:
    previous_running_balance = _resolve_latest_running_balance(transactions)
    if previous_running_balance is None:
        return parsed_row

    if parsed_row.running_balance is None:
        return _maybe_reconcile_single_token_balance_noise(
            parsed_row=parsed_row,
            previous_running_balance=previous_running_balance,
        )

    delta = round(parsed_row.running_balance - previous_running_balance, 2)
    if abs(delta) < 0.005:
        return parsed_row

    current_amount = parsed_row.transaction.amount
    projected_balance = previous_running_balance + current_amount
    current_error = abs(projected_balance - parsed_row.running_balance)
    if current_error <= 0.02:
        return parsed_row

    amount_looks_like_balance = abs(abs(current_amount) - abs(parsed_row.running_balance)) <= 0.05
    if not amount_looks_like_balance:
        return parsed_row

    normalized_transaction = NormalizedTransaction(
        date=parsed_row.transaction.date,
        description=parsed_row.transaction.description,
        amount=delta,
        type="inflow" if delta >= 0 else "outflow",
    )
    return _ParsedTransaction(
        transaction=normalized_transaction,
        source_page=parsed_row.source_page,
        source_line=parsed_row.source_line,
        running_balance=parsed_row.running_balance,
        external_reference_id=parsed_row.external_reference_id,
        has_explicit_amount_sign=parsed_row.has_explicit_amount_sign,
    )


def _maybe_reconcile_single_token_balance_noise(
    *,
    parsed_row: _ParsedTransaction,
    previous_running_balance: float,
) -> _ParsedTransaction:
    if parsed_row.has_explicit_amount_sign:
        return parsed_row
    current_amount = parsed_row.transaction.amount
    # Heuristic for OCR lines where debit disappeared and only running balance token survived.
    if abs(current_amount) < 1000:
        return parsed_row
    inferred_running_balance = abs(current_amount)
    delta = round(inferred_running_balance - previous_running_balance, 2)
    if abs(delta) < 0.01 or abs(delta) > 200:
        return parsed_row
    if abs(inferred_running_balance - previous_running_balance) > 200:
        return parsed_row

    normalized_transaction = NormalizedTransaction(
        date=parsed_row.transaction.date,
        description=parsed_row.transaction.description,
        amount=delta,
        type="inflow" if delta >= 0 else "outflow",
    )
    return _ParsedTransaction(
        transaction=normalized_transaction,
        source_page=parsed_row.source_page,
        source_line=parsed_row.source_line,
        running_balance=inferred_running_balance,
        external_reference_id=parsed_row.external_reference_id,
        has_explicit_amount_sign=parsed_row.has_explicit_amount_sign,
    )


def _resolve_latest_running_balance(transactions: list[_ParsedTransaction]) -> float | None:
    for item in reversed(transactions):
        if item.running_balance is not None:
            return item.running_balance
    return None


def _build_tabular_amount_details(
    *,
    amount_token_value: str,
    selected_role: str,
    raw_description: str,
    balance_token_value: str | None,
) -> dict[str, float | None]:
    signed_amount = compute_tabular_signed_amount(
        raw_amount=parse_pdf_amount(amount_token_value),
        role=selected_role,
        description=raw_description,
    )
    running_balance = parse_pdf_amount(balance_token_value) if balance_token_value is not None else None
    return {"signed_amount": signed_amount, "running_balance": running_balance}


def _build_tabular_opening_balance_row(
    *,
    raw_date: str,
    inferred_year: int | None,
    raw_description: str,
    selected_amount: SelectedTabularAmount,
    source_page: int,
    source_line: int,
) -> _ParsedTransaction | None:
    opening_balance = (
        parse_pdf_amount(selected_amount.balance_token.value)
        if selected_amount.balance_token is not None
        else parse_pdf_amount(selected_amount.token.value)
    )
    amount_value = parse_pdf_amount(selected_amount.token.value)
    if (
        selected_amount.balance_token is not None
        and abs(amount_value) > 0.000001
        and abs(abs(amount_value) - abs(opening_balance)) > 0.01
    ):
        return None

    return _build_parsed_transaction(
        date=parse_row_date(raw_date, fallback_year=inferred_year),
        description="SALDO ANTERIOR" if "ANTERIOR" in _normalize_text(raw_description) else "SALDO INICIAL",
        amount=opening_balance,
        source_page=source_page,
        source_line=source_line,
        running_balance=opening_balance,
        has_explicit_amount_sign=has_explicit_amount_sign(selected_amount.token.value),
    )


def _extract_compact_cd_amount(description: str) -> float | None:
    match = re.search(r"([+\-\u2212]?)\s*(\d{3,5})\s*([CD])\b", description.upper())
    if not match:
        return None
    sign_prefix = match.group(1)
    digits = int(match.group(2))
    suffix = match.group(3)
    value = digits / 100.0
    if sign_prefix in {"-", "\u2212"} or suffix == "D":
        return -abs(value)
    if sign_prefix == "+" or suffix == "C":
        return abs(value)
    return None


def _update_grouped_section_state(
    *,
    normalized_line: str,
    current_section_hint: str | None,
    description_parts: list[str],
) -> tuple[str | None, list[str], bool]:
    next_hint = resolve_grouped_section_hint(normalized_line, current_hint=current_section_hint)
    if next_hint != current_section_hint:
        return next_hint, [], True
    return next_hint, description_parts, False


def _handle_grouped_ignored_line(*, normalized_line: str, description_parts: list[str]) -> tuple[list[str], bool]:
    if normalized_line in {"-", "--"}:
        return description_parts, True
    if should_ignore_grouped_line(normalized_line):
        return [], True
    return description_parts, False


def _handle_grouped_amount_only_line(
    *,
    current_date: str,
    description_parts: list[str],
    line: _PdfLine,
    section_hint: str | None,
) -> tuple[_ParsedTransaction | None, list[str], bool]:
    if not is_amount_only_row(line.text):
        return None, description_parts, False

    parsed_row = _build_grouped_amount_only_transaction(
        date=current_date,
        description_parts=description_parts,
        line=line,
        section_hint=section_hint,
    )
    if parsed_row is None:
        return None, [], True
    return parsed_row, [], True


def _append_grouped_description_part(*, description_parts: list[str], raw_text: str) -> list[str]:
    cleaned_text = raw_text.strip()
    if not cleaned_text:
        return description_parts
    return [*description_parts, cleaned_text]


def _build_grouped_opening_balance_transaction(
    *,
    current_date: str,
    description_parts: list[str],
    line: _PdfLine,
    transactions: list[_ParsedTransaction],
    opening_balance_inserted: bool,
) -> _ParsedTransaction | None:
    if opening_balance_inserted or transactions:
        return None
    if not _is_balance_snapshot_description(description_parts):
        return None

    return _build_parsed_transaction(
        date=current_date,
        description="SALDO ANTERIOR",
        amount=parse_pdf_amount(line.text),
        source_page=line.page_number,
        source_line=line.line_number,
        has_explicit_amount_sign=has_explicit_amount_sign(line.text),
    )


def _prepare_grouped_amount_only_description_parts(
    *,
    transactions: list[_ParsedTransaction],
    last_transaction_index: int | None,
    description_parts: list[str],
    current_date: str,
    layout_profile: DeclarativeLayoutProfile | None,
) -> list[str]:
    if not _should_split_vangogh_grouped_amount_only_description_parts(
        transactions=transactions,
        last_transaction_index=last_transaction_index,
        description_parts=description_parts,
        current_date=current_date,
        layout_profile=layout_profile,
    ):
        return description_parts

    _flush_grouped_description_continuation(
        transactions=transactions,
        last_transaction_index=last_transaction_index,
        description_parts=description_parts[:-1],
        current_date=current_date,
    )
    return [description_parts[-1]]


def _parse_grouped_inherited_date_line(
    *,
    current_date: str,
    line: _PdfLine,
    section_hint: str | None,
    layout_profile: DeclarativeLayoutProfile | None,
) -> _ParsedTransaction | None:
    if is_amount_only_row(line.text):
        return None

    raw_text = line.text.strip()
    amount_tokens = find_amount_tokens(raw_text)
    if not amount_tokens:
        return None

    selected_amount = select_tabular_amount_token(amount_tokens, layout_profile=layout_profile)
    if selected_amount is None:
        return None

    raw_description = " ".join(raw_text[: selected_amount.description_end].split())
    if not raw_description or should_skip_transaction_description(raw_description):
        return None

    normalized_description = _normalize_text(raw_description)
    if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
        return None
    if normalized_description == "SALDO" or normalized_description.startswith("SALDO "):
        return None

    amount_details = _build_tabular_amount_details(
        amount_token_value=selected_amount.token.value,
        selected_role=selected_amount.role or "amount",
        raw_description=raw_description,
        balance_token_value=selected_amount.balance_token.value if selected_amount.balance_token else None,
    )
    return _build_parsed_transaction(
        date=current_date,
        description=raw_description,
        amount=amount_details["signed_amount"],
        source_page=line.page_number,
        source_line=line.line_number,
        running_balance=amount_details["running_balance"],
        has_explicit_amount_sign=has_explicit_amount_sign(selected_amount.token.value),
    )


def _should_split_vangogh_grouped_amount_only_description_parts(
    *,
    transactions: list[_ParsedTransaction],
    last_transaction_index: int | None,
    description_parts: list[str],
    current_date: str,
    layout_profile: DeclarativeLayoutProfile | None,
) -> bool:
    if layout_profile is None or layout_profile.profile_name != "santander_vangogh_resumo_consolidado_conta_corrente_v1":
        return False
    if len(description_parts) < 2 or last_transaction_index is None:
        return False
    if last_transaction_index < 0 or last_transaction_index >= len(transactions):
        return False

    last_transaction = transactions[last_transaction_index]
    return last_transaction.transaction.date == current_date


def _flush_grouped_description_continuation(
    *,
    transactions: list[_ParsedTransaction],
    last_transaction_index: int | None,
    description_parts: list[str],
    current_date: str | None,
) -> list[str]:
    if not description_parts or last_transaction_index is None or current_date is None:
        return description_parts
    if last_transaction_index < 0 or last_transaction_index >= len(transactions):
        return description_parts
    if _is_balance_snapshot_description(description_parts):
        return []

    continuation = " ".join(part for part in description_parts if part).strip()
    if not continuation or should_skip_transaction_description(continuation):
        return []

    last_transaction = transactions[last_transaction_index]
    if last_transaction.transaction.date != current_date:
        return description_parts

    updated_transaction = NormalizedTransaction(
        date=last_transaction.transaction.date,
        description=f"{last_transaction.transaction.description} {continuation}".strip(),
        amount=last_transaction.transaction.amount,
        type=last_transaction.transaction.type,
    )
    transactions[last_transaction_index] = _ParsedTransaction(
        transaction=updated_transaction,
        source_page=last_transaction.source_page,
        source_line=last_transaction.source_line,
        running_balance=last_transaction.running_balance,
        external_reference_id=last_transaction.external_reference_id,
        has_explicit_amount_sign=last_transaction.has_explicit_amount_sign,
    )
    return []


def _build_grouped_amount_only_transaction(
    *,
    date: str,
    description_parts: list[str],
    line: _PdfLine,
    section_hint: str | None,
) -> _ParsedTransaction | None:
    if not description_parts:
        return None

    description, resolved_section_hint = _resolve_grouped_description_and_hint(
        description_parts=description_parts,
        section_hint=section_hint,
    )
    if should_skip_transaction_description(description):
        return None
    signed_amount = parse_grouped_amount_line(
        raw_amount_text=line.text,
        description=description,
        section_hint=resolved_section_hint,
    )
    return _build_parsed_transaction(
        date=date,
        description=description,
        amount=signed_amount,
        source_page=line.page_number,
        source_line=line.line_number,
        has_explicit_amount_sign=has_explicit_amount_sign(line.text),
    )


def _resolve_grouped_description_and_hint(
    *, description_parts: list[str], section_hint: str | None
) -> tuple[str, str | None]:
    description = " ".join(description_parts).strip()
    if not description_parts:
        return description, section_hint

    last_part_normalized = _normalize_text(description_parts[-1].strip())
    if last_part_normalized in {"DEBITO", "DÉBITO", "CREDITO", "CRÉDITO"} and len(description_parts) >= 2:
        resolved_hint = "outflow" if "DEBIT" in last_part_normalized else "inflow"
        return " ".join(description_parts[:-1]).strip(), resolved_hint
    return description, section_hint


def _build_inline_transaction_from_date_rest(
    *,
    date: str,
    rest: str,
    section_hint: str | None,
    source_page: int | None = None,
    source_line: int | None = None,
) -> _ParsedTransaction | None:
    amount_match = extract_single_trailing_amount_match(rest)
    if amount_match is None:
        return None

    raw_description = amount_match.description
    if not raw_description or should_skip_transaction_description(raw_description):
        return None
    amount = parse_pdf_amount(amount_match.amount_token.value)
    signed_amount = compute_hint_signed_amount(raw_amount=amount, description=raw_description, section_hint=section_hint)
    return _build_parsed_transaction(
        date=date,
        description=raw_description,
        amount=signed_amount,
        source_page=source_page,
        source_line=source_line,
        has_explicit_amount_sign=has_explicit_amount_sign(amount_match.amount_token.value),
    )


def _build_parsed_transaction(
    *,
    date: str,
    description: str,
    amount: float,
    source_page: int | None = None,
    source_line: int | None = None,
    running_balance: float | None = None,
    external_reference_id: str | None = None,
    has_explicit_amount_sign: bool = False,
) -> _ParsedTransaction:
    return _ParsedTransaction(
        transaction=NormalizedTransaction(
            date=date,
            description=description,
            amount=amount,
            type="inflow" if amount >= 0 else "outflow",
        ),
        source_page=source_page,
        source_line=source_line,
        running_balance=running_balance,
        external_reference_id=external_reference_id,
        has_explicit_amount_sign=has_explicit_amount_sign,
    )


def _can_attach_grouped_running_balance(*, description_parts: list[str], last_transaction_index: int | None) -> bool:
    return bool(not description_parts and last_transaction_index is not None)


def _is_balance_snapshot_description(description_parts: list[str]) -> bool:
    description = " ".join(description_parts).strip()
    if not description:
        return False
    normalized = _normalize_text(description)
    return normalized == "SALDO" or normalized.startswith("SALDO ")


def _resolve_previous_running_balance(transactions: list[_ParsedTransaction], current_index: int) -> float | None:
    for index in range(current_index - 1, -1, -1):
        balance = transactions[index].running_balance
        if balance is not None:
            return balance

    current = transactions[current_index]
    normalized_description = _normalize_text(current.transaction.description)
    if normalized_description.startswith("SALDO ANTERIOR") or normalized_description.startswith("SALDO INICIAL"):
        return current.transaction.amount
    if current_index > 0:
        previous_description = _normalize_text(transactions[current_index - 1].transaction.description)
        if previous_description.startswith("SALDO ANTERIOR") or previous_description.startswith("SALDO INICIAL"):
            return transactions[current_index - 1].transaction.amount
    return None


def _has_adjacent_previous_running_balance_context(
    transactions: list[_ParsedTransaction], current_index: int
) -> bool:
    if current_index <= 0:
        return False
    previous_item = transactions[current_index - 1]
    if previous_item.running_balance is not None:
        return True
    previous_description = _normalize_text(previous_item.transaction.description)
    return previous_description.startswith("SALDO ANTERIOR") or previous_description.startswith("SALDO INICIAL")


def _attach_running_balance_and_reconcile_sign(
    *,
    transaction: _ParsedTransaction,
    running_balance: float,
    previous_running_balance: float | None,
) -> _ParsedTransaction:
    signed_amount = transaction.transaction.amount
    if (not transaction.has_explicit_amount_sign) and previous_running_balance is not None:
        delta = running_balance - previous_running_balance
        amount_abs = abs(signed_amount)
        positive_error = abs(delta - amount_abs)
        negative_error = abs(delta + amount_abs)
        if positive_error <= 0.05 and positive_error + 0.005 < negative_error:
            signed_amount = amount_abs
        elif negative_error <= 0.05 and negative_error + 0.005 < positive_error:
            signed_amount = -amount_abs

    normalized_transaction = NormalizedTransaction(
        date=transaction.transaction.date,
        description=transaction.transaction.description,
        amount=signed_amount,
        type="inflow" if signed_amount >= 0 else "outflow",
    )
    return _ParsedTransaction(
        transaction=normalized_transaction,
        source_page=transaction.source_page,
        source_line=transaction.source_line,
        running_balance=running_balance,
        external_reference_id=transaction.external_reference_id,
        has_explicit_amount_sign=transaction.has_explicit_amount_sign,
    )


def _normalize_text(value: str) -> str:
    return normalize_upper_text(value)


def _ascii_fold_upper(value: str) -> str:
    return value.upper().translate(
        str.maketrans(
            {
                "Á": "A",
                "À": "A",
                "Ã": "A",
                "Â": "A",
                "Ä": "A",
                "É": "E",
                "È": "E",
                "Ê": "E",
                "Ë": "E",
                "Í": "I",
                "Ì": "I",
                "Î": "I",
                "Ï": "I",
                "Ó": "O",
                "Ò": "O",
                "Ô": "O",
                "Õ": "O",
                "Ö": "O",
                "Ú": "U",
                "Ù": "U",
                "Û": "U",
                "Ü": "U",
                "Ç": "C",
            }
        )
    ).replace("?", "E")


def _find_first_column_alias(line: str, aliases: tuple[str, ...]) -> int | None:
    span = _find_first_column_alias_span(line, aliases)
    if span is None:
        return None
    return span[0]


def _find_first_column_alias_span(line: str, aliases: tuple[str, ...]) -> tuple[int, int] | None:
    folded_line = _ascii_fold_upper(line)
    spans = [(folded_line.find(alias), folded_line.find(alias) + len(alias)) for alias in aliases]
    valid_spans = [span for span in spans if span[0] >= 0]
    if not valid_spans:
        return None
    return min(valid_spans, key=lambda span: span[0])


def _resolve_tabular_column_positions(line_texts: list[str]) -> _TabularColumnPositions | None:
    for line in line_texts:
        credit_span = _find_first_column_alias_span(line, ("CREDITO", "CREDITOS"))
        debit_span = _find_first_column_alias_span(line, ("DEBITO", "DEBITOS"))
        balance_start = _find_first_column_alias(line, ("SALDO (R$)", "SALDO"))
        if credit_span is None or debit_span is None or balance_start is None:
            continue
        credit_start, credit_end = credit_span
        debit_start, debit_end = debit_span
        if credit_start < balance_start and debit_start < balance_start:
            return _TabularColumnPositions(
                credit_start=credit_start,
                credit_end=credit_end,
                debit_start=debit_start,
                debit_end=debit_end,
                balance_start=balance_start,
            )
    return None


def _maybe_upgrade_native_parse_with_layout_text(*, raw_bytes: bytes, baseline_result: PdfParseResult) -> PdfParseResult:
    try:
        layout_pages = _read_layout_native_pdf_page_texts(raw_bytes)
    except InvalidFileContentError:
        return baseline_result

    if not layout_pages:
        return baseline_result
    if "\n".join(layout_pages).strip() == baseline_result.extracted_text.strip():
        return baseline_result

    reference_month_year = text_extraction.read_pdf_creation_month_year(raw_bytes)

    try:
        previous_reference_month_year = _REFERENCE_MONTH_YEAR_CONTEXT
        globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = reference_month_year
        try:
            candidate_result = _parse_pdf_transactions_from_page_texts(
                layout_pages,
                preserve_layout_spacing=True,
            )
        finally:
            globals()["_REFERENCE_MONTH_YEAR_CONTEXT"] = previous_reference_month_year
    except InvalidFileContentError:
        return baseline_result

    if _is_better_parse_result(candidate=candidate_result, baseline=baseline_result):
        return candidate_result
    if _should_prefer_layout_text_result(candidate=candidate_result, baseline=baseline_result):
        return candidate_result
    return baseline_result


def _should_prefer_layout_text_result(*, candidate: PdfParseResult, baseline: PdfParseResult) -> bool:
    if len(candidate.transactions) != len(baseline.transactions):
        return False
    if str(candidate.parse_metrics.get("selected_parser")) != "tabular":
        return False
    if str(baseline.parse_metrics.get("selected_parser")) == "tabular":
        return False

    candidate_profile = get_layout_profile(candidate.layout.layout_name)
    if candidate_profile is None:
        return False
    expected_column_order = set(candidate_profile.expected_column_order)
    has_credit_debit_columns = "credit" in expected_column_order or "debit" in expected_column_order
    if not has_credit_debit_columns and candidate.layout.layout_name != baseline.layout.layout_name:
        return False

    candidate_outflows = sum(1 for transaction in candidate.transactions if transaction.amount < 0)
    baseline_outflows = sum(1 for transaction in baseline.transactions if transaction.amount < 0)
    if candidate_outflows <= baseline_outflows:
        return False

    candidate_balances = int(candidate.parse_metrics.get("canonical_with_running_balance_count", 0))
    baseline_balances = int(baseline.parse_metrics.get("canonical_with_running_balance_count", 0))
    return candidate_balances >= baseline_balances


def _infer_default_statement_year_from_lines(lines: list[_PdfLine]) -> int | None:
    return infer_default_statement_year(_extract_line_texts(lines))


def _extract_line_texts(lines: list[_PdfLine]) -> list[str]:
    return [line.text for line in lines]


def _is_inline_pending_continuation_blocker(raw_text: str) -> bool:
    normalized = _normalize_text(raw_text)
    if should_ignore_line(normalized):
        return True
    if "DATA" in normalized and "CREDITO" in normalized and "DEBITO" in normalized and "SALDO" in normalized:
        return True
    return "EXTRATO" in normalized and "CONTA" in normalized


def _is_inline_pending_noise_line(raw_text: str) -> bool:
    value = raw_text.strip()
    if not value:
        return True
    return bool(re.fullmatch(r"[|:;,\.\-_/\\Il!]+", value))


def _is_inline_columnar_amount_header(normalized_line: str) -> bool:
    if normalized_line in {"DOC", "VALOR", "SALDO"}:
        return True
    return normalized_line.startswith("DATA:") or normalized_line.startswith("HORA:")


def _is_inline_columnar_balance_header(normalized_line: str) -> bool:
    return normalized_line == "SALDO"

