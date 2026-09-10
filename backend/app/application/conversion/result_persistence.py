from app.application.conversion.persisted_conversion_result import PersistedConversionResult
from app.application.conversion_pipeline import ConversionPipelineResult
from app.application.repositories import AnalysisRepository
from app.application.structured_conversion import build_structured_conversion_result_from_analysis_data


def persist_conversion_result(
    *,
    storage: AnalysisRepository,
    pipeline_result: ConversionPipelineResult,
) -> PersistedConversionResult:
    analysis_data = pipeline_result.analysis_data
    analysis_data.structured_result = build_structured_conversion_result_from_analysis_data(analysis_data)
    expires_at = storage.save_analysis(analysis_data)
    return PersistedConversionResult(
        analysis_data=analysis_data,
        operational_summary=pipeline_result.operational_summary,
        top_expenses_rows=list(pipeline_result.top_expenses_rows),
        expires_at=expires_at,
    )
