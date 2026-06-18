from app.application.conversion_pipeline import ConversionPipelineResult
from app.application.repositories import AnalysisRepository
from app.application.structured_conversion import build_structured_conversion_result_from_analysis_data
from app.schemas import (
    AnalyzeResponse,
    BeforeAfterPreview,
    CategorySummary,
    Insight,
    OperationalSummary,
    ReconciliationSummary,
    TopExpense,
    TransactionPreview,
)


def persist_and_build_analyze_response(
    *,
    storage: AnalysisRepository,
    pipeline_result: ConversionPipelineResult,
) -> AnalyzeResponse:
    analysis_data = pipeline_result.analysis_data
    analysis_data.structured_result = build_structured_conversion_result_from_analysis_data(analysis_data)
    expires_at = storage.save_analysis(analysis_data)

    extension = analysis_data.file_type
    insights = [
        Insight(
            type=f"{extension}_real_parser",
            title=f"{extension.upper()} processado",
            description=f"Extrato {extension.upper()} processado com parser real e normalizacao inicial.",
        )
    ]
    review_insight = _build_export_review_insight(
        extension=extension,
        pdf_processing_metrics=analysis_data.pdf_processing_metrics,
    )
    if review_insight is not None:
        insights.append(review_insight)

    return AnalyzeResponse(
        analysis_id=analysis_data.analysis_id,
        file_type=extension,
        semantic_type=analysis_data.semantic_type,
        semantic_confidence=analysis_data.semantic_confidence,
        semantic_evidence=analysis_data.semantic_evidence or [],
        transactions_total=analysis_data.transactions_total,
        total_inflows=analysis_data.total_inflows,
        total_outflows=analysis_data.total_outflows,
        net_total=analysis_data.net_total,
        operational_summary=OperationalSummary(
            total_volume=pipeline_result.operational_summary.total_volume,
            inflow_count=pipeline_result.operational_summary.inflow_count,
            outflow_count=pipeline_result.operational_summary.outflow_count,
            reconciled_entries=pipeline_result.operational_summary.reconciled_entries,
            unmatched_entries=pipeline_result.operational_summary.unmatched_entries,
        ),
        reconciliation=ReconciliationSummary(
            matched_groups=analysis_data.matched_groups,
            reversed_entries=analysis_data.reversed_entries,
            potential_duplicates=analysis_data.potential_duplicates,
        ),
        categories=[
            CategorySummary(
                category="Outros",
                total=analysis_data.net_total,
                count=analysis_data.transactions_total,
            )
        ],
        top_expenses=[
            TopExpense(
                description=row.description,
                amount=row.amount,
                date=row.date,
                category="Outros",
            )
            for row in pipeline_result.top_expenses_rows
        ],
        insights=insights,
        preview_transactions=[
            TransactionPreview(
                date=row.date,
                description=row.description,
                amount=row.amount,
                running_balance=row.running_balance,
                category=row.category,
                reconciliation_status=row.reconciliation_status,
                warning_types=list(row.warning_types or []),
            )
            for row in analysis_data.preview_transactions
        ],
        preview_before_after=[
            BeforeAfterPreview(
                date=row.date,
                description_before=row.description_before,
                description_after=row.description_after,
                amount_before=row.amount_before,
                amount_after=row.amount_after,
            )
            for row in analysis_data.preview_before_after
        ],
        expires_at=expires_at,
        updated_at=analysis_data.updated_at,
        layout_inference_name=analysis_data.layout_inference_name,
        layout_inference_confidence=analysis_data.layout_inference_confidence,
        pdf_processing_metrics=analysis_data.pdf_processing_metrics,
        opening_balance=analysis_data.opening_balance,
        closing_balance=analysis_data.closing_balance,
        bank_name=analysis_data.bank_name,
        bank_branch=analysis_data.bank_branch,
        account_number=analysis_data.account_number,
        bank_code=analysis_data.bank_code,
    )


def _build_export_review_insight(
    *,
    extension: str,
    pdf_processing_metrics,
) -> Insight | None:
    if extension != "pdf" or pdf_processing_metrics is None:
        return None
    recommendation = str(_metrics_get(pdf_processing_metrics, "export_recommendation", "")).strip().lower()
    if recommendation != "review_recommended":
        return None
    reason = str(_metrics_get(pdf_processing_metrics, "export_recommendation_reason", "")).strip()
    reason_suffix = f" ({reason})" if reason else ""
    return Insight(
        type="pdf_export_review_recommended",
        title="Revisao manual recomendada",
        description=(
            "A exportacao permanece disponivel, mas recomendamos revisar as transacoes antes de concluir"
            f"{reason_suffix}."
        ),
    )


def _metrics_get(metrics, key: str, default):
    if metrics is None:
        return default
    if isinstance(metrics, dict):
        return metrics.get(key, default)
    return getattr(metrics, key, default)
