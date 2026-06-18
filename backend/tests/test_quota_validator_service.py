from types import SimpleNamespace

from app.application.conversion.quota_validator_service import QuotaConsumptionResult, QuotaValidatorService


class FakeAccessControlService:
    def __init__(self) -> None:
        self.ensure_calls: list[tuple[object, int]] = []
        self.consume_calls: list[tuple[object, int]] = []

    def ensure_quota_available(self, identity, *, required_units: int = 1) -> None:
        self.ensure_calls.append((identity, required_units))

    def consume_quota(self, identity, *, consumed_units: int = 1) -> int:
        self.consume_calls.append((identity, consumed_units))
        return 200 - consumed_units


def test_ensure_conversion_quota_available_uses_single_required_unit() -> None:
    access_control_service = FakeAccessControlService()
    identity = SimpleNamespace(quota_mode="pages")
    service = QuotaValidatorService(access_control_service=access_control_service)

    service.ensure_conversion_quota_available(identity=identity)

    assert access_control_service.ensure_calls == [(identity, 1)]


def test_consume_quota_for_conversion_uses_page_count_in_pages_mode() -> None:
    access_control_service = FakeAccessControlService()
    identity = SimpleNamespace(quota_mode="pages")
    analysis = SimpleNamespace(pdf_processing_metrics={"page_count": 3})
    service = QuotaValidatorService(access_control_service=access_control_service)

    result = service.consume_quota_for_conversion(identity=identity, analysis=analysis)

    assert result == QuotaConsumptionResult(consumed_units=3, quota_remaining=197)
    assert access_control_service.consume_calls == [(identity, 3)]


def test_consume_quota_for_conversion_uses_single_unit_in_conversion_mode() -> None:
    access_control_service = FakeAccessControlService()
    identity = SimpleNamespace(quota_mode="conversion")
    analysis = SimpleNamespace(pdf_processing_metrics={"page_count": 8})
    service = QuotaValidatorService(access_control_service=access_control_service)

    result = service.consume_quota_for_conversion(identity=identity, analysis=analysis)

    assert result == QuotaConsumptionResult(consumed_units=1, quota_remaining=199)
    assert access_control_service.consume_calls == [(identity, 1)]
