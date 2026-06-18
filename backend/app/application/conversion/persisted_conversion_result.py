from dataclasses import dataclass

from app.application.conversion_pipeline import OperationalPipelineSummary
from app.application.models import AnalysisData, NormalizedTransaction


@dataclass(frozen=True, slots=True)
class PersistedConversionResult:
    analysis_data: AnalysisData
    operational_summary: OperationalPipelineSummary
    top_expenses_rows: list[NormalizedTransaction]
    expires_at: str | None
