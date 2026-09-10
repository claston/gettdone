from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from app.application.conversion_quality_history import prepare_quality_record, replace_quality_issues


def record_user_conversion(
    conn,
    *,
    execute: Callable,
    now_iso: str,
    user_id: str,
    processing_id: str,
    filename: str,
    model: str,
    conversion_type: str,
    status: str,
    transactions_count: int | None,
    pages_count: int | None = None,
    scanned_likely: bool | None = None,
    ocr_used: bool = False,
    ocr_pages_processed: int = 0,
    duration_ms: int = 0,
    error_code: str | None = None,
    error_stage: str | None = None,
    error_subcode: str | None = None,
    exception_class: str | None = None,
    layout_inference_name: str | None = None,
    layout_inference_confidence: float | None = None,
    selected_parser: str | None = None,
    parser_selection_reason: str | None = None,
    pdf_page_count: int | None = None,
    extracted_char_count: int | None = None,
    ocr_attempted: bool = False,
    ocr_engine: str | None = None,
    file_sha256: str | None = None,
    canonical_warning_transactions_count: int = 0,
    balance_consistency_failed: int = 0,
    parser_confidence_band: str | None = None,
    parser_coverage_rate: float | None = None,
    warning_types: list[str] | None = None,
    failure_diagnostics: dict[str, object] | None = None,
    quality_issues: list[dict[str, object]] | None = None,
    created_at: str | None = None,
    expires_at: str | None = None,
) -> None:
    effective_created_at = created_at or now_iso
    assessment, reason_codes_json, warning_types_json, failure_diagnostics_json = prepare_quality_record(
        status=status,
        conversion_type=conversion_type,
        transactions_count=transactions_count,
        layout_name=layout_inference_name,
        layout_confidence=layout_inference_confidence,
        selected_parser=selected_parser,
        warning_count=canonical_warning_transactions_count,
        balance_failed=balance_consistency_failed,
        parser_confidence_band=parser_confidence_band,
        parser_coverage_rate=parser_coverage_rate,
        warning_types=warning_types,
        failure_diagnostics=failure_diagnostics,
    )
    execute(
        conn,
        """
        INSERT INTO user_conversions (
          analysis_id,
          user_id,
          created_at,
          expires_at,
          filename,
          model,
          conversion_type,
          status,
          transactions_count,
          pages_count,
          scanned_likely,
          ocr_used,
          ocr_pages_processed,
          duration_ms,
          error_code,
          error_stage,
          error_subcode,
          exception_class,
          layout_inference_name,
          layout_inference_confidence,
          selected_parser,
          parser_selection_reason,
          pdf_page_count,
          extracted_char_count,
          ocr_attempted,
          ocr_engine,
          file_sha256,
          canonical_warning_transactions_count,
          balance_consistency_failed,
          quality_status,
          quality_score,
          quality_rule_version,
          quality_reason_codes_json,
          parser_confidence_band,
          parser_coverage_rate,
          warning_types_json,
          failure_diagnostics_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(analysis_id)
        DO UPDATE SET
          user_id=excluded.user_id,
          created_at=excluded.created_at,
          expires_at=excluded.expires_at,
          filename=excluded.filename,
          model=excluded.model,
          conversion_type=excluded.conversion_type,
          status=excluded.status,
          transactions_count=excluded.transactions_count,
          pages_count=excluded.pages_count,
          scanned_likely=excluded.scanned_likely,
          ocr_used=excluded.ocr_used,
          ocr_pages_processed=excluded.ocr_pages_processed,
          duration_ms=excluded.duration_ms,
          error_code=excluded.error_code,
          error_stage=excluded.error_stage,
          error_subcode=excluded.error_subcode,
          exception_class=excluded.exception_class,
          layout_inference_name=excluded.layout_inference_name,
          layout_inference_confidence=excluded.layout_inference_confidence,
          selected_parser=excluded.selected_parser,
          parser_selection_reason=excluded.parser_selection_reason,
          pdf_page_count=excluded.pdf_page_count,
          extracted_char_count=excluded.extracted_char_count,
          ocr_attempted=excluded.ocr_attempted,
          ocr_engine=excluded.ocr_engine,
          file_sha256=excluded.file_sha256,
          canonical_warning_transactions_count=excluded.canonical_warning_transactions_count,
          balance_consistency_failed=excluded.balance_consistency_failed,
          quality_status=excluded.quality_status,
          quality_score=excluded.quality_score,
          quality_rule_version=excluded.quality_rule_version,
          quality_reason_codes_json=excluded.quality_reason_codes_json,
          parser_confidence_band=excluded.parser_confidence_band,
          parser_coverage_rate=excluded.parser_coverage_rate,
          warning_types_json=excluded.warning_types_json,
          failure_diagnostics_json=excluded.failure_diagnostics_json
        """,
        (
            processing_id,
            user_id,
            effective_created_at,
            expires_at,
            filename.strip() or f"{processing_id}.pdf",
            model.strip() or "Nao identificado",
            conversion_type.strip() or "pdf-ofx",
            status.strip() or "Sucesso",
            transactions_count,
            pages_count,
            scanned_likely,
            bool(ocr_used),
            max(0, int(ocr_pages_processed or 0)),
            max(0, int(duration_ms or 0)),
            (error_code or "").strip() or None,
            (error_stage or "").strip() or None,
            (error_subcode or "").strip() or None,
            (exception_class or "").strip() or None,
            (layout_inference_name or "").strip() or None,
            float(layout_inference_confidence) if layout_inference_confidence is not None else None,
            (selected_parser or "").strip() or None,
            (parser_selection_reason or "").strip() or None,
            pdf_page_count,
            extracted_char_count,
            bool(ocr_attempted),
            (ocr_engine or "").strip() or None,
            (file_sha256 or "").strip() or None,
            max(0, int(canonical_warning_transactions_count or 0)),
            max(0, int(balance_consistency_failed or 0)),
            assessment.status,
            assessment.score,
            assessment.rule_version,
            reason_codes_json,
            (parser_confidence_band or "").strip() or None,
            float(parser_coverage_rate) if parser_coverage_rate is not None else None,
            warning_types_json,
            failure_diagnostics_json,
        ),
    )
    replace_quality_issues(
        conn,
        execute=execute,
        conversion_id=processing_id,
        identity_type="registered",
        created_at=effective_created_at,
        quality_reason_codes=assessment.reason_codes,
        quality_issues=quality_issues,
        error_code=(error_code or "").strip() or None,
    )


def list_user_conversions(
    conn,
    *,
    fetchall: Callable,
    now_provider: Callable[[], datetime],
    user_id: str,
    limit: int = 20,
) -> list[dict[str, str | int]]:
    rows = fetchall(
        conn,
        """
        SELECT
          analysis_id,
          created_at,
          expires_at,
          filename,
          model,
          conversion_type,
          status,
          transactions_count,
          pages_count
        FROM user_conversions
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, max(1, min(limit, 100))),
    )

    now = now_provider()
    items: list[dict[str, str | int]] = []
    for row in rows:
        status = str(row["status"] or "Sucesso")
        expires_at = str(row["expires_at"] or "").strip()
        if expires_at and _is_expired(expires_at, now):
            status = "Expirado"
        item: dict[str, str | int] = {
            "processing_id": str(row["analysis_id"]),
            "created_at": str(row["created_at"]),
            "filename": str(row["filename"]),
            "model": str(row["model"]),
            "conversion_type": str(row["conversion_type"]),
            "status": status,
        }
        tx_count = row["transactions_count"]
        if isinstance(tx_count, int):
            item["transactions_count"] = tx_count
        page_count = row["pages_count"]
        if isinstance(page_count, int):
            item["pages_count"] = page_count
        items.append(item)
    return items


def _is_expired(expires_at_raw: str, now: datetime) -> bool:
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now
