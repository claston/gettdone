from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QuotaConsumptionResult:
    consumed_units: int
    quota_remaining: int


class QuotaValidatorService:
    def __init__(self, *, access_control_service) -> None:
        self.access_control_service = access_control_service

    def ensure_conversion_quota_available(self, *, identity) -> None:
        self.access_control_service.ensure_quota_available(identity, required_units=1)

    def consume_quota_for_conversion(self, *, identity, analysis) -> QuotaConsumptionResult:
        consumed_units = self.resolve_consumed_units(identity=identity, analysis=analysis)
        quota_remaining = self.access_control_service.consume_quota(
            identity,
            consumed_units=consumed_units,
        )
        return QuotaConsumptionResult(
            consumed_units=consumed_units,
            quota_remaining=quota_remaining,
        )

    def resolve_consumed_units(self, *, identity, analysis) -> int:
        if getattr(identity, "quota_mode", "conversion") != "pages":
            return 1
        pages_count = self._resolve_processed_pages(analysis)
        return pages_count if pages_count is not None else 1

    def _resolve_processed_pages(self, analysis) -> int | None:
        metrics = getattr(analysis, "pdf_processing_metrics", None)
        if metrics is None:
            return None
        if isinstance(metrics, dict):
            page_count = int(metrics.get("page_count", 0) or 0)
        else:
            page_count = int(getattr(metrics, "page_count", 0) or 0)
        return max(1, page_count)
