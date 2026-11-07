"""Explain extraction evidence without changing transactions or review decisions."""


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def build_pdf_review_guidance(analysis):
    if _get(analysis, "file_type") != "pdf" or _get(analysis, "financial_document") is not None:
        return None
    metrics = _get(analysis, "pdf_processing_metrics") or {}

    def metric(key, default=0):
        return _get(metrics, key, default) or default

    warnings = set(metric("canonical_warning_types_list", []))
    rows = [*(_get(analysis, "report_transactions") or []), *(_get(analysis, "preview_transactions") or [])]
    for row in rows:
        if not _get(row, "is_deleted", False):
            warnings.update(_get(row, "warning_types", []) or [])
    reason = metric("export_recommendation_reason", "")
    comparison = _get(analysis, "statement_balance_check")
    mismatch = bool(
        metric("balance_consistency_failed")
        or _get(comparison, "difference", 0)
        or warnings
        & {
            "balance_consistency_failed",
            "financial_validation_failed",
            "printed_summary_balance_mismatch",
            "printed_totals_differ_from_extracted_movements",
        }
    )
    if mismatch:
        return {
            "category": "financial_discrepancy",
            "title": "Divergência financeira encontrada",
            "message": "Os lançamentos e os saldos ou totais de referência não conferem. "
            "Confira a comparação no PDF antes de alterar valores ou concluir a revisão.",
        }
    shared = "shared_operation_reference" in warnings
    if not shared and not warnings and metric("export_recommendation", "") != "review_recommended":
        return None
    contextual = any(
        metric(key)
        for key in (
            "partial_statement",
            "document_period_unavailable",
            "opening_balance_ambiguous",
            "balance_bounds_unavailable",
            "ocr_unresolved_rows",
            "ocr_review_fields_count",
            "ocr_review_rows_count",
            "ocr_annotations_detected",
        )
    ) or any(_get(part, "requires_review", False) for part in (_get(analysis, "document_parts") or []))
    controls = (
        metric("financial_controls_validated") == 1
        and metric("balance_validation_status", "") == "validated"
        and metric("printed_balance_checks") > 0
        and metric("statement_row_coverage_rate") == 1
    )
    only_reference = warnings <= {"shared_operation_reference", "manual_review_recommended"}
    known_reason = reason in {
        "",
        "low_confidence_band",
        "medium_confidence_band",
        "high_confidence_band",
        "complete_movement_section_and_printed_controls_validated",
    }
    if shared and controls and only_reference and not contextual and known_reason:
        return {
            "category": "information",
            "title": "Referências de operação compartilhadas",
            "message": "Na leitura original, os controles financeiros impressos e a cobertura dos movimentos conferiram. "
            "Há lançamentos com a mesma referência de operação; isso não comprova duplicidade. "
            "Eles foram preservados como aparecem no PDF. Não exclua movimentos apenas pela referência.",
        }
    details = []
    if reason == "unverified_visual_recovery":
        details.append("A leitura por OCR foi concluída, mas sua validação financeira está incompleta.")
    # Counts describe their original, limited scope. Neither one proves a global balance.
    if "financial_validation_failed" not in warnings:
        if metric("balance_consistency_checked"):
            details.append(f"Na leitura original, {metric('balance_consistency_checked')} verificações entre saldos conferiram.")
        elif metric("printed_balance_checks"):
            details.append(f"Na leitura original, foram conferidos {metric('printed_balance_checks')} controles impressos.")
    if (metric("balance_consistency_checked") or metric("printed_balance_checks")) and "financial_validation_failed" not in warnings:
        details.append("Essas verificações não certificam o saldo global nem a cobertura completa do período. Confira os limites no PDF.")
    else:
        details.append(
            "A validação automática não dispõe de controles suficientes para confirmar todos os dados. "
            "Confira os lançamentos e a cobertura do período no PDF; "
            "confiança técnica reduzida, por si só, não indica erro financeiro."
        )
    if shared:
        details.append("Uma referência de operação compartilhada não comprova duplicidade; os movimentos foram preservados.")
    return {"category": "validation_incomplete", "title": "Validação automática incompleta", "message": " ".join(details)}
